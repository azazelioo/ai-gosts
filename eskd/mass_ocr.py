"""Local OCR for a labelled, ruled mass cell when the PDF text layer is insufficient."""
import re
from collections import defaultdict

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageOps
from scipy.ndimage import binary_closing, binary_fill_holes, binary_opening, find_objects, label


def cell_pairs(ink, *, fragmented=False):
    labels, _ = label(~ink)
    by_top = defaultdict(list)
    for key, area in enumerate(find_objects(labels), 1):
        y, x = area
        width, height = x.stop - x.start, y.stop - y.start
        if width >= 12 and height >= 5 and (labels[area] == key).mean() > (.28 if fragmented else .55):
            by_top[y.start].append(fitz.Rect(x.start, y.start, x.stop, y.stop))
    for row in by_top.values():
        for head in row:
            if fragmented and .65 < head.width/head.height < 2.2:
                estimated = _header_over_paired_columns(ink, head, by_top)
                if estimated is not None:
                    yield estimated, head
            if not 1.5 < head.width/head.height < 9:
                continue
            gap = max(3, round(head.height*.35))
            for top in range(int(head.y1), int(head.y1)+gap+1):
                for value in by_top.get(top, ()):
                    left = abs(head.x0-value.x0)
                    right = abs(head.x1-value.x1)
                    if not .6 < value.height/head.height < 5:
                        continue
                    if max(left, right) <= gap:
                        yield head, value
                    elif (fragmented and min(left, right) <= gap and .5*value.width < head.width < value.width
                          and value.height > 1.5*head.height):
                        # On scans touching letters split the white space in the
                        # header. Its intact value cell supplies the column edges.
                        yield fitz.Rect(value.x0, head.y0, value.x1, head.y1), value


def _header_over_paired_columns(ink, value, by_top):
    """Find the upper rule when lettering cuts the header's white interior."""
    x0, y0, x1, y1 = map(int, value)
    gap = max(3, round(value.height*.12))
    adjacent = (other for row in range(y0-gap, y0+gap+1) for other in by_top.get(row, ()))
    if not any(0 <= other.x0-x1 <= gap and abs(other.y1-y1) <= gap
               and .6*value.width < other.width < 1.7*value.width for other in adjacent):
        return None
    start = max(0, y0-round(value.height*.7))
    stop = y0-max(4, round(value.height*.16))
    if stop <= start:
        return None
    inset = max(2, round(value.width*.08))
    lines = np.flatnonzero(ink[start:stop, x0+inset:x1-inset].mean(axis=1) > .85)
    if not lines.size:
        return None
    return fitz.Rect(x0, start+int(lines[-1])+1, x1, y0-1)


def cell_image(page, box, scale):
    # The component bounds describe the white interior of the ruled cell.
    pix = page.get_pixmap(clip=box, matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY)
    image = pix.pil_image()
    margin = max(2, round(scale*.65))
    if min(image.size) <= 2*margin:
        return None
    image = image.crop((margin, margin, image.width-margin, image.height-margin))
    bounds = ImageOps.invert(image).point(lambda v: 255 if v > 110 else 0).getbbox()
    return image.crop(bounds) if bounds else None


def numeric_candidate(engine, image):
    # Check the digit count and decimal separator independently of OCR punctuation.
    components, _ = label(np.asarray(image) < 150)
    pieces = sorted(enumerate(find_objects(components), 1), key=lambda part: part[1][1].start)
    if pieces:
        tallest = max(box[0].stop-box[0].start for _, box in pieces)
        # Keep isolated punctuation. Discard tiny specks only if there is
        # already a distinct, larger punctuation-sized component.
        if any(.12*tallest < box[0].stop-box[0].start < .5*tallest for _, box in pieces):
            pieces = [(key, box) for key, box in pieces
                      if max(box[0].stop-box[0].start, box[1].stop-box[1].start) > tallest*.07]
    glyphs = [box for _, box in pieces]
    heights = [b[0].stop-b[0].start for b in glyphs]
    if not heights or len(heights) > 15:
        return None
    height = max(heights)
    digits = [i for i, h in enumerate(heights) if h >= .65*height]
    marks = [i for i in range(len(glyphs)) if i not in digits]
    if not digits or len(marks) > 1:
        return None
    sep = ''
    require_printed_separator = False
    if marks:
        i = marks[0]
        y, x = glyphs[i]
        baseline = np.median([glyphs[j][0].stop for j in digits])
        if not 0 < i < len(glyphs)-1 or y.start < baseline-.5*height:
            return None
        sep = ',' if y.stop-baseline > .07*height else '.'
        if heights[i] > .4*height or y.start < baseline-.28*height:
            if sep != ',' or x.stop-x.start > .35*height:
                return None
            require_printed_separator = True
    padded = ImageOps.expand(image, 32, fill=255)
    scores = defaultdict(list)
    upright_reading = None
    for tilt in (0, .2, .32):
        upright = padded.transform(padded.size, Image.Transform.AFFINE,
                                   (1, -tilt, tilt*padded.height/2, 0, 1, 0),
                                   Image.Resampling.BICUBIC, fillcolor=255)
        text, conf = engine(upright, numeric=True)
        if not re.fullmatch(r'\d+(?:[.,]\d+)?', text) or conf < 30:
            continue
        number = re.sub('[.,]', '', text)
        if require_printed_separator and text != number[:marks[0]] + sep + number[marks[0]:]:
            continue
        if len(number) == len(digits):
            scores[number].append(conf)
            if tilt == 0 and conf >= 85:
                upright_reading = text
    agreed = [number for number, confidence in scores.items()
              if len(confidence) >= 2 and max(confidence) >= 65]
    if len(agreed) == 1:
        number = agreed[0]
    elif not agreed and upright_reading:
        # Straight print can be degraded by both italic corrections. Require
        # an exact second reading including punctuation on a binary image.
        text, confidence = engine(padded.point(lambda p: 255 if p >= 150 else 0), numeric=True)
        if text != upright_reading or confidence < 75 or any(
                max(votes) >= 75 for other, votes in scores.items() if other != re.sub('[.,]', '', text)):
            return None
        number = re.sub('[.,]', '', text)
        expected = number if not sep else number[:marks[0]] + sep + number[marks[0]:]
        if text != expected:
            return None
    else:
        return None
    if upright_reading and re.sub('[.,]', '', upright_reading) != number:
        return None
    # Two enclosed counters distinguish a printed 8 from a common OCR reading of 9.
    verified = []
    for digit, index in zip(number, digits):
        key, box = pieces[index]
        mask = components[box] == key
        holes = binary_fill_holes(mask) & ~mask
        interiors, _ = label(holes)
        substantial = np.count_nonzero(np.bincount(interiors.ravel())[1:] >= max(3, mask.size*.015))
        if substantial >= 2 and digit != '8':
            return None
        if digit == '9' and substantial == 1:
            rows = np.nonzero(holes)[0]
            if rows.mean() >= mask.shape[0]*.44 and rows.max() >= mask.shape[0]*.63:
                # A central counter leaves little room for the tail of a 9.
                # Scanned zeros with a broken contour often get this reading.
                single = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8))
                isolated, conf = engine(ImageOps.expand(single, 16, fill=255), numeric=True)
                if isolated != '9' or conf < 85:
                    # Confirm the alternative on the entire original word AND
                    # against another observed zero in this very cell.
                    if not _matching_zero(mask, components, pieces, digits, number):
                        return None
                    alternative = number[:len(verified)] + '0' + number[len(verified)+1:]
                    original, confidence = engine(ImageOps.expand(image, 16, fill=255), numeric=True)
                    if (confidence < 50 or not re.fullmatch(r'\d+(?:[.,]\d+)?', original)
                            or re.sub('[.,]', '', original) != alternative):
                        return None
                    digit = '0'
        if digit == '1' and _has_seven_strokes(mask):
            single = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8))
            isolated, conf = engine(ImageOps.expand(single, 16, fill=255), numeric=True)
            if isolated != '7' or conf < 85:
                return None
            digit = isolated
        verified.append(digit)
    number = ''.join(verified)
    if sep:
        split = marks[0]
        return number[:split] + sep + number[split:]
    return number


def _matching_zero(mask, components, pieces, digits, number):
    def normalise(shape):
        return np.asarray(Image.fromarray(shape).resize((32, 40), Image.Resampling.NEAREST))

    prototype = normalise(mask)
    for digit, position in zip(number, digits):
        if digit != '0':
            continue
        key, box = pieces[position]
        other = components[box] == key
        if not .8 <= (mask.shape[1]/mask.shape[0]) / (other.shape[1]/other.shape[0]) <= 1.25:
            continue
        inner, count = label(binary_fill_holes(other) & ~other)
        if count != 1 or np.count_nonzero(inner) < .04*other.size:
            continue
        target = normalise(other)
        if 2*(prototype & target).sum() >= .85*(prototype.sum()+target.sum()):
            return True
    return False


def _has_seven_strokes(mask):
    height, width = mask.shape
    if width < height*.42 or height < 10:
        return False
    roof = mask[:max(1, height//6)].sum(axis=1).max()
    shoulder = np.nonzero(mask[height//4:height//2])[1]
    foot = np.nonzero(mask[-max(2, height//6):])[1]
    return (roof >= width*.78 and shoulder.size and foot.size
            and np.median(shoulder)-np.median(foot) > width*.22
            and foot.max()-foot.min() < width*.5)


def _mass_headers(page, engine, pairs, scale):
    found = []
    for head, value in pairs:
        head, value = head/scale, value/scale
        zoom = min(8, max(2, 36/head.height))
        header = cell_image(page, head, zoom)
        if header is None:
            continue
        padded = ImageOps.expand(header, 16, fill=255)
        name, _ = engine(padded, numeric=False)
        name = re.sub(r'[\W_]+', '', name.casefold())
        typical_cell = 2 < head.width/head.height < 6 and value.height > 1.5*head.height
        if name != 'масса' and (typical_cell or name.endswith('асса') or (name.startswith('м') and 3 <= len(name) <= 7)):
            for resolution in dict.fromkeys((zoom*.85, 4, min(8, zoom*1.35))):
                retry = cell_image(page, head, resolution)
                if retry is not None:
                    name, _ = engine(ImageOps.expand(retry, 12, fill=255), numeric=False)
                    if re.sub(r'[\W_]+', '', name.casefold()) == 'масса':
                        name = 'масса'
                        break
        if name != 'масса':
            continue
        if any((value & box).get_area() > .8 * max(value.get_area(), box.get_area())
               for _, box, _ in found):
            continue
        found.append((head, value, zoom))
    return found


def scan_mass_fields(page, engine):
    scale = min(2, 1600/max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY)
    ink = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(pix.height, pix.width) < 170
    pairs = list(cell_pairs(ink))
    if len(pairs) > 64:
        pairs = [(h, v) for h, v in pairs if v.height > 1.5*h.height]
    if len(pairs) > 64:
        pairs = []
    display = page.get_displaylist()
    found = _mass_headers(display, engine, pairs, scale)
    if not found and page.get_image_info():
        # Broken rules on scans can connect adjacent white cells. Reconstruct
        # the grid for locating them; recognise text on the original pixels.
        grid = np.zeros_like(ink)
        for gap, line in [((1, 3), (1, 15)), ((3, 1), (15, 1))]:
            closed = binary_closing(ink, structure=np.ones(gap, dtype=bool))
            grid |= binary_opening(closed, structure=np.ones(line, dtype=bool))
        repaired = list(cell_pairs(ink, fragmented=True))
        repaired.extend(cell_pairs(grid))
        repaired = list({(tuple(h), tuple(v)): (h, v) for h, v in repaired}.values())
        repaired = [(h, v) for h, v in repaired if v.height > 1.5*h.height]
        if len(repaired) <= 64:
            found = _mass_headers(display, engine, repaired, scale)
    return [_read_located_field(display, head, box, zoom, engine)
            for head, box, zoom in sorted(found, key=lambda item: (item[0].y0, item[0].x0))]


def _read_located_field(display, head, box, zoom, engine):
    field = dict(status='unread', method='cell_ocr', box_pt=list(box), header_box_pt=list(head))
    # Thin strokes and serif digits can split into pieces at the header's scale.
    # Re-render only the confirmed value cell, with a bounded pixel budget.
    for resolution in dict.fromkeys((zoom, min(8, zoom*2, 1200/max(box.width, box.height)))):
        image = cell_image(display, box, resolution)
        if image is None:
            break
        value = numeric_candidate(engine, image)
        if value:
            return dict(field, status='review', value=value)
    return field


def scan_mass(page, engine):
    fields = scan_mass_fields(page, engine)
    return fields[0] if len(fields) == 1 and fields[0]['status'] == 'review' else None

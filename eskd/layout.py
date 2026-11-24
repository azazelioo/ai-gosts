"""Locate near-page frames and labelled title blocks in the displayed orientation."""
import re
import os

import numpy as np
import pymupdf as fitz


def bounds(mask):
    # Search near the paper edges for continuous strokes. A text table alone
    # rarely spans both nearly the entire sheet width and its entire height.
    h, w = mask.shape
    ys = np.flatnonzero(mask.sum(axis=1) > w * .72)
    xs = np.flatnonzero(mask.sum(axis=0) > h * .72)
    if len(xs) < 2 or len(ys) < 2:
        return None
    # Prefer the inner ruled frame over a thin line tracing the paper edge.
    left, right = xs[xs < w*.17], xs[xs > w*.83]
    top, bottom = ys[ys < h*.12], ys[ys > h*.88]
    if not all(map(len, (left, right, top, bottom))):
        return None
    l, r, t, b = int(left[-1]), int(right[0]), int(top[-1]), int(bottom[0])
    if min(mask[t,l:r].mean(), mask[b,l:r].mean(),
           mask[t:b,l].mean(), mask[t:b,r].mean()) < .85:
        return None
    return l, t, r, b


class StampOCR:
    def __init__(self, stack, path):
        self.stack = stack
        self.path = path or os.environ.get("TESSDATA_PREFIX")
        self.engine = None
        self.unavailable = False

    def available(self):
        if self.unavailable:
            return False
        if self.engine is None:
            try:
                from tesserocr import PSM, PyTessBaseAPI
                args = {"path": str(self.path)} if self.path else {}
                self.engine = self.stack.enter_context(PyTessBaseAPI(lang="rus", psm=PSM.SPARSE_TEXT, **args))
            except (ImportError, RuntimeError):
                self.unavailable = True
                return False
        return True

    def cell_text(self, image, *, numeric):
        from tesserocr import PSM
        self.engine.SetPageSegMode(PSM.SINGLE_LINE)
        self.engine.SetVariable("tessedit_char_whitelist", "0123456789.," if numeric else "")
        self.engine.SetImage(image)
        return self.engine.GetUTF8Text().strip(), self.engine.MeanTextConf()

    def read(self, pixmap):
        if not self.available():
            return ""
        from tesserocr import PSM
        self.engine.SetPageSegMode(PSM.SPARSE_TEXT)
        self.engine.SetVariable("tessedit_char_whitelist", "")
        from PIL import Image
        from scipy.ndimage import binary_opening
        ink = np.asarray(pixmap.pil_image().convert("L")) < 180
        grid = binary_opening(ink, structure=np.ones((1, 65))) | binary_opening(ink, structure=np.ones((55, 1)))
        image = Image.fromarray((~(ink & ~grid)).astype(np.uint8)*255)
        # Compensate the slant of technical lettering after removing long rules.
        pad = round(.2*image.height)
        image = image.transform((image.width+pad, image.height), Image.Transform.AFFINE,
                                (1, -.2, 0, 0, 1, 0), fillcolor=255)
        self.engine.SetImage(image)
        return self.engine.GetUTF8Text().strip()


def _empty_mass_cell(page, header):
    """Verify a closed cell below the recognised header, including its visible contents."""
    scale = 3
    w, h = header.width, header.height
    clip = (header + (-w, 0, w, 4*h)) & page.rect
    pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(scale, scale),
                          colorspace=fitz.csGRAY, alpha=False)
    ink = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(pix.height, pix.width) < 235
    cx = round((header.x0 + w/2)*scale - pix.x)
    half = max(2, round(w*.3*scale))
    if cx-half < 0 or cx+half >= pix.width:
        return None
    # Horizontal rules must cross the middle of the mass column.
    rows = np.flatnonzero(ink[:, cx-half:cx+half].all(axis=1))
    bands = np.split(rows, np.flatnonzero(np.diff(rows) > 1)+1)
    for upper, lower in zip(bands, bands[1:]):
        top, bottom = int(upper[-1]), int(lower[0])
        if not (.55*h < (pix.y+top)/scale-header.y0 < 1.35*h
                and h < (bottom-top)/scale < 4*h):
            continue
        cols = np.flatnonzero(ink[top:bottom+1, :].all(axis=0))
        lefts, rights = cols[cols < cx-half], cols[cols > cx+half]
        if not (lefts.size and rights.size):
            continue
        left, right = int(lefts[-1]), int(rights[0])
        if not (.9*w < (right-left)/scale < 2*w):
            continue
        cell = ink[top:bottom+1, left:right+1]
        if not (cell[0].all() and cell[-1].all()) or cell[2:-2, 2:-2].any():
            continue
        return [(pix.x+left)/scale, (pix.y+top)/scale,
                (pix.x+right)/scale, (pix.y+bottom)/scale]
    return None


def read_mass_field(page):
    fields = _text_mass_fields(page)
    if len(fields) == 1 and fields[0]["status"] != "unread":
        return fields[0]
    return {"status": "unread"}


def _text_mass_fields(page):
    """Use neighbouring title-block headers to isolate the mass column."""
    words = [(fitz.Rect(w[:4]) * page.rotation_matrix, w[4])
             for w in page.get_text("words")]
    headers = [box for box, word in words if word.casefold() == "масса"]
    fields = [_text_mass_at(page, words, header) for header in headers]
    return [field for field in fields if field is not None]


def _text_mass_at(page, words, h):
    neighbors = [(box, word.casefold().rstrip(".")) for box, word in words
                 if abs(box.y0-h.y0) < h.height/2]
    if not (any(word == "лит" and 0 < h.x0-box.x0 < 2*h.width for box, word in neighbors)
            and any(word == "масштаб" and 0 < box.x0-h.x0 < 2*h.width for box, word in neighbors)):
        return None
    values = [(box, word) for box, word in words
              if h.y0 + h.height*.45 < box.y0 < h.y0 + h.height*2.4
              and abs(box.x0+box.x1-h.x0-h.x1) < h.width*.85]
    if not values:
        cell = _empty_mass_cell(page, h)
        if cell:
            return {"status": "empty", "box_pt": cell, "header_box_pt": list(h)}
    if len(values) != 1 or not re.fullmatch(r"\d+(?:[.,]\d+)?", values[0][1]):
        return {"status": "unread", "header_box_pt": list(h)}
    box, value = values[0]
    return {"status": "read", "value": value, "box_pt": list(box),
            "header_box_pt": list(h), "decimal_point": "." in value}


def inspect_layout(page, dest, ocr):
    scale = min(1.5, 1100/max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    ink = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(pix.height, pix.width) < 218
    frame = bounds(ink)
    fields = _text_mass_fields(page)
    if (not fields or any(f["status"] == "unread" for f in fields)) and ocr.available():
        from .mass_ocr import scan_mass_fields
        for candidate in scan_mass_fields(page, ocr.cell_text):
            header = fitz.Rect(candidate["header_box_pt"])
            matches = [idx for idx, field in enumerate(fields)
                       if header.intersects(fitz.Rect(field["header_box_pt"]))]
            if not matches:
                fields.append(candidate)
            elif len(matches) == 1 and fields[matches[0]]["status"] == "unread":
                fields[matches[0]] = candidate
    fields.sort(key=lambda field: (field["header_box_pt"][1], field["header_box_pt"][0]))
    for i, field in enumerate(fields, start=1):
        if field.get("method") == "cell_ocr":
            suffix = "" if len(fields) == 1 else f"-{i}"
            crop = f"page-{page.number+1}-mass{suffix}.png"
            zone = fitz.Rect(field["header_box_pt"]) | fitz.Rect(field["box_pt"])
            page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=zone).save(dest/crop)
            field["crop"] = crop
    result = {"coordinates": "displayed PDF page, points from top left", "mass_fields": fields,
              "frame": None, "title_block": None,
              "mass_field": fields[0] if len(fields) == 1 else {"status": "unread"}}
    if not frame:
        return result
    rect = fitz.Rect(*(v/scale for v in frame))
    result["frame"] = [round(v, 2) for v in rect]
    # Standard and shortened stamps occupy the lower right part of a ruled sheet.
    zone = fitz.Rect(max(rect.x0, rect.x1-540), max(rect.y0, rect.y1-170), rect.x1+2, rect.y1+2)
    words = page.get_text("words", clip=zone * page.derotation_matrix, sort=True)
    located = [(fitz.Rect(w[:4])*page.rotation_matrix, w[4]) for w in words]
    labels = {s.casefold().strip(".") for _, s in located}
    keys = labels & {"изм", "подп", "дата", "разраб", "масштаб", "листов", "докум", "лит"}
    method = "frame_and_printed_labels"
    region = zone * page.derotation_matrix
    text = page.get_text("text", clip=region, sort=True).strip()
    if len(keys) < 3 or not keys & {"изм", "разраб", "подп"}:
        if len(text) > 60 or not page.get_image_info():
            return result
        corner = ink[int(zone.y0*scale):int(zone.y1*scale), int(zone.x0*scale):int(zone.x1*scale)]
        rows = np.flatnonzero(corner.mean(axis=1) > .55)
        if len(rows) < 3 or np.count_nonzero(np.diff(rows)>1) < 2:
            return result
        sample = page.get_pixmap(matrix=fitz.Matrix(3,3), clip=zone)
        text = ocr.read(sample)
        keys = set(re.findall(r"\b(?:масштаб|подп|изм|листов|разраб|масса|дата|докум)\b", text.casefold()))
        if len(keys) < 3 or not keys & {"изм", "разраб", "подп"}:
            result["stamp_review"] = "ocr_unavailable" if ocr.unavailable else "unconfirmed_labels"
            return result
        method = "frame_and_ocr_labels"
    crop = f"page-{page.number+1}-stamp.png"
    page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=zone).save(dest/crop)
    scale_values = []
    for a,s in located:
        if s.casefold() == "масштаб":
            scale_values.extend(v for b,v in located if abs(a.x0-b.x0)<25 and
                                0 < b.y0-a.y0 < 44 and re.fullmatch(r"\d+(?:[.,]\d+)?:\d+(?:[.,]\d+)?", v))
    result["title_block"] = {"method": method, "crop": crop,
                             "box_pt": list(zone), "labels": sorted(keys),
                             "text": text,
                             "scale": scale_values[0] if len(scale_values)==1 else None}
    return result

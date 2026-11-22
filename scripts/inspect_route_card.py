"""Locate designation and material-profile fields in ESTD route cards with a PDF text layer.

Run: python scripts/inspect_route_card.py input.pdf --pages 11 22 --output result
Install scripts/requirements-route-card.txt. For outlined text install rus.traineddata
in <python-prefix>/share/tessdata or pass its directory via --tessdata. No network at runtime.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from contextlib import ExitStack
from html import escape
from pathlib import Path
from time import perf_counter

import numpy as np
import pdfplumber
from PIL import Image, ImageDraw, ImageOps


LABEL = "Обозначение документа"
DPI = 216
HEADER = "#006bca"
VALUE = "#bd4900"


def runs(mask):
    """Centres of contiguous line strokes, in pixels."""
    idx = np.flatnonzero(mask)
    groups = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    return [int((g[0] + g[-1]) // 2) for g in groups if len(g)]


def surrounding(lines, start, end):
    before = [x for x in lines if x < start]
    after = [x for x in lines if x > end]
    if before and after:
        return before[-1], after[0]
    return None


# A field definition names its printed heading and tells where to read the value.
FIELDS = (
    ("designation", LABEL, "row_a"),
    ("profile_size", "Профиль и размеры", "below"),
)


def word_box(w):
    return w["x0"], w["top"], w["x1"], w["bottom"]


def glyph_rotation(char):
    a, b = char["matrix"][:2]
    angle = math.degrees(math.atan2(b, a))
    quarter = round(angle / 90) * 90
    return quarter % 360 if abs(angle - quarter) < 3 else None


def form_rotation(chars, stream):
    # Use printed form headings so upright marginal text cannot outvote the form.
    angles = set()
    for _, label, _ in FIELDS:
        for hit in re.finditer(r"\s*".join(label.split()), stream, re.IGNORECASE):
            angles.update(glyph_rotation(c) for c in chars[hit.start():hit.end()] if c["text"].strip())
    return angles.pop() if len(angles) == 1 and None not in angles else 0


def rotate_box(box, width, height, degrees):
    l, t, r, b = box
    if degrees == 90:
        return height-b, l, height-t, r
    if degrees == 180:
        return width-r, height-b, width-l, height-t
    if degrees == 270:
        return t, width-r, b, width-l
    return box


def rotated_chars(page, degrees):
    ox, oy = page.bbox[:2]
    result = []
    for c in page.chars:
        if glyph_rotation(c) != degrees:
            continue
        local = c["x0"]-ox, c["top"]-oy, c["x1"]-ox, c["bottom"]-oy
        l,t,r,b = rotate_box(local, page.width, page.height, degrees)
        result.append(dict(c, x0=l, top=t, x1=r, bottom=b, doctop=t, upright=True,
                           width=r-l, height=b-t))
    return result


class LocalOCR:
    """Load Russian Tesseract data on demand and reuse it across selected pages."""

    def __init__(self, stack, tessdata):
        self.stack = stack
        self.path = tessdata
        self.engine = None
        self.unavailable = False

    def line(self, image, box):
        if self.unavailable:
            return ""
        if self.engine is None:
            try:
                from tesserocr import PyTessBaseAPI, PSM
                self.engine = self.stack.enter_context(
                    PyTessBaseAPI(path=str(self.path), lang="rus", psm=PSM.SINGLE_LINE))
            except (ImportError, RuntimeError, OSError):
                self.unavailable = True
                return ""
        l,t,r,b = box
        if r-l <= 6 or b-t <= 6:
            return ""
        fragment = image.crop((l+3, t+3, r-3, b-3))
        self.engine.SetImage(ImageOps.expand(fragment, border=12, fill="white"))
        line = " ".join(self.engine.GetUTF8Text().split())
        return line if self.engine.MeanTextConf() >= 65 else ""


def symbol_profile(chars):
    # Symbol encodes spaces, digits and operators in the private-use area.
    # Decode individual in-cell glyphs before assembling words across PDF spaces.
    mapping = {0xF020: " ", 0xF0B4: "×", 0xF0C6: "∅", 0xF02C: ",", 0xF02E: "."}
    decoded = []
    found = False
    for c in chars:
        text = c["text"]
        if c["fontname"].split("+")[-1].casefold() == "symbol" and len(text) == 1:
            code = ord(text)
            if 0xF000 <= code <= 0xF0FF:
                found = True
                text = chr(code-0xF000) if 0xF030 <= code <= 0xF039 else mapping.get(code)
                if text is None:
                    return ""
        decoded.append(dict(c, text=text))
    if not found:
        return ""
    value = " ".join(w["text"] for w in pdfplumber.utils.extract_words(decoded))
    return re.sub(r"∅\s*(?=\d)", "⌀ ", value)


def find_anchor(chars, stream, words_by_text, label):
    tokens = label.casefold().split()
    matches = [(word_box(w), w) for w in words_by_text.get(tokens[0], ())]
    for token in tokens[1:]:
        extended = []
        for box, prev in matches:
            height = max(1, prev["bottom"] - prev["top"])
            for w in words_by_text.get(token, ()):
                if (0 <= w["x0"] - prev["x1"] <= 2 * height
                        and abs(w["top"] - prev["top"]) < height / 2):
                    extended.append(((box[0], min(box[1], w["top"]), w["x1"],
                                      max(box[3], w["bottom"])), w))
        matches = extended
    if matches:
        return (matches[0][0], "words") if len(matches) == 1 else (None, "ambiguous")

    # A broken OCR layer can place successive glyphs backwards in a line.
    # Require the full literal heading in PDF stream order and the same baseline;
    # the approximate location still has to pass the four-border raster check.
    hits = list(re.finditer(r"\s*".join(map(re.escape, tokens)), stream, re.IGNORECASE))
    if len(hits) != 1:
        return None, "ambiguous" if hits else "missing"
    glyphs = chars[hits[0].start():hits[0].end()]
    height = max(c["height"] for c in glyphs)
    first = glyphs[:len(tokens[0])]
    if (max(c["top"] for c in glyphs) - min(c["top"] for c in glyphs) > height/2
            or sum(a["x0"] > b["x0"] for a, b in zip(first, first[1:])) < len(first)/2):
        return None, "missing"
    box = (min(c["x0"] for c in glyphs), min(c["top"] for c in glyphs),
           max(c["x1"] for c in glyphs), max(c["bottom"] for c in glyphs))
    return box, "pdf_stream"


def locate(ink, anchor, words, layout, read_marker=None):
    x0, y0, x1, y1 = anchor
    height = max(1, y1-y0)
    if x1 <= x0:
        return None, None, "Недостаточно геометрических данных."
    # Work on a strip around the label instead of three full-page masks.
    start = max(0, y0 - 5*height)
    stop = min(ink.shape[0], y1 + 15*height)
    band = ink[start:stop]
    pad = np.pad(band, ((1, 1), (0, 0)))
    horiz = pad[:-2] | pad[1:-1] | pad[2:]
    cy = (y0+y1)//2 - start
    edges = surrounding(runs(horiz[:, x0:x1].mean(axis=1) > .84), cy, cy)
    if not edges or edges[1] - edges[0] > 4*height:
        return None, None, "Горизонтальные границы не подтверждены."
    top, bottom = edges
    pad = np.pad(band[top:bottom+1], ((0, 0), (1, 1)))
    vert = pad[:, :-2] | pad[:, 1:-1] | pad[:, 2:]
    sides = surrounding(runs(vert.mean(axis=0) > .91), x0, x1)
    if not sides:
        return None, None, "Боковые границы не подтверждены."
    left, right = sides
    if min(horiz[k, left:right].mean() for k in edges) < .80:
        return None, None, "Не подтверждена полная ширина ячейки."
    header = (left, start+top, right, start+bottom)
    # A dashed inner divider can disappear from the raster projection. If
    # another printed heading is captured, do not return the combined cell.
    for text, wx0, wy0, wx1, wy1 in words:
        center_x = (wx0 + wx1) / 2
        if (header[1] < (wy0 + wy1)/2 < header[3] and left < center_x < right
                and not x0 <= center_x <= x1 and sum(c.isalpha() for c in text) >= 3):
            return None, None, "Внутри рамки есть посторонняя подпись. Нужен просмотр."
    rows = runs(horiz[:, left:right].mean(axis=1) > .80)
    bounds = None
    if layout == "below":
        lower = next((y for y in rows if y > bottom+2), None)
        if lower is not None and .6*height < lower-bottom < 4*height:
            # Both vertical borders must continue through the value row.
            if all(band[bottom:lower+1, max(0,x-2):x+3].any(axis=1).mean() > .80
                   for x in sides):
                bounds = bottom, lower
    elif layout == "row_a":
        centers = sorted((w[2]+w[4])//2-start for w in words
                         if re.fullmatch(r"[АA](?:\d{2})?", w[0]) and w[3] < left
                         and start+bottom < (w[2]+w[4])/2 < start+bottom+8*height)
        if centers:
            bounds = surrounding(rows, centers[0], centers[0])
        elif read_marker:
            # The printed A heading identifies the service-code column. Read only
            # that cell when the operation rows have been converted to outlines.
            markers = [w for w in words if w[0] in ("А", "A") and w[3] < left
                       and header[1] < (w[2]+w[4])/2 < header[3]]
            if len(markers) == 1:
                marker = markers[0]
                for upper, lower in zip(rows, rows[1:]):
                    if upper < bottom-2 or not .6*height < lower-upper < 4*height:
                        continue
                    section = band[upper+2:lower-1, :left]
                    walls = runs(section.mean(axis=0) > .8)
                    cell = surrounding(walls, marker[1], marker[3])
                    if cell and cell[1]-cell[0] < 10*height:
                        match = read_marker((cell[0], start+upper, cell[1], start+lower))
                        if match is None:
                            break  # An unreadable earlier marker could itself be row A.
                        if match:
                            bounds = upper, lower
                            break
    else:
        raise ValueError(f"Unknown field layout: {layout}")
    if bounds and bounds[0] >= bottom-2 and .6*height < bounds[1]-bounds[0] < 4*height:
        return header, (left, start+bounds[0], right, start+bounds[1]), "Сверьте текст PDF с оригиналом."
    return header, None, "Заголовок найден. Область значения требует просмотра."


def read_page(page, dest, ocr=None):
    chars = page.chars
    stream = "".join(c["text"] if len(c["text"]) == 1 else "\ufffd" for c in chars)
    rotation = form_rotation(chars, stream)
    if rotation:
        chars = rotated_chars(page, rotation)
        stream = "".join(c["text"] if len(c["text"]) == 1 else "\ufffd" for c in chars)
        raw_words = pdfplumber.utils.extract_words(chars)
    else:
        raw_words = page.extract_words()
    by_text = {}
    for w in raw_words:
        by_text.setdefault(w["text"].casefold(), []).append(w)
    anchors = [find_anchor(chars, stream, by_text, label) for _, label, _ in FIELDS]
    fields = {}
    for (key, label, _), (_, method) in zip(FIELDS, anchors):
        fields[key] = {"label": label, "method": method, "status": "review", "header": None,
                       "value": None, "note": "Нет однозначного текстового якоря."}
    result = {"page": page.page_number, "page_size_pt": [page.width, page.height],
              "has_text_layer": bool(page.chars),
              "rotation_clockwise": rotation, "fields": fields}
    if any(box is not None for box, _ in anchors):
        image = page.to_image(resolution=DPI, force_mediabox=True, antialias=bool(rotation)).original
        source_sx, source_sy = image.width/page.width, image.height/page.height
        sx, sy = source_sx, source_sy
        ox, oy = page.bbox[:2]
        if rotation:
            transpose = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                         270: Image.Transpose.ROTATE_90}
            image = image.transpose(transpose[rotation])
            ox = oy = 0
            if rotation in (90, 270):
                sx, sy = sy, sx

        def pixels(box):
            l,t,r,b = box
            return round((l-ox)*sx), round((t-oy)*sy), round((r-ox)*sx), round((b-oy)*sy)

        words = [(w["text"], *pixels(word_box(w))) for w in raw_words]
        ink = np.asarray(image.convert("L")) < 150

        def region(box, kind):
            l,t,r,b = box
            parts = [(x0, text) for text,x0,y0,x1,y1 in words
                     if max(0, min(x1,r)-max(x0,l)) >= .75*max(1,x1-x0) and t < (y0+y1)/2 < b]
            filename = f"page-{page.page_number}-{kind}.png"
            image.crop((l,t,r+1,b+1)).save(dest / filename)
            source_box = rotate_box(box, image.width, image.height, -rotation % 360)
            raw = " ".join(text for _,text in sorted(parts))
            cell = {"kind": kind, "box_px": list(source_box),
                    "box_pt": [round(v/scale, 2) for v, scale in zip(
                        source_box, (source_sx, source_sy, source_sx, source_sy))],
                    "raw_text": raw, "text": raw, "text_method": "pdf" if raw else "unread",
                    "crop": filename}
            if kind == "profile_size-value":
                glyphs = [c for c in chars if l < ((c["x0"]+c["x1"])/2-ox)*sx < r
                          and t < ((c["top"]+c["bottom"])/2-oy)*sy < b]
                value = symbol_profile(glyphs)
                if value:
                    cell.update(text=value, text_method="pdf_symbol")
            if kind in ("row-a", "profile_size-value") and not cell["text"] and ocr:
                value = ocr.line(image, box)
                if value:
                    cell.update(text=value, text_method="ocr")
            return cell

        outlines = []
        for (key, _, layout), (box, _) in zip(FIELDS, anchors):
            if box is None:
                continue
            entry = fields[key]
            def read_marker(marker_box):
                text = ocr.line(image, marker_box)
                marker = re.fullmatch(r"([А-ЯA])\s*(\d{2})?", text)
                if not marker:
                    return None
                if marker[1] in ("А", "A") and marker[2]:
                    entry["row_label"] = "А" + marker[2]
                    return True
                return False

            header, value, note = locate(ink, pixels(box), words, layout, read_marker if ocr else None)
            entry["note"] = note
            if header:
                entry["header"] = region(header, "header" if key == "designation" else key+"-header")
                entry["status"] = "header_only"
                outlines.append((header, HEADER))
            if value:
                entry["value"] = region(value, "row-a" if key == "designation" else key+"-value")
                entry["status"] = "located"
                outlines.append((value, VALUE))
        if ocr and ocr.unavailable:
            result["ocr_note"] = "Локальный OCR недоступен. Проверьте tesserocr и каталог rus.traineddata."
        if outlines:
            draw = ImageDraw.Draw(image)
            for box, color in outlines:
                draw.rectangle(box, outline=color, width=4)
            image.thumbnail((1650, 1650))
            result["preview"] = f"page-{page.page_number}.png"
            image.save(dest / result["preview"], compress_level=2)
    # Preserve the original designation-field contract for earlier consumers.
    primary = fields["designation"]
    result.update(status=primary["status"], note=primary["note"], header=primary["header"],
                  first_row_a=primary["value"])
    return result


def write_report(dest, source, pages):
    sections = []
    navigation = " ".join(
        f'<a href="#page-{p["page"]}">{p["page"]}</a>' for p in pages if "preview" in p
    )
    for p in pages:
        parts = [f'<section id="page-{p["page"]}"><h2>Страница {p["page"]}</h2>']
        if p.get("rotation_clockwise"):
            parts.append(f'<p>Автоповорот по часовой стрелке на {p["rotation_clockwise"]}°.</p>')
        if p.get("ocr_note"):
            parts.append(f'<p>{escape(p["ocr_note"])}</p>')
        if "preview" in p:
            parts.append(f'<a href="{p["preview"]}"><img loading="lazy" class="sheet" src="{p["preview"]}" alt="Разметка листа"></a>')
        if "review_image" in p:
            parts.append(f'<p>Мало доступного текста, основная часть листа может находиться в изображении.</p>'
                         f'<a href="{p["review_image"]}"><img loading="lazy" class="sheet" src="{p["review_image"]}" '
                         f'alt="Миниатюра страницы для просмотра"></a>')
        for field in p["fields"].values():
            parts.append(f'<h3>{escape(field["label"])}</h3><p>{escape(field["note"])}</p>')
            if "row_label" in field:
                parts.append(f'<p>Строка {escape(field["row_label"])}</p>')
            if field["method"] == "pdf_stream":
                parts.append("<p>Название восстановлено по порядку символов PDF. Ниже сохранён исходный текст слоя.</p>")
            for key, title in [("header", "Синий контур · заголовок"), ("value", "Оранжевый контур · значение")]:
                cell = field[key]
                if cell:
                    text = cell["raw_text"] or "Текстовый слой не дал символов"
                    parts.append(f'<p>{title}</p><img loading="lazy" class="crop" src="{cell["crop"]}" alt="Исходный фрагмент">'
                                 f'<p>Текст PDF: <code>{escape(text)}</code></p>')
                    if cell["text_method"] in ("ocr", "pdf_symbol"):
                        method = "Локальный OCR" if cell["text_method"] == "ocr" else "Декодирование Symbol"
                        parts.append(f'<p>{method}: <code>{escape(cell["text"])}</code></p>')
        section = "".join(parts) + "</section>"
        if "preview" not in p and "review_image" not in p:
            section = f'<details><summary>Страница {p["page"]} · нет подтверждённых рамок</summary>{section}</details>'
        sections.append(section)
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Графы маршрутной карты</title><style>body{font:17px/1.5 system-ui,sans-serif;max-width:1000px;'
            'margin:30px auto;padding:0 18px;color:#162737;background:#fafafa}section{padding:15px 0 30px;'
            'border-top:1px solid #aaa}.sheet{width:100%;height:auto}.crop{max-width:100%;background:white;'
            'outline:1px solid #ccc}h2,h3{margin-bottom:.4em}code{overflow-wrap:anywhere}</style>'
            f'<h1>Графы маршрутной карты</h1><p>{escape(source.name)} · физические страницы PDF</p>'
            + (f'<p>Листы с разметкой: {navigation}</p>' if navigation else "")
            + "".join(sections) + "</html>")
    (dest / "index.html").write_text(html, encoding="utf-8")


def analyze_document(source, dest, ocr, selection=None, progress=None):
    """Inspect physical pages in order; None selects the complete document."""
    with pdfplumber.open(source) as pdf:
        count = len(pdf.pages)
        requested = range(1, count+1) if selection is None else selection
        if any(n < 1 or n > count for n in requested):
            raise ValueError(f"PDF pages must be in 1..{count}")
        dest.mkdir(parents=True, exist_ok=True)
        pages = []
        for n in dict.fromkeys(requested):
            page = pdf.pages[n-1]
            tick = perf_counter()
            try:
                result = read_page(page, dest, ocr)
                # Full-document review: a page number alone can hide a raster appendix.
                if (selection is None and "preview" not in result and
                        sum(len(c["text"].strip()) for c in page.chars) <= 40 and
                        any(i["width"]*i["height"] >= .20*page.width*page.height for i in page.images)):
                    name = f"page-{n}-review.jpg"
                    thumbnail = page.to_image(resolution=80, antialias=True).original.convert("RGB")
                    thumbnail.thumbnail((1000, 1000))
                    thumbnail.save(dest/name, quality=80)
                    result["review_image"] = name
                if selection is None:
                    result["seconds"] = round(perf_counter()-tick, 4)
                pages.append(result)
            finally:
                page.close()
            if progress and (n % 25 == 0 or n == count):
                progress(n, count)
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"source": source.name, "sha256": digest, "label": LABEL, "dpi": DPI,
              "coordinates": "page-relative, top-left origin; box_px at render DPI", "pages": pages}
    (dest / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(dest, source, pages)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pages", type=int, nargs="+")
    selection.add_argument("--all-pages", action="store_true", help="Inspect every physical PDF page")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tessdata", type=Path, help="Local directory containing rus.traineddata",
                        default=os.environ.get("TESSDATA_PREFIX", Path(sys.prefix)/"share"/"tessdata"))
    args = parser.parse_args()
    with ExitStack() as stack:
        ocr = LocalOCR(stack, args.tessdata)
        try:
            report = analyze_document(args.pdf, args.output, ocr, None if args.all_pages else args.pages or [11])
        except ValueError as exc:
            parser.error(str(exc))
    for p in report["pages"]:
        print(f"page {p['page']}: {p['status']}")
    print(args.output.resolve() / "index.html")


if __name__ == "__main__":
    main()

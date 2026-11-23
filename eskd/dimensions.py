"""Read dimension annotations; coordinates refer to the displayed PDF page in points."""
import re

import pymupdf as fitz
from .dimension_lines import link_dimensions

NUM = r"\d+(?:[.,]\d+)?"
DEV = rf"(?:±|[+-]){NUM}"
ANGLE = rf"{NUM}°(?:{NUM}′)?(?:{NUM}″)?"
ANGLE_DEV = rf"(?:±|[+-]){NUM}[°′″]"
MEASURE = re.compile(
    rf"(?:(?P<count>\d+)×)?(?P<prefix>[⌀R])?(?P<value>{NUM})"
    rf"(?P<fit>[A-Za-z]{{1,2}}\d{{1,2}})?(?P<deviation>{DEV}(?:/{DEV})?)?"
)
CHAMFER = re.compile(rf"(?P<width>{NUM})×(?P<angle>{ANGLE})")


def parse_label(text):
    compact = re.sub(r"\s+", "", text).translate(str.maketrans(
        {"Ø":"⌀", "ø":"⌀", "∅":"⌀", "−":"-", "х":"×", "x":"×", "'":"′", '"':"″"}))
    reference = compact.startswith("(") and compact.endswith(")")
    if reference:
        compact = compact[1:-1]
    m = CHAMFER.fullmatch(compact)
    if m:
        return {"kind":"chamfer", "label":compact, **m.groupdict()}
    m = re.fullmatch(rf"(?P<angle>{ANGLE})(?P<deviation>{ANGLE_DEV})?", compact)
    if m:
        return {"kind":"angle", "label":compact, **m.groupdict()}
    m = MEASURE.fullmatch(compact)
    if not m:
        return None
    values = {k:v for k,v in m.groupdict().items() if v is not None}
    kind = {"⌀":"diameter", "R":"radius"}.get(values.pop("prefix", None), "linear")
    return {"kind":kind, "label":compact, "reference":reference, **values}


def pdf_lines(page):
    pending = None
    for block in page.get_text("dict", flags=0)["blocks"]:
        for line in block.get("lines", []):
            raw = "".join(s["text"] for s in line["spans"]).strip()
            if not raw or len(raw)>80:
                continue
            parts = []
            for span in line["spans"]:
                text = span["text"]
                font = span["font"].split("+")[-1]
                if font == "Symbol_A":
                    text = text.translate(str.maketrans({"Ç":"⌀", "Å":"°"}))
                if font.startswith("GOSTType"):
                    text = text.replace("•", "×")
                parts.append(text)
            decoded = "".join(parts).strip()
            box = fitz.Rect(line["bbox"])
            direction = line["dir"]
            if pending:
                old, label, prior, axis = pending
                if (axis == direction and re.fullmatch(r"[+−-]\d+(?:[.,]\d+)?['\"′″]?", decoded)
                        and parse_label(label+decoded) and adjacent(prior, box, axis)):
                    pending = (old+" "+raw, label+decoded, prior | box, axis)
                    continue
                yield old, label, prior*page.rotation_matrix
            pending = raw, decoded, box, direction
    if pending:
        old, label, prior, _ = pending
        yield old, label, prior*page.rotation_matrix


def adjacent(first, second, axis):
    """Require the exponent to start at the trailing end of the same text baseline."""
    dx, dy = axis
    def projected(box, ux, uy):
        return [p.x*ux+p.y*uy for p in (box.tl, box.tr, box.bl, box.br)]
    gap = min(projected(second, dx, dy))-max(projected(first, dx, dy))
    across = projected(first, -dy, dx)
    other = projected(second, -dy, dx)
    return -2 <= gap <= 3 and min(max(across),max(other)) >= max(min(across),min(other))


def scan_words(page, tessdata, region):
    import os
    from tesserocr import PyTessBaseAPI, PSM, RIL
    path = tessdata or os.environ.get("TESSDATA_PREFIX")
    options = {"path":str(path)} if path else {}
    scale = min(3, 3400/max(region.width, region.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=region, colorspace=fitz.csGRAY)
    image = pix.pil_image()
    with PyTessBaseAPI(lang="rus", psm=PSM.SPARSE_TEXT, **options) as engine:
        for upright in (True, False):
            view = image if upright else image.rotate(-90, expand=True)
            engine.SetImage(view)
            engine.Recognize()
            it = engine.GetIterator()
            if it is None:
                continue
            while True:
                box = it.BoundingBox(RIL.WORD)
                if box and it.Confidence(RIL.WORD)>=55:
                    word = (it.GetUTF8Text(RIL.WORD) or "").strip()
                    x,y,r,b = box
                    if not upright:
                        x,y,r,b = y,image.height-r,b,image.height-x
                    yield word, word, fitz.Rect((x+pix.x)/scale,(y+pix.y)/scale,
                                                (r+pix.x)/scale,(b+pix.y)/scale)
                if not it.Next(RIL.WORD):
                    break


def inspect_dimensions(page, layout, dest, tessdata):
    stamp = layout.get("title_block")
    excluded = fitz.Rect(stamp["box_pt"]) if stamp else None
    annotations = []
    seen = {}
    def collect(lines, method):
        for raw, decoded, box in lines:
            if excluded and excluded.contains((box.tl+box.br)*.5):
                continue
            parsed = parse_label(decoded)
            if not parsed or box.is_empty:
                continue
            previous = seen.setdefault(parsed["label"], [])
            if any((box & other).get_area() > .75*min(box.get_area(), other.get_area()) for other in previous):
                continue
            previous.append(box)
            plain = parsed["kind"]=="linear" and not parsed.get("fit") and not parsed.get("deviation")
            annotations.append({**parsed, "raw":raw, "box_pt":[round(v,2) for v in box],
                                "method":method, "status":"review" if plain or method=="ocr" else "parsed"})
    collect(pdf_lines(page), "pdf")
    note = None
    scanned = []
    for info in page.get_image_info():
        region = (fitz.Rect(info["bbox"])*page.rotation_matrix) & page.rect
        if region.get_area() < page.rect.get_area()*.08 or min(region.width, region.height)<45:
            continue
        if any(prior.contains(region) for prior in scanned):
            continue
        if any(a["status"]=="parsed" and region.contains(fitz.Rect(a["box_pt"])) for a in annotations):
            continue
        try:
            collect(scan_words(page, tessdata, region), "ocr")
            scanned.append(region)
        except (ImportError, RuntimeError) as exc:
            note = f"OCR unavailable: {exc}"
            break
    result = {"items": annotations, "note": note}
    if annotations:
        link_dimensions(page, annotations)
        from PIL import ImageDraw
        scale = min(2,1800/max(page.rect.width,page.rect.height))
        image = page.get_pixmap(matrix=fitz.Matrix(scale,scale)).pil_image()
        draw = ImageDraw.Draw(image)
        for item in annotations:
            if "dimension_line" in item:
                draw.line([tuple(round(v*scale) for v in point) for point in item["dimension_line"]["endpoints_pt"]],
                          fill="#00856e", width=2)
            draw.rectangle([round(v*scale) for v in item["box_pt"]],
                           outline="#cc6600" if item["status"]=="review" else "#167dc2", width=2)
        result["preview"] = f"page-{page.number+1}-dimensions.png"
        image.save(dest/result["preview"])
    return result

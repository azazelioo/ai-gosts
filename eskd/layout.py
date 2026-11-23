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

    def read(self, pixmap):
        if self.unavailable:
            return ""
        if self.engine is None:
            try:
                from tesserocr import PSM, PyTessBaseAPI
                args = {"path": str(self.path)} if self.path else {}
                self.engine = self.stack.enter_context(PyTessBaseAPI(lang="rus", psm=PSM.SPARSE_TEXT, **args))
            except (ImportError, RuntimeError):
                self.unavailable = True
                return ""
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


def inspect_layout(page, dest, ocr):
    scale = min(1.5, 1100/max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    ink = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(pix.height, pix.width) < 218
    frame = bounds(ink)
    result = {"coordinates": "displayed PDF page, points from top left",
              "frame": None, "title_block": None}
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

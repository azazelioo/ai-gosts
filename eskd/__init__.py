from contextlib import ExitStack
import pymupdf as fitz
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from .dimensions import inspect_dimensions
from .layout import StampOCR, inspect_layout


@dataclass(frozen=True)
class AnalysisResult:
    directory: Path
    report: dict


def analyze_pdf(source, *, output_dir, pages=None, layout=True, tessdata=None, dimensions="auto"):
    if dimensions not in ("auto", "on", "off") or (not layout and dimensions == "on"):
        raise ValueError("dimensions must be auto/on/off; on requires layout")
    source = Path(source)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=MODULE+"-", dir=root)).resolve()
    try:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with ExitStack() as stack:
            pdf = PdfReader(stack.enter_context(source.open("rb")))
            count = len(pdf.pages)
            document = stack.enter_context(fitz.open(source)) if layout else None
            ocr = StampOCR(stack, tessdata)
            selection = range(1, count+1) if pages is None else tuple(pages)
            if not selection or any(type(n) is not int or not 1 <= n <= count for n in selection):
                raise ValueError("Expected physical page numbers starting at one")
            results = []
            for n in dict.fromkeys(selection):
                result = inspect_page(pdf.pages[n-1], n)
                if layout:
                    page = document[n-1]
                    result["layout"] = inspect_layout(page, run, ocr)
                    if dimensions == "on" or (dimensions == "auto" and (pages is not None or result["layout"]["title_block"])):
                        result["dimensions"] = inspect_dimensions(page, result["layout"], run, tessdata)
                results.append(result)
        report = {"module": MODULE, "schema_version": 1, "source": source.name,
                  "sha256": digest, "standard": STANDARD, "pages": results}
        summarize(report)
        (run/"result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except BaseException:
        shutil.rmtree(run)
        raise
    return AnalysisResult(run, report)


MODULE = "eskd"
STANDARD = "ГОСТ 2.301-68, пп. 4–6, таблицы 1–3, издание 2007 г."
FORMATS = {"A0": (841, 1189), "A1": (594, 841), "A2": (420, 594),
           "A3": (297, 420), "A4": (210, 297), "A5": (148, 210)}
for base, short, first, lengths in [
    ("A0", 1189, 2, [1682, 2523]), ("A1", 841, 3, [1783, 2378]),
    ("A2", 594, 3, [1261, 1682, 2102]), ("A3", 420, 3, [891, 1189, 1486, 1783, 2080]),
    ("A4", 297, 3, [630, 841, 1051, 1261, 1471, 1682, 1892]),
]:
    for multiple, length in enumerate(lengths, first):
        FORMATS[f"{base}x{multiple}"] = (short, length)


def tolerance(nominal):
    if nominal <= 150:
        return 1.5
    return 2 if nominal <= 600 else 3


def capabilities():
    return {"module": MODULE, "schema_version": 1, "tasks": ["measure_pdf_sheet", "locate_frame", "read_title_block", "read_dimension_annotations", "link_vector_dimension_lines"],
            "formats_mm": dict(FORMATS), "standard": STANDARD}


def inspect_page(page, number):
    box = page.mediabox
    factor = float(page.user_unit) * 25.4 / 72
    width, height = float(box.width) * factor, float(box.height) * factor
    if page.rotation % 180 == 90:
        width, height = height, width
    short, long = sorted((width, height))
    match = next((code for code, (a, b) in FORMATS.items()
                  if abs(short-a) <= tolerance(a) and abs(long-b) <= tolerance(b)), None)
    return {"page": number, "sheet_mm": [round(width, 2), round(height, 2)],
            "format": match, "status": "matched" if match else "review",
            "method": "MediaBox", "box_pdf_units": [float(v) for v in box],
            "user_unit": float(page.user_unit), "rotation": page.rotation}


def summarize(report):
    from collections import Counter
    report["formats"] = dict(Counter(p["format"] or "review" for p in report["pages"]))

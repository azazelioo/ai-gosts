from contextlib import ExitStack
import pymupdf as fitz
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .scan import ScanReader


@dataclass(frozen=True)
class AnalysisResult:
    directory: Path
    report: dict


def analyze_pdf(source, *, output_dir, pages=None, tessdata=None):
    source = Path(source)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=MODULE+"-", dir=root)).resolve()
    try:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with fitz.open(source) as document, ExitStack() as stack:
            total_pages = len(document)
            selection = range(1, len(document)+1) if pages is None else tuple(pages)
            if not selection or any(type(n) is not int or not 1 <= n <= len(document) for n in selection):
                raise ValueError("Expected physical page numbers starting at one")
            results = []
            ocr = ScanReader(stack, tessdata)
            for n in dict.fromkeys(selection):
                page = document[n-1]
                text = page.get_text("text")
                method = "pdf"
                image_area = page.rect.get_area()
                if len(text.strip()) < 80 and any(fitz.Rect(info["bbox"]).get_area() > .2*image_area
                                                  for info in page.get_image_info()):
                    scanned = ocr.read(page)
                    if scanned.strip():
                        text = scanned
                        method = "ocr"
                    elif ocr.unavailable:
                        method = "ocr_unavailable"
                result = inspect_text(text, n)
                result["method"] = method
                results.append(result)
        report = {"module": MODULE, "schema_version": 1, "source": source.name,
                  "selection_scope": "full" if len(results)==total_pages else "selected",
                  "sha256": digest, "standard": STANDARD, "pages": results}
        summarize(report)
        (run/"result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except BaseException:
        shutil.rmtree(run)
        raise
    return AnalysisResult(run, report)


import re

MODULE = "espd"
STANDARD = "ГОСТ 19.505-79, п. 1.2, изм. 1"
SECTIONS = ("Назначение программы", "Условия выполнения программы",
            "Выполнение программы", "Сообщения оператору")


def capabilities():
    return {"module": MODULE, "schema_version": 1, "tasks": ["locate_operator_guide_sections",
            "read_scanned_headings", "find_message_evidence", "compare_toc_page_numbers"],
            "sections": list(SECTIONS), "standard": STANDARD}


def inspect_page(page, number):
    return inspect_text(page.extract_text() or "", number)


def inspect_text(text, number):
    lines = [" ".join(line.split()) for line in text.splitlines()]
    hits = []
    contents = any(line.casefold() in ("содержание", "оглавление") for line in lines)
    toc_entries = []
    if contents:
        for line in lines:
            match = re.fullmatch(r"(?:\d+[.)]?\s+)?(.+?)\s*[.…]{2,}\s*(\d+)\.?",line)
            if match:
                name = next((s for s in SECTIONS if s.casefold()==match[1].strip().casefold()),None)
                if name:
                    toc_entries.append({"section":name,"printed_page":int(match[2])})
    header = [line for line in lines if line][:4]
    numbers = [int(line) for line in header if re.fullmatch(r"[0-9]{1,5}",line)]
    if not contents:
        canon = {name.casefold(): name for name in SECTIONS}
        for i, line in enumerate(lines):
            # Some PDF exporters put a bullet and its list item on separate lines.
            if i and lines[i-1] in {"•", "●", "–", "—", "-"}:
                continue
            heading = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", line).rstrip(".").casefold()
            candidates = re.split(r"\s+(?:и|/)\s+|\s*;\s*", heading)
            end = i+1
            if heading and i+1 < len(lines) and heading+" "+lines[i+1].casefold() in canon:
                candidates = [heading+" "+lines[i+1].casefold()]
                end += 1
            if "назначение и условия выполнения программы" == heading:
                candidates = ["назначение программы", "условия выполнения программы"]
            if not all(part in canon for part in candidates):
                continue
            excerpt = []
            for body in lines[end:]:
                if not body:
                    continue
                other = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", body).rstrip(".").casefold()
                if other in canon or re.match(r"^\d+[.)]?\s+[А-ЯЁ]", body):
                    break
                excerpt.append(body)
                if sum(map(len, excerpt)) >= 240:
                    break
            for part in candidates:
                hits.append({"section": canon[part], "text": " ".join(lines[i:end]),
                             "method": "combined_heading" if len(candidates)>1 else "whole_line_heading",
                             "body_excerpt": " ".join(excerpt)[:300]})
    evidence = []
    if not contents:
        for i, line in enumerate(lines):
            if re.search(r"сообщени\w*\s+об?\s+(?:ошибк|сбо)|(?:выводится|появится|выдается)\s+сообщение",
                         line, re.IGNORECASE):
                evidence.append(" ".join(lines[i:i+3])[:300])
    return {"page": number, "headings": hits, "has_text": bool(text.strip()), "contents_page": contents,
            "message_evidence": evidence,"toc_entries":toc_entries,
            "printed_page_candidate":numbers[0] if len(numbers)==1 else None}


def summarize(report):
    sections = {}
    for name in SECTIONS:
        pages = [p["page"] for p in report["pages"] if any(h["section"] == name for h in p["headings"])]
        sections[name] = {"status": "located" if pages else "review", "pages": pages}
    report["sections"] = sections
    checks = []
    for toc in report["pages"]:
        for entry in toc["toc_entries"]:
            body = [p for p in report["pages"] if p["page"] in sections[entry["section"]]["pages"]]
            matches = [p["page"] for p in body if p["printed_page_candidate"]==entry["printed_page"]]
            checks.append({**entry,"toc_page":toc["page"],"heading_pages":[p["page"] for p in body],
                           "matching_pages":matches,"status":"matched" if matches else "review"})
    report["toc_checks"] = checks
    report["message_review_pages"] = [p["page"] for p in report["pages"] if p["message_evidence"]]

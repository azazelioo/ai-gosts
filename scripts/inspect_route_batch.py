"""Inspect all physical pages of one or more local PDFs. With no paths, open a file picker."""
import argparse
import json
import os
import sys
import tempfile
import webbrowser
from contextlib import ExitStack
from html import escape
from pathlib import Path
from time import perf_counter

if __package__:
    from .inspect_route_card import FIELDS, FORM_TYPES, LocalOCR, analyze_document
else:
    from inspect_route_card import FIELDS, FORM_TYPES, LocalOCR, analyze_document


def summarize(report):
    pages = report["pages"]
    fields = {}
    for key, _, _ in FIELDS:
        entries = [page["fields"][key] for page in pages]
        fields[key] = {
            "located": sum(f["value"] is not None for f in entries),
            "with_text": sum(bool((f["value"] or {}).get("text")) for f in entries),
        }
    form_pages = {code: [p["page"] for p in pages if p.get("form", {}).get("code") == code]
                  for code in FORM_TYPES}
    return {"sha256": report["sha256"], "pages": len(pages), "fields": fields,
            "form_pages": form_pages,
            "slowest_page": max(({"page": p["page"], "seconds": p["seconds"]} for p in pages),
                                key=lambda p: p["seconds"], default=None),
            "pages_with_boxes": sum("preview" in page for page in pages),
            "sketch_card_pages": form_pages["КЭ"],
            "profile_not_applicable": [p["page"] for p in pages
                                       if p["fields"]["profile_size"].get("status") == "not_applicable"],
            "image_review_pages": [page["page"] for page in pages if "review_image" in page],
            "pages_without_text_layer": sum(not page["has_text_layer"] for page in pages)}


def write_summary(dest, documents, seconds):
    rows = []
    for doc in documents:
        name = escape(doc["source"])
        if "error" in doc:
            cells = f'<td>{name}</td><td colspan="7">{escape(doc["error"])}</td>'
        else:
            cells = (f'<td><a href="{doc["report"]}">{name}</a></td><td>{doc["pages"]}</td>'
                     f'<td>{doc["pages_with_boxes"]}</td>')
            groups = []
            for code, numbers in doc["form_pages"].items():
                if numbers:
                    links = [f'<a href="{doc["report"]}#page-{n}">{n}</a>' for n in numbers]
                    groups.append(code + ' · ' + ', '.join(links))
            cells += '<td>' + ('<br>'.join(groups) or '—') + '</td>'
            for key, _, _ in FIELDS:
                field = doc["fields"][key]
                counts = f'{field["with_text"]} / {field["located"]}'
                if key == "profile_size" and doc["profile_not_applicable"]:
                    links = [f'<a href="{doc["report"]}#page-{n}">{n}</a>' for n in doc["profile_not_applicable"]]
                    counts += '<br>Не применимо (КЭ), стр. ' + ', '.join(links)
                cells += f'<td>{counts}</td>'
            links = [f'<a href="{doc["report"]}#page-{n}">{n}</a>' for n in doc["image_review_pages"]]
            cells += '<td>' + (', '.join(links) or '—') + '</td>'
            slow = doc["slowest_page"]
            cells += (f'<td>{slow["page"]} ({slow["seconds"]:.2f} с)</td>' if slow else '<td>—</td>')
        rows.append(f'<tr>{cells}<td>{doc["seconds"]:.2f}</td></tr>')
    pages = sum(d.get("pages", 0) for d in documents)
    errors = sum("error" in d for d in documents)
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width"><title>Результаты проверки PDF</title><style>'
            'body{font:16px/1.5 system-ui;color:#162737;margin:3rem auto;max-width:70rem;padding:0 1rem}'
            'table{border-collapse:collapse}th,td{padding:.6rem;text-align:left;border-bottom:1px solid #bbb}'
            '</style><h1>Проверка граф маршрутных карт</h1>'
            f'<p>Полностью обработано страниц: {pages}. Время обработки: {seconds:.2f} с. '
            f'Ошибок документов: {errors}.</p><p>В столбцах граф показано количество ячеек с извлечённым '
            'текстом и общее количество выделенных ячеек значений. Точность текста требует сверки с вырезкой.</p>'
            '<table><thead><tr><th>Документ</th><th>Страниц</th><th>Страниц с рамками</th><th>Виды карт</th>'
            '<th>Обозначение документа</th><th>Профиль и размеры</th><th>Изображения для просмотра</th>'
            '<th>Самая долгая страница</th><th>Секунды</th></tr></thead>'
            '<tbody>' + ''.join(rows) + '</tbody></table></html>')
    (dest/'index.html').write_text(html, encoding="utf-8")
    (dest/'summary.json').write_text(json.dumps(
        {"processing_seconds": seconds, "pages": pages, "failed_documents": errors, "documents": documents},
        indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, nargs="*")
    parser.add_argument("--output", type=Path, default=Path("route-reports"),
                        help="Parent directory for a new report folder on each run")
    parser.add_argument("--tessdata", type=Path,
                        default=os.environ.get("TESSDATA_PREFIX", Path(sys.prefix)/"share"/"tessdata"))
    args = parser.parse_args()
    sources = args.pdf
    if not sources:
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        try:
            sources = [Path(p) for p in filedialog.askopenfilenames(
                parent=root, title="Выберите один или несколько PDF", filetypes=[("PDF", "*.pdf")])]
        finally:
            root.destroy()
        if not sources:
            return 0
    args.output.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix="run-", dir=args.output)).resolve()
    start = perf_counter()
    documents = []
    with ExitStack() as stack:
        ocr = LocalOCR(stack, args.tessdata)
        for i, source in enumerate(sources, 1):
            folder = f"document-{i}"
            print(f"[{i}/{len(sources)}] {source.name}", flush=True)
            doc = {"source": source.name}
            tick = perf_counter()
            try:
                report = analyze_document(source, dest/folder, ocr,
                                          progress=lambda n, total: print(f"  {n}/{total}", flush=True))
                doc.update(summarize(report), report=f"{folder}/index.html")
                del report
            except Exception as exc:
                doc["error"] = f"{type(exc).__name__}: {exc}"
            doc["seconds"] = round(perf_counter()-tick, 4)
            documents.append(doc)
    write_summary(dest, documents, perf_counter()-start)
    print(dest/'index.html', flush=True)
    if not args.pdf:
        webbrowser.open((dest/'index.html').as_uri())
    return int(any("error" in d for d in documents))


if __name__ == "__main__":
    raise SystemExit(main())

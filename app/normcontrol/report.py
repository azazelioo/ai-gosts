from html import escape
from pathlib import Path
import json


def write_overview(run, report):
    rows = []
    for page in report["pages"]:
        names = ", ".join(page["modules"]).upper() or "Требуется просмотр"
        reason = page.get("review_reason") or "; ".join(page["signals"].values())
        if page["routing_conflict"]:
            reason += " · Расхождение между моделью и признаками страницы"
        pending = list(page["uncovered_mentions"])
        if page["unsupported_standard"]:
            pending.append(page["unsupported_standard"]["number"])
        rows.append(f"<tr><td>{page['page']}</td><td>{escape(str(page['ai'] or 'недоступен'))}</td>"
                    f"<td>{escape(names)}</td><td>{escape(reason)}</td><td>{escape(', '.join(pending))}</td></tr>")
    links = []
    measurements = []
    mass_notes = []
    for name, item in report["modules"].items():
        links.append(f'<li><a href="{item["report"]}">{name.upper()} · JSON</a> — {escape(item["scope"])}</li>')
        if name == "eskd":
            for page in json.loads((run/item["report"]).read_text(encoding="utf8"))["pages"]:
                layout = page.get("layout", {})
                fields = layout.get("mass_fields") or [layout.get("mass_field", {})]
                for index, field in enumerate(fields, 1):
                    place = f"Лист {page['page']}, графа «Масса»"
                    if len(fields) > 1:
                        place += f" №{index}"
                    note = _mass_note(place, field)
                    if note:
                        mass_notes.append(note)
                check = page.get("dimensions", {}).get("parallel_spacing")
                if not check:
                    continue
                measurements.append(f"<p>Лист {page['page']}. Привязано линий: {check['linked_lines']}. "
                                    f"Надписей без привязки: {check['unlinked_labels']}. "
                                    f"Пар для сверки: {len(check['findings'])}.</p>")
                for finding in check["findings"]:
                    labels = escape(" и ".join(finding["labels"]))
                    measurements.append(f"<p>{labels} · {finding['gap_mm']} мм. "
                                        f"{escape(check['recommendation'])}</p>")
        for image in sorted((run/name).glob("page-*-dimensions.png")):
            links.append(f'<li><a href="{name}/{image.name}">Размеры · {escape(image.stem)}</a></li>')
        overview = run/name/"index.html"
        if overview.exists():
            links.append(f'<li><a href="{name}/index.html">Вырезки {name.upper()}</a></li>')
    errors = "".join(f"<li>{escape(k)} — {escape(v)}</li>" for k,v in report["errors"].items())
    (run/"index.html").write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Результат проверки PDF</title>'
        '<style>body{font:16px system-ui;max-width:75rem;margin:2rem auto;padding:0 1rem}td,th{padding:.5rem;text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style>'
        '<h1>Результат проверки PDF</h1><p>Страниц: '+str(len(rows))+' · Время первого анализа: '
        +str(report["seconds"])+' с</p><table><thead><tr><th>Лист</th><th>Выбор ИИ</th><th>Обработчики</th>'
        '<th>Признаки</th><th>Требуют отдельной проверки</th></tr></thead><tbody>'+''.join(rows)
        +'</tbody></table>'
        +('<h2>Графа «Масса» в основной надписи</h2>'
          +''.join(mass_notes) if mass_notes else '')
        +('<h2>Расстояние между размерными линиями · ГОСТ 2.307-2011, п. 5.11</h2>'
          +''.join(measurements) if measurements else '')
        +'<h2>Отчёты</h2><ul>'+''.join(links)+'</ul>'
        +('<h2>Ошибки</h2><ul>'+errors+'</ul>' if errors else '')+'<a href="result.json">Полный JSON</a></html>',
        encoding="utf8")

def _mass_note(place, mass):
    status = mass.get("status")
    if status == "read":
        value = escape(mass["value"])
        message = value
        if mass.get("decimal_point"):
            message += f". Если методика требует запятую, запись должна быть {value.replace('.', ',')}"
    elif status == "review":
        message = f"вариант OCR {escape(mass['value'])}"
    elif status == "empty":
        message = "ячейка пуста. Проверьте, требуется ли значение для этого чертежа"
    elif status == "unread":
        message = "значение не установлено"
    else:
        return ""
    link = ""
    if mass.get("crop"):
        crop = escape(mass["crop"], quote=True)
        link = f' <a href="eskd/{crop}">Сверить с ячейкой чертежа</a>.'
    return f"<p>{place} — {message}.{link}</p>"

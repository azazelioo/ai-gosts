from html import escape
from pathlib import Path


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
    for name, item in report["modules"].items():
        links.append(f'<li><a href="{item["report"]}">{name.upper()} · JSON</a> — {escape(item["scope"])}</li>')
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
        +'</tbody></table><h2>Отчёты</h2><ul>'+''.join(links)+'</ul>'
        +('<h2>Ошибки</h2><ul>'+errors+'</ul>' if errors else '')+'<a href="result.json">Полный JSON</a></html>',
        encoding="utf8")

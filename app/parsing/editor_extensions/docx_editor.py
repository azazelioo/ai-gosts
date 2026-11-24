from collections.abc import Iterator
from pathlib import Path
from typing import Union

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.section import _Footer, _Header
from docx.table import _Cell
from docx.text.paragraph import Paragraph

# Всё, что содержит абзацы и таблицы: тело документа, ячейка, колонтитул.
Container = Union[DocxDocument, _Cell, _Header, _Footer]

# Только прямые текстовые узлы абзаца (включая гиперссылки), без вложенных текстовых блоков.
_T = "./w:r/w:t | ./w:hyperlink/w:r/w:t"


def _walk(container: Container, prefix: str) -> Iterator[tuple[str, Paragraph]]:
    """Абзацы контейнера и всех вложенных таблиц.

    container: тело документа, ячейка или колонтитул.
    prefix: начало ID для абзацев этого контейнера, например "body" или "s0/header".
    Возвращает пары (id, абзац); ID вида "body/t0/r1/c2/p0".
    """
    for i, p in enumerate(container.paragraphs):
        yield f"{prefix}/p{i}", p
    for t, table in enumerate(container.tables):
        seen = set()
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row.cells):
                if cell._tc in seen:  # объединённые ячейки встречаются повторно
                    continue
                seen.add(cell._tc)
                yield from _walk(cell, f"{prefix}/t{t}/r{r}/c{c}")


def iter_paragraphs(doc: DocxDocument) -> Iterator[tuple[str, Paragraph]]:
    """Все абзацы документа с устойчивыми ID, включая колонтитулы.

    doc: открытый документ, результат docx.Document(path).
    Возвращает пары (id, абзац). Порядок обхода фиксирован, поэтому ID совпадают
    для одного и того же файла.
    """
    yield from _walk(doc, "body")
    for s, section in enumerate(doc.sections):
        for kind in ("header", "footer"):
            part = getattr(section, kind)
            if not part.is_linked_to_previous:  # унаследованные не дублируем
                yield from _walk(part, f"s{s}/{kind}")


def extract(src: str | Path) -> dict[str, str]:
    """Достаёт текст абзацев.

    src: путь к исходному .docx.
    Возвращает {id: текст} только для непустых абзацев.
    """
    doc = Document(str(src))
    return {pid: p.text for pid, p in iter_paragraphs(doc) if p.text.strip()}


def _set_text(p: Paragraph, text: str) -> None:
    """Заменяет текст абзаца, не трогая картинки, поля и разрывы.

    p: абзац, который меняем.
    text: новый текст целиком; он записывается в первый текстовый узел,
    остальные текстовые узлы очищаются.
    """
    nodes = p._p.xpath(_T)
    if not nodes:
        p.add_run(text)
        return
    first, *rest = nodes
    first.text = text
    first.set(qn("xml:space"), "preserve")  # иначе пробелы по краям съедаются
    for t in rest:
        t.text = ""  # drawing, br, fldChar и т.д. остаются нетронутыми


def apply_edits(src: str | Path, dst: str | Path, edits: dict[str, str], force=False) -> Path:
    """Копирует документ и подставляет новый текст в изменённые абзацы.

    src: путь к исходному .docx (тот же файл, с которого делался extract).
    dst: куда сохранить результат.
    edits: {id: новый текст}; неизвестные ID игнорируются, неуказанные абзацы
    остаются как есть.
    Возвращает путь к сохранённому файлу (dst).
    """
    doc = Document(str(src))
    for pid, p in iter_paragraphs(doc):
        if pid in edits and (edits[pid] != p.text or force):
            _set_text(p, edits[pid])
    doc.save(str(dst))
    return Path(dst)
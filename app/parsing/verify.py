import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_BYTES = ("word/media/", "word/embeddings/", "word/charts/")


def _digest(data: bytes) -> bytes:
    return hashlib.md5(data).digest()


def _rels(data: bytes) -> set[tuple]:
    """Содержимое .rels без учёта порядка записей."""
    return {
        (r.get("Id"), r.get("Type"), r.get("Target"), r.get("TargetMode"))
        for r in ET.fromstring(data)
    }


def check_assets(src: str | Path, dst: str | Path) -> list[str]:
    """Возвращает список проблем: пропавшие или изменённые вложения и связи."""
    problems = []
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        names_b = set(b.namelist())
        for name in a.namelist():
            is_rels = name.startswith("word/") and name.endswith(".rels")
            if not (name.startswith(_BYTES) or is_rels):
                continue
            if name not in names_b:
                problems.append(f"пропал: {name}")
            elif is_rels:
                if _rels(a.read(name)) != _rels(b.read(name)):
                    problems.append(f"изменены связи: {name}")
            elif _digest(a.read(name)) != _digest(b.read(name)):
                problems.append(f"изменён: {name}")
    return problems
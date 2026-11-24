import shutil
from datetime import datetime
from pathlib import Path

from docling_core.types.doc import DoclingDocument, ImageRefMode


def save(doc: DoclingDocument, source: str | Path, out_dir: str | Path) -> Path:
    """Сохраняет документ как document_<timestamp>.json + папку assets.

    Возвращает путь к файлу разметки.
    """
    source, out_dir = Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    name = f"document_{datetime.now():%Y%m%d_%H%M%S_%f}"
    markup = out_dir / f"{name}.json"
    assets = out_dir / f"{name}_assets"
    assets.mkdir()

    # Относительный artifacts_dir -> в JSON попадут относительные пути к картинкам,
    # и папку можно переносить вместе с файлом.
    doc.save_as_json(
        markup,
        artifacts_dir=Path(assets.name),
        image_mode=ImageRefMode.REFERENCED,
    )
    shutil.copy2(source, assets / f"original{source.suffix}")
    return markup


def load(markup: str | Path) -> DoclingDocument:
    """Восстанавливает DoclingDocument из файла разметки."""
    markup = Path(markup)
    doc = DoclingDocument.load_from_json(markup)

    # Пути к картинкам в JSON относительные; делаем их абсолютными,
    # чтобы item.get_image(doc) работал независимо от текущей директории.
    for item in (*doc.pictures, *doc.tables):
        if item.image is not None and isinstance(item.image.uri, Path):
            if not item.image.uri.is_absolute():
                item.image.uri = (markup.parent / item.image.uri).resolve()
    return doc

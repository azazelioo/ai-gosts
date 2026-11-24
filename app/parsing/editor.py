# editor.py
from pathlib import Path
from .editor_extensions import docx_editor, pdf_editor, xlsx_editor

_SUFFIX = {".docx": docx_editor, ".xlsx": xlsx_editor, ".pdf": pdf_editor}

def _handler(path):
    return _SUFFIX[Path(path).suffix.lower()]

def extract(src):
    return _handler(src).extract(src)

def apply_edits(src, dst, edits, force=False):
    return _handler(src).apply_edits(src, dst, edits, force)
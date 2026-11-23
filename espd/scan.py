import os
from pathlib import Path

import pymupdf as fitz


class ScanReader:
    def __init__(self, stack, tessdata):
        self.stack = stack
        self.path = tessdata or os.environ.get("TESSDATA_PREFIX")
        self.engine = None
        self.unavailable = False

    def read(self, page):
        if self.unavailable:
            return ""
        if self.engine is None:
            try:
                from tesserocr import PyTessBaseAPI
                args = {"path": str(Path(self.path))} if self.path else {}
                self.engine = self.stack.enter_context(PyTessBaseAPI(lang="rus", **args))
            except (ImportError, RuntimeError):
                self.unavailable = True
                return ""
        scale = min(2.5, 2200/max(page.rect.width, page.rect.height))
        image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY).pil_image()
        self.engine.SetImage(image)
        return self.engine.GetUTF8Text()

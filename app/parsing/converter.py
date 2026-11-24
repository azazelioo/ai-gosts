from functools import cache
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DoclingDocument


@cache
def get_converter(ocr: bool = True) -> DocumentConverter:
    """Конвертер создаётся один раз: загрузка моделей дорогая."""
    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.generate_picture_images = True  # без этого картинки из PDF не вырезаются
    opts.images_scale = 2.0
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def parse(path: str | Path, ocr: bool = True) -> DoclingDocument:
    return get_converter(ocr).convert(str(path)).document

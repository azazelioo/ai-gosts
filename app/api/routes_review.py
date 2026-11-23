"""Local references and isolated paragraph edits used by the DOCX review pane."""
import re
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

router = APIRouter(prefix="/review")
SOURCE = Path(__file__).resolve().parents[1] / "storage/standards/gost_7_32_2017/source.pdf"
LIST_ITEM = re.compile(r"^(?:[-–—]|[0-9]+\)|[а-я]\))\s+", re.I)


@router.get("/standards/gost_7_32_2017.pdf")
def standard_pdf():
    if not SOURCE.is_file():
        raise HTTPException(404, "Локальный PDF стандарта не найден")
    return FileResponse(SOURCE, media_type="application/pdf")


@router.post("/paragraph-fix")
async def paragraph_fix(document: UploadFile = File(...), paragraph_index: int = Form(...),
                        expected_text: str = Form(...), subtype: str = Form(...)):
    try:
        doc = Document(BytesIO(await document.read()))
    except Exception as exc:
        raise HTTPException(400, "Не удалось открыть DOCX") from exc
    paragraphs = doc.paragraphs
    if not 1 <= paragraph_index <= len(paragraphs):
        raise HTTPException(409, "Абзац отсутствует в этой версии документа")
    p = paragraphs[paragraph_index - 1]
    if " ".join(p.text.split()) != expected_text:
        raise HTTPException(409, "Текст абзаца изменился. Повторите проверку.")
    if subtype == "enumeration_indent_invalid" and LIST_ITEM.match(expected_text):
        p.paragraph_format.first_line_indent = Mm(12.5)
    elif (subtype == "formula_not_centered" and p._p.xpath(".//m:oMath")
          and re.fullmatch(r"[\s.,;:()\d]*", expected_text)):
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        raise HTTPException(422, "Это замечание требует ручной проверки")
    result = BytesIO()
    doc.save(result)
    return Response(result.getvalue(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

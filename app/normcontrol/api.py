import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .catalog import CHECKS, catalog, connect, import_standard, initialize, search
from contextlib import closing
from .routing import Dispatcher
from .guard import LocalRequestGuard
from .service import review

api = FastAPI(title="Локальный нормоконтроль PDF", docs_url=None, redoc_url=None)
api.add_middleware(LocalRequestGuard)
analysis_slot = BoundedSemaphore(1)


def storage():
    root = Path(os.getenv("NORMCONTROL_STORAGE","app/storage/normcontrol")).resolve()
    initialize(root)
    return root


@contextmanager
def uploaded(document):
    root = storage()
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(dir=root) as folder:
        path = Path(folder)/"document.pdf"
        with path.open("wb") as stream:
            for chunk in iter(lambda:document.file.read(1024*1024), b""):
                digest.update(chunk)
                stream.write(chunk)
        try:
            with path.open("rb") as stream:
                if b"%PDF-" not in stream.read(1024):
                    raise ValueError("Нет заголовка PDF")
                stream.seek(0)
                pdf = PdfReader(stream)
                if pdf.is_encrypted or not len(pdf.pages):
                    raise ValueError("Требуется PDF без пароля")
                if len(pdf.pages) > int(os.getenv("NORMCONTROL_MAX_PAGES","1200")):
                    raise HTTPException(413,"Превышен лимит страниц PDF")
                pixel_limit = int(os.getenv("NORMCONTROL_MAX_RENDER_PIXELS","120000000"))
                for page in pdf.pages:
                    width, height = float(page.mediabox.width), float(page.mediabox.height)
                    scale = float(page.user_unit)*3  # ESTD renders at 216 DPI.
                    if not (width > 0 and height > 0 and scale > 0 and width*height*scale**2 <= pixel_limit):
                        raise HTTPException(413,"Размер листа превышает бюджет отрисовки")
        except (PyPdfError, ValueError, OSError) as exc:
            raise HTTPException(422,"Не удалось прочитать PDF") from exc
        yield root,path,digest.hexdigest()


@api.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Проверка PDF</title>
    <main><h1>Локальная проверка PDF</h1>
    <form action="analyze" method="post" enctype="multipart/form-data">
    <label>Документ <input type="file" name="document" accept="application/pdf" required></label>
    <button>Проверить документ</button></form><p><a href="catalog">Каталог стандартов и проверок</a></p>
    <h2>Добавить стандарт</h2><form action="catalog/import" method="post" enctype="multipart/form-data">
    <label>Обозначение <input name="number" required></label><label>Редакция <input name="edition" required></label>
    <label>Название <input name="title" required></label>
    <input type="file" name="document" accept="application/pdf" required><button>Сохранить в каталог</button>
    </form></main></html>"""


@api.get("/catalog")
def list_catalog():
    return {"standards":catalog(storage()),"checks":[dict(module=m,number=n,edition=e,scope=s,check=c)
            for m,(n,e,_,s,c) in CHECKS.items()]}


@api.post("/catalog/import")
def upload_standard(document: UploadFile=File(...), number: str=Form(...), edition: str=Form(...), title: str=Form(...)):
    with uploaded(document) as (root,path,digest):
        try:
            return import_standard(root,path,digest,number,edition,title)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc


@api.get("/catalog/search")
def search_catalog(q: str):
    return {"hits":search(storage(),q)}


@api.get("/catalog/{standard_id}/source")
def standard_source(standard_id: str):
    root = storage()
    with closing(connect(root)) as db:
        row = db.execute("SELECT sha256 FROM standards WHERE id=?", (standard_id,)).fetchone()
    if not row or not row[0]:
        raise HTTPException(404,"PDF этой редакции не загружен")
    return FileResponse(root/"sources"/(row[0]+".pdf"), media_type="application/pdf")


@api.get("/health")
def health():
    storage()
    model = Dispatcher()
    try:
        return {"ready":True,"model":model.model,"digest":model.identity(),"modules":list(CHECKS)}
    except (ValueError,OSError,KeyError,StopIteration):
        return {"ready":False,"model":model.model,"mode":"rules_only","modules":list(CHECKS)}


@api.post("/analyze")
def analyze(request: Request, document: UploadFile=File(...)):
    with analysis_slot, uploaded(document) as (root,path,digest):
        result = review(root,path,digest)
    base = str(request.url_for("report_file",report_id=result["id"],artifact="result.json")).rsplit("/",1)[0]
    if "text/html" in request.headers.get("accept",""):
        return RedirectResponse(base+"/index.html",status_code=303)
    return dict(result,document=document.filename,report_url=base+"/result.json",overview_url=base+"/index.html",
                module_reports={k:base+"/"+v["report"] for k,v in result["modules"].items()})


@api.get("/reports/{report_id}/{artifact:path}",name="report_file")
def report_file(report_id: str, artifact: str):
    if not re.fullmatch(r"(?:[a-f0-9]{32}|[a-f0-9]{64})",report_id):
        raise HTTPException(404)
    base = storage()/"reports"/report_id
    path = (base/artifact).resolve()
    if not path.is_relative_to(base) or not path.is_file() or path.suffix not in {".json",".png",".jpg",".html"}:
        raise HTTPException(404)
    return FileResponse(path)

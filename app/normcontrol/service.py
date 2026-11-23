import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from contextlib import ExitStack
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import pymupdf
import eskd
import espd
import estd
from espd.scan import ScanReader
from .cache import link_or_copy, stage
from .catalog import CHECKS, catalog, key
from .routing import Dispatcher, profile
from .report import write_overview

MODULES = {"eskd":eskd, "espd":espd, "estd":estd}
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def build_digest():
    digest = hashlib.sha256()
    for folder in [Path(__file__).parent, *(Path(m.__file__).parent for m in MODULES.values())]:
        for path in sorted(folder.glob("*.py")):
            digest.update(path.read_bytes())
    for package in ("PyMuPDF","pypdf","pdfplumber","pypdfium2","tesserocr","numpy","scipy","Pillow"):
        digest.update(f"{package}:{version(package)}".encode())
    return digest.hexdigest()


def review(root, source, digest, dispatcher=None):
    started = perf_counter()
    standards = catalog(root)
    dispatcher = dispatcher or Dispatcher()
    model_id = None
    errors = {}
    try:
        model_id = dispatcher.identity()
    except (ValueError, OSError, KeyError, StopIteration) as exc:
        errors["dispatcher"] = str(exc) or "Модель отсутствует"
    tessdata = os.environ.get("TESSDATA_PREFIX")
    trained = Path(tessdata or "")/"rus.traineddata"
    tess_identity = [tessdata,trained.stat().st_size,trained.stat().st_mtime_ns] if trained.exists() else [tessdata]
    payload = [digest,build_digest(),dispatcher.model,model_id,standards,tess_identity]
    cache_id = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    folder = root/"reports"
    folder.mkdir(exist_ok=True)
    cached = folder/cache_id/"result.json"
    if model_id and cached.is_file():
        return dict(json.loads(cached.read_text("utf8")),cache_hit=True)
    run = Path(tempfile.mkdtemp(dir=folder,prefix=".working-"))
    try:
        def extract(target):
            with pymupdf.open(source) as pdf, ExitStack() as stack:
                if not pdf.is_pdf or pdf.needs_pass or not len(pdf):
                    raise ValueError("Нужен PDF без пароля")
                ocr = ScanReader(stack,tessdata)
                profiles = [profile(page,ocr) for page in pdf]
            (target/"pages.json").write_text(json.dumps(profiles,ensure_ascii=False),"utf8")

        profile_dir, profile_hit = stage(root,"profiles",[digest,build_digest(),tess_identity],extract)
        profiles = json.loads((profile_dir/"pages.json").read_text("utf8"))
        coded = {key(number,edition) for number,edition,*_ in CHECKS.values()}
        by_word, by_number, descriptors = {}, {}, {}
        for standard in standards:
            if standard["id"] in coded:
                continue
            ident = standard["id"]
            terms = set(re.findall(r"\w{4,}",standard["title"].casefold()))
            descriptors[ident] = standard,terms
            by_number.setdefault(standard["number"].casefold(),set()).add(ident)
            for term in terms:
                by_word.setdefault(term,set()).add(ident)
        for page in profiles:
            words = set(re.findall(r"\w{4,}",page["text"].casefold()))
            mentioned = set().union(*(by_number.get(n.casefold(),set()) for n in page["mentions"]))
            possible = mentioned.union(*(by_word.get(w,set()) for w in words))
            candidates = []
            for ident in possible:
                standard,terms = descriptors[ident]
                overlap = len(terms & words)
                if ident in mentioned or (terms and overlap >= min(2,len(terms))):
                    candidates.append((ident in mentioned,overlap,ident,standard))
            page["catalog_candidates"] = {f"s{i}":{"id":s["id"],"number":s["number"],
                "edition":s["edition"],"title":s["title"]} for i,(_,_,_,s) in enumerate(sorted(candidates,reverse=True)[:5])}
        decisions = []
        pending = []
        for page in profiles:
            if not page["review_reason"] and not (page["eligible_modules"] or page["catalog_candidates"]):
                page["review_reason"] = "Недостаточно признаков для запуска доступных проверок"
            if not page["review_reason"]:
                pending.append(page)
        picks = {}
        for start in range(0,len(pending),6):
            batch = pending[start:start+6]
            if model_id and "dispatcher" not in errors:
                try:
                    picks.update(dispatcher.choose(batch))
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    errors["dispatcher"] = str(exc)
        for p in profiles:
            ai = picks.get(str(p["page"]))
            signals = set(p["signals"])
            conflict = ai in CHECKS and ai not in p["eligible_modules"]
            assigned = sorted(signals | ({ai} if ai in CHECKS and not conflict else set()))
            chosen = p["catalog_candidates"].get(ai)
            decisions.append({"page":p["page"],"modules":assigned,"ai":ai,"routing_conflict":conflict,
                              "catalog_candidates":list(p["catalog_candidates"].values()),
                              "unsupported_standard":chosen,"review_reason":p["review_reason"],
                              "ai_evaluated":str(p["page"]) in picks,"signals":p["signals"],
                              "scan_ocr":p["scan_ocr"],"status":"review" if conflict or chosen or not assigned else "assigned",
                              "mentioned_standards":p["mentions"]})
        modules = {}
        for name, module in MODULES.items():
            pages = [d["page"] for d in decisions if name in d["modules"]]
            if not pages:
                continue
            try:
                def execute(target):
                    result = module.analyze_pdf(source,output_dir=target,pages=pages,tessdata=tessdata)
                    result.directory.rename(target/"output")
                data, hit = stage(root,name,[digest,build_digest(),tess_identity,pages],execute)
                shutil.copytree(data/"output",run/name,copy_function=link_or_copy)
                number,edition,_,scope,check = CHECKS[name]
                modules[name] = {"pages":pages,"report":name+"/result.json","cache_hit":hit,
                                 "standard_id":key(number,edition),"scope":scope,"check":check}
            except Exception as exc:
                log.exception("PDF analyzer failed: %s",name)
                errors[name] = str(exc)
        report_id = uuid.uuid4().hex if errors else cache_id
        report = {"schema_version":1,"id":report_id,"source_sha256":digest,"cache_hit":False,
                  "status":"partial" if errors else "processed","model":dispatcher.model,"model_digest":model_id,
                  "profile_cache_hit":profile_hit,"pages":decisions,"modules":modules,"errors":errors,
                  "seconds":round(perf_counter()-started,3)}
        for page in decisions:
            page["uncovered_mentions"] = [n for n in page["mentioned_standards"] if
                re.sub(r"\s+", "", n.upper()).replace("–","-").replace("—","-") not in
                {CHECKS[m][0].replace(" ","") for m in page["modules"] if m in modules}]
        write_overview(run,report)
        (run/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),"utf8")
        try:
            run.rename(folder/report_id)
        except OSError:
            if errors or not cached.exists():
                raise
            return dict(json.loads(cached.read_text("utf8")),cache_hit=True)
        return report
    finally:
        if run.exists():
            shutil.rmtree(run)

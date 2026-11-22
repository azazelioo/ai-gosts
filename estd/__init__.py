"""Public interface to the local ESTD document analyzer."""
import os
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
import shutil

from .reader import FIELDS, FORM_TYPES, SCHEMA_VERSION, LocalOCR, analyze_document
from .norms import normative_references


@dataclass(frozen=True)
class AnalysisResult:
    """The JSON-compatible report and the directory containing its evidence."""
    directory: Path
    report: dict


def capabilities():
    """Describe the tool to a local caller or model router."""
    return {"module": "estd", "schema_version": SCHEMA_VERSION, "accepts": ["application/pdf"],
            "forms": {code: name for code, (_, name) in FORM_TYPES.items()},
            "fields": {key: name for key, name, _ in FIELDS},
            "tasks": ["identify_form", "extract_fields", "compare_form_code_prediction"],
            "normative_reference_forms": ["МК"]}


def analyze_pdf(source, *, output_dir, pages=None, tessdata=None, form_model=None):
    """Analyze a PDF locally; pages are physical, one-based, or None for the entire PDF."""
    selection = None if pages is None else tuple(pages)
    classifier = None
    if form_model is not None:
        from .learning import FormCodeModel
        classifier = FormCodeModel(form_model)
    if selection is not None and (not selection or any(type(n) is not int or n < 1 for n in selection)):
        raise ValueError("pages must contain one or more positive integers")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix="estd-", dir=root)).resolve()
    try:
        with ExitStack() as stack:
            path = tessdata or os.environ.get("TESSDATA_PREFIX", Path(sys.prefix)/"share"/"tessdata")
            report = analyze_document(Path(source), run, LocalOCR(stack, path), selection, classifier=classifier)
        return AnalysisResult(run, report)
    except BaseException:
        # This directory belongs exclusively to this call, including concurrent calls.
        shutil.rmtree(run)
        raise

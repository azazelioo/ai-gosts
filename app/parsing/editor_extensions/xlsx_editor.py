from pathlib import Path
from openpyxl import load_workbook

def extract(src): 
    """
    Ещё не запускал
    """
    wb = load_workbook(src)
    return {f"{ws.title}!{c.coordinate}": c.value
            for ws in wb for row in ws.iter_rows() for c in row
            if c.data_type == "s" and str(c.value or "").strip()}

def apply_edits(src, dst, edits, force=False):
    """
    Ещё не запускал
    """
    wb = load_workbook(src)
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                key = f"{ws.title}!{c.coordinate}"
                if key in edits and c.data_type == "s" and (force or edits[key] != c.value):
                    c.value = edits[key]
    wb.save(dst)
    return Path(dst)
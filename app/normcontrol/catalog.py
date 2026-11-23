"""Local edition catalogue and searchable, page-addressable source text."""
import hashlib
import os
import re
import sqlite3
from contextlib import closing

import pymupdf


CHECKS = {
    "eskd": ("ГОСТ 2.301-68", "2007", "Форматы", "пп. 4–6, таблицы 1–3", "sheet_format"),
    "espd": ("ГОСТ 19.505-79", "1987", "Руководство оператора", "п. 1.2", "section_headings"),
    "estd": ("ГОСТ 3.1118-82", "2012", "Маршрутные карты", "таблица 4, графы 10 и 19", "route_card_fields"),
}


def key(number, edition):
    return hashlib.sha256((number.strip().casefold()+"\n"+edition.strip().casefold()).encode()).hexdigest()


def connect(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/"catalog.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    return db


def initialize(root):
    with closing(connect(root)) as db, db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS standards(id TEXT PRIMARY KEY, number TEXT, edition TEXT, title TEXT, sha256 TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(id UNINDEXED, page UNINDEXED, text);
        """)
        for number, edition, title, _, _ in CHECKS.values():
            db.execute("INSERT OR IGNORE INTO standards VALUES(?,?,?,?,NULL)", (key(number,edition),number,edition,title))


def catalog(root):
    with closing(connect(root)) as db:
        return [dict(r) for r in db.execute("SELECT * FROM standards ORDER BY number,edition")]


def import_standard(root, path, digest, number, edition, title):
    if not all(s.strip() for s in (number,edition,title)):
        raise ValueError("Нужны обозначение, редакция и название")
    ident = key(number,edition)
    with pymupdf.open(path) as pdf, closing(connect(root)) as db, db:
        if pdf.needs_pass or not pdf.is_pdf or not len(pdf):
            raise ValueError("Требуется доступный PDF")
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT sha256 FROM standards WHERE id=?", (ident,)).fetchone()
        if old and old[0] and old[0] != digest:
            raise ValueError("Для этой редакции уже сохранён другой PDF")
        if not old or not old[0]:
            sources = root/"sources"
            sources.mkdir(exist_ok=True)
            try:
                os.link(path,sources/(digest+".pdf"))
            except FileExistsError:
                pass
            db.execute("INSERT OR REPLACE INTO standards VALUES(?,?,?,?,?)",(ident,number,edition,title,digest))
            db.executemany("INSERT INTO pages VALUES(?,?,?)", ((ident,i+1,p.get_text()) for i,p in enumerate(pdf)))
        return {"id":ident,"sha256":digest,"pages":len(pdf)}


def search(root, query):
    words = re.findall(r"\w+",query)[:12]
    if not words:
        return []
    term = " AND ".join('"'+word+'"' for word in words)
    with closing(connect(root)) as db:
        return [dict(r) for r in db.execute("""
          SELECT standards.number, standards.edition, standards.id, pages.page,
                 snippet(pages,2,'[',']','…',25) AS excerpt
          FROM pages JOIN standards ON standards.id=pages.id
          WHERE pages MATCH ? ORDER BY rank LIMIT 30""",(term,))]

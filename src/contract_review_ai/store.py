"""SQLite persistence for uploaded documents, reviews and questions.

One file, no server: the service is single-tenant and write volume is one row per upload,
review or question (docs/decisions/004-sqlite.md)."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .models import Answer, Document, Report

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at REAL NOT NULL, doc_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL REFERENCES documents(id),
    created_at REAL NOT NULL, report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL REFERENCES documents(id),
    created_at REAL NOT NULL, answer_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_doc ON reviews(doc_id, created_at);
CREATE INDEX IF NOT EXISTS questions_doc ON questions(doc_id, created_at);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def ping(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1").fetchone() == (1,)

    def add_document(self, doc: Document) -> str:
        doc_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?)",
                (doc_id, doc.filename, time.time(), doc.model_dump_json()),
            )
            self._db.commit()
        return doc_id

    def get_document(self, doc_id: str) -> Document | None:
        with self._lock:
            row = self._db.execute(
                "SELECT doc_json FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        return Document.model_validate_json(row[0]) if row else None

    def list_documents(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, filename, created_at FROM documents ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "filename": r[1], "created_at": r[2]} for r in rows]

    def add_review(self, doc_id: str, report: Report) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO reviews (doc_id, created_at, report_json) VALUES (?, ?, ?)",
                (doc_id, time.time(), report.model_dump_json()),
            )
            self._db.commit()

    def latest_review(self, doc_id: str) -> Report | None:
        with self._lock:
            row = self._db.execute(
                "SELECT report_json FROM reviews WHERE doc_id=? ORDER BY created_at DESC LIMIT 1",
                (doc_id,),
            ).fetchone()
        return Report.model_validate_json(row[0]) if row else None

    def add_answer(self, doc_id: str, answer: Answer) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO questions (doc_id, created_at, answer_json) VALUES (?, ?, ?)",
                (doc_id, time.time(), answer.model_dump_json()),
            )
            self._db.commit()

    def answers(self, doc_id: str) -> list[Answer]:
        with self._lock:
            rows = self._db.execute(
                "SELECT answer_json FROM questions WHERE doc_id=? ORDER BY created_at",
                (doc_id,),
            ).fetchall()
        return [Answer.model_validate_json(r[0]) for r in rows]

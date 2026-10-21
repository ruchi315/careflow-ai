"""SQLite persistence (stdlib only). One connection per call; WAL mode for concurrent readers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, role TEXT NOT NULL, display_name TEXT NOT NULL,
    pw_salt TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS cases (
    code TEXT PRIMARY KEY, access_hash TEXT NOT NULL, patient_json TEXT NOT NULL, language TEXT,
    vitals_json TEXT, consent_json TEXT NOT NULL, status TEXT NOT NULL, priority TEXT, ai_priority TEXT,
    priority_source TEXT, analysis_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    created_by TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_code TEXT NOT NULL, type TEXT NOT NULL, lang TEXT,
    filename TEXT, path TEXT, mime TEXT, sha256 TEXT, content TEXT, label TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_code TEXT NOT NULL, version INTEGER NOT NULL, text TEXT NOT NULL,
    ai_draft TEXT, status TEXT NOT NULL, author TEXT NOT NULL, role TEXT NOT NULL, priority_final TEXT,
    override_reason TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS flag_reviews (
    case_code TEXT NOT NULL, flag_id TEXT NOT NULL, status TEXT NOT NULL, comment TEXT, reviewer TEXT,
    created_at TEXT NOT NULL, PRIMARY KEY (case_code, flag_id));
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL,
    action TEXT NOT NULL, case_code TEXT, detail TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sources_case ON sources(case_code);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit(case_code);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def row_to_case(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("patient_json", "vitals_json", "consent_json", "analysis_json"):
        d[k[:-5]] = json.loads(d.pop(k)) if d.get(k) else None
    d.pop("access_hash", None)
    return d


def get_case(code: str) -> dict | None:
    with conn() as c:
        return row_to_case(c.execute("SELECT * FROM cases WHERE code=?", (code,)).fetchone())


def get_access_hash(code: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT access_hash FROM cases WHERE code=?", (code,)).fetchone()
        return r["access_hash"] if r else None


def list_cases(statuses: tuple[str, ...]) -> list[dict]:
    q = f"SELECT * FROM cases WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at"
    with conn() as c:
        return [row_to_case(r) for r in c.execute(q, statuses).fetchall()]


def update_case(code: str, **fields) -> None:
    sets, vals = [], []
    for k, v in fields.items():
        if k in ("patient", "vitals", "consent", "analysis"):
            k, v = k + "_json", (json.dumps(v, ensure_ascii=False) if v is not None else None)
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals.append(now())
    with conn() as c:
        c.execute(f"UPDATE cases SET {', '.join(sets)} WHERE code=?", (*vals, code))


def sources_for(code: str) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM sources WHERE case_code=? ORDER BY id", (code,)).fetchall()]


def notes_for(code: str) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM notes WHERE case_code=? ORDER BY version", (code,)).fetchall()]


def flag_reviews(code: str) -> dict[str, dict]:
    with conn() as c:
        return {r["flag_id"]: dict(r) for r in c.execute("SELECT * FROM flag_reviews WHERE case_code=?", (code,)).fetchall()}

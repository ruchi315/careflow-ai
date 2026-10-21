"""Append-only, hash-chained audit log of AI and human actions.

Each entry stores sha256(prev_hash + canonical entry). Editing or deleting any past row
breaks the chain, which `verify()` detects.
"""

from __future__ import annotations

import hashlib
import json
import threading

from app import db

GENESIS = "0" * 64
_lock = threading.Lock()


def _digest(prev: str, ts: str, actor: str, role: str, action: str, case_code: str | None, detail: str) -> str:
    payload = json.dumps([prev, ts, actor, role, action, case_code, detail], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def log(actor: str, role: str, action: str, case_code: str | None = None, detail: dict | None = None) -> None:
    detail_s = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
    with _lock, db.conn() as c:
        r = c.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = r["hash"] if r else GENESIS
        ts = db.now()
        h = _digest(prev, ts, actor, role, action, case_code, detail_s)
        c.execute("INSERT INTO audit (ts, actor, role, action, case_code, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
                  (ts, actor, role, action, case_code, detail_s, prev, h))


def entries(case_code: str | None = None, limit: int = 500) -> list[dict]:
    with db.conn() as c:
        if case_code:
            rows = c.execute("SELECT * FROM audit WHERE case_code=? ORDER BY id DESC LIMIT ?", (case_code, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
        out.append(d)
    return out


def verify() -> dict:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM audit ORDER BY id").fetchall()
    prev = GENESIS
    for r in rows:
        if r["prev_hash"] != prev or _digest(prev, r["ts"], r["actor"], r["role"], r["action"], r["case_code"], r["detail"]) != r["hash"]:
            return {"ok": False, "entries": len(rows), "broken_at": r["id"]}
        prev = r["hash"]
    return {"ok": True, "entries": len(rows), "head": prev}

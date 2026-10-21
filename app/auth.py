"""Staff authentication (signed tokens) and role-based access control."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, Header, HTTPException, Query

from app import config, db

ROLES = ("intake", "nurse", "doctor", "admin")

# What each role may do. Clinical content is never shown to intake or admin roles (data minimisation).
PERMISSIONS = {
    "view_queue": {"intake", "nurse", "doctor"},
    "view_clinical": {"nurse", "doctor"},
    "create_case": {"intake", "nurse", "doctor"},
    "raise_priority": {"nurse", "doctor"},
    "lower_priority": {"doctor"},
    "edit_note": {"nurse", "doctor"},
    "approve_note": {"doctor"},
    "review_flags": {"nurse", "doctor"},
    "record_answers": {"intake", "nurse", "doctor"},
    "view_audit": {"admin"},
    "manage_consent": {"admin"},
    "view_analytics": {"admin", "doctor"},
}

DEMO_USERS = [
    ("intake.ravi", "intake", "Ravi (Front desk)"),
    ("nurse.asha", "nurse", "Asha (Triage nurse)"),
    ("dr.mehta", "doctor", "Dr. Mehta (Emergency physician)"),
    ("admin.dpo", "admin", "Data Protection Officer"),
]
DEMO_PASSWORD = "demo"


def hash_pw(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()


def seed_users() -> None:
    with db.conn() as c:
        for username, role, name in DEMO_USERS:
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                continue
            salt, h = hash_pw(DEMO_PASSWORD)
            c.execute("INSERT INTO users VALUES (?,?,?,?,?,1)", (username, role, name, salt, h))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(username: str, role: str, name: str) -> str:
    body = _b64(json.dumps({"u": username, "r": role, "n": name, "exp": int(time.time()) + config.TOKEN_TTL_SECONDS}).encode())
    sig = _b64(hmac.new(config.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(config.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(body))
        if data["exp"] < time.time():
            return None
        return {"username": data["u"], "role": data["r"], "name": data["n"]}
    except Exception:
        return None


def login(username: str, password: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not r:
        return None
    _, h = hash_pw(password, r["pw_salt"])
    if not hmac.compare_digest(h, r["pw_hash"]):
        return None
    return {"token": issue_token(r["username"], r["role"], r["display_name"]),
            "user": {"username": r["username"], "role": r["role"], "name": r["display_name"]}}


def current_user(authorization: str | None = Header(default=None), t: str | None = Query(default=None)) -> dict:
    """Bearer token from the header; `?t=` is accepted only so <audio>/<img> tags can load evidence files."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    elif t:
        token = t
    user = read_token(token) if token else None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def can(user: dict, perm: str) -> bool:
    return user["role"] in PERMISSIONS[perm]


def require(perm: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if not can(user, perm):
            raise HTTPException(403, f"Role '{user['role']}' may not {perm.replace('_', ' ')}")
        return user
    return dep

"""CareFlow AI - FastAPI application (API + static web UI)."""

from __future__ import annotations

import json
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import audit, auth, config, db, services
from app.knowledge import consent as consent_text
from app.knowledge import followups as followup_bank
from app.pipeline import asr, ocr, slm
from app.pipeline.redflags import LEVELS

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    auth.seed_users()
    yield


app = FastAPI(title="CareFlow AI", version="1.0.0", lifespan=lifespan,
              description="Human-in-the-loop multilingual triage assistant. Decision support only - no diagnosis.")
STATIC = config.BASE_DIR / "static"
MAX_UPLOAD = 25 * 1024 * 1024
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".opus", ".aac", ".flac"}
REPORT_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


# ------------------------------------------------------------------------------------------ meta

@app.get("/api/health")
def health():
    return {"ok": True, "engines": {
        "asr": f"faster-whisper-{config.ASR_MODEL}" if asr.asr_available() else None,
        "ocr": "rapidocr-onnx" if ocr.ocr_available() else None,
        "slm": {"model": config.SLM_MODEL, "url": config.SLM_URL} if slm.enabled() else None,
    }, "languages": config.LANGUAGES}


@app.get("/api/consent")
def get_consent(lang: str = "en"):
    c = consent_text()
    return {"version": c["version"],
            "scopes": {k: {"required": v["required"], "text": v.get(lang) or v["en"]} for k, v in c["scopes"].items()},
            "rights": c["rights"].get(lang) or c["rights"]["en"]}


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn):
    res = auth.login(body.username, body.password)
    if not res:
        audit.log(body.username[:40], "unknown", "auth.failed")
        raise HTTPException(401, "Invalid username or password")
    audit.log(res["user"]["username"], res["user"]["role"], "auth.login")
    return res


@app.get("/api/me")
def me(user: dict = Depends(auth.current_user)):
    return {**user, "permissions": sorted(p for p in auth.PERMISSIONS if auth.can(user, p))}


# ------------------------------------------------------------------------------------------ intake

async def _store_uploads(code: str, files: list[UploadFile], lang: str | None) -> list[str]:
    stored = []
    for f in files or []:
        if not f or not f.filename:
            continue
        data = await f.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename} is larger than 25 MB")
        ext = Path(f.filename).suffix.lower()
        mime = f.content_type or mimetypes.guess_type(f.filename)[0]
        if ext in AUDIO_EXT or (mime or "").startswith("audio/"):
            kind = "audio"
        elif ext in REPORT_EXT:
            kind = "report"
        else:
            raise HTTPException(415, f"Unsupported file type: {f.filename}")
        services.add_file_source(code, kind, f.filename, data, mime, lang)
        stored.append(f"{kind}:{f.filename}")
    return stored


def _intake(patient: dict, language: str | None, consent: dict, vitals: dict | None, text: str | None,
            actor: str, role: str) -> tuple[str, str]:
    try:
        code, access = services.create_case(patient, language, consent, vitals, actor, role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if text and text.strip():
        services.add_text_source(code, text.strip(), language, label="patient message")
    return code, access


def _parse_json(s: str | None, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        raise HTTPException(400, "Malformed JSON field")


def _clean_patient(p: dict) -> dict:
    age = p.get("age")
    try:
        age = float(age) if age not in (None, "") else None
        if age is not None and age.is_integer():
            age = int(age)
    except (TypeError, ValueError):
        age = None
    return {"name": (p.get("name") or "").strip()[:80] or None, "age": age, "sex": (p.get("sex") or "").strip()[:1].upper() or None,
            "phone": (p.get("phone") or "").strip()[:20] or None}


@app.post("/api/intake/patient")
async def patient_intake(patient: str = Form(...), consent: str = Form(...), language: str = Form("en"),
                         text: str | None = Form(None), files: list[UploadFile] = File(default=[])):
    """Public kiosk/phone intake. Consent is mandatory and recorded before any processing."""
    cons = _parse_json(consent, {})
    cons["version"] = consent_text()["version"]
    code, access = _intake(_clean_patient(_parse_json(patient, {})), language, cons, None, text, "patient", "patient")
    await _store_uploads(code, files, language)
    services.enqueue(code)
    return {"code": code, "access_code": access, "message": followup_bank()["status_messages"]["received"].get(language)}


@app.post("/api/intake/staff")
async def staff_intake(patient: str = Form(...), consent: str = Form(...), language: str = Form("en"),
                       vitals: str | None = Form(None), text: str | None = Form(None), files: list[UploadFile] = File(default=[]),
                       user: dict = Depends(auth.require("create_case"))):
    cons = _parse_json(consent, {})
    cons["version"] = consent_text()["version"]
    cons["method"] = cons.get("method", "verbal, recorded by staff")
    v = {k: float(x) for k, x in _parse_json(vitals, {}).items() if x not in (None, "")}
    code, access = _intake(_clean_patient(_parse_json(patient, {})), language, cons, v, text, user["username"], user["role"])
    await _store_uploads(code, files, language)
    services.enqueue(code)
    return {"code": code, "access_code": access}


# ------------------------------------------------------------------------------------------ patient self-service

def _patient_case(code: str, access: str) -> dict:
    if not services.check_access(code, access):
        raise HTTPException(404, "Case not found or wrong access code")
    return db.get_case(code)


@app.get("/api/patient/{code}")
def patient_status(code: str, access: str):
    c = _patient_case(code, access)
    a = c["analysis"] or {}
    lang = c["language"] or "en"
    qs = [{"id": q["id"], "text": q["text_local"], "lang": q["lang"]} for q in a.get("questions", []) if q["audience"] == "patient"]
    answered = {s["label"] for s in db.sources_for(code) if (s["label"] or "").startswith("answer to:")}
    qs = [q for q in qs if f"answer to: {q['text']}" not in answered]
    return {"code": code, "status": c["status"], "language": lang,
            "message": a.get("patient_message") or followup_bank()["status_messages"]["received"].get(lang),
            "questions": qs if c["status"] not in ("withdrawn", "erased") else []}


class AnswerIn(BaseModel):
    question_id: str
    question_text: str
    answer: str


@app.post("/api/patient/{code}/answer")
def patient_answer(code: str, access: str, body: AnswerIn):
    c = _patient_case(code, access)
    if c["status"] in ("withdrawn", "erased"):
        raise HTTPException(409, "Case is closed")
    services.record_answer(code, body.question_id, body.question_text, body.answer.strip()[:2000], c["language"], "patient", "patient")
    return {"ok": True}


@app.post("/api/patient/{code}/withdraw")
def patient_withdraw(code: str, access: str):
    _patient_case(code, access)
    services.withdraw(code, "patient", "patient")
    return {"ok": True}


@app.post("/api/patient/{code}/erase")
def patient_erase(code: str, access: str):
    _patient_case(code, access)
    services.erase(code, "patient", "patient", "patient request")
    return {"ok": True}


# ------------------------------------------------------------------------------------------ staff: queue & cases

@app.get("/api/queue")
def get_queue(user: dict = Depends(auth.require("view_queue"))):
    return {"cases": services.queue_view(user["role"])}


def _case_or_404(code: str) -> dict:
    c = services.case_view(code)
    if not c or c["status"] in ("erased",):
        raise HTTPException(404, "Case not found")
    return c


_last_view: dict[tuple[str, str], float] = {}


@app.get("/api/cases/{code}")
def get_case(code: str, user: dict = Depends(auth.require("view_clinical"))):
    c = _case_or_404(code)
    # Log each user's access to a case, but not every auto-refresh (at most once per 10 minutes).
    key, now_t = (user["username"], code), time.time()
    if now_t - _last_view.get(key, 0) > 600:
        _last_view[key] = now_t
        audit.log(user["username"], user["role"], "case.viewed", code)
    return c


@app.get("/api/cases/{code}/sources/{sid}/file")
def source_file(code: str, sid: int, user: dict = Depends(auth.require("view_clinical"))):
    for s in db.sources_for(code):
        if s["id"] == sid and s["path"]:
            audit.log(user["username"], user["role"], "source.opened", code, {"source_id": sid})
            return FileResponse(s["path"], media_type=s["mime"] or mimetypes.guess_type(s["path"])[0])
    raise HTTPException(404, "No such file")


class PriorityIn(BaseModel):
    level: str
    reason: str


@app.post("/api/cases/{code}/priority")
def change_priority(code: str, body: PriorityIn, user: dict = Depends(auth.require("raise_priority"))):
    _case_or_404(code)
    try:
        return services.set_priority(code, body.level, body.reason, user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class FlagIn(BaseModel):
    status: str
    comment: str = ""


@app.post("/api/cases/{code}/flags/{flag_id:path}")
def review_flag(code: str, flag_id: str, body: FlagIn, user: dict = Depends(auth.require("review_flags"))):
    _case_or_404(code)
    try:
        services.review_flag(code, flag_id, body.status, body.comment, user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class NoteIn(BaseModel):
    text: str
    approve: bool = False
    priority_final: str | None = None
    override_reason: str | None = None


@app.post("/api/cases/{code}/note")
def save_note(code: str, body: NoteIn, user: dict = Depends(auth.require("edit_note"))):
    _case_or_404(code)
    if body.priority_final and body.priority_final not in LEVELS:
        raise HTTPException(400, "Unknown priority")
    try:
        return services.save_note(code, body.text, user, body.approve, body.priority_final, body.override_reason)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class StaffAnswerIn(BaseModel):
    question_id: str
    question_text: str
    answer: str


@app.post("/api/cases/{code}/answers")
def staff_answer(code: str, body: StaffAnswerIn, user: dict = Depends(auth.require("record_answers"))):
    c = _case_or_404(code)
    services.record_answer(code, body.question_id, body.question_text, body.answer.strip()[:2000], c["language"],
                           user["username"], user["role"])
    return {"ok": True}


@app.post("/api/cases/{code}/reprocess")
def reprocess(code: str, user: dict = Depends(auth.require("view_clinical"))):
    _case_or_404(code)
    audit.log(user["username"], user["role"], "case.reprocess", code)
    services.enqueue(code)
    return {"ok": True}


@app.get("/api/cases/{code}/fhir")
def fhir(code: str, user: dict = Depends(auth.require("view_clinical"))):
    _case_or_404(code)
    try:
        bundle = services.fhir_bundle(code)
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit.log(user["username"], user["role"], "case.exported", code, {"format": "FHIR R4 Bundle (collection)"})
    return JSONResponse(bundle, headers={"Content-Disposition": f'attachment; filename="{code}-fhir.json"'})


@app.get("/api/alerts")
def alerts(since: str = "", user: dict = Depends(auth.require("view_clinical"))):
    rows = [r for r in services.queue_view(user["role"]) if r["priority"] in ("P1", "P2") and r["status"] == "awaiting_review"
            and r["created_at"] > since]
    return {"alerts": rows, "now": db.now()}


# ------------------------------------------------------------------------------------------ governance

@app.get("/api/audit")
def get_audit(case: str | None = None, user: dict = Depends(auth.require("view_audit"))):
    return {"entries": audit.entries(case), "verify": audit.verify()}


@app.get("/api/admin/cases")
def admin_cases(user: dict = Depends(auth.require("manage_consent"))):
    rows = []
    for c in db.list_cases(services.ACTIVE + ("withdrawn", "erased")):
        rows.append({"code": c["code"], "status": c["status"], "created_at": c["created_at"],
                     "consent": {k: c["consent"].get(k) for k in ("triage", "store_voice", "research", "version", "method", "erased")}})
    return {"cases": rows}


class EraseIn(BaseModel):
    reason: str


@app.post("/api/admin/cases/{code}/erase")
def admin_erase(code: str, body: EraseIn, user: dict = Depends(auth.require("manage_consent"))):
    if not db.get_case(code):
        raise HTTPException(404, "Case not found")
    if not body.reason.strip():
        raise HTTPException(400, "Reason required")
    services.erase(code, user["username"], user["role"], body.reason)
    return {"ok": True}


@app.get("/api/analytics")
def get_analytics(user: dict = Depends(auth.require("view_analytics"))):
    return services.analytics()


# ------------------------------------------------------------------------------------------ static UI

@app.get("/")
def root():
    return RedirectResponse("/static/index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

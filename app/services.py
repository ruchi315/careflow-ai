"""Case lifecycle: intake -> background processing -> review -> approval / erasure."""

from __future__ import annotations

import hashlib
import json
import queue
import secrets
import shutil
import threading
import traceback
from datetime import datetime
from pathlib import Path

from app import audit, config, db
from app.pipeline import orchestrator
from app.pipeline.redflags import LEVEL_NAMES, LEVELS, most_urgent, rank

ACTIVE = ("processing", "awaiting_review", "in_review", "approved")
_jobs: "queue.Queue[str]" = queue.Queue()
_worker_started = False


def _code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "CF-" + "".join(secrets.choice(alphabet) for _ in range(5))


def hash_access(code: str, access: str) -> str:
    return hashlib.sha256(f"{code}:{access}".encode()).hexdigest()


def check_access(code: str, access: str) -> bool:
    h = db.get_access_hash(code)
    return bool(h) and secrets.compare_digest(h, hash_access(code, access))


def create_case(patient: dict, language: str | None, consent: dict, vitals: dict | None, actor: str, role: str,
                created_at: str | None = None) -> tuple[str, str]:
    if not consent.get("triage"):
        raise ValueError("Consent for triage processing is required")
    code = _code()
    access = f"{secrets.randbelow(10 ** 6):06d}"
    ts = created_at or db.now()
    consent = {**consent, "version": consent.get("version"), "collected_at": ts, "collected_by": actor,
               "method": consent.get("method", "tap")}
    with db.conn() as c:
        c.execute("INSERT INTO cases (code, access_hash, patient_json, language, vitals_json, consent_json, status, "
                  "created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (code, hash_access(code, access), json.dumps(patient, ensure_ascii=False), language,
                   json.dumps(vitals or {}), json.dumps(consent, ensure_ascii=False), "processing", ts, ts, actor))
    audit.log(actor, role, "case.created", code, {"language": language, "consent": {k: v for k, v in consent.items() if k in ("triage", "store_voice", "research", "version", "method")}})
    return code, access


def add_text_source(code: str, text: str, lang: str | None, label: str | None = None) -> int:
    with db.conn() as c:
        cur = c.execute("INSERT INTO sources (case_code, type, lang, content, label, sha256, created_at) VALUES (?,?,?,?,?,?,?)",
                        (code, "text", lang, text, label, hashlib.sha256(text.encode()).hexdigest(), db.now()))
        return cur.lastrowid


def add_file_source(code: str, kind: str, filename: str, data: bytes, mime: str | None, lang: str | None) -> int:
    folder = config.UPLOAD_DIR / code
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in Path(filename).name if ch.isalnum() or ch in "._-")[:80] or "upload"
    path = folder / f"{secrets.token_hex(4)}_{safe}"
    path.write_bytes(data)
    with db.conn() as c:
        cur = c.execute("INSERT INTO sources (case_code, type, lang, filename, path, mime, sha256, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (code, kind, lang, safe, str(path), mime, hashlib.sha256(data).hexdigest(), db.now()))
        return cur.lastrowid


# ---------------------------------------------------------------------------------------- processing

def _process(code: str) -> None:
    case = db.get_case(code)
    if not case or case["status"] in ("withdrawn", "erased"):
        return
    srcs = db.sources_for(code)
    pipeline_sources = [{"id": s["id"], "type": s["type"], "content": s["content"], "path": s["path"],
                         "filename": s["filename"], "lang": s["lang"], "label": s["label"],
                         "asr_cache": json.loads(s["content"]) if s["type"] == "audio" and s["content"] else None}
                        for s in srcs]
    try:
        analysis = orchestrator.run({"code": code, "patient": case["patient"], "language": case["language"],
                                     "vitals": case["vitals"], "created_at": case["created_at"]}, pipeline_sources)
    except Exception as exc:  # fail safe: never drop a case
        traceback.print_exc()
        err = f"{type(exc).__name__}: {exc}"
        prev = case.get("analysis")
        if prev and not prev.get("failsafe"):
            # Keep the last good analysis, mark it stale, and send the case back for human review.
            prev["pipeline_error"] = err
            db.update_case(code, status="awaiting_review", analysis=prev)
        else:
            db.update_case(code, status="awaiting_review", priority=most_urgent(case.get("priority"), "P3"),
                           ai_priority=None, priority_source="failsafe", analysis={"error": err, "failsafe": True})
        audit.log("ai:careflow-pipeline", "ai", "pipeline.failed", code, {"error": err[:300]})
        return
    # Cache transcripts so re-runs are fast and still work after a recording is deleted.
    for ar in analysis.get("audio", []):
        raw = ar.pop("raw", None)
        src = next((s for s in srcs if s["id"] == ar.get("source_id")), None)
        if raw and raw.get("available") and src and not src["content"]:
            with db.conn() as cn:
                cn.execute("UPDATE sources SET content=? WHERE id=?", (json.dumps(raw, ensure_ascii=False), src["id"]))
    # Consent scope: without "store_voice", recordings are deleted once transcribed.
    if not (case.get("consent") or {}).get("store_voice"):
        for s in srcs:
            if s["type"] == "audio" and s["path"] and Path(s["path"]).exists():
                Path(s["path"]).unlink()
                with db.conn() as cn:
                    cn.execute("UPDATE sources SET path=NULL, label=? WHERE id=?",
                               ("recording deleted after transcription (no consent to store voice)", s["id"]))
                audit.log("ai:careflow-pipeline", "ai", "audio.deleted", code, {"source_id": s["id"], "reason": "consent scope"})
    ai = analysis["priority"]["level"]
    fields = {"analysis": analysis, "ai_priority": ai}
    if case.get("priority_source") in ("human_raise", "human_lower"):
        # A human decision stands, but new information can still RAISE it.
        fields["priority"] = most_urgent(case["priority"], ai)
    else:
        fields["priority"] = ai
        fields["priority_source"] = "ai_rules"
    fields["status"] = "awaiting_review" if case["status"] in ("processing", "approved") else case["status"]
    db.update_case(code, **fields)
    audit.log("ai:careflow-pipeline", "ai", "pipeline.completed", code, {
        "pipeline_version": analysis["pipeline_version"], "priority": ai,
        "rules_fired": [r["rule_id"] for r in analysis["priority"]["red_flags"]],
        "flags": sorted({f["type"] for f in analysis["flags"]}), "engines": analysis["engines"],
        "timings_ms": analysis["timings_ms"], "slm_added": len(analysis.get("slm_added", [])),
    })


def _worker() -> None:
    while True:
        code = _jobs.get()
        try:
            _process(code)
        finally:
            _jobs.task_done()


def enqueue(code: str) -> None:
    global _worker_started
    if not _worker_started:
        threading.Thread(target=_worker, daemon=True, name="careflow-worker").start()
        _worker_started = True
    if db.get_case(code)["status"] not in ("withdrawn", "erased"):
        db.update_case(code, status="processing")
    _jobs.put(code)


def process_now(code: str) -> None:
    _process(code)


# ---------------------------------------------------------------------------------------- views

def _wait_minutes(created_at: str) -> int:
    return max(0, int((datetime.now() - datetime.fromisoformat(created_at)).total_seconds() // 60))


def queue_view(user_role: str) -> list[dict]:
    cases = db.list_cases(ACTIVE)
    out = []
    for c in cases:
        p = c["priority"] or "P3"
        wait = _wait_minutes(c["created_at"])
        steps = wait // config.AGEING_MINUTES[p] if p in config.AGEING_MINUTES else 0
        effective = max(rank("P2"), rank(p) - steps) if steps else rank(p)
        a = c["analysis"] or {}
        row = {"code": c["code"], "name": c["patient"].get("name"), "age": c["patient"].get("age"),
               "sex": c["patient"].get("sex"), "language": c["language"], "status": c["status"], "priority": c["priority"],
               "priority_name": LEVEL_NAMES.get(p), "wait_min": wait, "aged": effective < rank(p),
               "created_at": c["created_at"], "_sort": (c["status"] == "approved", effective, c["created_at"])}
        if user_role in ("nurse", "doctor"):
            flags = a.get("flags", [])
            row.update({
                "summary": a.get("summary"), "ai_priority": c["ai_priority"], "priority_source": c["priority_source"],
                "red_flags": [r["title"] for r in a.get("priority", {}).get("red_flags", [])][:3],
                "flag_counts": {t: sum(1 for f in flags if f["type"] == t) for t in sorted({f["type"] for f in flags})},
                "failsafe": bool(a.get("failsafe")),
            })
        out.append(row)
    out.sort(key=lambda r: r.pop("_sort"))
    return out


def case_view(code: str) -> dict | None:
    c = db.get_case(code)
    if not c:
        return None
    reviews = db.flag_reviews(code)
    a = c["analysis"] or {}
    for f in a.get("flags", []):
        if f["id"] in reviews:
            f["status"] = reviews[f["id"]]["status"]
            f["review"] = reviews[f["id"]]
    c["sources"] = [{k: s[k] for k in ("id", "type", "lang", "filename", "mime", "label", "created_at", "content", "sha256")}
                    for s in db.sources_for(code)]
    c["notes"] = db.notes_for(code)
    c["wait_min"] = _wait_minutes(c["created_at"])
    c["levels"] = [{"level": lv, "name": LEVEL_NAMES[lv]} for lv in LEVELS]
    return c


# ---------------------------------------------------------------------------------------- actions

def set_priority(code: str, level: str, reason: str, user: dict) -> dict:
    c = db.get_case(code)
    if level not in LEVELS:
        raise ValueError("Unknown priority level")
    current = c["priority"] or "P3"
    lowering = rank(level) > rank(current)
    if lowering and user["role"] != "doctor":
        raise PermissionError("Only a doctor can lower a priority")
    if not reason.strip():
        raise ValueError("A reason is required to change the priority")
    db.update_case(code, priority=level, priority_source="human_lower" if lowering else "human_raise")
    audit.log(user["username"], user["role"], "priority.override", code,
              {"from": current, "to": level, "ai_priority": c["ai_priority"], "reason": reason})
    return {"priority": level}


def review_flag(code: str, flag_id: str, status: str, comment: str, user: dict) -> None:
    if status not in ("acknowledged", "resolved", "open"):
        raise ValueError("bad status")
    with db.conn() as cn:
        cn.execute("INSERT OR REPLACE INTO flag_reviews VALUES (?,?,?,?,?,?)", (code, flag_id, status, comment, user["username"], db.now()))
    audit.log(user["username"], user["role"], f"flag.{status}", code, {"flag": flag_id, "comment": comment})


def save_note(code: str, text: str, user: dict, approve: bool, priority_final: str | None, override_reason: str | None) -> dict:
    c = db.get_case(code)
    a = c["analysis"] or {}
    notes = db.notes_for(code)
    version = (notes[-1]["version"] + 1) if notes else 1
    ai_draft = a.get("note_draft")
    status = "approved" if approve else "draft"
    if approve:
        if user["role"] != "doctor":
            raise PermissionError("Only a doctor can approve the final note")
        pf = priority_final or c["priority"]
        if pf != c["ai_priority"] and not (override_reason or "").strip():
            raise ValueError("The final priority differs from the AI suggestion - give a reason")
        if pf != c["priority"]:
            set_priority(code, pf, override_reason or "set at approval", user)
    with db.conn() as cn:
        cn.execute("INSERT INTO notes (case_code, version, text, ai_draft, status, author, role, priority_final, override_reason, created_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)", (code, version, text, ai_draft, status, user["username"], user["role"],
                                                    priority_final if approve else None, override_reason, db.now()))
    edited = (text.strip() != (ai_draft or "").strip())
    if approve:
        db.update_case(code, status="approved", reviewed_at=db.now())
    elif c["status"] == "awaiting_review":
        db.update_case(code, status="in_review")
    audit.log(user["username"], user["role"], "note.approved" if approve else "note.saved", code,
              {"version": version, "edited_from_ai_draft": edited, "priority_final": priority_final,
               "override_reason": override_reason})
    return {"version": version, "status": status}


def record_answer(code: str, question_id: str, question_text: str, answer: str, lang: str | None, actor: str, role: str) -> None:
    add_text_source(code, answer, lang, label=f"answer to: {question_text}")
    audit.log(actor, role, "followup.answered", code, {"question": question_id})
    enqueue(code)


def withdraw(code: str, actor: str, role: str) -> None:
    db.update_case(code, status="withdrawn")
    audit.log(actor, role, "consent.withdrawn", code, {})


def erase(code: str, actor: str, role: str, reason: str) -> None:
    folder = config.UPLOAD_DIR / code
    if folder.exists():
        shutil.rmtree(folder)
    with db.conn() as cn:
        cn.execute("DELETE FROM sources WHERE case_code=?", (code,))
        cn.execute("DELETE FROM notes WHERE case_code=?", (code,))
        cn.execute("DELETE FROM flag_reviews WHERE case_code=?", (code,))
    db.update_case(code, status="erased", patient={"erased": True}, vitals=None, analysis=None,
                   consent={"erased": True, "erased_at": db.now()})
    # Tombstone only - no personal data in the audit log.
    audit.log(actor, role, "data.erased", code, {"reason": reason})


# ---------------------------------------------------------------------------------------- export & analytics

def fhir_bundle(code: str) -> dict:
    c = db.get_case(code)
    notes = [n for n in db.notes_for(code) if n["status"] == "approved"]
    if not notes:
        raise ValueError("Only an approved note can be exported")
    note = notes[-1]
    a = c["analysis"] or {}
    pid = f"patient-{code}"
    entries = [{"resource": {"resourceType": "Patient", "id": pid, "name": [{"text": c["patient"].get("name")}],
                             "gender": {"M": "male", "F": "female"}.get((c["patient"].get("sex") or "")[:1].upper(), "unknown")}}]
    obs_codes = {"spo2": ("59408-5", "Oxygen saturation", "%"), "hr": ("8867-4", "Heart rate", "/min"),
                 "sbp": ("8480-6", "Systolic blood pressure", "mm[Hg]"), "temp_c": ("8310-5", "Body temperature", "Cel")}
    for k, v in (a.get("facts", {}).get("vitals") or {}).items():
        if k in obs_codes:
            loinc, disp, unit = obs_codes[k]
            entries.append({"resource": {"resourceType": "Observation", "status": "preliminary", "subject": {"reference": f"Patient/{pid}"},
                                         "code": {"coding": [{"system": "http://loinc.org", "code": loinc, "display": disp}]},
                                         "valueQuantity": {"value": v["value"], "unit": unit}}})
    for lab in a.get("facts", {}).get("labs", []):
        entries.append({"resource": {"resourceType": "Observation", "status": "preliminary", "subject": {"reference": f"Patient/{pid}"},
                                     "code": {"text": lab.get("label", lab["test"])},
                                     "valueQuantity": {"value": lab["value"], "unit": lab.get("unit")},
                                     "interpretation": [{"text": lab.get("status", "")}]}})
    for f in a.get("flags", []):
        if f["type"] != "missing":
            entries.append({"resource": {"resourceType": "Flag", "status": "active", "subject": {"reference": f"Patient/{pid}"},
                                         "code": {"text": f"[{f['type']}] {f['message']}"}}})
    entries.append({"resource": {"resourceType": "Composition", "status": "final", "type": {"text": "Triage note"},
                                 "subject": {"reference": f"Patient/{pid}"}, "date": note["created_at"],
                                 "author": [{"display": note["author"]}], "title": f"Triage note {code}",
                                 "section": [{"title": "Note", "text": {"status": "generated", "div": note["text"]}},
                                             {"title": "Priority", "text": {"status": "generated",
                                              "div": f"{note['priority_final'] or c['priority']} (AI suggested {c['ai_priority']})"}}]}})
    return {"resourceType": "Bundle", "type": "collection", "timestamp": db.now(), "entry": entries}


def analytics() -> dict:
    cases = db.list_cases(ACTIVE + ("withdrawn",))
    by_priority = {lv: 0 for lv in LEVELS}
    overrides = {"raised": 0, "lowered": 0, "agreed": 0}
    review_minutes, flag_types = [], {}
    for c in cases:
        if c["priority"]:
            by_priority[c["priority"]] += 1
        if c["status"] == "approved" and c["ai_priority"] and c["priority"]:
            d = rank(c["priority"]) - rank(c["ai_priority"])
            overrides["agreed" if d == 0 else ("raised" if d < 0 else "lowered")] += 1
            if c.get("reviewed_at"):
                review_minutes.append((datetime.fromisoformat(c["reviewed_at"]) - datetime.fromisoformat(c["created_at"])).total_seconds() / 60)
        for f in (c["analysis"] or {}).get("flags", []):
            flag_types[f["type"]] = flag_types.get(f["type"], 0) + 1
    return {"cases": len(cases), "by_priority": by_priority, "approved_vs_ai": overrides,
            "median_minutes_to_review": round(sorted(review_minutes)[len(review_minutes) // 2], 1) if review_minutes else None,
            "flags_by_type": dict(sorted(flag_types.items(), key=lambda x: -x[1]))}

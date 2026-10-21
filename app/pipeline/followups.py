"""M6 timeline, M7 missing-information detector and M10 follow-up question generator."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.knowledge import followups, lexicon, missing_fields

SEV_RANK = {"high": 0, "medium": 1, "low": 2}


def build_timeline(facts: dict, submitted_at: str) -> list[dict]:
    t0 = datetime.fromisoformat(submitted_at)
    items = []
    for s in facts["symptoms"].values():
        if not s["present"]:
            continue
        if s.get("duration_hours") is not None:
            onset = t0 - timedelta(hours=s["duration_hours"])
            items.append({"concept": s["concept"], "label": s["label"], "onset": onset.isoformat(timespec="minutes"),
                          "duration_hours": s["duration_hours"], "trend": (s.get("trend") or {}).get("value"),
                          "evidence": [d["evidence"] for d in s["durations"]]})
        else:
            items.append({"concept": s["concept"], "label": s["label"], "onset": None, "duration_hours": None,
                          "trend": (s.get("trend") or {}).get("value"), "evidence": []})
    items.sort(key=lambda x: (x["onset"] is None, x["onset"] or ""))
    return items


def _mentioned(facts: dict, concept: str) -> bool:
    return concept in facts["symptoms"] or concept in facts["history"]


def _check(check, facts: dict, patient: dict, symptom: dict | None) -> bool:
    """True when the information is PRESENT."""
    if check == "duration":
        return bool(symptom and symptom.get("duration_hours") is not None)
    if check == "severity":
        return bool(symptom and symptom.get("severity"))
    if check == "never":
        return False
    if check == "specific_pain":
        return any(v["present"] and lexicon()["concepts"][k].get("pain") and not lexicon()["concepts"][k].get("generic")
                   for k, v in facts["symptoms"].items())
    if "patient" in check:
        v = patient.get(check["patient"])
        return v not in (None, "", "unknown")
    if "known" in check:
        return bool(facts.get(f"{check['known']}_known"))
    if "mentioned" in check:
        return _mentioned(facts, check["mentioned"])
    if "vital" in check:
        return check["vital"] in facts["vitals"]
    if "history_any" in check:
        return any(h in facts["history"] for h in check["history_any"])
    raise ValueError(check)


def missing_info(facts: dict, patient: dict) -> list[dict]:
    spec = missing_fields()
    symptomatic = any(s["present"] for s in facts["symptoms"].values())
    flags, seen = [], set()

    def add(item, about=None):
        if item["field"] in seen:
            return
        seen.add(item["field"])
        what = item["field"].replace("_", " ")
        msg = f"Missing: {what}" + (f" (needed for {about})" if about else "")
        flags.append({"id": f"missing:{item['field']}", "type": "missing", "field": item["field"], "severity": item["severity"],
                      "message": msg, "evidence": [], "status": "open", "question": item["question"]})

    for item in spec["global"]:
        if item.get("only_if_symptomatic") and not symptomatic:
            continue
        if not _check(item["check"], facts, patient, None):
            add(item)
    for concept, items in spec["per_concept"].items():
        s = facts["symptoms"].get(concept)
        if not s or not s["present"]:
            continue
        for item in items:
            if not _check(item["check"], facts, patient, s):
                add(item, s["label"])
    flags.sort(key=lambda f: SEV_RANK[f["severity"]])
    return flags


CONFLICT_QUESTION = {"diabetes_history": "conflict_diabetes", "duration": "conflict_duration", "name": "conflict_identity",
                     "sex": "conflict_identity", "age": "conflict_identity"}
QUALITY_QUESTION = {"unreadable_report": "reupload_report", "asr_uncertain": "confirm_voice",
                    "translation_uncertain": "confirm_translation", "asr_unavailable": "confirm_voice"}


def generate_questions(flags: list[dict], lang: str, limit: int = 6) -> list[dict]:
    bank = followups()["questions"]
    out, used = [], set()

    def add(qid: str, reason: str, flag_id: str, weight: int):
        if qid in used or qid not in bank:
            return
        used.add(qid)
        q = bank[qid]
        local = q.get(lang) if q["audience"] == "patient" else None
        out.append({"id": qid, "audience": q["audience"], "text_en": q["en"], "text_local": local or q["en"],
                    "lang": lang if local else "en", "reason": reason, "flag_id": flag_id, "weight": weight,
                    "answer": None})

    for f in flags:
        if f["type"] == "missing":
            add(f["question"], f["message"], f["id"], SEV_RANK[f["severity"]] * 10 + 5)
        elif f["type"] == "conflict":
            qid = CONFLICT_QUESTION.get(f.get("field") or "", "conflict_symptom")
            add(qid, f["message"], f["id"], 3)
        elif f["type"] in QUALITY_QUESTION:
            add(QUALITY_QUESTION[f["type"]], f["message"], f["id"], 4)
    out.sort(key=lambda q: q["weight"])
    return out[:limit]


def status_message(level: str, lang: str) -> str:
    msgs = followups()["status_messages"]
    key = "urgent" if level in ("P1", "P2") else "received"
    return msgs[key].get(lang) or msgs[key]["en"]

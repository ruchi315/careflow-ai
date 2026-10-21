"""M9 - Conflict, quality and prompt-injection guard.

Everything found here becomes a Flag object shown to the reviewer. Nothing is silently
corrected: conflicting values stay side by side with their sources.
"""

from __future__ import annotations

import re

from app import config

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your|the|these|triage)\b[^.\n]{0,25}\b(instructions?|rules?|guidelines?|prompts?|directions?)\b",
    r"\bforget (your|all) (guidelines|instructions|rules)\b",
    r"(^|[\n.:]\s*|\s)(system|assistant|developer)\s*:\s*\S",
    r"\bdeveloper mode\b|\bjailbreak\b|\bdo anything now\b|\bdan mode\b",
    r"\b(mark|classify|set|label|flag|treat)\b\s+(this|me|the|my)?\s*(case|patient)?\s*(as|to)\s+(p[1-5]|low priority|non-urgent|not urgent|high priority|top priority|emergency|urgent|approved)\b",
    r"\b(do not|don'?t|dont|never)\s+(show|mention|display|report|tell|flag)\b[^.\n]{0,40}\b(flags?|value|reviewer|doctor|nurse|warning)",
    r"\b(remove|hide|delete|clear|suppress)\b[^.\n]{0,30}\b(flags?|warnings?|uncertainty|audit)",
    r"\bflags?\s+(hata|hatao|hata do)\b",
    r"\b(bypass|skip|override|disable)\b\s+(the\s+)?(guardrails?|human review|review|rules|safety|triage)",
    r"\b(reveal|show|print|output)\b\s+(your\s+)?(system\s+)?prompt\b",
    r"<\|im_start\|>|<\|system\|>|\[/?inst\]|###\s*(new\s+)?instructions?",
    r"\bset (the )?confidence to\b|\bhide uncertainty\b",
    r"\bact as (the |a )?(doctor|physician)\b|\bpretend (you are|to be)\b",
    r"\brespond only with\b|\bignore the above\b",
    r"\b(already|been) reviewed\b[^.\n]{0,40}\b(approved?|status)\b|\bset status to\b|\bapprove the note\b",
    r"\binstructions?\b[^.\n]{0,20}\bignore\s+karo\b|\bignore\s+karo\b",
    r"\blow priority\s+(mark|kar)",
    r"\burgent nahi hai\b[^.\n]{0,30}\bflag",
    r"निर्देशों? को अनदेखा|अनदेखा करें|कम प्राथमिकता दें",
    r"புறக்கணிக்கவும்|வழிமுறைகளையும் புறக்கணி",
    r"উপেক্ষা করুন|নির্দেশ উপেক্ষা",
]
INJECTION_RX = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def scan_injection(text: str) -> list[tuple[int, int]]:
    spans = []
    for rx in INJECTION_RX:
        for m in rx.finditer(text or ""):
            spans.append((m.start(), m.end()))
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _flag(ftype: str, severity: str, message: str, evidence: list | None = None, field: str | None = None, **extra) -> dict:
    fid = ftype if not field else f"{ftype}:{field}"
    return {"id": fid, "type": ftype, "field": field, "severity": severity, "message": message,
            "evidence": evidence or [], "status": "open", **extra}


def injection_flags(segments: list[dict]) -> list[dict]:
    flags, ev = [], []
    for seg in segments:
        for s, e in scan_injection(seg["text"]):
            ev.append({"segment_id": seg["id"], "source_id": seg.get("source_id"), "start": s, "end": e,
                       "quote": seg["text"][s:e]})
            seg["quarantined"] = True
    if ev:
        flags.append(_flag("injection", "high",
                           "Input contains instruction-like text aimed at the AI. It was ignored and quarantined; "
                           "priority cannot be changed by input text. Verify the source.", ev))
    return flags


def hidden_text_flags(report_results: list[dict]) -> list[dict]:
    flags = []
    for rr in report_results:
        hidden = [ln for ln in rr["segments"] if ln.get("hidden")]
        if hidden:
            flags.append(_flag("hidden_text", "high",
                               f"Report '{rr['filename']}' contains text that is invisible on the printed page "
                               f"({len(hidden)} line(s)). Possible tampering.",
                               [{"segment_id": h["id"], "source_id": h.get("source_id"), "start": 0, "end": len(h["text"]),
                                 "quote": h["text"]} for h in hidden], field=str(rr["source_id"])))
    return flags


def quality_flags(report_results: list[dict], asr_results: list[dict], text_segments: list[dict]) -> list[dict]:
    flags = []
    for rr in report_results:
        q = rr.get("quality", {})
        if rr["kind"] == "image":
            n, conf = q.get("ocr_lines", 0), q.get("ocr_mean_conf", 0)
            if rr.get("method") == "none":
                flags.append(_flag("ocr_unavailable", "medium", f"OCR engine unavailable - '{rr['filename']}' needs manual reading.",
                                   field=str(rr["source_id"])))
            elif n < 3 or conf < 0.6:
                flags.append(_flag("unreadable_report", "high",
                                   f"Report '{rr['filename']}' could not be read reliably (lines read: {n}, mean OCR confidence: "
                                   f"{conf:.2f}, sharpness {q.get('sharpness')}, contrast {q.get('contrast')}). Values were NOT guessed - "
                                   "ask for a clearer photo or the original.", field=str(rr["source_id"])))
            else:
                low = [s for s in rr["segments"] if (s.get("conf") or 1) < config.OCR_CONF_THRESHOLD]
                if low:
                    flags.append(_flag("ocr_low_confidence", "medium",
                                       f"{len(low)} line(s) in '{rr['filename']}' were read with low confidence - check them against the image.",
                                       [{"segment_id": s["id"], "source_id": s.get("source_id"), "start": 0, "end": len(s["text"]), "quote": s["text"]} for s in low],
                                       field=str(rr["source_id"])))
        if rr["kind"] == "pdf" and not rr["segments"]:
            flags.append(_flag("unreadable_report", "high", f"No text could be extracted from '{rr['filename']}'.",
                               field=str(rr["source_id"])))
        if rr.get("labs_found") == 0 and rr["segments"] and rr["kind"] in ("pdf", "image"):
            flags.append(_flag("no_lab_values", "low", f"No recognised lab values in '{rr['filename']}'. Review the document manually.",
                               field=str(rr["source_id"])))
    for ar in asr_results:
        if not ar.get("available"):
            flags.append(_flag("asr_unavailable", "medium", f"Voice note '{ar['filename']}' could not be transcribed ({ar.get('error')}). Listen to it manually.",
                               field=str(ar["source_id"])))
            continue
        low = [s for s in ar["segments"] if (s.get("conf") or 0) < config.ASR_CONF_THRESHOLD]
        lang_unsure = not ar.get("language_forced") and ar.get("language_prob", 1) < 0.6
        if low or lang_unsure or not ar["segments"]:
            flags.append(_flag("asr_uncertain", "medium",
                               f"Transcription of '{ar['filename']}' is uncertain" +
                               (f" ({len(low)} low-confidence segment(s))" if low else "") +
                               (" (spoken language unclear)" if lang_unsure else "") +
                               ". Listen to the recording before relying on it.",
                               [{"segment_id": s["id"], "source_id": s.get("source_id"), "start": 0, "end": len(s["text"]), "quote": s["text"]} for s in low],
                               field=str(ar["source_id"])))
    for seg in text_segments:
        g = seg.get("gloss")
        if g and g["method"] != "identity" and g["confidence"] < config.TRANSLATION_CONF_THRESHOLD:
            flags.append(_flag("translation_uncertain", "medium",
                               f"Only {int(g['confidence'] * 100)}% of the {seg.get('lang_name', seg.get('lang'))} text was understood by the "
                               "translator. The original text is shown; confirm with the patient or an interpreter.",
                               [{"segment_id": seg["id"], "source_id": seg.get("source_id"), "start": 0, "end": len(seg["text"]), "quote": seg["text"]}],
                               field=seg["id"]))
    return flags


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) > 1 and t not in {"mr", "mrs", "ms", "dr", "baby", "of"}}


def conflict_flags(facts: dict, patient: dict, report_results: list[dict]) -> list[dict]:
    flags = []
    hist = facts["history"]
    labs = facts["labs"]

    # Patient denies diabetes but labs say otherwise.
    dm = hist.get("diabetes")
    if dm and any(m["negated"] for m in dm["mentions"]):
        bad = [lab for lab in labs if (lab["test"] == "hba1c" and lab["value"] >= 6.5)
               or (lab["test"] == "glucose_fasting" and lab["value"] >= 126)
               or (lab["test"] == "glucose_random" and lab["value"] >= 200)]
        if bad:
            ev = [m["evidence"] for m in dm["mentions"] if m["negated"]] + [b["evidence"] for b in bad]
            flags.append(_flag("conflict", "medium",
                               "Patient reports NO diabetes, but the report shows " +
                               ", ".join(f"{b['label']} {b['value']:g} {b['unit']}" for b in bad) +
                               " (in the diabetic range). Both are shown; please clarify.", ev, field="diabetes_history"))

    # Same symptom reported positive in one place and denied in another.
    for key, s in facts["symptoms"].items():
        pos = [m for m in s["mentions"] if not m["negated"]]
        neg = [m for m in s["mentions"] if m["negated"]]
        if pos and neg and {m["source_id"] for m in pos} != {m["source_id"] for m in neg}:
            flags.append(_flag("conflict", "medium", f"'{s['label']}' is reported in one input and denied in another.",
                               [m["evidence"] for m in pos + neg], field=f"symptom_{key}"))

    # Durations that disagree across sources (factor > 2).
    for key, s in facts["symptoms"].items():
        ds = s.get("durations", [])
        by_src = {}
        for d in ds:
            by_src.setdefault(d.get("source_id"), d)
        if len(by_src) >= 2:
            vals = sorted(by_src.values(), key=lambda d: d["hours"])
            lo, hi = vals[0], vals[-1]
            if lo["hours"] > 0 and hi["hours"] / lo["hours"] > 2:
                flags.append(_flag("conflict", "medium",
                                   f"Different durations for '{s['label']}': \"{lo['evidence']['quote']}\" vs \"{hi['evidence']['quote']}\". "
                                   "The longer duration is used for rules until clarified.",
                                   [lo["evidence"], hi["evidence"]], field="duration"))

    # Fever denied but measured temperature >= 38.
    fever = facts["symptoms"].get("fever")
    t = facts["vitals"].get("temp_c")
    if fever and not fever["present"] and t and t["value"] >= 38:
        flags.append(_flag("conflict", "medium", f"Fever denied but recorded temperature is {t['value']} C.",
                           [m["evidence"] for m in fever["mentions"]] + [t["evidence"]], field="fever"))

    # Identity checks against report headers.
    for rr in report_results:
        h = rr.get("header") or {}
        if "name" in h and patient.get("name"):
            a, b = _name_tokens(patient["name"]), _name_tokens(h["name"]["value"])
            if a and b and not (a & b):
                flags.append(_flag("conflict", "high",
                                   f"Name on report '{rr['filename']}' (\"{h['name']['value']}\") does not match the patient "
                                   f"(\"{patient['name']}\"). The report may belong to someone else.",
                                   [h["name"]["evidence"]], field="name"))
        if "sex" in h and patient.get("sex") and h["sex"]["value"] != patient["sex"][0].upper():
            flags.append(_flag("conflict", "high",
                               f"Sex on report '{rr['filename']}' ({h['sex']['value']}) differs from the patient record ({patient['sex']}).",
                               [h["sex"]["evidence"]], field="sex"))
        if "age" in h and patient.get("age") is not None and abs(h["age"]["value"] - float(patient["age"])) > 2:
            flags.append(_flag("conflict", "medium",
                               f"Age on report '{rr['filename']}' ({h['age']['value']}) differs from the patient record ({patient['age']}).",
                               [h["age"]["evidence"]], field="age"))
    # De-duplicate by id
    out, seen = [], set()
    for f in flags:
        if f["id"] not in seen:
            seen.add(f["id"])
            out.append(f)
    return out

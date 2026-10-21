"""Draft triage/referral note (deterministic template) + no-diagnosis language filter.

The note only restates what was reported, measured or flagged, with sources. It is always
marked as an AI draft until a clinician approves it.
"""

from __future__ import annotations

import re

from app.config import LANGUAGES

# Diagnostic claims (not questions such as "Do you have...", not "no diagnosis").
DIAGNOSTIC_RX = re.compile(
    r"(?<!no )\bdiagnos(is|ed|e)\b|(?<!do )(?<!did )\byou have (a |an )?(heart|stroke|infection|disease|cancer|diabetes)|"
    r"\bsuffering from\b|\bconfirmed\b|\blikely (has|have|is)\b|\bconsistent with\b|\bmyocardial infarction\b|"
    r"\bheart attack\b|\bmeningitis\b|\bappendicitis\b|\bpneumonia\b|\bsepsis\b|\bdengue\b|\bmalaria\b|"
    r"\btuberculosis\b|\bcancer\b", re.I)


def contains_diagnostic_language(text: str) -> bool:
    return bool(DIAGNOSTIC_RX.search(text or ""))


def _dur(h: float | None) -> str:
    if h is None:
        return "onset not stated"
    if h < 1:
        return f"{round(h * 60)} min"
    if h < 48:
        return f"{h:g} h"
    if h < 24 * 14:
        return f"{h / 24:g} days"
    if h < 24 * 60:
        return f"{h / 168:g} weeks"
    return f"{h / 720:g} months"


def draft_note(case: dict, analysis: dict) -> str:
    p = case["patient"]
    f = analysis["facts"]
    pr = analysis["priority"]
    lines = [
        "TRIAGE NOTE - DRAFT (AI-generated, requires review by a qualified healthcare professional)",
        f"Case {case['code']} | {p.get('name') or 'Name not given'} | Age {p.get('age') if p.get('age') is not None else 'not given'} | "
        f"Sex {p.get('sex') or 'not given'} | Language: {LANGUAGES.get(analysis['language']['primary'], analysis['language']['primary'])}",
        "",
        f"Suggested review priority: {pr['level']} - {pr['name']} (rule-based support; final decision by clinician).",
        f"Reason: {pr['explanation']}.",
        "",
        "Presenting concerns (patient-reported):",
    ]
    present = [s for s in f["symptoms"].values() if s["present"]]
    if present:
        for s in present:
            extra = [_dur(s.get("duration_hours"))]
            if s.get("severity"):
                extra.append(f"severity {s['severity']['value']:g}/10")
            if s.get("trend"):
                extra.append(s["trend"]["value"])
            src = "voice" if any(m["segment_kind"] == "asr" for m in s["mentions"]) else "text"
            lines.append(f"  - {s['label']} ({', '.join(extra)}) [{src}]")
    else:
        lines.append("  - No active symptoms reported.")
    neg = [s for s in list(f["symptoms"].values()) + list(f["history"].values()) if not s["present"]]
    if neg:
        lines.append("Reported as absent: " + ", ".join(s["label"] for s in neg) + ".")
    hist = [h for h in f["history"].values() if h["present"]]
    if hist:
        lines.append("Relevant history (patient-reported): " + ", ".join(h["label"] for h in hist) + ".")
    if f["medications"]:
        lines.append("Medications mentioned: " + ", ".join(sorted({m["name"] for m in f["medications"]})) + ".")
    if f["allergies"]:
        lines.append("Allergies mentioned: " + ", ".join(sorted({a["name"] for a in f["allergies"]})) + ".")
    if f["vitals"]:
        names = {"spo2": "SpO2 %", "hr": "HR /min", "sbp": "SBP mmHg", "dbp": "DBP mmHg", "temp_c": "Temp C", "rr": "RR /min"}
        lines.append("Vitals: " + ", ".join(f"{names.get(k, k)} {v['value']:g}" for k, v in f["vitals"].items()) + ".")
    abn = [lab for lab in f["labs"] if lab.get("status", "normal") != "normal"]
    if f["labs"]:
        lines.append("Reports (extracted, verify against source):")
        for lab in f["labs"]:
            rng = ""
            if lab.get("ref_low") is not None or lab.get("ref_high") is not None:
                rng = f", ref {lab.get('ref_low', '')}-{lab.get('ref_high', '')}"
            lines.append(f"  - {lab.get('label', lab['test'])}: {lab['value']:g} {lab.get('unit', '')} "
                         f"({lab.get('status', 'reported')}{rng})")
        if not abn:
            lines.append("  (all extracted values within reference range)")
    lines.append("")
    if pr["red_flags"]:
        lines.append("Possible urgency signals (for clinician assessment):")
        for r in pr["red_flags"]:
            lines.append(f"  - {r['title']} [{r['rule_id']}{', via SLM' if r.get('via_slm') else ''}]")
    else:
        lines.append("Possible urgency signals: none matched by the rulebook.")
    open_flags = [x for x in analysis["flags"] if x["status"] == "open" and x["type"] != "missing"]
    if open_flags:
        lines.append("Open issues for the reviewer:")
        for x in open_flags:
            lines.append(f"  - [{x['type']}] {x['message']}")
    miss = [x for x in analysis["flags"] if x["type"] == "missing"]
    if miss:
        lines.append("Information still missing: " + ", ".join(x["field"].replace("_", " ") for x in miss) + ".")
    if analysis["questions"]:
        lines.append("Suggested follow-up questions:")
        for q in analysis["questions"]:
            lines.append(f"  - {q['text_en']}")
    lines += ["", "This draft contains no diagnosis. It summarises reported information to support prioritisation."]
    return "\n".join(lines)

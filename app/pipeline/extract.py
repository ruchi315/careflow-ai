"""M5 - Structured extraction with provenance.

Deterministic, lexicon-driven extraction over every non-report segment. Each fact keeps
`evidence` = [{segment_id, start, end, quote}] pointing into the original text, so the
clinician can click any fact and see exactly where it came from.
An optional SLM pass (see slm.py) can ADD concepts, but only with a verbatim quote.
"""

from __future__ import annotations

import re

from app.knowledge import lexicon
from app.pipeline.text_utils import (
    clause_end, find_concepts, normalize, sentence_bounds, to_number, to_original_span,
)

# Mentions of these concepts are dropped when they sit inside a mention of the parent.
SUBSUMED_BY = {
    "pain": None,  # generic: dropped inside ANY other pain concept
    "weakness": {"arm_weakness"},
    "vomiting": {"vomiting_blood"},
    "headache": set(),
    "bleeding": {"vomiting_blood", "blood_in_stool"},
    "nausea": set(),
}

NEG_PRE = re.compile(r"\b(no|not|denies|denied|without|never|negative for|nor|don'?t have|doesn'?t have|do not have|does not have)\b")
NEG_POST = re.compile(r"(^|[\s,])(nahi|nahin|nhi)\b|नहीं|नही|இல்லை|নেই|\bনা\b")

_UNIT_HOURS = {
    "minute": 1 / 60, "min": 1 / 60, "hour": 1, "hr": 1, "day": 24, "week": 168, "month": 720, "year": 8760,
    "din": 24, "dino": 24, "ghante": 1, "ghanta": 1, "hafte": 168, "hafta": 168, "mahine": 720, "mahina": 720, "saal": 8760,
    "दिन": 24, "घंटे": 1, "घंटा": 1, "घंटों": 1, "हफ्ते": 168, "हफ़्ते": 168, "महीने": 720, "साल": 8760,
    "நாட்களாக": 24, "நாளாக": 24, "நாட்கள்": 24, "நாள்": 24, "மணி நேரமாக": 1, "மணிநேரமாக": 1,
    "வாரமாக": 168, "வாரங்களாக": 168, "மாதமாக": 720,
    "দিন": 24, "ঘণ্টা": 1, "সপ্তাহ": 168, "মাস": 720,
}
_NUM = r"(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|couple of)"
DURATION_PATTERNS = [
    re.compile(rf"(?:(?:for|since|from|past|last|about|around|over)\s+(?:the\s+)?(?:past\s+|last\s+)?)?{_NUM}\s+(minute|min|hour|hr|day|week|month|year)s?\b(?!\s*old)(?:\s+ago)?"),
    re.compile(r"(\d+|ek|do|teen|char|chaar|paanch|panch|saat)\s+(din|dino|ghante|ghanta|hafte|hafta|mahine|mahina|saal)\b(?:\s+se)?"),
    re.compile(r"(\d+|एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ)\s*(दिन|घंटे|घंटा|घंटों|हफ्ते|हफ़्ते|महीने|साल)(?:\s*से)?"),
    re.compile(r"(\d+|ஒரு|இரண்டு|மூன்று|நான்கு|ஐந்து)\s*(நாட்களாக|நாளாக|நாட்கள்|நாள்|மணி நேரமாக|மணிநேரமாக|வாரமாக|வாரங்களாக|மாதமாக)"),
    re.compile(r"(\d+|এক|দুই|তিন|চার|পাঁচ)\s*(দিন|ঘণ্টা|সপ্তাহ|মাস)(?:\s*ধরে)?"),
]
RELATIVE_ONSETS = [
    (re.compile(r"since (?:the )?(?:this )?morning|this morning|aaj subah|subah se|आज सुबह"), 6),
    (re.compile(r"since (?:the |this )?evening|since afternoon"), 4),
    (re.compile(r"since last night|last night|kal raat|कल रात"), 12),
    (re.compile(r"since yesterday|yesterday|kal shaam|kal se|कल से|कल शाम"), 24),
    (re.compile(r"since today|today"), 3),
]

SEVERITY_PATTERNS = [
    (re.compile(r"\b(\d{1,2})\s*/\s*10\b"), None),
    (re.compile(r"\b(severe|severely|very bad|worst|unbearable|excruciating|intense|terrible|bahut tez|bahut zyada)\b|बहुत तेज"), 8),
    (re.compile(r"\b(moderate)\b"), 5),
    (re.compile(r"\b(mild|slight|halka|halki|thoda)\b|हल्का|हल्की"), 3),
    (re.compile(r"\b(high|tez)\b|तेज़?"), 7),
]
TREND_PATTERNS = [
    (re.compile(r"getting worse|worsening|worse|increasing|badh raha|badhta|बढ़ रहा|அதிகரிக்கிறது|বাড়ছে"), "worsening"),
    (re.compile(r"getting better|improving|better|kam ho raha|helping|कम हो रहा|குறைகிறது|কমছে"), "improving"),
]

VITAL_PATTERNS = {
    "spo2": re.compile(r"(?:spo2|sp02|spo 2|oxygen(?: level| saturation)?|o2 sat\w*|saturation)\D{0,12}?(\d{2,3})\s*%?"),
    "bp": re.compile(r"(?:\bbp\b|blood pressure)\D{0,12}?(\d{2,3})\s*/\s*(\d{2,3})"),
    "hr": re.compile(r"(?:pulse|heart rate|\bhr\b)\D{0,10}?(\d{2,3})"),
    "temp": re.compile(r"(?:fever|temp(?:erature)?|bukhar|बुखार)\D{0,10}?(\d{2,3}(?:\.\d)?)\s*(?:°|deg(?:rees)?)?\s*([cf])?\b"),
}
TEXT_LAB_PATTERNS = {
    "glucose_random": re.compile(r"(?:sugar|glucose|grbs|\brbs\b|शुगर)\D{0,10}?(\d{2,3})\b"),
}
ALLERGY_POS = re.compile(r"allerg(?:y|ic) (?:to|from) ([a-z][a-z \-]{1,30})|([a-z]+) se allergy")
ALLERGY_NONE = re.compile(r"\bno (?:known )?(?:drug )?allerg\w*|\bnkda\b|koi allergy nahi|कोई एलर्जी नहीं")
MEDS_NONE = re.compile(r"\b(?:no|not taking any) (?:regular )?(?:medicines?|medications?|drugs)\b|koi dawai nahi|कोई दवा नहीं")


def _negated(norm: str, nstart: int, nend: int, key: str) -> bool:
    # Pre-window (English style "no fever"), limited to the current clause.
    pre = norm[max(0, nstart - 30):nstart]
    cut = max(pre.rfind(","), pre.rfind("."), pre.rfind(";"), pre.rfind(" but "), pre.rfind(" and "))
    if cut >= 0:
        pre = pre[cut + 1:]
    if NEG_PRE.search(pre):
        return True
    # Post-window (Hinglish/Hindi/Tamil/Bengali style "bukhar nahi hai").
    end = clause_end(norm, nend, 14)
    post = norm[nend:end]
    return bool(NEG_POST.search(post))


def _nearest(items: list[dict], pos: int, sent: tuple[int, int]) -> dict | None:
    best, dist = None, 10 ** 9
    for it in items:
        if not (sent[0] <= it["nstart"] < sent[1]):
            continue
        d = min(abs(it["nstart"] - pos), abs(it["nend"] - pos))
        if d < dist:
            best, dist = it, d
    return best


def extract_segment(seg: dict) -> dict:
    """Extract raw mentions from ONE segment. Offsets refer to seg['text']."""
    text = seg["text"] or ""
    norm, imap = normalize(text)
    ev = lambda ns, ne: _evidence(seg, text, imap, ns, ne)  # noqa: E731

    concepts = lexicon()["concepts"]
    raw = find_concepts(text)
    # Subsumption: drop generic / nested mentions.
    keep = []
    for m in raw:
        parent = SUBSUMED_BY.get(m.key, "keep")
        if parent != "keep":
            inside = [o for o in raw if o is not m and o.start <= m.start and m.end <= o.end and (o.end - o.start) > (m.end - m.start)]
            if parent is None:
                inside = [o for o in inside if concepts[o.key].get("pain") or concepts[o.key].get("category") == "symptom"]
            else:
                inside = [o for o in inside if o.key in parent]
            if inside:
                continue
        keep.append(m)

    mentions = []
    for m in keep:
        spec = concepts[m.key]
        mentions.append({
            "concept": m.key, "label": spec["label"], "category": spec.get("category", "symptom"),
            "negated": _negated(norm, m.nstart, m.nend, m.key),
            "nstart": m.nstart, "nend": m.nend,
            "evidence": ev(m.nstart, m.nend),
            "confidence": round(0.95 * (seg.get("conf") or 1.0), 3),
            "origin": "rules",
        })

    sentences = [(s, e) for s, e in sentence_bounds(norm)]
    symptom_mentions = [x for x in mentions if x["category"] == "symptom" and not x["negated"]]

    def sent_of(pos):
        for s in sentences:
            if s[0] <= pos < s[1]:
                return s
        return (0, len(norm))

    # Durations -> attach to every positive symptom in the same sentence.
    durations = []
    for rx in DURATION_PATTERNS:
        for m in rx.finditer(norm):
            n = to_number(m.group(1))
            unit = m.group(2)
            if n is None or unit not in _UNIT_HOURS:
                continue
            durations.append({"hours": round(n * _UNIT_HOURS[unit], 3), "nstart": m.start(), "nend": m.end(),
                              "evidence": ev(m.start(), m.end())})
    for rx, hours in RELATIVE_ONSETS:
        for m in rx.finditer(norm):
            if any(d["nstart"] <= m.start() < d["nend"] for d in durations):
                continue
            durations.append({"hours": hours, "nstart": m.start(), "nend": m.end(), "evidence": ev(m.start(), m.end()), "relative": True})
    for d in durations:
        sent = sent_of(d["nstart"])
        for sm in symptom_mentions:
            if sent[0] <= sm["nstart"] < sent[1]:
                sm.setdefault("durations", []).append(d)

    # Severity and trend -> nearest positive symptom in the sentence.
    for rx, fixed in SEVERITY_PATTERNS:
        for m in rx.finditer(norm):
            target = _nearest(symptom_mentions, m.start(), sent_of(m.start()))
            if not target or "severity" in target:
                continue
            val = fixed if fixed is not None else float(m.group(1))
            if val is None or val > 10:
                continue
            target["severity"] = {"value": val, "evidence": ev(m.start(), m.end())}
    for rx, trend in TREND_PATTERNS:
        for m in rx.finditer(norm):
            target = _nearest(symptom_mentions, m.start(), sent_of(m.start()))
            if target and "trend" not in target:
                target["trend"] = {"value": trend, "evidence": ev(m.start(), m.end())}

    vitals = []
    for name, rx in VITAL_PATTERNS.items():
        for m in rx.finditer(norm):
            if name == "bp":
                vitals.append({"name": "sbp", "value": float(m.group(1)), "evidence": ev(m.start(), m.end())})
                vitals.append({"name": "dbp", "value": float(m.group(2)), "evidence": ev(m.start(), m.end())})
            elif name == "temp":
                v = float(m.group(1))
                unit = (m.group(2) or "").lower()
                if unit == "f" or 93 <= v <= 110:
                    v = round((v - 32) * 5 / 9, 1)
                if 34 <= v <= 43:
                    vitals.append({"name": "temp_c", "value": v, "evidence": ev(m.start(), m.end())})
            else:
                v = float(m.group(1))
                if name == "spo2" and not 50 <= v <= 100:
                    continue
                vitals.append({"name": name, "value": v, "evidence": ev(m.start(), m.end())})

    labs = []
    for name, rx in TEXT_LAB_PATTERNS.items():
        for m in rx.finditer(norm):
            labs.append({"test": name, "value": float(m.group(1)), "unit": "mg/dL", "evidence": ev(m.start(), m.end()),
                         "origin": "patient_text"})

    allergies, allergies_none = [], False
    for m in ALLERGY_POS.finditer(norm):
        allergies.append({"name": (m.group(1) or m.group(2)).strip(), "evidence": ev(m.start(), m.end())})
    if ALLERGY_NONE.search(norm):
        allergies_none = True
    meds, meds_none = [], False
    for drug in lexicon().get("medications", []):
        for m in re.finditer(rf"\b{re.escape(drug)}\b", norm):
            meds.append({"name": drug, "evidence": ev(m.start(), m.end())})
    if MEDS_NONE.search(norm):
        meds_none = True

    for x in mentions:
        x.pop("nstart", None)
        x.pop("nend", None)
        for d in x.get("durations", []):
            d.pop("nstart", None)
            d.pop("nend", None)
    return {"mentions": mentions, "vitals": vitals, "labs": labs, "allergies": allergies,
            "allergies_none": allergies_none, "medications": meds, "medications_none": meds_none}


def _evidence(seg: dict, text: str, imap: list[int], ns: int, ne: int) -> dict:
    s, e = to_original_span(imap, ns, ne)
    return {"segment_id": seg["id"], "source_id": seg.get("source_id"), "start": s, "end": e, "quote": text[s:e]}


def aggregate(per_segment: list[tuple[dict, dict]]) -> dict:
    """Merge mentions across segments into one structured case."""
    symptoms: dict[str, dict] = {}
    history: dict[str, dict] = {}
    vitals: dict[str, dict] = {}
    labs: list[dict] = []
    allergies, meds = [], []
    allergies_known = meds_known = False

    for seg, res in per_segment:
        for m in res["mentions"]:
            bucket = history if m["category"] == "history" else symptoms
            item = bucket.setdefault(m["concept"], {
                "concept": m["concept"], "label": m["label"], "category": m["category"],
                "present": False, "mentions": [], "durations": [], "severity": None, "trend": None,
                "confidence": 0.0, "origins": set(),
            })
            item["mentions"].append({"evidence": m["evidence"], "negated": m["negated"], "confidence": m["confidence"],
                                     "origin": m.get("origin", "rules"), "source_id": seg.get("source_id"), "segment_kind": seg.get("kind")})
            item["origins"].add(m.get("origin", "rules"))
            if not m["negated"]:
                item["present"] = True
                item["confidence"] = max(item["confidence"], m["confidence"])
            for d in m.get("durations", []):
                item["durations"].append({**d, "source_id": seg.get("source_id")})
            if m.get("severity") and (item["severity"] is None or m["severity"]["value"] > item["severity"]["value"]):
                item["severity"] = m["severity"]
            if m.get("trend") and not item["trend"]:
                item["trend"] = m["trend"]
        for v in res["vitals"]:
            cur = vitals.get(v["name"])
            if cur is None:
                vitals[v["name"]] = {**v, "source_id": seg.get("source_id")}
        labs.extend({**lab, "source_id": seg.get("source_id")} for lab in res["labs"])
        allergies.extend(res["allergies"])
        meds.extend(res["medications"])
        allergies_known |= res["allergies_none"] or bool(res["allergies"])
        meds_known |= res["medications_none"] or bool(res["medications"])

    # A generic "pain" mention adds nothing once a specific pain site is known.
    concepts = lexicon()["concepts"]
    if "pain" in symptoms and any(v["present"] and concepts[k].get("pain") and not concepts[k].get("generic")
                                  for k, v in symptoms.items()):
        symptoms.pop("pain")

    for bucket in (symptoms, history):
        for item in bucket.values():
            item["origins"] = sorted(item["origins"])
            if not item["present"]:
                item["confidence"] = max((m["confidence"] for m in item["mentions"]), default=0.0)
            hrs = [d["hours"] for d in item["durations"]]
            item["duration_hours"] = max(hrs) if hrs else None

    return {
        "symptoms": symptoms, "history": history, "vitals": vitals, "labs": labs,
        "allergies": allergies, "allergies_known": allergies_known,
        "medications": meds, "medications_known": meds_known,
    }

"""M8 - Red-flag rule engine and priority computation.

Layer 1 (always on): deterministic, versioned YAML rules (knowledge/redflag_rules.yaml).
Layer 2 (optional):  SLM-proposed concepts (with verbatim quotes) are added to the facts
                     BEFORE rules run, so the SLM can only ever *raise* urgency via the rules.
Final priority = the most urgent of (rule-based level, base level). Lowering is reserved
for a human with a written reason.
"""

from __future__ import annotations

from app.knowledge import lexicon, rules

LEVELS = ["P1", "P2", "P3", "P4", "P5"]
LEVEL_NAMES = {"P1": "Immediate", "P2": "Emergent", "P3": "Urgent", "P4": "Less urgent", "P5": "Non-urgent"}


def rank(level: str) -> int:
    return LEVELS.index(level)


def most_urgent(*levels: str | None) -> str:
    present = [lv for lv in levels if lv]
    return min(present, key=rank) if present else "P5"


class Ctx:
    def __init__(self, facts: dict, patient: dict):
        self.facts = facts
        self.age = patient.get("age")
        self.symptoms = {k: v for k, v in facts["symptoms"].items() if v["present"]}
        self.vitals = facts["vitals"]
        self.labs: dict[str, list[dict]] = {}
        for lab in facts["labs"]:
            self.labs.setdefault(lab["test"], []).append(lab)
        self.pain_keys = {k for k, spec in lexicon()["concepts"].items() if spec.get("pain")}


def _cmp(value: float, cond: dict) -> bool:
    if "lt" in cond and not value < cond["lt"]:
        return False
    if "lte" in cond and not value <= cond["lte"]:
        return False
    if "gt" in cond and not value > cond["gt"]:
        return False
    if "gte" in cond and not value >= cond["gte"]:
        return False
    return True


def _eval(cond, ctx: Ctx, ev: list) -> bool:
    if isinstance(cond, str):
        s = ctx.symptoms.get(cond)
        if s:
            ev.extend(m["evidence"] for m in s["mentions"] if not m["negated"])
            return True
        return False
    if "any" in cond:
        hit = False
        for c in cond["any"]:
            sub: list = []
            if _eval(c, ctx, sub):
                ev.extend(sub)
                hit = True
        return hit
    if "all" in cond:
        sub: list = []
        if all(_eval(c, ctx, sub) for c in cond["all"]):
            ev.extend(sub)
            return True
        return False
    if "age_gte" in cond:
        return ctx.age is not None and ctx.age >= cond["age_gte"]
    if "age_lt" in cond:
        return ctx.age is not None and ctx.age < cond["age_lt"]
    if "vital" in cond:
        v = ctx.vitals.get(cond["vital"])
        if v and _cmp(v["value"], cond):
            ev.append(v["evidence"])
            return True
        return False
    if "lab" in cond:
        hit = False
        for lab in ctx.labs.get(cond["lab"], []):
            if _cmp(lab["value"], cond):
                ev.append(lab["evidence"])
                hit = True
        return hit
    if "duration_gte" in cond:
        spec = cond["duration_gte"]
        s = ctx.symptoms.get(spec["concept"])
        if s and s.get("duration_hours") is not None and s["duration_hours"] >= spec["hours"]:
            ev.extend(d["evidence"] for d in s["durations"])
            return True
        return False
    if "severity_gte" in cond:
        hit = False
        for k in ctx.pain_keys:
            s = ctx.symptoms.get(k)
            if s and s.get("severity") and s["severity"]["value"] >= cond["severity_gte"]:
                ev.append(s["severity"]["evidence"])
                hit = True
        return hit
    raise ValueError(f"unknown condition {cond}")


def evaluate(facts: dict, patient: dict) -> dict:
    ctx = Ctx(facts, patient)
    book = rules()
    fired = []
    for r in book["rules"]:
        ev: list = []
        ok_all = all(_eval(c, ctx, ev) for c in r.get("all", []))
        ok_any = True
        if r.get("any"):
            ok_any = _eval({"any": r["any"]}, ctx, ev)
        if ok_all and ok_any and (r.get("all") or r.get("any")):
            uniq, seen = [], set()
            for e in ev:
                key = (e.get("segment_id"), e.get("start"), e.get("end"))
                if key not in seen:
                    seen.add(key)
                    uniq.append(e)
            origins = set()
            for e in uniq:
                for s in ctx.symptoms.values():
                    for m in s["mentions"]:
                        if m["evidence"] == e:
                            origins.add(m.get("origin", "rules"))
            fired.append({"rule_id": r["id"], "title": r["title"], "priority": r["priority"],
                          "reference": r.get("reference", ""), "evidence": uniq,
                          "via_slm": origins == {"slm"}})
    fired.sort(key=lambda f: rank(f["priority"]))
    base = book["base_priority"]["symptomatic" if ctx.symptoms else "asymptomatic"]
    rule_level = most_urgent(*(f["priority"] for f in fired)) if fired else None
    level = most_urgent(rule_level, base)
    if fired:
        top = fired[0]
        why = f"{top['title']} (rule {top['rule_id']})"
    elif ctx.symptoms:
        why = "Symptoms reported, no red-flag rule matched (base level for symptomatic cases)"
    else:
        why = "No active symptoms reported (base level)"
    return {"level": level, "name": LEVEL_NAMES[level], "rule_level": rule_level, "base_level": base,
            "explanation": why, "red_flags": fired, "rulebook_version": book["version"]}

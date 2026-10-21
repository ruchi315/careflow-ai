"""Lab-report parsing: header (name/age/sex) + lab rows -> normalised values with status."""

from __future__ import annotations

import re

from app.knowledge import lab_ranges

NUM = r"([<>]?\s*\d+(?:\.\d+)?)"
RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)")
NAME_RX = re.compile(r"patient\s*name\s*[:\-]?\s*([A-Za-z][A-Za-z .']{1,60})", re.I)
AGE_RX = re.compile(r"\bage\s*[:\-]?\s*(\d{1,3})", re.I)
SEX_RX = re.compile(r"(?:sex|gender)\s*[:\-]?\s*(male|female|m|f)\b", re.I)


def _alias_index() -> list[tuple[str, str]]:
    out = []
    for key, spec in lab_ranges().items():
        for alias in spec["aliases"]:
            out.append((alias.lower(), key))
    out.sort(key=lambda x: -len(x[0]))
    return out


def parse_header(lines: list[dict]) -> dict:
    """lines: [{segment_id, text}] -> {name, age, sex} each with evidence."""
    header = {}
    for ln in lines:
        t = ln["text"]
        if "name" not in header and (m := NAME_RX.search(t)):
            name = re.split(r"\s{2,}|\bage\b|\bsex\b|\bdate\b", m.group(1), flags=re.I)[0].strip()
            header["name"] = {"value": name, "evidence": _ev(ln, m.start(1), m.start(1) + len(name))}
        if "age" not in header and (m := AGE_RX.search(t)):
            header["age"] = {"value": int(m.group(1)), "evidence": _ev(ln, m.start(), m.end())}
        if "sex" not in header and (m := SEX_RX.search(t)):
            header["sex"] = {"value": m.group(1)[0].upper(), "evidence": _ev(ln, m.start(), m.end())}
    return header


def parse_labs(lines: list[dict]) -> list[dict]:
    aliases = _alias_index()
    ranges = lab_ranges()
    results, seen = [], set()
    for ln in lines:
        low = ln["text"].lower()
        for alias, key in aliases:
            m = re.match(rf"\s*{re.escape(alias)}\b[\s:\-]*", low)
            if not m:
                continue
            rest = low[m.end():]
            v = re.search(NUM, rest)
            if not v:
                break
            try:
                value = float(v.group(1).replace("<", "").replace(">", "").strip())
            except ValueError:
                break
            after = rest[v.end():]
            unit_m = re.match(r"\s*/?\s*([a-z%/µu.]+(?:/[a-z]+)?)", after)
            unit = unit_m.group(1).strip("/") if unit_m else ranges[key].get("unit", "")
            rr = RANGE.search(after)
            spec = ranges[key]
            lo = float(rr.group(1)) if rr else spec.get("low")
            hi = float(rr.group(2)) if rr else spec.get("high")
            status = "normal"
            if lo is not None and value < lo:
                status = "low"
            if hi is not None and value > hi:
                status = "high"
            if spec.get("critical_low") is not None and value < spec["critical_low"]:
                status = "critical_low"
            if spec.get("critical_high") is not None and value > spec["critical_high"]:
                status = "critical_high"
            if key in seen:
                break
            seen.add(key)
            results.append({
                "test": key, "label": spec["label"], "value": value, "unit": unit or spec.get("unit", ""),
                "ref_low": lo, "ref_high": hi, "status": status,
                "range_source": "report" if rr else "default",
                "evidence": _ev(ln, 0, len(ln["text"])),
                "confidence": ln.get("conf", 1.0),
            })
            break
    return results


def _ev(ln: dict, s: int, e: int) -> dict:
    return {"segment_id": ln["id"], "source_id": ln.get("source_id"), "start": s, "end": e, "quote": ln["text"][s:e]}

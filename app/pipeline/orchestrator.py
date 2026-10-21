"""Pipeline orchestrator: sources -> segments -> facts -> priority/flags/questions -> draft note.

Pure function of its inputs (no database access), so the same code runs in the web app,
in the evaluation script and in tests.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from app import config
from app.knowledge import lexicon
from app.pipeline import asr, extract, guards, labs, note, ocr, redflags, slm, translate
from app.pipeline.followups import build_timeline, generate_questions, missing_info, status_message
from app.pipeline.text_utils import detect_language

PIPELINE_VERSION = "1.0.0"
VITAL_LABELS = [("spo2", "SpO2", "%"), ("hr", "HR", "/min"), ("rr", "RR", "/min"), ("sbp", "SBP", "mmHg"),
                ("dbp", "DBP", "mmHg"), ("temp_c", "Temp", "C")]


def _form_segment(case: dict) -> tuple[dict | None, list[dict]]:
    """Structured form inputs (demographics/vitals typed by staff) become a citable segment."""
    p, v = case.get("patient", {}), case.get("vitals") or {}
    parts, vitals, pos = [], [], 0
    text = ""
    for key, label, unit in VITAL_LABELS:
        if v.get(key) is None:
            continue
        piece = f"{label}: {v[key]:g} {unit}"
        s = len(text)
        text += piece + "; "
        vitals.append({"name": key, "value": float(v[key]), "evidence": {"segment_id": "F.1", "source_id": "form",
                       "start": s, "end": s + len(piece), "quote": piece}})
    if p.get("age") is not None:
        text += f"Age: {p['age']}; "
    if p.get("sex"):
        text += f"Sex: {p['sex']}; "
    if not text:
        return None, []
    del parts, pos
    return {"id": "F.1", "source_id": "form", "kind": "form", "text": text.strip(), "lang": "en", "conf": 1.0}, vitals


def run(case: dict, sources: list[dict]) -> dict:
    timings: dict[str, int] = {}
    t_all = time.time()

    def tick(name, t):
        timings[name] = timings.get(name, 0) + int((time.time() - t) * 1000)

    hint = case.get("language")
    segments: list[dict] = []
    asr_results, report_results = [], []

    form_seg, form_vitals = _form_segment(case)
    if form_seg:
        segments.append(form_seg)

    for src in sources:
        sid = src["id"]
        if src["type"] == "text":
            t = time.time()
            lang, lconf = detect_language(src["content"])
            segments.append({"id": f"S{sid}.1", "source_id": sid, "kind": "text", "text": src["content"],
                             "lang": lang, "lang_conf": lconf, "conf": 1.0, "label": src.get("label")})
            tick("langid", t)
        elif src["type"] == "audio":
            t = time.time()
            if src.get("asr_cache"):  # transcript kept from an earlier run (recording may be deleted)
                res = dict(src["asr_cache"])
            elif src.get("path"):
                res = asr.transcribe(Path(src["path"]), src.get("lang") or hint)
            else:
                res = {"available": False, "segments": [], "error": "recording not available"}
            tick("asr", t)
            res["raw"] = {k: v for k, v in res.items() if k != "raw"}
            res.update({"source_id": sid, "filename": src.get("filename")})
            res["segments"] = [{**s, "id": f"S{sid}.{i + 1}", "source_id": sid, "kind": "asr",
                                "lang": detect_language(s["text"])[0]} for i, s in enumerate(res.get("segments", []))]
            segments.extend(res["segments"])
            asr_results.append(res)
        elif src["type"] == "report":
            t = time.time()
            path = Path(src["path"])
            kind = "pdf" if path.suffix.lower() == ".pdf" else "image"
            rr = ocr.read_pdf(path) if kind == "pdf" else ocr.read_image(path)
            tick("ocr", t)
            segs = []
            for i, ln in enumerate(rr["lines"]):
                seg = {"id": f"S{sid}.{i + 1}", "source_id": sid, "kind": "ocr" if "bbox" in ln else "pdf_text",
                       "text": ln["text"], "lang": "en", "conf": ln.get("conf", 1.0)}
                for k in ("bbox", "page", "line", "hidden", "words"):
                    if k in ln:
                        seg[k] = ln[k]
                segs.append(seg)
            t = time.time()
            usable = [s for s in segs if (s.get("conf") or 0) >= 0.5 and not s.get("hidden")]
            header = labs.parse_header(usable)
            lab_values = labs.parse_labs(usable)
            tick("labs", t)
            report_results.append({"source_id": sid, "filename": src.get("filename"), "kind": kind, "method": rr["method"],
                                   "quality": rr.get("quality", {}), "segments": segs, "header": header,
                                   "labs": lab_values, "labs_found": len(lab_values), "image_size": rr.get("image_size"),
                                   "pages": rr.get("pages")})
            segments.extend(segs)

    # Translation / gloss for patient-language segments.
    t = time.time()
    for seg in segments:
        if seg["kind"] in ("text", "asr"):
            seg["lang_name"] = config.LANGUAGES.get(seg["lang"], seg["lang"])
            seg["gloss"] = translate.translate(seg["text"], seg["lang"])
    tick("translate", t)

    # Prompt-injection scan over EVERYTHING (patient text, transcripts and report text).
    t = time.time()
    flags = guards.injection_flags(segments)
    tick("guards", t)

    # Extraction (patient words only - report lines feed the lab parser instead).
    t = time.time()
    patient_segs = [s for s in segments if s["kind"] in ("text", "asr", "form")]
    per_segment = [(s, extract.extract_segment(s)) for s in patient_segs]
    tick("extract", t)

    # Optional SLM: may ADD concepts with verbatim quotes (never remove, never lower priority).
    slm_added = []
    if slm.enabled():
        t = time.time()
        allowed = {k: v["label"] for k, v in lexicon()["concepts"].items() if v.get("category") == "symptom"}
        clean = [s for s in patient_segs if s["kind"] != "form" and not s.get("quarantined")]
        for item in slm.extract_signals(clean, allowed):
            for seg, res in per_segment:
                if seg["id"] == item["segment_id"]:
                    if any(m["concept"] == item["concept"] and not m["negated"] for m in res["mentions"]):
                        break
                    res["mentions"].append({
                        "concept": item["concept"], "label": allowed[item["concept"]], "category": "symptom",
                        "negated": False, "confidence": 0.6, "origin": "slm",
                        "evidence": {"segment_id": seg["id"], "source_id": seg.get("source_id"), "start": item["start"],
                                     "end": item["end"], "quote": item["quote"]}})
                    slm_added.append(item)
                    break
        tick("slm", t)

    facts = extract.aggregate(per_segment)
    for v in form_vitals:  # typed vitals win over vitals mentioned in free text
        facts["vitals"][v["name"]] = {**v, "source_id": "form"}
    for rr in report_results:
        facts["labs"].extend({**lab, "source_id": rr["source_id"], "origin": "report"} for lab in rr["labs"])

    t = time.time()
    priority = redflags.evaluate(facts, case.get("patient", {}))
    tick("rules", t)

    t = time.time()
    flags += guards.hidden_text_flags(report_results)
    flags += guards.quality_flags(report_results, asr_results, [s for s in segments if s["kind"] in ("text", "asr")])
    flags += guards.conflict_flags(facts, case.get("patient", {}), report_results)
    flags += missing_info(facts, case.get("patient", {}))
    tick("guards", t)

    langs = [s["lang"] for s in segments if s["kind"] in ("text", "asr")]
    primary = hint or (max(set(langs), key=langs.count) if langs else "en")
    questions = generate_questions(flags, primary)
    timeline = build_timeline(facts, case.get("created_at") or datetime.now().isoformat(timespec="seconds"))

    analysis = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "language": {"primary": primary, "detected": sorted(set(langs))},
        "segments": segments,
        "reports": [{k: v for k, v in rr.items() if k != "segments"} for rr in report_results],
        "audio": [{k: v for k, v in ar.items() if k != "segments"} for ar in asr_results],
        "facts": facts,
        "timeline": timeline,
        "priority": priority,
        "flags": flags,
        "questions": questions,
        "slm_added": slm_added,
        "patient_message": status_message(priority["level"], primary),
        "engines": {
            "asr": f"faster-whisper-{config.ASR_MODEL}" if asr.asr_available() else None,
            "ocr": "rapidocr-onnx" if ocr.ocr_available() else None,
            "slm": config.SLM_MODEL if slm.enabled() else None,
            "rules": priority["rulebook_version"],
        },
    }
    present = [s["label"] for s in facts["symptoms"].values() if s["present"]]
    p = case.get("patient", {})
    who = f"{p.get('age') if p.get('age') is not None else '?'}{(p.get('sex') or '?')[0]}"
    analysis["summary"] = (f"{who}: " + (", ".join(present[:5]) if present else "no active symptoms reported") +
                           f". Suggested {priority['level']} ({priority['name']}).")
    if slm.enabled():
        s = slm.one_line_summary("; ".join(present))
        if s:
            analysis["summary_slm"] = s
    analysis["note_draft"] = note.draft_note(case, analysis)
    timings["total"] = int((time.time() - t_all) * 1000)
    analysis["timings_ms"] = timings
    return analysis

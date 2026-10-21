"""Evaluate the CareFlow pipeline on the synthetic dataset.

Usage:
  python -m scripts.evaluate                    # full pipeline incl. ASR + OCR
  python -m scripts.evaluate --oracle-asr       # use gold transcripts instead of running ASR
  python -m scripts.evaluate --cases CF-001 CF-013

Writes eval/results.json and eval/report.md. Metrics are computed on SYNTHETIC data and
are a regression check for the rules, not evidence of clinical performance.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.pipeline import orchestrator, slm  # noqa: E402
from app.pipeline.guards import scan_injection  # noqa: E402
from app.pipeline.redflags import LEVELS  # noqa: E402

PRECISE_FLAG_TYPES = ("injection", "conflict", "unreadable_report")


def load_dataset(ds: Path) -> list[dict]:
    return json.loads((ds / "cases.json").read_text(encoding="utf-8"))


def to_pipeline_input(c: dict, ds: Path, oracle_asr: bool) -> tuple[dict, list[dict]]:
    case = {"code": c["case_id"], "patient": c["patient"], "language": c["language"], "vitals": c.get("vitals") or {},
            "created_at": "2026-10-19T10:00:00"}
    sources = []
    for i, inp in enumerate(c["inputs"], start=1):
        if inp["type"] == "text":
            sources.append({"id": i, "type": "text", "content": inp["content"], "lang": inp.get("lang")})
        elif inp["type"] == "audio" and oracle_asr:
            sources.append({"id": i, "type": "text", "content": inp["transcript"], "lang": inp.get("lang"),
                            "label": "oracle transcript"})
        else:
            sources.append({"id": i, "type": inp["type"] if inp["type"] != "report" else "report",
                            "path": str(ds / inp["file"]), "filename": Path(inp["file"]).name, "lang": inp.get("lang")})
    return case, sources


def flag_keys(analysis: dict) -> set[str]:
    keys = set()
    for f in analysis["flags"]:
        if f["type"] in ("conflict", "missing"):
            keys.add(f"{f['type']}:{f['field']}")
        else:
            keys.add(f["type"])
    return keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(config.DATASET_DIR))
    ap.add_argument("--oracle-asr", action="store_true")
    ap.add_argument("--cases", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "eval"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    ds = Path(args.dataset)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cases = load_dataset(ds)
    if args.cases:
        cases = [c for c in cases if c["case_id"] in set(args.cases)]

    rows = []
    for c in cases:
        case, sources = to_pipeline_input(c, ds, args.oracle_asr)
        t = time.time()
        a = orchestrator.run(case, sources)
        ms = int((time.time() - t) * 1000)
        gold = c["gold"]
        pred = a["priority"]["level"]
        fired = {r["rule_id"] for r in a["priority"]["red_flags"]}
        present = {k for k, v in a["facts"]["symptoms"].items() if v["present"]}
        fk = flag_keys(a)
        row = {
            "case_id": c["case_id"], "scenario": c["scenario"], "gold": gold["priority"], "pred": pred,
            "delta": LEVELS.index(pred) - LEVELS.index(gold["priority"]),
            "gold_red_flags": gold["red_flags"], "fired": sorted(fired),
            "red_flag_hit": all(r in fired for r in gold["red_flags"]),
            "gold_flags": gold["flags"], "pred_flags": sorted(fk),
            "gold_symptoms": gold["symptoms"], "pred_symptoms": sorted(present),
            "ms": ms, "timings": a["timings_ms"],
        }
        rows.append(row)
        if not args.quiet:
            mark = "OK " if row["delta"] == 0 else ("UNDER" if row["delta"] > 0 else "over ")
            print(f"{c['case_id']} gold {gold['priority']} pred {pred} {mark} {ms:>6} ms  fired={sorted(fired)}")

    n = len(rows)
    exact = sum(r["delta"] == 0 for r in rows)
    within1 = sum(abs(r["delta"]) <= 1 for r in rows)
    under = [r for r in rows if r["delta"] > 0]
    over = [r for r in rows if r["delta"] < 0]
    urgent = [r for r in rows if r["gold"] in ("P1", "P2")]
    urgent_caught = [r for r in urgent if r["pred"] in ("P1", "P2")]
    rf_rows = [r for r in rows if r["gold_red_flags"]]
    rf_hit = [r for r in rf_rows if r["red_flag_hit"]]

    # Flag detection for the precise flag types (injection / conflict / unreadable report).
    def precise(keys):
        return {k for k in keys if k.split(":")[0] in PRECISE_FLAG_TYPES}
    tp = fn = fp = 0
    for r in rows:
        g, p = precise(set(r["gold_flags"])), precise(set(r["pred_flags"]))
        tp += len(g & p)
        fn += len(g - p)
        fp += len(p - g)
    miss_gold = [(r, k) for r in rows for k in r["gold_flags"] if k.startswith("missing:")]
    miss_hit = [(r, k) for r, k in miss_gold if k in r["pred_flags"]]
    sym_gold = sum(len(r["gold_symptoms"]) for r in rows)
    sym_hit = sum(len(set(r["gold_symptoms"]) & set(r["pred_symptoms"])) for r in rows)

    attacks = json.loads((ds / "injection_attacks.json").read_text(encoding="utf-8"))
    benign = json.loads((ds / "benign_controls.json").read_text(encoding="utf-8"))
    inj_tp = sum(bool(scan_injection(s)) for s in attacks)
    inj_fp = sum(bool(scan_injection(s)) for s in benign)

    lat = sorted(r["ms"] for r in rows)
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else 0
    confusion = {g: {p: sum(1 for r in rows if r["gold"] == g and r["pred"] == p) for p in LEVELS} for g in LEVELS}

    metrics = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cases": n,
        "mode": "oracle-asr" if args.oracle_asr else "full",
        "engines": {"asr": None if args.oracle_asr else f"faster-whisper-{config.ASR_MODEL}", "slm": config.SLM_MODEL if slm.enabled() else None},
        "priority_exact": round(exact / n, 3) if n else 0,
        "priority_within_1": round(within1 / n, 3) if n else 0,
        "under_triage_cases": [r["case_id"] for r in under],
        "over_triage_cases": [r["case_id"] for r in over],
        "urgent_recall_P1P2": round(len(urgent_caught) / len(urgent), 3) if urgent else None,
        "red_flag_rule_recall": round(len(rf_hit) / len(rf_rows), 3) if rf_rows else None,
        "precise_flag_recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "precise_flag_precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "missing_info_recall": round(len(miss_hit) / len(miss_gold), 3) if miss_gold else None,
        "symptom_recall": round(sym_hit / sym_gold, 3) if sym_gold else None,
        "injection_detection_rate": f"{inj_tp}/{len(attacks)}",
        "injection_false_positives": f"{inj_fp}/{len(benign)}",
        "latency_ms_p50": p50, "latency_ms_p95": p95,
        "confusion": confusion,
    }
    (out / "results.json").write_text(json.dumps({"metrics": metrics, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")

    md = ["# CareFlow AI - evaluation on the synthetic dataset", "",
          f"Run: {metrics['run_at']} | mode: {metrics['mode']} | cases: {n} | SLM: {metrics['engines']['slm'] or 'off'}", "",
          "> Synthetic data, labels authored by the team against the rulebook rubric. This is a regression check, not clinical validation.", "",
          "| Metric | Value |", "| --- | --- |"]
    for k in ("priority_exact", "priority_within_1", "urgent_recall_P1P2", "red_flag_rule_recall", "precise_flag_recall",
              "precise_flag_precision", "missing_info_recall", "symptom_recall", "injection_detection_rate",
              "injection_false_positives", "latency_ms_p50", "latency_ms_p95"):
        md.append(f"| {k} | {metrics[k]} |")
    md += ["", f"Under-triaged (pred less urgent than gold): {', '.join(metrics['under_triage_cases']) or 'none'}",
           f"Over-triaged: {', '.join(metrics['over_triage_cases']) or 'none'}", "",
           "## Confusion matrix (rows = gold, columns = predicted)", "", "| gold \\ pred | " + " | ".join(LEVELS) + " |",
           "| --- |" + " --- |" * len(LEVELS)]
    for g in LEVELS:
        md.append(f"| {g} | " + " | ".join(str(confusion[g][p]) for p in LEVELS) + " |")
    md += ["", "## Per case", "", "| Case | Gold | Pred | Red flags fired | Flags | ms |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        md.append(f"| {r['case_id']} | {r['gold']} | {r['pred']} | {', '.join(r['fired']) or '-'} | "
                  f"{', '.join(k for k in r['pred_flags'] if not k.startswith('missing'))} | {r['ms']} |")
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if k != "confusion"}, indent=2))


if __name__ == "__main__":
    main()

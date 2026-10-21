"""Load synthetic dataset cases into the app database so the dashboard has a realistic queue.

Usage:
  python -m scripts.seed_demo --reset            # all 45 cases
  python -m scripts.seed_demo --reset --demo     # only the scripted demo cases
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth, config, db, services  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(config.DATASET_DIR))
    ap.add_argument("--reset", action="store_true", help="delete the database and uploads first")
    ap.add_argument("--demo", action="store_true", help="only cases marked demo=true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    ds = Path(args.dataset)

    if args.reset:
        for p in (config.DB_PATH, Path(str(config.DB_PATH) + "-wal"), Path(str(config.DB_PATH) + "-shm")):
            p.unlink(missing_ok=True)
        shutil.rmtree(config.UPLOAD_DIR, ignore_errors=True)
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init()
    auth.seed_users()

    cases = json.loads((ds / "cases.json").read_text(encoding="utf-8"))
    if args.demo:
        cases = [c for c in cases if c.get("demo")]
    if args.limit:
        cases = cases[: args.limit]
    rng = random.Random(42)
    now = datetime.now()
    for i, c in enumerate(cases):
        created = (now - timedelta(minutes=rng.randint(1, 150))).isoformat(timespec="seconds")
        consent = {"triage": True, "store_voice": True, "research": False, "version": "1.0-2026-10", "method": "synthetic seed"}
        p = dict(c["patient"])
        code, access = services.create_case(p, c["language"], consent, c.get("vitals"), "seed.script", "system", created_at=created)
        for inp in c["inputs"]:
            if inp["type"] == "text":
                services.add_text_source(code, inp["content"], inp.get("lang"), label="patient message")
            else:
                f = ds / inp["file"]
                kind = "audio" if inp["type"] == "audio" else "report"
                mime = {"pdf": "application/pdf", "png": "image/png", "wav": "audio/wav"}.get(f.suffix[1:].lower())
                services.add_file_source(code, kind, f.name, f.read_bytes(), mime, inp.get("lang"))
        services.process_now(code)
        case = db.get_case(code)
        print(f"[{i + 1}/{len(cases)}] {c['case_id']} -> {code} (access {access})  priority {case['priority']}  gold {c['gold']['priority']}")


if __name__ == "__main__":
    main()

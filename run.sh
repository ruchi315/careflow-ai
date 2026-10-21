#!/usr/bin/env bash
# One-command start: creates the venv on first run, seeds the demo queue, starts the server.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
if [ ! -f data/careflow.db ] || [ "${1:-}" = "--reset" ]; then
  .venv/bin/python -m scripts.seed_demo --reset
fi
echo "CareFlow AI on http://localhost:${PORT:-8000}  (staff logins: dr.mehta / nurse.asha / intake.ravi / admin.dpo, password: demo)"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

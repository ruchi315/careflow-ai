import os
import sys
import tempfile
from pathlib import Path

# Isolated database/uploads and no heavy models for unit tests (ASR is exercised by scripts/evaluate.py).
_tmp = tempfile.mkdtemp(prefix="careflow-test-")
os.environ["CAREFLOW_DATA_DIR"] = _tmp
os.environ["CAREFLOW_DB"] = str(Path(_tmp) / "test.db")
os.environ.setdefault("CAREFLOW_ASR", "off")
os.environ["CAREFLOW_SLM_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

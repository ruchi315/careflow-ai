"""Runtime configuration (environment variables with safe defaults)."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = BASE_DIR / "app" / "knowledge"
DATA_DIR = Path(os.environ.get("CAREFLOW_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.environ.get("CAREFLOW_DB", DATA_DIR / "careflow.db"))
DATASET_DIR = Path(os.environ.get("CAREFLOW_DATASET", BASE_DIR.parent / "careflow_dataset"))

# Secret used to sign staff session tokens. Override in any shared deployment.
SECRET_KEY = os.environ.get("CAREFLOW_SECRET", "dev-only-change-me")
TOKEN_TTL_SECONDS = int(os.environ.get("CAREFLOW_TOKEN_TTL", 12 * 3600))

# Speech recognition (faster-whisper, CPU int8). Set CAREFLOW_ASR=off to disable.
ASR_ENABLED = os.environ.get("CAREFLOW_ASR", "on") != "off"
ASR_MODEL = os.environ.get("CAREFLOW_ASR_MODEL", "small")
ASR_THREADS = int(os.environ.get("CAREFLOW_ASR_THREADS", "8"))

# OCR (RapidOCR / ONNX). Set CAREFLOW_OCR=off to disable.
OCR_ENABLED = os.environ.get("CAREFLOW_OCR", "on") != "off"

# Optional Small Language Model behind any OpenAI-compatible endpoint
# (Ollama: http://localhost:11434/v1, llama.cpp server, vLLM ...). Empty = disabled.
SLM_URL = os.environ.get("CAREFLOW_SLM_URL", "").rstrip("/")
SLM_MODEL = os.environ.get("CAREFLOW_SLM_MODEL", "qwen2.5:7b-instruct")
SLM_TIMEOUT = float(os.environ.get("CAREFLOW_SLM_TIMEOUT", "60"))

# Confidence thresholds
ASR_CONF_THRESHOLD = 0.55
OCR_CONF_THRESHOLD = 0.70
TRANSLATION_CONF_THRESHOLD = 0.60

# Queue ageing: minutes after which a waiting case is moved up one place-band in the queue.
# This only changes queue ORDER, never the clinical priority.
AGEING_MINUTES = {"P3": 60, "P4": 90, "P5": 120}

LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "hi-en": "Hinglish",
    "ta": "Tamil",
    "bn": "Bengali",
}

for _d in (DATA_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)

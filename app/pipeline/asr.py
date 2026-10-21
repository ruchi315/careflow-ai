"""M2 - Speech recognition with per-segment confidence (faster-whisper, CPU int8).

Audio is decoded with ffmpeg (so browser webm/ogg recordings work too) and transcribed
with an optional language hint and a short domain prompt that biases Whisper towards
clinical vocabulary. Each segment gets a confidence built from Whisper's average token
log-probability and no-speech probability; low-confidence segments are flagged.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from app import config

WHISPER_LANG = {"en": "en", "hi": "hi", "hi-en": "hi", "ta": "ta", "bn": "bn"}
DOMAIN_PROMPT = {
    "en": "Patient describing symptoms: chest pain, sweating, fever, cough, headache, dizziness, breathlessness, vomiting.",
    "hi": "मरीज़ के लक्षण: सीने में दर्द, पसीना, बुखार, खांसी, सांस फूलना, उल्टी, चक्कर।",
    "ta": "நோயாளியின் அறிகுறிகள்: நெஞ்சு வலி, காய்ச்சல், இருமல், மூச்சு திணறல், வாந்தி.",
    "bn": "রোগীর উপসর্গ: বুকে ব্যথা, জ্বর, কাশি, শ্বাসকষ্ট, বমি।",
}
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _model():
    if not config.ASR_ENABLED:
        return None
    try:
        from faster_whisper import WhisperModel
        return WhisperModel(config.ASR_MODEL, device="cpu", compute_type="int8", cpu_threads=config.ASR_THREADS)
    except Exception:  # pragma: no cover - optional dependency / offline
        return None


def asr_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return config.ASR_ENABLED and shutil.which("ffmpeg") is not None


def decode_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def transcribe(path: Path, lang_hint: str | None = None) -> dict:
    """Returns {available, language, language_prob, segments:[{start_ms,end_ms,text,conf}], duration_s}."""
    if not asr_available():
        return {"available": False, "segments": [], "error": "ASR engine not installed or disabled"}
    model = _model()
    if model is None:
        return {"available": False, "segments": [], "error": "ASR model could not be loaded"}
    audio = decode_audio(path)
    lang = WHISPER_LANG.get(lang_hint or "", None)
    best = _run(model, audio, lang)
    # The patient's chosen language is a hint, not a fact (people mix languages). If the forced
    # language gives a poor transcript, retry with automatic detection and keep the better one.
    if lang is not None and best["mean_conf"] < 0.4:
        auto = _run(model, audio, None)
        if auto["mean_conf"] > best["mean_conf"]:
            best = auto
    return {"available": True, **best, "duration_s": round(len(audio) / 16000, 2), "model": f"faster-whisper-{config.ASR_MODEL}"}


def _run(model, audio: np.ndarray, lang: str | None) -> dict:
    with _lock:  # CTranslate2 model is shared; serialise calls
        segs, info = model.transcribe(audio, language=lang, task="transcribe", beam_size=5,
                                      initial_prompt=DOMAIN_PROMPT.get(lang or "en"), vad_filter=False,
                                      condition_on_previous_text=False)
        segs = list(segs)
    out = []
    for s in segs:
        conf = math.exp(s.avg_logprob) * (1 - s.no_speech_prob)
        if s.compression_ratio > 2.4:  # repetitive hallucination
            conf *= 0.5
        out.append({"start_ms": int(s.start * 1000), "end_ms": int(s.end * 1000), "text": s.text.strip(),
                    "conf": round(conf, 3)})
    mean = sum(s["conf"] for s in out) / len(out) if out else 0.0
    return {"language": info.language, "language_prob": round(info.language_probability, 3),
            "language_forced": lang is not None, "segments": out, "mean_conf": round(mean, 3)}

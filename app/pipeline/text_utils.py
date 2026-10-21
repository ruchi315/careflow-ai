"""Text normalisation, script/language detection and multilingual phrase matching.

All matching happens on a *normalised* copy of the text, but every match is mapped
back to character offsets in the ORIGINAL text so evidence can be highlighted exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.knowledge import lexicon

_DIGITS = {}
for base in (0x0966, 0x09E6, 0x0BE6):  # Devanagari, Bengali, Tamil digits
    for i in range(10):
        _DIGITS[chr(base + i)] = str(i)
_DROP = {"‌", "‍", "﻿"}  # ZWNJ, ZWJ, BOM

# Characters that delimit words for boundary checks (works across scripts).
DELIMS = r"\s,.;:!?।()\"'\-/"
LB = rf"(?<![^{DELIMS}])"  # left boundary: start of text or a delimiter before
RB = rf"(?![^{DELIMS}])"  # right boundary: end of text or a delimiter after


def normalize(text: str) -> tuple[str, list[int]]:
    """Return (normalised_text, index_map) where index_map[i] is the original offset."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        if ch in _DROP:
            continue
        ch = _DIGITS.get(ch, ch)
        low = ch.lower()
        if len(low) != 1:
            low = ch
        out.append(low)
        idx.append(i)
    return "".join(out), idx


def to_original_span(index_map: list[int], start: int, end: int) -> tuple[int, int]:
    if not index_map:
        return 0, 0
    s = index_map[min(start, len(index_map) - 1)]
    e = index_map[min(end, len(index_map)) - 1] + 1 if end > 0 else s
    return s, e


def script_of(text: str) -> str:
    counts = {"deva": 0, "taml": 0, "beng": 0, "latn": 0}
    for ch in text:
        o = ord(ch)
        if 0x0900 <= o <= 0x097F:
            counts["deva"] += 1
        elif 0x0B80 <= o <= 0x0BFF:
            counts["taml"] += 1
        elif 0x0980 <= o <= 0x09FF:
            counts["beng"] += 1
        elif ch.isascii() and ch.isalpha():
            counts["latn"] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else "latn"


_HINGLISH_MARKERS = {
    "hai", "hain", "mein", "nahi", "nahin", "aur", "kya", "raha", "rahi", "hoon", "ko", "se",
    "ke", "ki", "ka", "bahut", "din", "ghante", "dard", "bukhar", "khansi", "ulti", "pasina",
    "seene", "pet", "sar", "kal", "aaj", "mujhe", "mera", "meri", "ho", "gaya", "gayi", "jab",
}


def detect_language(text: str) -> tuple[str, float]:
    """Very small script + keyword language identifier. Returns (code, confidence)."""
    s = script_of(text)
    if s == "deva":
        return "hi", 0.95
    if s == "taml":
        return "ta", 0.95
    if s == "beng":
        return "bn", 0.95
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return "en", 0.5
    hits = sum(1 for w in words if w in _HINGLISH_MARKERS)
    ratio = hits / len(words)
    if hits >= 2 and ratio >= 0.15:
        return "hi-en", min(0.95, 0.6 + ratio)
    return "en", max(0.6, 1 - ratio * 2)


def is_indic(pattern: str) -> bool:
    return script_of(pattern) != "latn"


@dataclass
class Match:
    key: str
    start: int  # offsets in ORIGINAL text
    end: int
    nstart: int  # offsets in normalised text
    nend: int
    text: str


@lru_cache(maxsize=None)
def concept_patterns() -> list[tuple[str, re.Pattern]]:
    """One compiled regex per (concept, pattern), longest patterns first."""
    out = []
    for key, spec in lexicon()["concepts"].items():
        for lang in ("en", "hi-en", "hi", "ta", "bn"):
            for pat in spec.get(lang, []) or []:
                p = normalize(pat)[0]
                if is_indic(p):
                    rx = re.compile(LB + "(?:" + p + ")")
                else:
                    rx = re.compile(LB + "(?:" + p + ")" + RB)
                out.append((key, rx))
    return out


def find_concepts(text: str) -> list[Match]:
    norm, imap = normalize(text)
    found: list[Match] = []
    for key, rx in concept_patterns():
        for m in rx.finditer(norm):
            s, e = to_original_span(imap, m.start(), m.end())
            found.append(Match(key, s, e, m.start(), m.end(), text[s:e]))
    # Deduplicate: keep the longest match per concept per overlapping region.
    found.sort(key=lambda m: (m.key, m.start, -(m.end - m.start)))
    dedup: list[Match] = []
    for m in found:
        if dedup and dedup[-1].key == m.key and m.start < dedup[-1].end:
            continue
        dedup.append(m)
    return dedup


def sentence_bounds(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in re.finditer(r"[.!?।\n]+", text):
        bounds.append((start, m.end()))
        start = m.end()
    if start < len(text):
        bounds.append((start, len(text)))
    return bounds or [(0, len(text))]


def clause_end(text: str, pos: int, limit: int = 14) -> int:
    """End of the clause starting at pos (stops at punctuation or a conjunction)."""
    seg = text[pos:pos + limit]
    m = re.search(r"[,.;।!?\n]|\b(and|aur)\b|और|மற்றும்|আর", seg)
    return pos + (m.start() if m else len(seg))


NUMBER_WORDS = {
    # English
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "few": 3, "couple of": 2, "couple": 2,
    # Hinglish
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "saat": 7,
    # Hindi
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8,
    # Tamil
    "ஒரு": 1, "ஒன்று": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
    # Bengali
    "এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5,
}


def to_number(token: str) -> float | None:
    token = token.strip().lower()
    try:
        return float(token)
    except ValueError:
        return NUMBER_WORDS.get(token)

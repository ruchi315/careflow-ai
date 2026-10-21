"""M4 - Translation to an English pivot with an explicit confidence.

Default (no model download needed): a lexicon gloss. Clinical phrases and durations are
translated from the multilingual lexicon, common function words from a small dictionary,
and anything unknown is left in the original script. Confidence = share of the text the
gloss actually understood, so partial understanding is visible and flagged.
If an SLM endpoint is configured, the SLM translation is used and the lexical coverage is
still reported as a cross-check.
"""

from __future__ import annotations

import re

from app.knowledge import lexicon
from app.pipeline import slm
from app.pipeline.extract import DURATION_PATTERNS, _UNIT_HOURS
from app.pipeline.text_utils import find_concepts, normalize, to_number, to_original_span

_UNIT_WORD = {1 / 60: "minutes", 1: "hours", 24: "days", 168: "weeks", 720: "months", 8760: "years"}


def _letters(s: str) -> int:
    return sum(1 for ch in s if ch.isalpha() or (0x0900 <= ord(ch) <= 0x0DFF))


def lexical_gloss(text: str, lang: str) -> dict:
    if lang == "en":
        return {"text_en": text, "confidence": 1.0, "method": "identity"}
    repl: list[tuple[int, int, str]] = []
    for m in find_concepts(text):
        repl.append((m.start, m.end, lexicon()["concepts"][m.key]["label"]))
    norm, imap = normalize(text)
    for rx in DURATION_PATTERNS:
        for m in rx.finditer(norm):
            n = to_number(m.group(1))
            hours = _UNIT_HOURS.get(m.group(2))
            if n is None or hours is None:
                continue
            s, e = to_original_span(imap, m.start(), m.end())
            repl.append((s, e, f"for {int(n) if n == int(n) else n} {_UNIT_WORD.get(hours, m.group(2))}"))
    # Resolve overlaps: prefer longer spans.
    repl.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    chosen, last_end = [], -1
    for r in repl:
        if r[0] >= last_end:
            chosen.append(r)
            last_end = r[1]

    fw = (lexicon().get("function_words") or {}).get(lang, {}) or {}
    out, covered, pos = [], 0, 0

    def gloss_free(chunk: str):
        nonlocal covered
        for tok in re.findall(r"\S+", chunk):
            core = re.sub(r"^[^\wऀ-෿]+|[^\wऀ-෿]+$", "", tok)
            key = normalize(core)[0]
            if not core:
                continue
            if key in fw:
                covered += _letters(core)
                if fw[key]:
                    out.append(fw[key])
            elif re.fullmatch(r"\d+(\.\d+)?", key):
                out.append(key)
            else:
                out.append(core)

    for s, e, label in chosen:
        gloss_free(text[pos:s])
        out.append(label)
        covered += _letters(text[s:e])
        pos = e
    gloss_free(text[pos:])
    total = max(1, _letters(text))
    conf = round(min(1.0, covered / total), 2)
    return {"text_en": " ".join(out), "confidence": conf, "method": "lexicon_gloss"}


def translate(text: str, lang: str) -> dict:
    lex = lexical_gloss(text, lang)
    if lang == "en" or not slm.enabled():
        return lex
    res = slm.translate(text, lang)
    if res:
        return {"text_en": res, "confidence": max(0.75, lex["confidence"]), "method": "slm",
                "lexical_coverage": lex["confidence"]}
    return lex

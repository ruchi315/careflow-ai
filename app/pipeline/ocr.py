"""M3 - Report reading: PDF text layer, OCR for images/scans, quality scoring,
and hidden-text detection (text present in the PDF layer but not visible on the page).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from app import config


@lru_cache(maxsize=1)
def _engine():
    if not config.OCR_ENABLED:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:  # pragma: no cover - optional dependency
        return None


def ocr_available() -> bool:
    return _engine() is not None


def image_quality(path: Path) -> dict:
    """Sharpness (variance of Laplacian) and brightness of a downscaled grey image."""
    img = Image.open(path).convert("L")
    img.thumbnail((1000, 1000))
    a = np.asarray(img, dtype=np.float32)
    lap = (a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1] - 4 * a[1:-1, 1:-1])
    # Contrast between ink and paper: spread between dark and light percentiles.
    contrast = float(np.percentile(a, 95) - np.percentile(a, 5))
    return {"sharpness": round(float(lap.var()), 1), "brightness": round(float(a.mean()), 1),
            "contrast": round(contrast, 1)}


def _ocr_lines(path: Path) -> list[dict]:
    """Run OCR and group word boxes into text rows (left-to-right, top-to-bottom)."""
    eng = _engine()
    if eng is None:
        return []
    res, _ = eng(str(path))
    boxes = []
    for box, text, score in res or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        boxes.append({"text": text, "conf": float(score), "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)})
    boxes.sort(key=lambda b: ((b["y1"] + b["y2"]) / 2, b["x1"]))
    rows: list[list[dict]] = []
    for b in boxes:
        cy = (b["y1"] + b["y2"]) / 2
        h = max(8.0, b["y2"] - b["y1"])
        if rows:
            last = rows[-1]
            lcy = sum((x["y1"] + x["y2"]) / 2 for x in last) / len(last)
            if abs(cy - lcy) < 0.6 * h:
                last.append(b)
                continue
        rows.append([b])
    lines = []
    for r in rows:
        r.sort(key=lambda b: b["x1"])
        lines.append({
            "text": "  ".join(b["text"] for b in r),
            "conf": round(sum(b["conf"] for b in r) / len(r), 3),
            "bbox": [round(min(b["x1"] for b in r)), round(min(b["y1"] for b in r)),
                     round(max(b["x2"] for b in r)), round(max(b["y2"] for b in r))],
            "words": [{"text": b["text"], "bbox": [round(b["x1"]), round(b["y1"]), round(b["x2"]), round(b["y2"])],
                       "conf": round(b["conf"], 3)} for b in r],
        })
    return lines


def read_image(path: Path) -> dict:
    q = image_quality(path)
    lines = _ocr_lines(path)
    with Image.open(path) as im:
        size = im.size
    mean_conf = round(sum(l["conf"] for l in lines) / len(lines), 3) if lines else 0.0
    return {"method": "ocr" if ocr_available() else "none", "lines": lines, "quality": {**q, "ocr_mean_conf": mean_conf,
            "ocr_lines": len(lines)}, "image_size": size, "pages": 1}


def _rasterize(pdf: Path, outdir: Path, dpi: int = 110) -> list[Path]:
    if not shutil.which("pdftoppm"):
        return []
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(outdir / "page")], check=True,
                   capture_output=True, timeout=60)
    return sorted(outdir.glob("page*.png"))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def read_pdf(path: Path) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    lines = []
    for pno, page in enumerate(reader.pages, start=1):
        txt = page.extract_text() or ""
        for lno, ln in enumerate(t for t in txt.splitlines() if t.strip()):
            lines.append({"text": ln.strip(), "conf": 1.0, "page": pno, "line": lno + 1})

    result = {"method": "pdf_text", "lines": lines, "quality": {"text_layer": bool(lines)}, "pages": len(reader.pages),
              "hidden_lines": []}

    if ocr_available():
        with tempfile.TemporaryDirectory() as td:
            pages = _rasterize(path, Path(td))
            if not lines:
                # Scanned PDF without a text layer: OCR every page.
                result["method"] = "ocr_scanned_pdf"
                for pno, img in enumerate(pages, start=1):
                    r = read_image(img)
                    for ln in r["lines"]:
                        result["lines"].append({**ln, "page": pno})
                    result["quality"] = r["quality"]
            elif pages:
                # Hidden-text check: words in the text layer that never appear visually.
                visible = set()
                for img in pages:
                    for ln in _ocr_lines(img):
                        visible |= _words(ln["text"])
                for ln in lines:
                    w = _words(ln["text"])
                    if len(w) >= 4 and len(w & visible) / len(w) < 0.3:
                        ln["hidden"] = True
                        result["hidden_lines"].append(ln["text"])
    return result

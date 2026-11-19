"""PDF -> per-page English text.

Primary extractor is pypdf on every machine, so the parsing patterns see the same text on the
Mac and the Pi. pdftotext (when installed) is a second reading used to fill fields the first
missed. Pages that carry no text (scanned annexures) are OCR'd one by one when tesseract exists.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

_DEVANAGARI_TOKEN = re.compile(r"\S*[ऀ-ॿ]\S*")
EMPTY_PAGE_CHARS = 120     # below this a page is treated as an image
MAX_OCR_PAGES = 10         # bounded cost on the Pi


def page_texts(path: Path) -> list[str]:
    try:
        pages = [clean(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    except Exception:  # noqa: BLE001 - damaged PDF: fall back to poppler
        pages = alt_page_texts(path)
    empty = [i for i, t in enumerate(pages) if len(t) < EMPTY_PAGE_CHARS]
    if empty:
        for i, text in _ocr(path, empty[:MAX_OCR_PAGES]).items():
            if len(text) > len(pages[i]):
                pages[i] = text
    return pages


def alt_page_texts(path: Path) -> list[str]:
    """Second reading with poppler's pdftotext (layout mode keeps table rows on one line)."""
    if not shutil.which("pdftotext"):
        return []
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                         capture_output=True, text=True, timeout=120).stdout
    return [clean(p) for p in out.split("\f")]


def clean(text: str) -> str:
    """GeM documents are bilingual line by line; keep only the English so labels sit next to values."""
    text = text.replace("₹", "Rs ")  # keep the rupee sign's meaning before stripping non-ASCII
    text = _DEVANAGARI_TOKEN.sub(" ", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("“", '"').replace("”", '"')
    text = re.sub(r"[^\x20-\x7E\n]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ocr(path: Path, page_indexes: list[int]) -> dict[int, str]:
    if not page_indexes or not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return {}
    out: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for i in page_indexes:
            n = str(i + 1)
            subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-f", n, "-l", n, str(path), f"{tmp}/p{n}"],
                           capture_output=True, timeout=120)
            for img in Path(tmp).glob(f"p{n}*.png"):
                r = subprocess.run(["tesseract", str(img), "-", "-l", "eng"], capture_output=True, text=True, timeout=180)
                out[i] = clean(r.stdout)
                img.unlink()
    return out

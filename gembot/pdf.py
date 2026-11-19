"""PDF -> per-page English text. pdftotext when installed (the Pi), pypdf otherwise (the Mac)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from pypdf import PdfReader

_DEVANAGARI_TOKEN = re.compile(r"\S*[ऀ-ॿ]\S*")


def page_texts(path: Path) -> list[str]:
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, text=True, timeout=120).stdout
        pages = out.split("\f")
    else:
        pages = [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    pages = [clean(p) for p in pages]
    if sum(len(p) for p in pages) < 200:  # scanned document: no text layer
        ocr = _ocr(path)
        if ocr:
            return ocr
    return pages


def _ocr(path: Path, max_pages: int = 8) -> list[str]:
    """OCR fallback (Pi has tesseract + pdftoppm). Capped pages: eligibility details sit up front."""
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return []
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-f", "1", "-l", str(max_pages),
                        str(path), f"{tmp}/p"], capture_output=True, timeout=300)
        out = []
        for img in sorted(Path(tmp).glob("p*.png")):
            r = subprocess.run(["tesseract", str(img), "-", "-l", "eng"], capture_output=True, text=True, timeout=180)
            out.append(clean(r.stdout))
        return out


def clean(text: str) -> str:
    """GeM documents are bilingual line by line; keep only the English so labels sit next to values."""
    text = _DEVANAGARI_TOKEN.sub(" ", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[^\x20-\x7E\n]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

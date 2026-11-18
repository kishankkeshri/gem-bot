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
    return [clean(p) for p in pages]


def clean(text: str) -> str:
    """GeM documents are bilingual line by line; keep only the English so labels sit next to values."""
    text = _DEVANAGARI_TOKEN.sub(" ", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[^\x20-\x7E\n]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

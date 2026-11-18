"""Optional Gemini pass: fills only facts static parsing missed, plus a 2-line scope summary.

Guard against made-up facts: every fact must carry a quote, and the quote must actually appear
on the page it claims (after whitespace normalisation). Facts that fail this are dropped.
Results are cached per document, so re-runs cost nothing.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

FIELDS = {
    "end_date": "bid submission last date, DD-MM-YYYY",
    "emd": "EMD / bid security amount in rupees, digits only",
    "emd_exempt": "'Yes' if EMD is waived/exempt (for everyone or for MSEs), else omit",
    "epbg_pct": "performance security / ePBG percent, number only",
    "est_value": "estimated cost / value in rupees, digits only",
    "turnover_min": "minimum average annual turnover required, in rupees, digits only",
    "experience": "past experience / similar work requirement, short",
    "oem_auth_required": "'Yes' if OEM / manufacturer authorisation is required",
    "licences": "licences / registrations the bidder must hold (e.g. FSSAI, electrical contractor licence, drug licence, labour licence), comma separated",
    "delivery_days": "delivery / completion period in days, number only",
    "kind": "one of: goods, services, works",
}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING", "description": "Two short lines: what exactly is bought / done, and the key technical requirement."},
        "facts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "field": {"type": "STRING", "enum": list(FIELDS)},
            "value": {"type": "STRING"},
            "page": {"type": "INTEGER"},
            "quote": {"type": "STRING", "description": "Exact words copied from that page, 5-25 words."},
        }, "required": ["field", "value", "page", "quote"]}},
    },
    "required": ["summary", "facts"],
}
FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def enabled() -> bool:
    return os.environ.get("AI_PROVIDER", "off") == "gemini" and bool(os.environ.get("GEMINI_API_KEY"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _models() -> list[str]:
    first = os.environ.get("GEMINI_MODEL", FALLBACK_MODELS[0])
    return [first] + [m for m in FALLBACK_MODELS if m != first]


def _call(prompt: str) -> Optional[dict]:
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA}}
    for model in _models():
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, json=body, timeout=90)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                return dict(json.loads("".join(p.get("text", "") for p in parts)), model=model)
            if r.status_code == 429 and attempt == 0:
                time.sleep(20)  # per-minute quota: wait once, then move on
                continue
            break  # busy (503) or other error: next model
    return None


def fill(pages: list[str], facts: dict, cache: Path) -> tuple[dict, Optional[str], Optional[str]]:
    """Returns (new_facts, summary, model). new_facts only contains fields missing from `facts`."""
    if cache.exists():
        out = json.loads(cache.read_text())
    else:
        missing = {k: v for k, v in FIELDS.items() if k not in facts or k == "kind"}
        doc = "\n".join(f"[PAGE {i}]\n{t[:6000]}" for i, t in enumerate(pages[:25], 1))
        prompt = ("You read Indian government tender documents. From the document below, extract ONLY these fields "
                  "when the document states them. Never guess; omit a field that is not stated.\n"
                  + "\n".join(f"- {k}: {v}" for k, v in missing.items())
                  + "\nFor each fact give the page number and an exact quote from that page.\n\n" + doc)
        out = _call(prompt)
        if out is None:
            return {}, None, None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, indent=1))

    new = {}
    for f in out.get("facts", []):
        k, page, quote = f.get("field"), f.get("page") or 0, f.get("quote") or ""
        if k not in FIELDS or k in facts or k in new or not (1 <= page <= len(pages)):
            continue
        if len(_norm(quote)) < 8 or _norm(quote) not in _norm(pages[page - 1]):
            continue  # quote not on that page -> don't trust it
        v = f["value"].strip()
        if k in ("emd_exempt", "oem_auth_required"):
            if v.lower() not in ("yes", "true"):
                continue
            v = True
        new[k] = {"value": v, "page": page, "quote": f"...{quote}...", "by": "ai"}
    return new, out.get("summary"), out.get("model")

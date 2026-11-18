"""Tiny JSON store shared by the daily run and the button listener (moves to Supabase later).

    data/state.json = {"bids": {short_id: {key, title, org, end, doc_url, pdf, status, message_id}}}
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "data" / "state.json"


def short_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {"bids": {}}


def save(state: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    os.replace(tmp, PATH)  # atomic: the listener never reads a half-written file

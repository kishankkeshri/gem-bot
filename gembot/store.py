"""Supabase copy of everything the bot finds (plain PostgREST over HTTPS, server key from .env).

The Pi's data/ files stay the working store; Supabase is the durable history and backup:
  bids          one row per tender (facts with page + quote in jsonb), Watch/Skip status
  analyses      the GREEN/AMBER/RED verdict per firm, one row per run that rated the bid
  notifications which Telegram message showed the bid
  sources       last run time / last error per portal
Every call here is best-effort: a Supabase problem is logged and never stops the daily run.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import requests

from gembot.parse_static import inr

TIMEOUT = 30


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
                and os.environ.get("SUPABASE_SYNC", "1") != "0")


def _req(method: str, table: str, *, params: dict | None = None, body=None, prefer: str = "") -> list:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    r = requests.request(method, f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}", params=params,
                         data=json.dumps(body, default=str) if body is not None else None, headers=h, timeout=TIMEOUT)
    if not r.ok:  # message only - never the key
        raise RuntimeError(f"Supabase {method} {table} {r.status_code}: {r.text[:200]}")
    return r.json() if r.text else []


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return inr(v)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _ts(v):
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%b-%Y %I:%M %p", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(v), fmt).isoformat() + "+05:30"
        except (TypeError, ValueError):
            pass
    return None


def _yn(v):
    return {"Yes": True, "No": False}.get(v) if v else None


def bid_row(b: dict) -> dict:
    f, row = b["f"], b["row"]
    from gembot.report import item
    return {
        "source_id": row["source"], "external_id": str(row["external_id"]),
        "bid_number": f("bid_number"), "org": f("org") or row.get("org"), "location": row.get("location"),
        "consignee_pin": f("consignee_pin"), "region_confirmed": b.get("region_confirmed"),
        "item": item(b)[:500], "category": row.get("category"), "bid_type": b["best"].get("kind"),
        "qty": f("qty") or (str(row["qty"]) if row.get("qty") else None),
        "est_value": _num(f("est_value")), "emd": _num(f("emd")), "epbg_pct": _num(f("epbg_pct")),
        "start_date": _ts(f("start_date")), "end_date": b["end"].isoformat() if b.get("end") else None,
        "mse_relax": f("mse_relax"), "startup_relax": f("startup_relax"), "make": f("make"), "model": f("model"),
        "indenter": f("consignee_officer"),
        "buyer": " · ".join(x for x in (row.get("buyer_account"), f("buyer_email") or f("purchase_office")) if x) or None,
        "oem_auth_required": True if f("oem_auth_required") else None,
        "delivery_days": _int(f("delivery_days")), "doc_url": row.get("doc_url"),
        "facts": json.loads(json.dumps(b["facts"], default=str)),
    }


def sync_run(bids: list[dict], st: dict) -> str:
    """Upsert this run's bids + verdicts + the card message ids. Returns a one-line result."""
    if not enabled() or not bids:
        return "supabase: nothing to sync" if enabled() else "supabase: off"
    by_key = {v["key"]: v for v in st.get("bids", {}).values() if v.get("key")}
    rows = [bid_row(b) for b in bids]
    for r in rows:  # keep Watch/Skip in step with the chat
        s = by_key.get(f"{r['source_id']}:{r['external_id']}", {})
        r["status"] = s.get("status", "new")
    saved = []
    for i in range(0, len(rows), 200):
        saved += _req("POST", "bids", params={"on_conflict": "source_id,external_id", "select": "id,source_id,external_id"},
                      body=rows[i:i + 200], prefer="resolution=merge-duplicates,return=representation")
    ids = {f"{s['source_id']}:{s['external_id']}": s["id"] for s in saved}
    analyses, notes = [], []
    for b in bids:
        key = f"{b['row']['source']}:{b['row']['external_id']}"
        bid_id = ids.get(key)
        if not bid_id:
            continue
        model = b["facts"].get("summary", {}).get("by") if isinstance(b["facts"].get("summary"), dict) else None
        for a in b["by_entity"].values():
            analyses.append({"bid_id": bid_id, "entity": a["entity"], "rating": a["rating"], "reasons": a["reasons"],
                             "blockers": a["blockers"], "actions": a["actions"], "model": model or "rules"})
        if by_key.get(key, {}).get("message_id"):
            notes.append({"bid_id": bid_id, "message_id": by_key[key]["message_id"]})
    for i in range(0, len(analyses), 500):
        _req("POST", "analyses", body=analyses[i:i + 500], prefer="return=minimal")
    if notes:
        _req("POST", "notifications", params={"on_conflict": "bid_id,message_id"}, body=notes,
             prefer="resolution=ignore-duplicates,return=minimal")
    return f"supabase: {len(saved)} bids, {len(analyses)} verdicts, {len(notes)} cards saved"


def source_status(name: str, error: str | None) -> None:
    if not enabled():
        return
    _req("PATCH", "sources", params={"id": f"eq.{name}"}, prefer="return=minimal",
         body={"last_run_at": datetime.now(timezone.utc).isoformat(), "last_error": error})


def set_status(key: str, status: str) -> None:
    """Called by the button listener on Watch / Skip."""
    if not enabled():
        return
    source, _, ext = key.partition(":")
    _req("PATCH", "bids", params={"source_id": f"eq.{source}", "external_id": f"eq.{ext}"},
         body={"status": status}, prefer="return=minimal")


def backfill_state(st: dict) -> int:
    """Cards sent before Supabase existed: store what the chat knows about them (title, org, end, status)."""
    rows = []
    for v in st.get("bids", {}).values():
        if not v.get("key"):
            continue
        source, _, ext = v["key"].partition(":")
        rows.append({"source_id": source, "external_id": ext, "item": (v.get("title") or "")[:500], "org": v.get("org"),
                     "end_date": v.get("end"), "doc_url": v.get("doc_url"), "status": v.get("status", "new"),
                     "facts": {"backfilled": {"value": True, "page": 0, "quote": f"rating at send: {v.get('rating')}"}}})
    for i in range(0, len(rows), 200):  # ignore-duplicates: never overwrite a full row with this thin one
        _req("POST", "bids", params={"on_conflict": "source_id,external_id"}, body=rows[i:i + 200],
             prefer="resolution=ignore-duplicates,return=minimal")
    return len(rows)


if __name__ == "__main__":  # python -m gembot.store --backfill
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    from gembot import state
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if "--backfill" in sys.argv:
        print(f"backfilled {backfill_state(state.load())} cards; statuses:",
              {s: sum(1 for v in state.load()['bids'].values() if v.get('status') == s) for s in ('new', 'watch', 'skip')})
    print(json.dumps(_req("GET", "sources", params={"select": "id,last_run_at,last_error"}), indent=1))

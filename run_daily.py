"""Daily run: fetch listings -> new rows -> PDF -> static facts -> rules -> Excel + Telegram.

    .venv/bin/python run_daily.py            # dry run: writes the Excel, prints the Telegram text
    .venv/bin/python run_daily.py --send     # also sends to Telegram
    .venv/bin/python run_daily.py --all      # re-analyse every open bid, not only new ones
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

import warnings

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from gembot import extract_ai, parse_static, pdf, report, rules  # noqa: E402
from gembot.http import PoliteSession
from gembot.sources import iitism

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCES = [iitism]


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"[config] {name} is not set in .env - stopping")
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    closing_days = int(os.environ.get("CLOSING_SOON_DAYS", "3"))
    http = PoliteSession(os.environ.get("HTTP_USER_AGENT", "GemBot/0.1"),
                         float(os.environ.get("HTTP_MIN_DELAY_SECONDS", "3")))
    entities = yaml.safe_load((ROOT / "config/entities.yaml").read_text())["entities"]
    today = date.today()

    seen_path = DATA / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    failures: list[str] = []
    bids: list[dict] = []

    for src in SOURCES:
        try:
            rows = src.fetch(http)
        except Exception as e:  # noqa: BLE001 - report, never skip silently
            failures.append(f"{src.NAME}: listing unreachable today ({e.__class__.__name__}: {e})")
            continue
        for row in rows:
            key = f"{row['source']}:{row['external_id']}"
            if row["listing_end_date"] and row["listing_end_date"] < today:
                continue
            if key in seen and not args.all:
                continue
            if args.limit and len(bids) >= args.limit:
                break
            try:
                bids.append(analyse(row, http, entities, today, closing_days))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                failures.append(f"{row['external_id']}: could not read document ({e.__class__.__name__})")
            seen.add(key)

    bids = [b for b in bids if b["best"]["rating"] != "SKIP"]
    bids.sort(key=lambda b: (rules.ORDER[b["best"]["rating"]], b["days_left"] if b["days_left"] is not None else 999))

    xlsx = report.write_excel(bids, DATA / "reports" / f"bids_{today:%Y-%m-%d}.xlsx", failures)
    text = report.telegram_summary(bids, today, failures, closing_days)
    print(text, f"\n\nExcel: {xlsx}", sep="")

    if args.send:
        report.send_telegram(need("TELEGRAM_BOT_TOKEN"), need("TELEGRAM_CHAT_ID"), text, xlsx if bids else None)
        print("sent to Telegram")
    DATA.mkdir(exist_ok=True)
    seen_path.write_text(json.dumps(sorted(seen), indent=0))


def analyse(row: dict, http: PoliteSession, entities: list[dict], today: date, closing_days: int) -> dict:
    facts: dict = {}
    if row["doc_url"] and row["doc_url"].lower().split("?")[0].endswith(".pdf"):
        path = DATA / "pdfs" / f"{row['source']}_{row['external_id'].replace('/', '_')}.pdf"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(http.get(row["doc_url"]).content)
        pages = pdf.page_texts(path)
        (DATA / "text").mkdir(parents=True, exist_ok=True)
        (DATA / "text" / (path.stem + ".txt")).write_text("\n\f\n".join(pages))
        facts = parse_static.parse(pages)
        if facts.get("doc_format", {}).get("value") == "Institute RFQ":
            facts.pop("item", None)  # the listing title is more reliable than a guessed subject line
        if extract_ai.enabled():
            new, summary, model = extract_ai.fill(pages, facts, DATA / "ai" / (path.stem + ".json"))
            facts.update(new)
            if summary:
                facts["summary"] = {"value": summary, "page": 0, "quote": "", "by": model}

    # The listing title also carries signals ("SITC of feeder panels", "canteen services").
    for k, v in parse_static._common([row["title"]]).items():
        if v and k not in facts:
            facts[k] = dict(v, page=0, quote=f"listing title: {row['title'][:80]}")

    by_entity = {e["id"]: rules.rate(facts, row, e, today, closing_days) for e in entities}
    best = rules.best(list(by_entity.values()))
    end = rules.end_date(facts, row["listing_end_date"])
    evidence = "\n".join(f"{k}{' (AI)' if v.get('by') == 'ai' else ''}: p.{v['page']} {v['quote']}" for k, v in facts.items()
                         if isinstance(v, dict) and v.get("quote") and not k.endswith("_signals"))
    return {
        "row": row, "facts": facts, "by_entity": by_entity, "best": best, "end": end,
        "days_left": (end - today).days if end else None, "evidence": evidence[:3000],
        "f": lambda k, _f=facts: (str(_f[k]["value"]) if k in _f and _f[k]["value"] not in (True, None) else
                                  ("Yes" if k in _f and _f[k]["value"] is True else None)),
    }


if __name__ == "__main__":
    main()

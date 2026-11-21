"""Fresh push of every still-open bid the bot has already carded, filtered to one rating and the region.

  python tools/resend_open.py                      # print the list only
  python tools/resend_open.py --send               # re-send as new cards (after the chat was cleared)
  python tools/resend_open.py --rating AMBER GREEN # which ratings to include (default AMBER)

Each bid is re-rated today from its cached PDF (no new AI calls). A GeM bid is kept only when its
delivery PIN is confirmed in config/geo.yaml's region. The new message ids go into state.json, so
the Watch / Skip buttons keep working. Nothing is marked as seen and no new searches are made.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import run_daily  # noqa: E402
from gembot import cards, state  # noqa: E402
from gembot.report import item, rupees  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--rating", nargs="+", default=["AMBER"])
    args = ap.parse_args()
    want = {r.upper() for r in args.rating}

    run_daily.AI_BUDGET = 0  # cached AI answers only
    entities = yaml.safe_load((ROOT / "config/entities.yaml").read_text())["entities"]
    geo = yaml.safe_load((ROOT / "config/geo.yaml").read_text())
    closing_days = 3
    today = date.today()

    pins = re.compile(r"\b(?:" + "|".join(geo["pin_prefixes"]) + r")\d{3}\b")
    places = [p.lower() for p in geo["places"]]
    picked, numbers, skipped = [], set(), {"closed": 0, "outside region": 0, "no PDF": 0, "other rating": 0}
    for v in state.load()["bids"].values():
        end = date.fromisoformat(v["end"]) if v.get("end") else None
        if end and end < today:
            skipped["closed"] += 1
            continue
        source, _, ext = v["key"].partition(":")
        # state may hold another machine's absolute path (cards sent from the Mac): look up by file name here
        pdf_path = ROOT / "data" / "pdfs" / Path(v.get("pdf") or "").name if v.get("pdf") else None
        if not pdf_path or not pdf_path.exists():
            skipped["no PDF"] += 1
            continue
        txt = ROOT / "data" / "text" / (pdf_path.stem + ".txt")
        if source == "gem" and txt.exists():  # cheap region check first: the full re-read OCRs annexures
            t = txt.read_text(errors="ignore")
            if not (pins.search(t) or any(p in t.lower() for p in places)):
                skipped["outside region"] += 1
                continue
        row = {"source": source, "external_id": ext, "category": "GeM" if source == "gem" else "",
               "title": v.get("title") or "", "doc_url": v.get("doc_url"), "listing_end_date": end,
               "org": v.get("org") or "", "location": ""}
        try:
            b = run_daily.analyse(row, None, entities, today, closing_days)
        except Exception as e:  # noqa: BLE001
            print(f"  could not re-read {ext}: {e}")
            continue
        b["region_confirmed"] = run_daily.in_region(b["facts"], row, geo)
        if not b["region_confirmed"]:
            skipped["outside region"] += 1
            continue
        if b["end"] and b["end"] <= today:  # closing today: run in the evening, too late to act on
            skipped["closed"] += 1
            continue
        if b["best"]["rating"] not in want:
            skipped["other rating"] += 1
            continue
        n = b["f"]("bid_number") or ext
        if n in numbers:  # the institute page sometimes lists a GeM bid again
            continue
        numbers.add(n)
        picked.append(b)

    picked.sort(key=lambda b: (b["days_left"] if b["days_left"] is not None else 999))
    print(f"{len(picked)} open {'/'.join(sorted(want))} bids in the region; skipped {skipped}")
    for i, b in enumerate(picked, 1):
        print(f"{i:>3}. {b['end']} ({b['days_left']}d) {b['f']('bid_number') or b['row']['external_id']} | "
              f"{item(b)[:45]} | qty {b['f']('qty')} | {(b['f']('org') or b['row']['org'])[:35]} | "
              f"EMD {rupees(b['f']('emd')) if b['f']('emd') else 'none'}")
    if not args.send or not picked:
        return

    token, chat = run_daily.need("TELEGRAM_BOT_TOKEN"), run_daily.need("TELEGRAM_CHAT_ID")
    cards.tg(token, "sendMessage", chat_id=chat, parse_mode="HTML",
             text=f"🔁 <b>Fresh list: {len(picked)} open {'/'.join(sorted(want))} bids, Dhanbad region</b>\n"
                  f"Closing soonest first. Tap ⭐ Watch on the ones you can supply.")
    time.sleep(1.1)
    run_daily.send_cards(picked, entities, today, [], closing_days, header=False)
    cards.tg(token, "sendMessage", chat_id=chat, text=f"✅ End of fresh list ({len(picked)} bids).")
    print("sent")


if __name__ == "__main__":
    main()

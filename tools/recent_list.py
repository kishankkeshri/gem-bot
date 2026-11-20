"""One compact Telegram list of GeM bids published in the last N days for the region in config/geo.yaml.

  python tools/recent_list.py --days 3            # print
  python tools/recent_list.py --days 3 --send     # Telegram

Uses the same search + PDF reading as the daily run (PDFs and AI answers are cached), marks nothing as seen.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import run_daily  # noqa: E402
from gembot import cards  # noqa: E402
from gembot.http import PoliteSession  # noqa: E402
from gembot.report import EMOJI, item, rupees  # noqa: E402
from gembot.sources import gem  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()
    today = date.today()
    since = today - timedelta(days=args.days - 1)  # 3 days = today and the two days before
    geo = yaml.safe_load((ROOT / "config/geo.yaml").read_text())
    entities = yaml.safe_load((ROOT / "config/entities.yaml").read_text())["entities"]
    http = PoliteSession(os.environ.get("HTTP_USER_AGENT", "GemBot/0.1"), float(os.environ.get("HTTP_MIN_DELAY_SECONDS", "3")))

    # Same searches as the daily run; note each bid's start date on the way (fetch() itself drops it).
    starts: dict[str, date] = {}
    plain_search = gem.search

    def search_noting_dates(h, keyword, token):
        docs = plain_search(h, keyword, token)
        for d in docs:
            st = gem._date(gem._first(d.get("final_start_date_sort")))
            if st:
                starts[gem._first(d.get("b_bid_number"))] = st.date()
        return docs

    gem.search = search_noting_dates
    rows = [r for r in gem.fetch(http, geo["places"] + geo.get("extra_search", []))
            if starts.get(r["external_id"]) and starts[r["external_id"]] >= since]
    print(f"{len(rows)} bids published since {since}", flush=True)

    keep, outside = [], 0
    for i, row in enumerate(rows, 1):
        print(f"  [{i}/{len(rows)}] {row['external_id']}", flush=True)
        try:
            b = run_daily.analyse(row, http, entities, today, 3)
        except Exception as e:  # noqa: BLE001
            print("   could not read:", e)
            b = None
        if b is None:
            continue
        if b["best"]["rating"] == "SKIP":
            continue
        b["region_confirmed"] = run_daily.in_region(b["facts"], row, geo)
        if not b["region_confirmed"] and b["facts"].get("consignee_pin"):
            outside += 1
            continue
        b["start"] = starts[row["external_id"]]
        keep.append(b)

    keep.sort(key=lambda b: (b["start"], b["row"]["external_id"]), reverse=True)
    head = (f"<b>📋 Dhanbad · GeM bids published {since:%d %b} – {today:%d %b}</b>\n"
            f"{len(keep)} bids · 🟢 {sum(b['best']['rating'] == 'GREEN' for b in keep)} · "
            f"🟠 {sum(b['best']['rating'] == 'AMBER' for b in keep)} · 🔴 {sum(b['best']['rating'] == 'RED' for b in keep)}"
            + (f"\n({outside} others matched a Dhanbad name but deliver outside Dhanbad – left out)" if outside else "")
            + "\n❔ = delivery place not stated in the document")
    lines, day = [], None
    for b in keep:
        if b["start"] != day:
            day = b["start"]
            lines.append(f"\n<b>— Published {day:%a %d %b} —</b>")
        f = b["f"]
        emd = rupees(f("emd")) if f("emd") else ("Nil" if f("emd_required") == "No" else "EMD ?")
        end = b["end"].strftime("%d %b") if b.get("end") else "?"
        org = (f("org") or b["row"]["org"] or "")[:45]
        q = f" ×{f('qty')}" if f("qty") else ""
        mark = "" if b["region_confirmed"] else " ❔"
        link = html.escape(b["row"]["doc_url"])
        lines.append(f"{EMOJI[b['best']['rating']]} <a href=\"{link}\">{html.escape(item(b)[:70])}</a>{q}{mark}\n"
                     f"    {html.escape(org)} · closes {end} · EMD {html.escape(emd)}")
    msgs, cur = [], head
    for line in lines:
        if len(cur) + len(line) > 3800:
            msgs.append(cur)
            cur = ""
        cur += "\n" + line
    msgs.append(cur)
    for m in msgs:
        print(m.replace("\n", "\n"))
    if args.send:
        token_tg, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
        for m in msgs:
            cards.tg(token_tg, "sendMessage", chat_id=chat, parse_mode="HTML", disable_web_page_preview="true", text=m)
            time.sleep(1.1)
        print(f"sent {len(msgs)} message(s)")


if __name__ == "__main__":
    main()

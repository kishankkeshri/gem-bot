"""Sunday buying report + backup.

  * reads the last 7 days of bids and verdicts from Supabase
  * sends one Telegram message: what was bought, who bought, brands, EMD sizes, what blocks you most,
    watched bids closing this week, and source health
  * writes a gzipped JSON backup of Supabase + the Pi's local state to data/backups/ (keeps 8 weeks)

  python run_weekly.py            # print only
  python run_weekly.py --send     # Telegram + backup
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")

from gembot import cards, state, store  # noqa: E402


def _all(table: str, params: dict) -> list[dict]:
    out, step = [], 1000
    while True:
        page = store._req("GET", table, params={**params, "limit": str(step), "offset": str(len(out))})
        out += page
        if len(page) < step:
            return out


def _item_group(s: str) -> str:
    s = re.sub(r"\(.*?\)|[^A-Za-z ]", " ", s or "").lower()
    words = [w for w in s.split() if len(w) > 2 and w not in {"and", "for", "the", "with", "supply", "item", "items"}]
    return " ".join(words[:3]).title() or "Other"


def _blocker_group(s: str) -> str:
    s = re.sub(r"\(p\.\s*\d+\)|Rs [\d,.]+\s*(lakh|crore|L|Cr)?|\d[\d,.]*", "", s)
    return re.sub(r"\s+", " ", s).strip(" -–:;")[:70]


def build(bids: list[dict], verdicts: list[dict], sources: list[dict], entities: dict, today: date) -> str:
    since = today - timedelta(days=7)
    L = [f"<b>📊 Weekly buying report · {since:%d %b} – {today:%d %b}</b>"]
    if not bids:
        L.append("No new bids stored this week.")
    else:
        by_src = Counter(b["source_id"] for b in bids)
        L.append(f"{len(bids)} new bids · " + " · ".join(f"{k} {v}" for k, v in by_src.most_common()))
        latest = {}
        for v in sorted(verdicts, key=lambda v: v["created_at"]):
            latest[(v["bid_id"], v["entity"])] = v
        for eid, e in entities.items():
            r = Counter(v["rating"] for (bid, en), v in latest.items() if en == eid)
            if r:
                L.append(f"{html.escape(e.get('name', eid))}: 🟢 {r['GREEN']} · 🟠 {r['AMBER']} · 🔴 {r['RED']}")

        L.append("\n<b>🛒 Most bought</b>")
        for name, n in Counter(_item_group(b["item"]) for b in bids).most_common(8):
            L.append(f"• {html.escape(name)} – {n}")

        L.append("\n<b>🏛 Busiest buyers</b>")
        for name, n in Counter((b["org"] or "?")[:60] for b in bids).most_common(6):
            L.append(f"• {html.escape(name)} – {n}")

        makes = Counter(b["make"].strip().title() for b in bids if b.get("make"))
        if makes:
            L.append("\n<b>🏭 Brands asked for</b> (a dealer letter for these opens bids)")
            L.append(" · ".join(f"{html.escape(m)} {n}" for m, n in makes.most_common(10)))

        buckets = Counter()
        for b in bids:
            e = b.get("emd")
            buckets["Nil / not stated" if not e else "< Rs 10k" if e < 1e4 else "Rs 10k–50k" if e < 5e4
                    else "Rs 50k–2 L" if e < 2e5 else "> Rs 2 L"] += 1
        L.append("\n<b>💰 EMD sizes</b>")
        L.append(" · ".join(f"{k} {buckets[k]}" for k in ("Nil / not stated", "< Rs 10k", "Rs 10k–50k", "Rs 50k–2 L", "> Rs 2 L") if buckets[k]))

        for eid, e in entities.items():
            blk = Counter(_blocker_group(x) for (bid, en), v in latest.items() if en == eid for x in v["blockers"])
            if blk:
                L.append(f"\n<b>⛔ What blocks {html.escape(e.get('name', eid))} most</b> (fix the top one first)")
                L += [f"• {html.escape(k)} – {n} bids" for k, n in blk.most_common(4)]

        repeat = Counter((b.get("buyer") or "").split(" · ")[0] for b in bids if b.get("buyer"))
        rep = [(k, n) for k, n in repeat.most_common(5) if n >= 3]
        if rep:
            L.append("\n<b>🔁 Repeat buyer accounts</b> (worth a visit)")
            L += [f"• {html.escape(k[:60])} – {n} bids" for k, n in rep]

    watch = [v for v in state.load()["bids"].values() if v.get("status") == "watch" and v.get("end")]
    soon = sorted((v for v in watch if today <= date.fromisoformat(v["end"][:10]) <= today + timedelta(days=7)),
                  key=lambda v: v["end"])
    if soon:
        L.append("\n<b>⭐ Watched, closing this week</b>")
        L += [f"• {date.fromisoformat(v['end'][:10]):%a %d %b} – {html.escape((v.get('title') or '')[:60])}" for v in soon]

    bad = [s for s in sources if s.get("last_error")]
    stale = [s for s in sources if s.get("last_run_at") is None and s["id"] in ("gem", "iitism")]
    if bad or stale:
        L.append("\n<b>⚠️ Sources</b>")
        L += [f"• {s['id']}: {html.escape(s['last_error'][:100])}" for s in bad]
        L += [f"• {s['id']}: never ran" for s in stale]
    return "\n".join(L)


def backup(today: date) -> Path:
    dump = {"made": datetime.now(timezone.utc).isoformat(),
            "supabase": {t: _all(t, {"select": "*", "order": "id" if t != "notifications" else "bid_id"})
                         for t in ("sources", "bids", "analyses", "notifications")} if store.enabled() else {},
            "state": state.load(),
            "seen": json.loads((DATA / "seen.json").read_text()) if (DATA / "seen.json").exists() else []}
    d = DATA / "backups"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"gembot_{today:%Y-%m-%d}.json.gz"
    with gzip.open(p, "wt") as fh:
        json.dump(dump, fh, default=str)
    for old in sorted(d.glob("gembot_*.json.gz"))[:-8]:
        old.unlink()
    return p


def main() -> None:
    import os

    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()
    today = date.today()
    entities = {e["id"]: e for e in yaml.safe_load((ROOT / "config/entities.yaml").read_text())["entities"]}
    since = (today - timedelta(days=7)).isoformat()
    bids = _all("bids", {"select": "id,source_id,item,org,make,emd,buyer,first_seen",
                         "first_seen": f"gte.{since}", "facts->backfilled": "is.null", "order": "id"})
    ids = {b["id"] for b in bids}
    verdicts = [v for v in _all("analyses", {"select": "bid_id,entity,rating,blockers,created_at",
                                             "created_at": f"gte.{since}", "order": "id"}) if v["bid_id"] in ids]
    sources = store._req("GET", "sources", params={"select": "id,last_run_at,last_error"})
    text = build(bids, verdicts, sources, entities, today)
    print(re.sub(r"<[^>]+>", "", text))
    if args.send:
        p = backup(today)
        print(f"backup: {p} ({p.stat().st_size // 1024} KB)")
        token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
        parts, cur = [], ""
        for line in text.split("\n"):
            if len(cur) + len(line) > 3900:
                parts.append(cur)
                cur = ""
            cur += line + "\n"
        for part in parts + [cur]:
            cards.tg(token, "sendMessage", chat_id=chat, parse_mode="HTML", disable_web_page_preview="true", text=part)


if __name__ == "__main__":
    main()

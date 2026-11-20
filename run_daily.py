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

from gembot import cards, extract_ai, parse_static, pdf, report, rules, state, store  # noqa: E402
from gembot.http import PoliteSession
from gembot.sources import gem, iitism  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCES = {"iitism": iitism, "gem": gem}
# New Gemini calls per run (cached documents are free). GeM PDFs parse well statically,
# so the budget mostly goes to free-form institute documents.
AI_BUDGET = int(os.environ.get("AI_MAX_CALLS", "40"))
REFRESH = False


def in_region(facts: dict, row: dict, geo: dict) -> bool:
    """GeM keyword hits are confirmed against the bid PDF's consignee PIN / address."""
    if row["source"] != "gem":
        return True
    pin = str(facts.get("consignee_pin", {}).get("value", ""))
    addr = str(facts.get("consignee", {}).get("value", "")).lower()
    if pin and any(pin.startswith(p) for p in geo["pin_prefixes"]):
        return True
    return any(pl.lower() in addr for pl in geo["places"])


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"[config] {name} is not set in .env - stopping")
    return v


class _Tee:
    """Everything printed also goes to data/last_run.log, so a run can be reviewed afterwards."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()

    def flush(self):
        for st in self.streams:
            st.flush()


def _single_instance():
    """Refuse to start while another run is going - two runs would send every card twice."""
    import fcntl
    lock = open(DATA / "run.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Another run_daily.py is already running - let it finish (see data/last_run.log).")
    return lock  # keep the handle open for the life of the process


def main() -> None:
    DATA.mkdir(exist_ok=True)
    _lock = _single_instance()  # noqa: F841
    log = open(DATA / "last_run.log", "w")
    sys.stdout = _Tee(sys.__stdout__, log)
    sys.stderr = _Tee(sys.__stderr__, log)
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--source", choices=list(SOURCES), action="append",
                    help="run only these sources (default: all)")
    ap.add_argument("--probe", action="store_true",
                    help="GeM: fetch one page for 'Dhanbad', save raw JSON to data/gem_probe.json, exit")
    ap.add_argument("--daily", action="store_true",
                    help="scheduled mode: do nothing if today's run already finished cleanly "
                         "(the Pi fires this at boot, 07:30 and 13:30, so a Pi that was off or a failed run catches up)")
    ap.add_argument("--refresh", action="store_true",
                    help="with --send: edit cards already in the chat instead of sending new ones")
    args = ap.parse_args()
    global REFRESH
    REFRESH = args.refresh

    load_dotenv(ROOT / ".env")
    closing_days = int(os.environ.get("CLOSING_SOON_DAYS", "3"))
    http = PoliteSession(os.environ.get("HTTP_USER_AGENT", "GemBot/0.1"),
                         float(os.environ.get("HTTP_MIN_DELAY_SECONDS", "3")))
    entities = yaml.safe_load((ROOT / "config/entities.yaml").read_text())["entities"]
    geo = yaml.safe_load((ROOT / "config/geo.yaml").read_text())
    today = date.today()

    if args.probe:
        gem.MAX_PAGES_PER_PLACE = 1
        token = gem._token(http)
        docs = gem.search(http, "Dhanbad", token)
        DATA.mkdir(exist_ok=True)
        (DATA / "gem_probe.json").write_text(json.dumps(docs, indent=1, default=str))
        print(f"GeM probe OK: {len(docs)} bids on page 1 for 'Dhanbad' -> data/gem_probe.json")
        for d in docs[:10]:
            print(" ", d.get("b_bid_number"), "|", str(d.get("b_category_name"))[:60])
        return

    done_path = DATA / "last_complete_run.txt"
    if args.daily and done_path.exists() and done_path.read_text().strip() == today.isoformat():
        print(f"today's run already completed ({today}) - nothing to do")
        return

    seen_path = DATA / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    if not args.all:  # cards already sent by a run that was cut off (power loss) must not repeat
        seen |= {v["key"] for v in state.load()["bids"].values() if v.get("key")}
    failures: list[str] = []
    bids: list[dict] = []

    out_of_region = 0
    stream = args.send and not REFRESH  # send cards in batches while reading, not after hours of PDFs
    sent_numbers: set = set()
    pending: list[dict] = []
    started = False

    def flush() -> None:
        nonlocal pending
        batch = []
        for b in pending:
            n = b["f"]("bid_number")
            if b["best"]["rating"] == "SKIP" or (n and n in sent_numbers):
                continue
            sent_numbers.add(n)
            batch.append(b)
        batch.sort(key=lambda b: (rules.ORDER[b["best"]["rating"]], b["days_left"] if b["days_left"] is not None else 999))
        if batch:
            send_cards(batch, entities, today, failures, closing_days, header=False)
        pending = []

    rows_by_source: list = []
    for name in (args.source or list(SOURCES)):
        src = SOURCES[name]
        try:
            rows = (src.fetch(http, geo["places"] + geo.get("extra_search", []), failures) if name == "gem"
                    else src.fetch(http))
            print(f"[{name}] {len(rows)} listings")
            _store(store.source_status, name, None)
        except Exception as e:  # noqa: BLE001 - report, never skip silently
            traceback.print_exc()
            failures.append(f"{src.NAME}: listing unreachable today ({e.__class__.__name__}: {e})")
            _store(store.source_status, name, str(e)[:500])
            continue
        rows_by_source.append(rows)

    todo = [r for rows in rows_by_source for r in rows
            if not (r["listing_end_date"] and r["listing_end_date"] < today)
            and (args.all or f"{r['source']}:{r['external_id']}" not in seen)]
    if stream:
        cards.tg(need("TELEGRAM_BOT_TOKEN"), "sendMessage", chat_id=need("TELEGRAM_CHAT_ID"), parse_mode="HTML",
                 text=cards.start_message(len(todo), today, failures))
    for rows in [todo]:
        for row in rows:
            key = f"{row['source']}:{row['external_id']}"
            if args.limit and len(bids) >= args.limit:
                break
            print(f"  [{len(bids) + 1}/{len(todo)}] {row['external_id']} {row['title'][:50]}", flush=True)
            try:
                b = analyse(row, http, entities, today, closing_days)
                # Never drop: a missed bid costs more than an extra card. Just label it.
                b["region_confirmed"] = in_region(b["facts"], row, geo)
                if not b["region_confirmed"]:
                    out_of_region += 1
                bids.append(b)
                pending.append(b)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                failures.append(f"{row['external_id']}: could not read document ({e.__class__.__name__})")
            seen.add(key)
            if stream and len(pending) >= 20:
                flush()
                seen_path.write_text(json.dumps(sorted(seen), indent=0))
    if stream:
        flush()

    if out_of_region:
        print(f"[gem] {out_of_region} bids matched a district name but the delivery PIN isn't confirmed in the region (kept, labelled)")
    bids = [b for b in bids if b["best"]["rating"] != "SKIP"]
    seen_numbers, unique = set(), []
    for b in bids:  # IIT sometimes lists one GeM bid under two tender numbers
        n = b["f"]("bid_number")
        if n and n in seen_numbers:
            continue
        seen_numbers.add(n)
        unique.append(b)
    bids = unique
    bids.sort(key=lambda b: (rules.ORDER[b["best"]["rating"]], b["days_left"] if b["days_left"] is not None else 999))

    xlsx = DATA / "reports" / f"bids_{today:%Y-%m-%d}.xlsx"
    if bids or not xlsx.exists():  # a run with nothing new must not wipe today's report
        report.write_excel(bids, xlsx, failures)
    text = report.telegram_summary(bids, today, failures, closing_days)
    print(text, f"\n\nExcel: {xlsx}", sep="")

    if args.send and REFRESH:
        send_cards(bids, entities, today, failures, closing_days)
    if stream and bids:  # (nothing new was already said in the start message)
        cards.tg(need("TELEGRAM_BOT_TOKEN"), "sendMessage", chat_id=need("TELEGRAM_CHAT_ID"), parse_mode="HTML",
                 text=cards.header(bids, today, failures, closing_days, done=True))
        print("sent to Telegram")
    if args.send:  # a dry run must not mark bids as seen, or the real run would skip them
        seen_path.write_text(json.dumps(sorted(seen), indent=0))
        print(_store(store.sync_run, bids, state.load()) or ("supabase: sync failed (see above)" if store.enabled() else "supabase: off"))
        if not failures and not args.source:  # only a full, clean run counts; otherwise the next trigger retries
            done_path.write_text(today.isoformat())


def _store(fn, *a):
    """Supabase is the backup copy: a failure there is logged, never fatal."""
    if not store.enabled():
        return None
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        print(f"[supabase] {fn.__name__} failed: {e}", flush=True)
        return None


def send_cards(bids: list[dict], entities: list[dict], today: date, failures: list[str], closing_days: int,
               header: bool = True) -> None:
    import time as _t
    token, chat = need("TELEGRAM_BOT_TOKEN"), need("TELEGRAM_CHAT_ID")
    by_id = {e["id"]: e for e in entities}
    max_cards = int(os.environ.get("MAX_CARDS", "1000"))  # default: every bid gets a card
    st = state.load()
    failed: list[str] = []
    if header and not REFRESH:
        cards.tg(token, "sendMessage", chat_id=chat, parse_mode="HTML",
                 text=cards.header(bids, today, failures, closing_days))
    # Every bid gets a card (RED last, already sorted). RED_AS_LIST=1 folds REDs into one summary message.
    red_as_list = os.environ.get("RED_AS_LIST", "0") == "1"
    worth = [b for b in bids if not (red_as_list and b["best"]["rating"] == "RED")]
    for b in worth[:max_cards]:
        key = f"{b['row']['source']}:{b['row']['external_id']}"
        sid = state.short_id(key)
        prev = st["bids"].get(sid, {})
        body = dict(chat_id=chat, parse_mode="HTML", disable_web_page_preview="true",
                    text=cards.card(b, by_id, today), reply_markup=cards.keyboard(sid, prev.get("status", "new")))
        try:
            if REFRESH and prev.get("message_id"):  # update the card already in the chat instead of a duplicate
                try:
                    cards.tg(token, "editMessageText", message_id=prev["message_id"], **body)
                except RuntimeError as e:  # "message is not modified" is fine
                    print("  edit skipped:", str(e)[:100])
                msg = {"message_id": prev["message_id"]}
            else:
                msg = cards.tg(token, "sendMessage", **body)
        except Exception as e:  # noqa: BLE001 - one bad card must not stop the rest
            print(f"  card failed for {key}: {e}", flush=True)
            failed.append(key)
            continue
        st["bids"][sid] = {"key": key, "title": report.item(b), "org": b["f"]("org") or b["row"]["org"],
                           "end": b["end"].isoformat() if b["end"] else None, "doc_url": b["row"]["doc_url"],
                           "pdf": str(b["pdf"]) if b.get("pdf") else None, "rating": b["best"]["rating"],
                           "status": prev.get("status", "new"), "message_id": msg["message_id"]}
        state.save(st)  # after every card, so buttons keep working even if the run dies later
        _t.sleep(1.1)  # Telegram allows ~1 message/second per chat
    if len(worth) > max_cards:
        cards.tg(token, "sendMessage", chat_id=chat, text=f"… {len(worth) - max_cards} more bids not shown (MAX_CARDS={max_cards}).")
    reds = [b for b in bids if b["best"]["rating"] == "RED"]
    if reds and red_as_list and not REFRESH:
        for text in cards.red_list(reds):
            cards.tg(token, "sendMessage", chat_id=chat, parse_mode="HTML", disable_web_page_preview="true", text=text)
            _t.sleep(1.1)
    if failed:
        cards.tg(token, "sendMessage", chat_id=chat,
                 text=f"⚠️ {len(failed)} card(s) could not be sent - see data/last_run.log")
    state.save(st)


def analyse(row: dict, http: PoliteSession, entities: list[dict], today: date, closing_days: int) -> dict:
    facts: dict = {}
    path = None
    if row["doc_url"] and (row["source"] == "gem" or row["doc_url"].lower().split("?")[0].endswith(".pdf")):
        path = DATA / "pdfs" / f"{row['source']}_{row['external_id'].replace('/', '_')}.pdf"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(http.get(row["doc_url"]).content)
        pages = pdf.page_texts(path)
        (DATA / "text").mkdir(parents=True, exist_ok=True)
        (DATA / "text" / (path.stem + ".txt")).write_text("\n\f\n".join(pages))
        facts = parse_static.parse(pages, pdf.alt_page_texts(path))
        if facts.get("doc_format", {}).get("value") == "Institute RFQ":
            facts.pop("item", None)  # the listing title is more reliable than a guessed subject line
        for k, v in parse_static.make_model_from_title(row["title"]).items():
            facts.setdefault(k, v)
        cache = DATA / "ai" / (path.stem + ".v3.json")  # v3: OCR'd annexures, stricter EMD-waiver question
        global AI_BUDGET
        if extract_ai.enabled() and (cache.exists() or AI_BUDGET > 0):
            AI_BUDGET -= 0 if cache.exists() else 1
            new, summary, model = extract_ai.fill(pages, facts, cache)
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
        "row": row, "facts": facts, "by_entity": by_entity, "pdf": path, "best": best, "end": end,
        "days_left": (end - today).days if end else None, "evidence": evidence[:3000],
        "f": lambda k, _f=facts: (str(_f[k]["value"]) if k in _f and _f[k]["value"] not in (True, None) else
                                  ("Yes" if k in _f and _f[k]["value"] is True else None)),
    }


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - tell the owner instead of failing silently on the Pi
        traceback.print_exc()
        try:
            load_dotenv(ROOT / ".env")
            cards.tg(os.environ["TELEGRAM_BOT_TOKEN"], "sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                     text=f"❌ Daily bid run failed: {exc.__class__.__name__}: {str(exc)[:300]}\n"
                          "Logs: journalctl -u gembot-daily -n 100")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)

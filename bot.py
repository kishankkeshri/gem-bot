"""Button + command listener (long polling; run it as a service on the Pi).

    .venv/bin/python bot.py

Buttons on each bid card:  ⭐ Watch · ⏭ Skip · 📄 PDF
Commands:  /watching  - list bids you starred      /help
Only answers the chat id in TELEGRAM_CHAT_ID; everyone else is ignored.
"""
from __future__ import annotations

import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from dotenv import load_dotenv

from gembot import state, store
from gembot.cards import keyboard, tg

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or sys.exit("TELEGRAM_BOT_TOKEN not set")
CHAT = str(os.environ.get("TELEGRAM_CHAT_ID") or sys.exit("TELEGRAM_CHAT_ID not set"))
UA = os.environ.get("HTTP_USER_AGENT", "GemBot/0.1")


PDF_WORKERS = ThreadPoolExecutor(max_workers=2)  # uploads run here so taps never queue behind them
ACK_WORKERS = ThreadPoolExecutor(max_workers=4)
PDF_REPEAT_SECONDS = 300  # a second PDF tap on the same bid within this window is ignored
_pdf_last: dict[str, float] = {}


def _ack(q: dict, text: str) -> None:
    """The little toast on a tap, sent in the background with a short timeout: it must never hold up
    the next tap, and Telegram expires it after ~15 s anyway."""
    def go():
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery",
                          data={"callback_query_id": q["id"], "text": text}, timeout=8)
        except Exception as e:  # noqa: BLE001 - never put the token-bearing URL in the log
            print(f"  (toast skipped: {e.__class__.__name__})", flush=True)
    ACK_WORKERS.submit(go)


def on_button(q: dict) -> None:
    action, _, sid = q.get("data", "").partition(":")
    st = state.load()
    bid = st["bids"].get(sid)
    print(f"tap {action}:{sid} {(bid or {}).get('key', '?')} {(bid or {}).get('title', '')[:50]}", flush=True)
    if not bid:
        _ack(q, "Bid not found (older than the store)")
        return
    msg = q.get("message", {})
    if action in ("w", "s"):
        new = {"w": "watch", "s": "skip"}[action]
        bid["status"] = "new" if bid.get("status") == new else new  # tap again to undo
        state.save(st)
        try:
            store.set_status(bid.get("key", ""), bid["status"])
        except Exception as e:  # noqa: BLE001 - the chat still works if Supabase is down
            print("supabase status sync failed:", e, flush=True)
        _ack(q, {"watch": "Added to watch list ⭐", "skip": "Skipped", "new": "Undone"}[bid["status"]])
        tg(TOKEN, "editMessageReplyMarkup", chat_id=CHAT, message_id=msg.get("message_id"),
           reply_markup=keyboard(sid, bid["status"]))
    elif action == "p":
        now = time.time()
        if now - _pdf_last.get(sid, 0) < PDF_REPEAT_SECONDS:
            _ack(q, "Already sent – scroll down for the PDF")
            print("  repeat tap ignored", flush=True)
            return
        _pdf_last[sid] = now
        _ack(q, "Sending the document…")
        PDF_WORKERS.submit(send_pdf, sid, dict(bid), msg.get("message_id"))


def send_pdf(sid: str, bid: dict, reply_to) -> None:
    """Every PDF tap ends in the PDF, or in a message with the bid number and link - never in silence."""
    label = bid["key"].split(":", 1)[-1]
    try:
        pdfs = ROOT / "data" / "pdfs"
        candidates = [Path(bid["pdf"]) if bid.get("pdf") else None,  # the path may be another machine's
                      pdfs / Path(bid["pdf"]).name if bid.get("pdf") else None, pdfs / f"{sid}.pdf"]
        pdf = next((p for p in candidates if p and p.exists()), None)
        if pdf is None and bid.get("doc_url"):
            pdf = pdfs / f"{sid}.pdf"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(bid["doc_url"], headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            pdf.write_bytes(r.content)
        if pdf is None:
            raise FileNotFoundError("no document link")
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in label) + ".pdf"
        data = pdf.read_bytes()
        last = None
        for attempt in range(3):
            try:
                tg(TOKEN, "sendDocument", chat_id=CHAT, reply_to_message_id=reply_to, allow_sending_without_reply="true",
                   caption=bid["title"][:200], files={"document": (name, data, "application/pdf")})
                print(f"  pdf sent {label}", flush=True)
                return
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3 * (attempt + 1))
        raise last
    except Exception as e:  # noqa: BLE001
        print(f"  pdf FAILED {label}: {e}", flush=True)
        try:
            tg(TOKEN, "sendMessage", chat_id=CHAT, reply_to_message_id=reply_to, allow_sending_without_reply="true",
               text=f"⚠️ Could not attach the PDF for {label} ({bid['title'][:60]}). Open it here: {bid.get('doc_url') or 'portal'}")
        except Exception as e2:  # noqa: BLE001
            print(f"  could not even send the fallback for {label}: {e2}", flush=True)


def on_message(m: dict) -> None:
    text = (m.get("text") or "").strip().lower()
    if text.startswith("/watching"):
        bids = [b for b in state.load()["bids"].values() if b.get("status") == "watch"]
        bids.sort(key=lambda b: b.get("end") or "9999")
        lines = [f"<b>⭐ Watching ({len(bids)})</b>"] + [
            f"• {html.escape(b['title'][:70])} – ends {b.get('end') or '?'}" for b in bids] if bids else ["Nothing on the watch list yet."]
        tg(TOKEN, "sendMessage", chat_id=CHAT, text="\n".join(lines), parse_mode="HTML")
    elif text.startswith(("/help", "/start")):
        tg(TOKEN, "sendMessage", chat_id=CHAT,
           text="Daily bid cards arrive each morning.\nButtons: ⭐ Watch · ⏭ Skip · 📄 PDF\n/watching – your watch list")


def main() -> None:
    offset = None
    print("listening for buttons…", flush=True)
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": '["message","callback_query"]'}
            if offset:
                params["offset"] = offset
            r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", params=params, timeout=65)
            r.raise_for_status()
            for u in r.json()["result"]:
                offset = u["update_id"] + 1
                q, m = u.get("callback_query"), u.get("message")
                chat = str((q or {}).get("message", {}).get("chat", {}).get("id") or (m or {}).get("chat", {}).get("id"))
                if chat != CHAT:
                    continue  # not you
                try:
                    on_button(q) if q else on_message(m)
                except Exception as e:  # noqa: BLE001 - one bad update must not stop the listener
                    print("update failed:", e, flush=True)
        except requests.RequestException as e:
            print("poll error:", e, flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()

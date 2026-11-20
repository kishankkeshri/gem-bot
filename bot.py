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


def on_button(q: dict) -> None:
    action, _, sid = q.get("data", "").partition(":")
    st = state.load()
    bid = st["bids"].get(sid)
    if not bid:
        tg(TOKEN, "answerCallbackQuery", callback_query_id=q["id"], text="Bid not found (older than the store)")
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
        tg(TOKEN, "answerCallbackQuery", callback_query_id=q["id"],
           text={"watch": "Added to watch list ⭐", "skip": "Skipped", "new": "Undone"}[bid["status"]])
        tg(TOKEN, "editMessageReplyMarkup", chat_id=CHAT, message_id=msg.get("message_id"),
           reply_markup=keyboard(sid, bid["status"]))
    elif action == "p":
        tg(TOKEN, "answerCallbackQuery", callback_query_id=q["id"], text="Sending the document…")
        pdf = Path(bid["pdf"]) if bid.get("pdf") else None
        if not (pdf and pdf.exists()) and bid.get("doc_url"):
            pdf = ROOT / "data" / "pdfs" / f"{sid}.pdf"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(requests.get(bid["doc_url"], headers={"User-Agent": UA}, timeout=60).content)
        if pdf and pdf.exists():
            name = "".join(c if c.isalnum() or c in "-_" else "_" for c in bid["key"].split(":", 1)[-1]) + ".pdf"
            with open(pdf, "rb") as fh:
                tg(TOKEN, "sendDocument", chat_id=CHAT, reply_to_message_id=msg.get("message_id"),
                   caption=bid["title"][:200], files={"document": (name, fh, "application/pdf")})
        else:
            tg(TOKEN, "sendMessage", chat_id=CHAT, text="No PDF for this bid - open the portal link.")


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

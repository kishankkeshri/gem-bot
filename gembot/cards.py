"""Phone-first Telegram cards: one message per bid, the facts a small bidder decides on, three buttons."""
from __future__ import annotations

import html
import re
import time
from datetime import date

import requests

from gembot.parse_static import inr
from gembot.report import EMOJI, item, rupees

API = "https://api.telegram.org/bot{token}/{method}"


def tg(token: str, method: str, **data) -> dict:
    files = data.pop("files", None)
    for attempt in range(3):
        r = requests.post(API.format(token=token, method=method), data=data, files=files, timeout=60)
        if r.status_code == 429:  # Telegram flood control: wait what it asks
            time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
            continue
        if not r.ok:  # report Telegram's own reason; never put the token-bearing URL in logs
            raise RuntimeError(f"Telegram {method} {r.status_code}: {r.json().get('description', r.text[:200])}")
        return r.json()["result"]
    raise RuntimeError(f"Telegram {method} kept rate-limiting")


def keyboard(sid: str, status: str = "new") -> str:
    import json
    watch = "⭐ Watching" if status == "watch" else "⭐ Watch"
    skip = "⏭ Skipped" if status == "skip" else "⏭ Skip"
    return json.dumps({"inline_keyboard": [[
        {"text": watch, "callback_data": f"w:{sid}"},
        {"text": skip, "callback_data": f"s:{sid}"},
        {"text": "📄 PDF", "callback_data": f"p:{sid}"},
    ]]})


def _d(s) -> str:
    """'26-11-2026 11:00:00' -> '26 Nov 11:00'"""
    if not s:
        return "?"
    from datetime import datetime
    s = str(s)
    for fmt, out in (("%d-%m-%Y %H:%M:%S", "%d %b %H:%M"), ("%d-%b-%Y %I:%M %p", "%d %b %H:%M"),
                     ("%d-%m-%Y", "%d %b"), ("%d.%m.%Y", "%d %b"), ("%d/%m/%Y", "%d %b"), ("%Y-%m-%d", "%d %b")):
        try:
            return datetime.strptime(s, fmt).strftime(out).replace(" 00:00", "")
        except ValueError:
            pass
    return s


def size_line(b: dict) -> str:
    est = b["f"]("est_value")
    if est:
        return f"Est. value {rupees(est)}"
    e = inr(b["f"]("emd")) if b["f"]("emd") else None
    if e:  # EMD is usually 2-5% of the bid value
        return f"≈ bid size {rupees(e / 0.05)} – {rupees(e / 0.02)}"
    return "size not stated"


def emd_exemption(b: dict, entities: dict) -> str:
    a = b["best"]
    ent = entities.get(a["entity"], {})
    if not b["f"]("emd") and (b["f"]("emd_required") == "No" or b["f"]("emd_exempt")):
        return "✅ no EMD in this bid"
    if not b["f"]("emd"):
        return "❔ EMD not stated"
    if b["f"]("emd_exemption_allowed") == "No":
        return "❌ no exemption in this tender – everyone pays"
    makers = b["facts"].get("mse_emd_makers_only")
    if makers and ent.get("role_for_goods") != "manufacturer" and a["kind"] != "services":
        return f"❌ only MSE makers / service providers (p.{makers['page']}) – resellers pay"
    if a["kind"] == "services" and ent.get("mse"):
        return "✅ MSE exemption likely (service bid) – upload Udyam"
    if ent.get("role_for_goods") == "manufacturer" and ent.get("mse"):
        return "✅ MSE exemption likely (manufacturer)"
    note = " (doc mentions exemption – check if traders allowed)" if b["f"]("emd_mse_exempt_text") else ""
    return f"❌ not for resellers on goods – you pay{note}"


def _short_specs(s: str, limit: int = 220) -> str:
    s = re.sub(r"\s*/\s*", " / ", str(s)).replace("STANDARD ", "").strip(" /")
    s = re.sub(r"(\d+(?:, \d+){4,})", lambda m: ", ".join(m.group(1).split(", ")[:4]) + "…", s)  # long value lists
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def contacts(b: dict, today: date) -> list[str]:
    """Who is behind the bid, as printed in the document. Tech-evaluation committee names are never published."""
    f, out = b["f"], []
    out.append(f"👤 Indenter (consignee): <b>{html.escape(f('consignee_officer'))}</b>" if f("consignee_officer")
               else "👤 Indenter: not named in this document")
    buyer = [html.escape(x) for x in (b["row"].get("buyer_account"), f("buyer_email") or f("purchase_office")) if x]
    if buyer:
        out.append("🏢 Buyer " + " · ".join(buyer))
    if f("competent_authority"):
        d = f" ({html.escape(f('competent_designation'))})" if f("competent_designation") else ""
        out.append(f"✍️ Approving authority: <b>{html.escape(f('competent_authority'))}</b>{d}")
    if f("prebid_date"):
        from datetime import datetime
        try:
            when = datetime.strptime(f("prebid_date"), "%d-%m-%Y %H:%M")
            past = " (over)" if when.date() < today else ""
        except ValueError:
            past = ""
        venue = f" · {html.escape(f('prebid_venue')[:70])}" if f("prebid_venue") else ""
        out.append(f"🤝 Pre-bid {_d(f('prebid_date') + ':00')}{past}{venue}")
    return out


def yn(v) -> str:
    return {"Yes": "✅ Yes", "No": "❌ No"}.get(v or "", "❔ not stated")


def card(b: dict, entities: dict, today: date) -> str:
    a, f = b["best"], b["f"]
    days = b["days_left"]
    when = "closes TODAY" if days == 0 else (f"closes in {days} day{'s' if days != 1 else ''}" if days is not None else "end date not stated")
    if days is not None and days <= 3:
        when = f"⏰ <b>{when}</b>"
    ent_name = entities.get(a["entity"], {}).get("name", a["entity"])
    org = f("org") or b["row"]["org"]
    place = " ".join(x for x in [f("consignee_pin"), b["row"].get("location")] if x)
    qty = f" · Qty {f('qty')}" if f("qty") else ""

    L = [f"{EMOJI[a['rating']]} <b>{a['rating']}</b> · {html.escape(ent_name)} · {when}",
         f"<b>{html.escape(item(b)[:140])}</b>{qty}"]
    tt = f("tender_type")
    if tt in ("Single", "Limited") or f("single_tender"):
        L.append(f"🎯 <b>{'Limited' if tt == 'Limited' else 'Single'}-tender enquiry</b> – "
                 + ("invited vendors only" if tt == "Limited" else "aimed at one OEM / its dealer"))
    if f("summary"):
        L.append(f"<i>{html.escape(f('summary')[:260])}</i>")
    L += [
        f"🏛 {html.escape(org[:90])}" + (f" · 📍{html.escape(place)}" if place else ""),
        *([] if b.get("region_confirmed", True) else
          [f"❔ Delivery address not confirmed in your region (matched “{html.escape(str(b['row'].get('matched_keyword')))}” on GeM)"
           + (f" · consignee: {html.escape(str(f('consignee'))[:60])}" if f("consignee") else "")]),
        f"📅 Start {_d(f('start_date'))} · End <b>{_d(f('end_date') or b['end'])}</b> · Opens {_d(f('opening_date'))}",
        f"💰 EMD <b>{html.escape(rupees(f('emd')) if f('emd') else ('Nil' if f('emd_required') == 'No' else 'not stated'))}</b>"
        + (f" total for {f('emd_schedules')} schedules" if f("emd_schedules") else "") + f" · {size_line(b)}",
        f"🏷 MSE EMD exemption: {emd_exemption(b, entities)}",
        f"🇮🇳 Local (MII) pref: {yn(f('mii_preference')) if f('mii_preference') else ('ℹ️ mentioned' if f('local_pref_signals') else yn(None))} · MSE pref: {yn(f('mse_preference'))}",
        f"📋 Exp/turnover relax – MSE: {yn(f('mse_relax'))} · Startup: {yn(f('startup_relax'))}",
    ]
    make, model = f("make"), f("model")
    if make or model:
        pac = " · <b>only this brand (PAC)</b>" if f("pac") else ""
        parts = ([f"Brand <b>{html.escape(make)}</b>"] if make else []) + ([f"Model <b>{html.escape(model)}</b>"] if model else [])
        L.append("🏭 " + " · ".join(parts) + pac)
    specs = f("key_specs") or f("spec_text")
    if specs:
        L.append(f"🔧 {html.escape(_short_specs(specs))}")
    elif f("spec_in_buyer_doc"):
        L.append("🔧 Specs in a <b>separate buyer document</b> – open on GeM")
    terms = []
    if f("warranty"):
        terms.append(f"warranty {f('warranty')}")
    if f("installation"):
        terms.append("installation in scope")
    if f("inspection") == "Yes":
        terms.append("3rd-party inspection")
    elif f("inspection_by_buyer"):
        terms.append("buyer inspects on delivery")
    if f("training"):
        terms.append("training to staff")
    if f("tender_fee") and inr(f("tender_fee")):
        terms.append(f"tender fee {rupees(f('tender_fee'))}")
    if f("ra_enabled") == "Yes":
        terms.append("reverse auction")
    if f("payment_days"):
        terms.append(f"payment ~{f('payment_days')} days")
    if terms:
        L.append("🛡 " + " · ".join(terms))
    L += contacts(b, today)
    extra = []
    if f("integrity_pact"):
        extra.append("Integrity Pact")
    if f("oem_auth_required") and not make:
        extra.append("OEM letter needed")
    if f("turnover_min"):
        extra.append(f"turnover ≥ {rupees(f('turnover_min'))}")
    if f("delivery_days"):
        extra.append(f"delivery {f('delivery_days')} days")
    if f("epbg_pct"):
        extra.append(f"PBG {f('epbg_pct')}%")
    if extra:
        L.append("📌 " + " · ".join(extra))
    if a["blockers"]:
        L.append(f"⛔ {html.escape('; '.join(a['blockers'])[:200])}")
    if a["actions"]:
        L.append(f"👉 {html.escape(a['actions'][0][:160])}")
    no = f("bid_number") or b["row"]["external_id"]
    L.append(f"<code>{html.escape(no)}</code>")
    return "\n".join(L)


def start_message(n: int, today: date, failures: list[str]) -> str:
    L = [f"<b>📬 New bids · {today:%d %b %Y}</b>",
         f"{n} new listing{'s' if n != 1 else ''} to read – cards follow as each document is checked"
         if n else "Nothing new today."]
    L += [f"⚠️ {html.escape(x[:200])}" for x in failures]
    return "\n".join(L)


def header(bids: list[dict], today: date, failures: list[str], closing_days: int, done: bool = False) -> str:
    bids = [b for b in bids if b["best"]["rating"] != "SKIP"]
    c = {k: sum(1 for b in bids if b["best"]["rating"] == k) for k in ("GREEN", "AMBER", "RED")}
    soon = sum(1 for b in bids if b["days_left"] is not None and b["days_left"] <= closing_days and b["best"]["rating"] != "RED")
    L = [f"<b>{'✅ Done' if done else '📬 New bids'} · {today:%d %b %Y}</b>",
         f"{len(bids)} new · 🟢 {c['GREEN']} · 🟠 {c['AMBER']} · 🔴 {c['RED']}" + (f" · ⏰ {soon} closing soon" if soon else "")]
    L += [f"⚠️ {html.escape(x[:200])}" for x in failures]
    if not bids and not failures:
        L.append("Nothing new today.")
    return "\n".join(L)


def red_list(reds: list[dict]) -> list[str]:
    """One or more messages, each under Telegram's 4096-character limit."""
    msgs, cur = [], f"<b>🔴 Not realistic now ({len(reds)})</b>"
    for b in reds:
        why = (b["best"]["blockers"] or ["see document"])[0]
        link = b["row"]["doc_url"]
        name = html.escape(item(b)[:60])
        line = f"\n• {f'<a href={chr(34)}{html.escape(link)}{chr(34)}>{name}</a>' if link else name} – {html.escape(why[:90])}"
        if len(cur) + len(line) > 3900:
            msgs.append(cur)
            cur = "<b>🔴 (continued)</b>"
        cur += line
    msgs.append(cur)
    return msgs

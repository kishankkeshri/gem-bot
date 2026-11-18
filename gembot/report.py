"""Outputs: a sortable Excel table (full detail + evidence) and a short Telegram summary."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import re

from gembot.parse_static import inr

FILL = {"GREEN": "C6EFCE", "AMBER": "FFEB9C", "RED": "FFC7CE"}


def item(b: dict) -> str:
    s = b["f"]("item") or b["row"]["title"]
    s = re.split(r"\s*GeMARPTS", s)[0].strip()
    parts, seen = [], set()
    for part in s.split(";"):  # "as per scope of work; as per scope of work; ..." -> once
        k = part.strip().lower()
        if k and k not in seen:
            seen.add(k)
            parts.append(part.strip())
    return "; ".join(parts)


def rupees(s) -> str:
    n = inr(s)
    if n is None:
        return str(s)
    if n >= 1e7:
        return f"Rs {n / 1e7:.2f} Cr"
    if n >= 1e5:
        return f"Rs {n / 1e5:.2f} L"
    return f"Rs {n:,.0f}"


def emd(b: dict) -> str:
    if b["f"]("emd"):
        return rupees(b["f"]("emd"))
    if b["f"]("emd_required") == "No" or b["f"]("emd_exempt"):
        return "No"
    return "not stated"
EMOJI = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}

COLUMNS = [  # (header, width, getter)
    ("Rating", 9, lambda b: b["best"]["rating"]),
    ("Firm", 6, lambda b: b["best"]["entity"] if b["best"]["rating"] != "RED" else "-"),
    ("Closes", 11, lambda b: b["end"].strftime("%d-%m-%Y") if b["end"] else "not stated"),
    ("Days left", 8, lambda b: b["days_left"] if b["days_left"] is not None else ""),
    ("Kind", 9, lambda b: b["best"]["kind"]),
    ("Tender no.", 20, lambda b: b["row"]["external_id"]),
    ("GeM bid no.", 22, lambda b: b["f"]("bid_number")),
    ("Organisation", 22, lambda b: b["f"]("org") or b["row"]["org"]),
    ("Item", 45, item),
    ("What is needed (AI summary)", 50, lambda b: b["f"]("summary")),
    ("Qty", 8, lambda b: b["f"]("qty")),
    ("Est. value", 12, lambda b: rupees(b["f"]("est_value")) if b["f"]("est_value") else "not stated"),
    ("EMD", 12, emd),
    ("ePBG / PS %", 9, lambda b: b["f"]("epbg_pct") or ("No" if b["f"]("epbg_required") == "No" else "not stated")),
    ("MSE relax", 8, lambda b: b["f"]("mse_relax") or "not stated"),
    ("Startup relax", 8, lambda b: b["f"]("startup_relax") or "not stated"),
    ("OEM letter", 8, lambda b: "Yes" if b["f"]("oem_auth_required") else "not stated"),
    ("Min turnover", 14, lambda b: b["f"]("turnover_min") or "not stated"),
    ("Delivery days", 8, lambda b: b["f"]("delivery_days") or "not stated"),
    ("Consignee", 30, lambda b: " ".join(x for x in [b["f"]("consignee_pin"), b["f"]("consignee")] if x)),
    ("Docs from seller", 40, lambda b: b["f"]("docs_required")),
    ("Blocker", 40, lambda b: "; ".join(b["best"]["blockers"])),
    ("Action needed (suggestion)", 45, lambda b: "; ".join(b["best"]["actions"])),
    ("Why", 40, lambda b: "; ".join(b["best"]["reasons"])),
    ("Firm A", 8, lambda b: b["by_entity"].get("A", {}).get("rating")),
    ("Firm B", 8, lambda b: b["by_entity"].get("B", {}).get("rating")),
    ("Document", 30, lambda b: b["row"]["doc_url"]),
    ("Evidence (page: quote)", 80, lambda b: b["evidence"]),
]


def write_excel(bids: list[dict], path: Path, failures: list[str]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bids"
    ws.append([c[0] for c in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for b in bids:
        ws.append([c[2](b) for c in COLUMNS])
        r = ws.max_row
        ws.cell(r, 1).fill = PatternFill("solid", fgColor=FILL.get(b["best"]["rating"], "FFFFFF"))
        link = b["row"]["doc_url"]
        if link:
            ws.cell(r, len(COLUMNS) - 1).hyperlink = link
    for i, c in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = c[1]
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    if failures:
        ws2 = wb.create_sheet("Source problems")
        for f in failures:
            ws2.append([f])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def telegram_summary(bids: list[dict], today: date, failures: list[str], closing_days: int) -> str:
    counts = {k: sum(1 for b in bids if b["best"]["rating"] == k) for k in ("GREEN", "AMBER", "RED")}
    lines = [f"<b>Bid digest {today:%d %b %Y}</b>",
             f"{len(bids)} open bids · 🟢 {counts['GREEN']} · 🟠 {counts['AMBER']} · 🔴 {counts['RED']}"]
    soon = [b for b in bids if b["days_left"] is not None and b["days_left"] <= closing_days and b["best"]["rating"] != "RED"]
    if soon:
        lines.append(f"⏰ Closing in ≤{closing_days} days: {len(soon)}")
    for f in failures:
        lines.append(f"⚠️ {html.escape(f)}")
    for b in bids:
        if b["best"]["rating"] == "RED":
            continue
        a = b["best"]
        bits = [f"closes {b['end']:%d-%m}" if b["end"] else "end not stated",
                f"EMD {emd(b)}", f"Firm {a['entity']}"]
        lines.append(f"\n{EMOJI[a['rating']]} <b>{html.escape(item(b)[:80])}</b>\n{' · '.join(bits)}")
        step = (a["actions"] or a["reasons"])[:1]
        if step:
            lines.append("→ " + html.escape(step[0][:120]))
        if b["row"]["doc_url"]:
            lines.append(f'<a href="{html.escape(b["row"]["doc_url"])}">document</a>')
    if counts["RED"]:
        lines.append(f"\n🔴 {counts['RED']} not realistic now - see the Excel for reasons.")
    text = "\n".join(lines)
    return text if len(text) < 4000 else text[:3950] + "\n… (full list in the Excel)"


def send_telegram(token: str, chat_id: str, text: str, file: Path | None = None) -> None:
    base = f"https://api.telegram.org/bot{token}"
    r = requests.post(f"{base}/sendMessage", data={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                                   "disable_web_page_preview": "true"}, timeout=30)
    r.raise_for_status()
    if file:
        with open(file, "rb") as fh:
            requests.post(f"{base}/sendDocument", data={"chat_id": chat_id},
                          files={"document": (file.name, fh)}, timeout=60).raise_for_status()

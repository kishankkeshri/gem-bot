"""Static extraction. Every fact is {value, page, quote}; a missing fact is simply absent ("not stated").

Two document shapes so far:
  * GeM bid document  - fixed bilingual template, English labels are reliable.
  * Free-form RFQ     - an institute's own letter (e.g. IIT ISM "Request for quotation").
"""
from __future__ import annotations

import re
from typing import Optional

Fact = dict  # {"value": ..., "page": int, "quote": str}


def _find(pages: list[str], pattern: str, group: int = 1, flags: int = re.I) -> Optional[Fact]:
    rx = re.compile(pattern, flags)
    for i, text in enumerate(pages, 1):
        m = rx.search(text)
        if m:
            a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
            value = m.group(group).strip(" ,;/") if group else m.group(0)
            return {"value": value, "page": i, "quote": "..." + text[a:b].strip() + "..."}
    return None


def _has(pages: list[str], pattern: str) -> Optional[Fact]:
    f = _find(pages, f"({pattern})")
    if f:
        f["value"] = True
    return f


def inr(s: str) -> Optional[float]:
    """'1,39,00,000' -> 13900000.0 ; '139 Lakh' -> 13900000.0 ; '1.5 crore' -> 15000000.0"""
    if s is None:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(lakh|lac|crore|cr)?", str(s), re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return n * 1e5 if unit in ("lakh", "lac") else n * 1e7 if unit in ("crore", "cr") else n


def is_gem(pages: list[str]) -> bool:
    return bool(_find(pages[:2], r"Bid Number\s*:?\s*(GEM/\d{4}/[BR]/\d+)"))


def parse(pages: list[str]) -> dict:
    facts = _parse_gem(pages) if is_gem(pages) else _parse_rfq(pages)
    facts.update(_common(pages))
    return {k: v for k, v in facts.items() if v}


def _parse_gem(p: list[str]) -> dict:
    f = {
        "doc_format": {"value": "GeM bid", "page": 1, "quote": ""},
        "bid_number": _find(p, r"Bid Number\s*:?\s*(GEM/\d{4}/[BR]/\d+)"),
        "end_date": _find(p, r"Bid End Date/Time\s*(\d{2}-\d{2}-\d{4}(?: \d{2}:\d{2}:\d{2})?)"),
        "opening_date": _find(p, r"Bid Opening\s*Date/Time\s*(\d{2}-\d{2}-\d{4}(?: \d{2}:\d{2}:\d{2})?)"),
        "start_date": _find(p, r"Dated\s*:\s*(\d{2}-\d{2}-\d{4})"),
        "ministry": _find(p, r"Ministry/State Name\s*(.+?)\s*/\s*Department Name"),
        "org": _find(p, r"Organisation Name\s*(.+?)\s*/\s*Office Name"),
        "qty": _find(p, r"Total Quantity\s*([\d,]+)"),
        "item": _find(p, r"Item Category\s*(.+?)(?=\s[\d\s]*/\s*(?:MSE Relaxation|Startup Relaxation|Document required|Minimum Average|Years of Past))"),
        "mse_relax": _find(p, r"MSE Relaxation for Years Of Experience\s*and\s*Turnover\s*(Yes|No)"),
        "startup_relax": _find(p, r"Startup Relaxation for Years Of Experience\s*and\s*Turnover\s*(Yes|No)"),
        "docs_required": _find(p, r"Document required from seller\s*(.+?)(?=\s*\*\s*In case|\s*\?|\s*/\s*Do you want)"),
        "turnover_min": _find(p, r"Minimum Average Annual Turnover of the bidder\s*(?:\([^)]*\))?\s*([\d.,]+\s*(?:Lakh|Crore)?)"),
        "oem_turnover_min": _find(p, r"OEM Average Turnover\s*(?:\([^)]*\))?\s*([\d.,]+\s*(?:Lakh|Crore)?)"),
        "experience_years": _find(p, r"Years of Past Experience Required[^/\d]{0,60}?(\d+)\s*Year"),
        "past_performance_pct": _find(p, r"Past Performance\s*(\d+)\s*%"),
        "bid_type": _find(p, r"Type of Bid\s*(.+?)\s*/"),
        "emd_required": _find(p, r"EMD Detail\s*/?\s*(?:Advisory Bank\s*)?.{0,40}?Required\s*(Yes|No)"),
        "emd": _find(p, r"EMD Amount\s*([\d,]+)"),
        "epbg_required": _find(p, r"ePBG Detail\s*/?\s*.{0,40}?Required\s*(Yes|No)"),
        "epbg_pct": _find(p, r"ePBG Percentage\s*\(%\)\s*([\d.]+)"),
        "est_value": _find(p, r"Estimated Bid Value\s*([\d,]{4,})"),
        "mse_preference": _find(p, r"MSE Purchase Preference\s*(Yes|No)"),
        "mii_preference": _find(p, r"MII Purchase Preference\s*(Yes|No)"),
    }
    # Consignee rows: "<name> 826004,<address> <qty> <delivery days>"
    c = _find(p, r"(\d{6})\s*,\s*([^/]{5,220}?)\s(\d[\d,]*)\s(\d{1,4})(?=\s|$)", group=0)
    if c:
        m = re.search(r"(\d{6})\s*,\s*([^/]{5,220}?)\s(\d[\d,]*)\s(\d{1,4})(?=\s|$)", c["value"])
        f["consignee_pin"] = dict(c, value=m.group(1))
        f["consignee"] = dict(c, value=m.group(2).strip())
        f["delivery_days"] = dict(c, value=m.group(4))
    # "Make stag Model supreme3star" - values must be real words, not the empty "Make / Model" header
    rx_mm = r"\bMake\s+([A-Za-z0-9][^/]{0,40}?)\s+Model\s+([A-Za-z0-9][^/]{0,40}?)\s*(?:/|Technical|\(|$)"
    mm = _find(p, rx_mm, group=0, flags=0)
    if mm:
        m = re.search(rx_mm, mm["value"])
        f["make"], f["model"] = dict(mm, value=m.group(1)), dict(mm, value=m.group(2))
    f["pac"] = _has(p, r"PAC Only|Under PAC")  # the PAC *definition* clause is boilerplate in every bid
    f["spec_in_buyer_doc"] = _has(p, r"Buyer Specification Document Download")
    f["spec_text"] = (_find(p, r"Bid Requirement \(Allowed Values\)\s*(.+?)\s*(?:/\s*){2,}Consignees")
                      or _find(p, r"Specification\s*/\s*Values\s*/\s*Core\s*(.+?)\s*/\s*Addon"))
    f["inspection"] = _find(p, r"Inspection Required[^)]{0,80}\)\s*(Yes|No)")
    f["ra_enabled"] = _find(p, r"Bid to RA enabled\s*(Yes|No)")
    f["warranty"] = _find(p, r"Warranty period of the supplied products shall be\s*(\d+\s*years?)") or \
        _find(p, r"Warranty \(in Year\)\s*([\d, ]+(?:Or higher)?)")
    if f.get("emd"):  # an amount means EMD applies, whatever a loose "Required No" matched
        f["emd_required"] = dict(f["emd"], value="Yes")
    if f.get("docs_required") and re.search(r"OEM Authori[sz]ation", f["docs_required"]["value"], re.I):
        f["oem_auth_required"] = dict(f["docs_required"], value=True)
    return f


def _parse_rfq(p: list[str]) -> dict:
    f = {
        "doc_format": {"value": "Institute RFQ", "page": 1, "quote": ""},
        "item": _find(p, r"Subject\s*:\s*(.+?)(?=\s+(?:Sir|Dear|Madam)\b)"),
        "end_date": _find(p, r"(?:latest by|last date[^.]{0,30}?|submission deadline\s*:?)\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})"),
        "opening_date": _find(p, r"(?:opening of (?:the )?tenders?|tender opening)[^.]{0,20}?\s(\d{1,2}[./-]\d{1,2}[./-]\d{4})"),
        "emd": _find(p, r"EMD[^.]{0,120}?Rs\.?\s*([\d,]+)"),
        "emd_exempt": _has(p, r"EMD[^.]{0,60}(?:not required|exempt|nil)"),
        "epbg_pct": _find(p, r"[Pp]erformance [Ss]ecurity.{0,200}?\(?(\d+(?:\.\d+)?)\s*%"),
        "delivery_days": _find(p, r"delivered within\s*(\d+)\s*days"),
        "oem_auth_required": _has(p, r"authori[sz]ation (?:certificate |letter )?from (?:the )?OEM|OEM authori[sz]ation"),
        "turnover_min": _find(p, r"turnover.{0,150}?(?:Rs\.?\s*)?([\d.,]+\s*(?:lakh|crore))"),
        "est_value": _find(p, r"estimated (?:cost|value)[^.]{0,40}?Rs\.?\s*([\d,]{4,})"),
        "consignee_pin": _find(p, r"Dhanbad\s*[-–]?\s*(826\d{3})"),
        "warranty": _find(p, r"warranty\s*/?\s*(?:guarantee)?[^.]{0,60}?(\d+\s*(?:years?|months?))"),
        "spec_text": _find(p, r"Technical Specifications?(?: for [^:]{0,60})?\s*(.{40,400}?)(?=\s(?:Annexure|Price Schedule|$))"),
    }
    return f


def make_model_from_title(title: str) -> dict:
    """Institute listings often say '(Make: Stag, Model: Stag 3-Star ...)'."""
    out = {}
    for k in ("make", "model"):
        m = re.search(rf"\b{k}\s*:\s*([^,;)]+)", title, re.I)
        if m:
            out[k] = {"value": m.group(1).strip(), "page": 0, "quote": f"listing title: {title[:80]}"}
    return out


def _common(p: list[str]) -> dict:
    return {
        "works_signals": _has(p, r"bidding capacity|civil work|construction of|renovation|similar (?:nature of )?works? (?:completed|executed)"),
        "licence_signals": _has(p, r"drug licen[cs]e|wholesale drug|pharmac|medicine|petroleum|diesel|\bfuel\b|\bscrap\b|explosive licen[cs]e"),
        "electrical_signals": _has(p, r"electrical (?:contractor )?licen[cs]e|feeder panel|sub-?station|\bHT\b panel|\bLT\b panel|cabling work"),
        "food_signals": _has(p, r"\bcanteen\b|catering|mess services|FSSAI"),
        "software_licence": _has(p, r"software licen[cs]e|perpetual licen[cs]e|subscription licen[cs]e|LCA Software|\bsoftware\b.{0,30}licen[cs]e"),
        "it_services": _has(p, r"software development|web portal|mobile app(?:lication)? development|website development|data migration|system integration"),
        "iso_required": _has(p, r"ISO\s*9001"),
        "emd_mse_exempt_text": _has(p, r"exempt(?:ed|ion)? from (?:submission of |furnishing |payment of )?(?:the )?(?:EMD|Earnest Money|bid security)|EMD exemption"),
        "installation": _has(p, r"Supply\s*(?:&|and)\s*Installation|\bSITC\b|installation,? testing and commissioning"),
        "payment_days": _find(p, r"[Pp]ayment[^.]{0,140}?within\s*(\d+)\s*days"),
        "local_pref_signals": _has(p, r"Class[- ]?I local supplier|local supplier|Make in India|preference to (?:the )?local"),
        "experience_signals": _has(p, r"similar (?:work|supply|services?).{0,60}(?:80|50|40)\s*%|past experience"),
    }

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
    # A label at the bottom of one page with its value at the top of the next (GeM page footers
    # like "2 / 9" sit in between): search the joined text, attribute it to the page it starts on.
    joined, starts, pos = "", [], 0
    for t in pages:
        starts.append(pos)
        joined += t + " "
        pos = len(joined)
    m = rx.search(joined)
    if m:
        page = max(i for i, s in enumerate(starts, 1) if s <= m.start())
        a, b = max(0, m.start() - 40), min(len(joined), m.end() + 40)
        value = m.group(group).strip(" ,;/") if group else m.group(0)
        return {"value": value, "page": page, "quote": "..." + joined[a:b].strip() + "..."}
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


def is_cppp(pages: list[str]) -> bool:
    return bool(_find(pages[:2], r"(eProcurement System Government of India).{0,400}Tender ID"))


def parse(pages: list[str], alt_pages: Optional[list[str]] = None) -> dict:
    """Parse the primary reading; then fill any gaps from the second reading (pdftotext)."""
    facts = _parse_one(pages)
    if alt_pages:
        for k, v in _parse_one(alt_pages).items():
            facts.setdefault(k, v)
    return facts


def _parse_one(pages: list[str]) -> dict:
    if is_gem(pages):
        facts = _parse_gem(pages)
    elif is_cppp(pages):
        facts = _parse_cppp(pages)
        for k, v in _parse_rfq(pages).items():  # the NIT pages after the CPPP printout read like an RFQ
            if v and k != "doc_format":
                facts.setdefault(k, v)
    else:
        facts = _parse_rfq(pages)
    for k, v in _common(pages).items():
        if v:
            facts.setdefault(k, v)
    return {k: v for k, v in facts.items() if v}


_CPPP_DT = r"(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2} [AP]M)"


def _parse_cppp(p: list[str]) -> dict:
    """CPPP (eprocure.gov.in) 'Tender Details' printout - fixed labels, values right after them."""
    f = {
        "doc_format": {"value": "CPPP tender", "page": 1, "quote": ""},
        "bid_number": _find(p, r"Tender ID\s*(\d{4}_[A-Z0-9]+_\d+_\d+)"),
        "org": _find(p, r"Organisation Chain\s*(.+?)\s*Tender Reference"),
        "item": _find(p, r"Work /Item\(s\)\s*Title\s*(.+?)\s*Work Description"),
        "tender_type": _find(p, r"Tender Type\s*(Open|Limited|Single|Global)"),
        "cppp_category": _find(p, r"Tender Category\s*(Goods|Works|Services)"),
        "emd": _find(p, r"EMD Amount in\s*(?:Rs\s*)?([\d,]+(?:\.\d+)?)"),
        "emd_exemption_allowed": _find(p, r"EMD Exemption\s*(?:Allowed\s*)?(Yes|No)"),
        "tender_fee": _find(p, r"Tender Fee in\s*(?:Rs\s*)?([\d,]+(?:\.\d+)?)"),
        "est_value": _find(p, r"Tender Value in\s*(?:Rs\s*)?([\d,]{4,})"),
        "product_category": _find(p, r"Product Category\s*(.+?)\s*Sub category"),
        "consignee_pin": _find(p, r"Pincode\s*(\d{6})"),
        "start_date": _find(p, r"Publish Date\s*" + _CPPP_DT),
        "end_date": _find(p, r"Bid Submission End Date\s*" + _CPPP_DT),
        "opening_date": _find(p, r"Bid Opening Date\s*" + _CPPP_DT),
        "prebid_date": _find(p, r"Pre Bid Meeting Date\s*" + _CPPP_DT),
        "purchase_office": _find(p, r"Tender Inviting Authority\s*Name\s*(.+?)\s*Address"),
        "period_days": _find(p, r"Period Of Work\(Days\)\s*(\d+)"),
    }
    if f["org"]:  # "IIT (ISM) Dhanbad||Administration||Purchase and Stores" -> the organisation itself
        f["org"] = dict(f["org"], value=f["org"]["value"].split("||")[0].strip())
    if f["cppp_category"]:
        f["kind"] = dict(f["cppp_category"], value=f["cppp_category"]["value"].lower())
    if f["tender_type"] and f["tender_type"]["value"] in ("Single", "Limited"):
        f["restricted_tender"] = f["tender_type"]
    if f["emd"] and not inr(f["emd"]["value"]):
        f["emd_required"] = dict(f["emd"], value="No")
        f["emd"] = None
    return f


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
        "emd": _find(p, r"EMD Amount\s*(?:\(In INR\))?\s*([\d,]+)"),
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
    f["buyer_email"] = _find(p, r"Buyer Email id\s*:?\s*(\S+@\S+)")
    f["hod_email"] = _find(p, r"HOD Email id\s*:?\s*(\S+@\S+)")
    f["competent_authority"] = _find(p, r"Name of Competent Authority\s*(.+?)\s*/\s*Designation")
    f["competent_designation"] = _find(p, r"Designation of Competent Authority\s*(.+?)\s*/")
    f["consignee_officer"] = _find(p, r"Delivery Days\s*1\s+([A-Z][A-Za-z. ]{2,40}?)\s+\d{6}", flags=0)
    f["tech_clar_days"] = _find(p, r"Time allowed for Technical Clarifications during technical evaluation\s*(\d+)\s*Days")
    f["inspection"] = _find(p, r"Inspection Required[^)]{0,80}\)\s*(Yes|No)")
    f["ra_enabled"] = _find(p, r"Bid to RA enabled\s*(Yes|No)")
    f["warranty"] = _find(p, r"Warranty period of the supplied products shall be\s*(\d+\s*years?)") or \
        _find(p, r"Warranty \(in Year\)\s*([\d, ]+(?:Or higher)?)")
    # Multi-schedule bids carry one EMD per schedule: report the total (a bidder may quote all of them)
    sched = re.findall(r"Schedule\s*(\d+)\s*/\s*EMD Amount\s*\(In INR\)\s*([\d,]+)", " ".join(p))
    if len(sched) > 1 and f.get("emd"):
        per = {}
        for s, amt in sched:
            per.setdefault(s, inr(amt) or 0)
        f["emd"] = dict(f["emd"], value=f"{sum(per.values()):.0f}")
        f["emd_schedules"] = dict(f["emd"], value=str(len(per)))
    if f.get("emd") and not inr(f["emd"]["value"]):  # "EMD Amount 0" = no EMD
        f["emd_required"] = dict(f["emd"], value="No")
        f.pop("emd")
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
        "emd": _find(p, r"EMD.{0,160}?Rs\.?\s*([\d,]{3,})(?:\.\d+)?\s*/?-?"),
        # only a real waiver: "EMD Exemption Allowed: Yes" (exemption for eligible MSEs) is NOT this
        "emd_exempt": _has(p, r"EMD\s*(?:is\s*)?(?:not required|nil\b|not applicable)|no EMD (?:is )?required"),
        "epbg_pct": _find(p, r"[Pp]erformance [Ss]ecurity.{0,200}?\(?(\d+(?:\.\d+)?)\s*%"),
        "delivery_days": _find(p, r"delivered within\s*(\d+)\s*days"),
        "oem_auth_required": _has(p, r"authori[sz]ation (?:certificate |letter )?from (?:the )?(?:Original Equipment Manufacturer|OEM)|OEM authori[sz]ation|Tender Specific Authori[sz]ation"),
        "turnover_min": _find(p, r"turnover.{0,150}?(?:Rs\.?\s*)?([\d.,]+\s*(?:lakh|crore))"),
        "est_value": _find(p, r"estimated (?:cost|value)[^.]{0,40}?Rs\.?\s*([\d,]{4,})"),
        "consignee_pin": _find(p, r"Dhanbad\s*[-–]?\s*(826\d{3})"),
        "purchase_office": _find(p, r"((?:Deputy|Assistant) Registrar\s*\(\s*[^)]{2,40}\))"),
        "buyer_email": _find(p, r"Email\s*:\s*([\w.\-]+@[\w.\-]+\.\w+)"),
        "warranty": _find(p, r"warranty\s*/?\s*(?:guarantee)?[^.]{0,60}?(\d+\s*(?:years?|months?))"),
        "spec_text": _find(p, r"Technical Specifications?(?: for [^:]{0,60})?\s*(.{40,400}?)(?=\s(?:Annexure|Price Schedule|$))"),
    }
    st = f.get("spec_text")
    if st and re.search(r"_{3,}|as under|should strictly|quoted item|Page \d+ of", st["value"], re.I):
        f["spec_text"] = None  # a blank form or a cross-reference, not the specs themselves
    return f


def _month_to_days(f: Optional[Fact]) -> Optional[Fact]:
    return dict(f, value=str(int(f["value"]) * 30)) if f else None


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
        "prebid_date": _find(p, r"Pre-?\s?Bid (?:Date\s*(?:and|&)\s*Time)?[^0-9]{0,40}?(\d{2}-\d{2}-\d{4} \d{2}:\d{2})"),
        "prebid_venue": _find(p, r"Pre-?\s?Bid Venue\s*(?:\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}\s*)?([A-Z][^/]{5,80}?)(?=\s*(?:/|\d+\s*/|\d+$|$))"),
        # GeM boilerplate mentions it in passing ("other than EMD and signed Integrity Pact"); flag only real asks
        "integrity_pact": _has(p, r"copy of signed integrity pact|\(Integrity Pact\)[^.]{0,80}rejected|Integrity Pact (?:is|shall be) (?:mandatory|required)"),
        "single_tender": _has(p, r"Single Tender Enquiry|\bSTE\b|Proprietary Article Certificate[^.]{0,40}enclosed"),
        "mse_emd_makers_only": _has(p, r"(?:must|should) be offering goods manufactured by themselves|acting as an agent for some other vendor|exemption[^.]{0,60}not (?:be )?(?:granted|applicable)[^.]{0,40}(?:traders|resellers|agents)"),
        "training": _has(p, r"provide training|training (?:to|of) (?:the )?(?:designated )?(?:staff|users)"),
        "inspection_by_buyer": _has(p, r"Institute authority will inspect|pre-?dispatch inspection|joint inspection"),
        "installation": _has(p, r"Supply\s*(?:&|and)\s*Installation|\bSITC\b|installation,? testing and commissioning"),
        "payment_days": _find(p, r"[Pp]ayment[^.]{0,140}?within\s*(\d+)\s*days")
        or _month_to_days(_find(p, r"[Pp]ayment[^.]{0,140}?within\s*(?:one|two|three|\w+)?\s*\(?0?(\d)\)?\s*months?")),
        "local_pref_signals": _has(p, r"Class[- ]?I local supplier|local supplier|Make in India|preference to (?:the )?local"),
        "experience_signals": _has(p, r"similar (?:work|supply|services?).{0,60}(?:80|50|40)\s*%|past experience"),
    }

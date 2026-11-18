"""Deterministic eligibility rules. GREEN = realistic now, AMBER = fixable steps, RED = not realistic now.

The rules here encode what we learned about GeM / institute tenders; an AI pass may add reasons
later but can never upgrade a rating set by these rules.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from gembot.parse_static import inr

ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2, "SKIP": 3}


def _v(facts: dict, key: str):
    f = facts.get(key)
    return f["value"] if f else None


def end_date(facts: dict, listing_end: Optional[date]) -> Optional[date]:
    raw = _v(facts, "end_date")
    if raw:
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
    return listing_end


def bid_kind(facts: dict, listing_category: str, title: str) -> str:
    """goods | services | works. A software *licence* purchase is goods even for a software firm."""
    text = f"{listing_category} {title} {_v(facts, 'item') or ''}".lower()
    if _v(facts, "works_signals") or listing_category.lower() in ("works", "work", "civil"):
        return "works"
    if _v(facts, "software_licence"):
        return "goods"
    if _v(facts, "kind") in ("goods", "services", "works"):  # AI-read, quote-verified
        return _v(facts, "kind")
    if _v(facts, "food_signals"):
        return "services"
    if _v(facts, "it_services") or any(w in text for w in ("service", "hiring", "manpower", "amc", "maintenance contract")):
        return "services"
    return "goods"


def rate(facts: dict, row: dict, entity: dict, today: date, closing_days: int = 3) -> dict:
    reasons, blockers, actions = [], [], []
    hard = False
    kind = bid_kind(facts, row.get("category", ""), row.get("title", ""))
    end = end_date(facts, row.get("listing_end_date"))

    if end and end < today:
        return {"entity": entity["id"], "rating": "SKIP", "kind": kind, "reasons": ["closed"], "blockers": [], "actions": []}

    # ── fit of the entity to the kind of bid ───────────────────────────────
    fits = set(entity.get("fits", []))
    if kind == "works":
        hard = True
        blockers.append("Works tender: needs completed similar work orders (80/50/40% rule) and bidding capacity")
        actions.append("Only via subcontracting under an established contractor")
    elif kind == "services" and not (fits & {"services", "it_services"}):
        hard = True
        blockers.append(f"{entity['name']} isn't set up for this kind of service")
    elif kind == "services" and "it_services" in fits and "services" not in fits and not _v(facts, "it_services"):
        hard = True
        blockers.append("Not a software/IT service")
    elif kind == "goods" and "goods_supply" not in fits:
        if _v(facts, "software_licence"):
            reasons.append("Software licence purchase = goods bid (reseller of the OEM's licence), not custom development")
            actions.append("Needs the software OEM's reseller authorisation")
        else:
            hard = True
            blockers.append(f"{entity['name']} isn't set up for goods supply")

    read_doc = "doc_format" in facts and any(
        k in facts for k in ("end_date", "emd", "emd_required", "emd_exempt", "delivery_days", "bid_number", "summary"))
    if not read_doc:
        actions.append("Document not read automatically (scanned?) - open the link and check EMD / eligibility")
    if _v(facts, "licence_signals") and not entity.get("licences"):
        hard = True
        blockers.append("Item needs a trade licence (drug / petroleum / scrap)")
    if _v(facts, "electrical_signals") and "electrical_contractor" not in entity.get("licences", []):
        hard = True
        blockers.append("Electrical installation: needs an electrical contractor licence")
    if _v(facts, "food_signals") and "fssai" not in entity.get("licences", []):
        actions.append("Canteen/catering: needs FSSAI licence and usually past catering experience")
    routine = {"pan", "gst", "gstin", "udyam", "msme", "pan card", "gst registration", "itr"}
    special = [x.strip() for x in str(_v(facts, "licences") or "").split(",") if x.strip().lower() not in routine and x.strip()]
    if special:
        actions.append(f"Document asks for: {', '.join(special)}")
    if _v(facts, "experience") and not _v(facts, "experience_signals") and not entity.get("past_govt_orders"):
        if not ((_v(facts, "mse_relax") == "Yes" and entity.get("mse")) or
                (_v(facts, "startup_relax") == "Yes" and entity.get("dpiit_startup"))):
            actions.append(f"Experience asked: {_v(facts, 'experience')[:120]}")

    # ── EMD ────────────────────────────────────────────────────────────────
    emd_amt = inr(_v(facts, "emd")) if _v(facts, "emd") else None
    emd_req = _v(facts, "emd_required")
    if read_doc and not (emd_req or emd_amt or _v(facts, "emd_exempt")):
        actions.append("EMD not stated - confirm in document")
    if not emd_amt and (emd_req == "No" or _v(facts, "emd_exempt")):
        reasons.append("No EMD")
    elif emd_amt:
        exempt = entity.get("mse") and (kind == "services" or entity.get("role_for_goods") == "manufacturer")
        if exempt:
            reasons.append(f"EMD Rs {emd_amt:,.0f}, MSE exemption likely ({kind}) - upload Udyam")
        else:
            actions.append(f"Arrange EMD Rs {emd_amt:,.0f} (resellers get no MSE exemption on goods)")

    # ── OEM authorisation ──────────────────────────────────────────────────
    if _v(facts, "oem_auth_required"):
        if entity.get("oem_authorisations"):
            reasons.append("OEM authorisation on file")
        else:
            actions.append("Get an OEM / authorised-dealer letter for the asked make")

    # ── turnover & experience ──────────────────────────────────────────────
    relax_ok = (_v(facts, "mse_relax") == "Yes" and entity.get("mse")) or \
               (_v(facts, "startup_relax") == "Yes" and entity.get("dpiit_startup"))
    t_min = inr(_v(facts, "turnover_min")) if _v(facts, "turnover_min") else None
    exp = _v(facts, "experience_years") or _v(facts, "past_performance_pct") or _v(facts, "experience_signals")
    if t_min or exp:
        if relax_ok:
            reasons.append("Turnover/experience relaxed for MSE/startup - upload proof")
        elif t_min and t_min > entity.get("avg_turnover_3y_inr", 0):
            hard = True
            blockers.append(f"Min avg turnover Rs {t_min:,.0f} and no relaxation")
        elif exp and not entity.get("past_govt_orders"):
            hard = True
            blockers.append("Past experience required, no relaxation, no past orders")
    elif _v(facts, "mse_relax") == "Yes":
        reasons.append("MSE relaxation: Yes")

    if _v(facts, "iso_required"):
        actions.append("ISO 9001 asked - check if mandatory")
    if entity["id"] == "A" and not entity.get("gem_profile_complete") and _v(facts, "doc_format") == "GeM bid":
        actions.append("Finish GeM profile (Beneficial Ownership)")

    if _v(facts, "mse_preference") == "Yes" and entity.get("mse") and (kind == "services" or entity.get("role_for_goods") == "manufacturer"):
        reasons.append("MSE purchase preference applies")

    rating = "RED" if hard else ("AMBER" if actions else "GREEN")
    if end and (end - today).days <= closing_days:
        reasons.insert(0, f"Closes in {(end - today).days} day(s)")
    return {"entity": entity["id"], "rating": rating, "kind": kind,
            "reasons": reasons[:3], "blockers": blockers, "actions": actions}


def best(analyses: list[dict]) -> dict:
    return sorted(analyses, key=lambda a: (ORDER[a["rating"]], len(a["blockers"]), len(a["actions"])))[0]

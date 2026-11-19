"""GeM BidPlus public bid list (no login, no captcha).

How the public page works (bidplus.gem.gov.in/all-bids, read 2026-11-18):
  * the page embeds a per-session form token `csrf_bd_gem_nk`
  * it POSTs {payload: JSON, csrf_bd_gem_nk} to /all-bids-data, which returns Solr-style JSON
    (response.response.numFound / start / docs[]), 10 bids per page
  * the keyword box searches "Bid Number, Department, Ministry, Items, BOQ Title, Category,
    Consignee Location (City and State)" -> one fullText search per district name
  * each bid's document is public at /showbidDocument/<b_id>

robots.txt (same date) disallows only /resources/ and a few ePBG endpoints. We search once a day,
one request every few seconds, and confirm the region from the bid PDF's consignee PIN afterwards
(a keyword hit on "Bokaro" can also be a ministry or item name).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from gembot.http import PoliteSession

NAME = "gem"
BASE = "https://bidplus.gem.gov.in"
MAX_PAGES_PER_PLACE = 15  # 150 bids per keyword; Dhanbad/Ranchi may need more, raise once measured


def _token(http: PoliteSession) -> str:
    html = http.get(f"{BASE}/all-bids").text
    m = re.search(r"'csrf_bd_gem_nk'\s*:\s*'([a-f0-9]+)'", html)
    if not m:
        raise RuntimeError("form token not found on all-bids page - layout changed?")
    return m.group(1)


def _date(ms_or_iso) -> "datetime | None":
    if ms_or_iso in (None, ""):
        return None
    try:
        if isinstance(ms_or_iso, (int, float)):
            return datetime.fromtimestamp(ms_or_iso / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(ms_or_iso).replace("Z", "+00:00"))
    except ValueError:
        return None


def _dump(r) -> None:
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "gem_last_response.txt"
    p.parent.mkdir(exist_ok=True)
    p.write_text(f"HTTP {r.status_code}\n{dict(r.headers)}\n\n{r.text[:20000]}")


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def search(http: PoliteSession, keyword: str, token: str) -> list[dict]:
    docs, page = [], 1
    while page <= MAX_PAGES_PER_PLACE:
        payload = {"page": page,
                   "param": {"searchBid": keyword, "searchType": "fullText"},
                   "filter": {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "",
                              "byEndDate": {"from": "", "to": ""}, "sort": "Bid-Start-Date-Latest"}}
        r = http.post(f"{BASE}/all-bids-data",
                      data={"payload": json.dumps(payload), "csrf_bd_gem_nk": token},
                      headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/all-bids"})
        try:
            data = r.json()
        except ValueError:
            _dump(r)
            raise RuntimeError(f"all-bids-data did not return JSON (HTTP {r.status_code}); saved to data/gem_last_response.txt")
        if data.get("code") != 200:
            _dump(r)
            raise RuntimeError(f"all-bids-data returned code {data.get('code')}")
        resp = data["response"]["response"]
        docs += resp.get("docs", [])
        if resp["start"] + 10 >= resp["numFound"]:
            break
        page += 1
    return docs


def fetch(http: PoliteSession, places: list[str]) -> list[dict]:
    token = _token(http)
    by_id: dict[str, dict] = {}
    for place in places:
        found = search(http, place, token)
        print(f"[gem] {place}: {len(found)} ongoing bids", flush=True)
        for d in found:
            if _first(d.get("b_bid_type")) not in (1, None):  # 1 = bid; 2/5 = reverse auctions (values come as lists)
                continue
            bid_no = _first(d.get("b_bid_number"))
            if not bid_no or bid_no in by_id:
                continue
            end = _date(_first(d.get("final_end_date_sort")))
            category = _first(d.get("b_category_name")) or ""
            boq = _first(d.get("bbt_title")) or ""
            by_id[bid_no] = {
                "source": NAME,
                "external_id": bid_no,
                "category": "GeM",
                "title": f"{category} ({boq})" if boq else category,
                "doc_url": f"{BASE}/showbidDocument/{_first(d.get('b_id'))}",
                "listing_end_date": end.date() if end else None,
                "org": " / ".join(x for x in [_first(d.get("ba_official_details_minName")),
                                              _first(d.get("ba_official_details_deptName"))] if x),
                "location": place,
                "matched_keyword": place,
                "buyer_account": _first(d.get("b.b_created_by")),
                "qty": _first(d.get("b_total_quantity")),
            }
    return list(by_id.values())

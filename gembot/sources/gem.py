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
import time
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
        print(f"[gem] {keyword}: page {page}, {len(docs)}/{resp['numFound']}", flush=True)
        if resp["start"] + 10 >= resp["numFound"]:
            break
        page += 1
    return docs


def fetch(http: PoliteSession, places: list[str], failures: "list[str] | None" = None) -> list[dict]:
    """Search every place. A failing place is retried with a fresh session token, then reported and
    skipped - it never throws away the bids already collected for other places."""
    token = _token(http)
    by_id: dict[str, dict] = {}
    failed: list[str] = []
    in_a_row = 0
    for place in places:
        found = None
        for attempt in range(2):
            try:
                found = search(http, place, token)
                break
            except Exception as e:  # noqa: BLE001 - token expiry shows up as 404/403 or a non-JSON page
                print(f"[gem] {place}: {e.__class__.__name__}: {e} - new token, retrying", flush=True)
                time.sleep(20 * (attempt + 1))
                try:
                    http.s.cookies.clear()
                    token = _token(http)
                except Exception as e2:  # noqa: BLE001
                    print(f"[gem] token refresh failed: {e2}", flush=True)
        if found is None:
            failed.append(place)
            in_a_row += 1
            if in_a_row >= 4:  # GeM is down or blocking: stop asking, keep what we have
                failed += [p for p in places[places.index(place) + 1:]]
                break
            continue
        in_a_row = 0
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
    if failed and not by_id:
        raise RuntimeError(f"every search failed ({len(failed)} places)")
    if failed and failures is not None:
        failures.append(f"gem: {len(failed)}/{len(places)} searches failed ({', '.join(failed[:8])}"
                        + ("…" if len(failed) > 8 else "") + ") - retried at the next run")
    return list(by_id.values())

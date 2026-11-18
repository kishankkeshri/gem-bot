"""IIT (ISM) Dhanbad tenders page: one public HTML table, no API."""
from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from gembot.http import PoliteSession

NAME = "iitism"
URL = "https://www.iitism.ac.in/tenders"


def fetch(http: PoliteSession) -> list[dict]:
    soup = BeautifulSoup(http.get(URL).text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("tenders table not found - page layout changed?")
    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        link = tds[4].find("a")
        last = tds[5].get_text(strip=True)
        try:
            end = datetime.strptime(last, "%d-%m-%Y").date()
        except ValueError:
            end = None
        rows.append({
            "source": NAME,
            "external_id": tds[1].get_text(strip=True),
            "category": tds[2].get_text(strip=True),
            "title": " ".join(tds[3].get_text(" ", strip=True).split()),
            "doc_url": link["href"] if link and link.has_attr("href") else None,
            "listing_end_date": end,
            "org": "IIT (ISM) Dhanbad",
            "location": "Dhanbad",
        })
    return rows

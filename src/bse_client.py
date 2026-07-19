from __future__ import annotations

from datetime import date
from typing import Any

import requests


API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ANNOUNCEMENT_PAGE = (
    "https://www.bseindia.com/stock-share-price/jd-cables-ltd/"
    "jdcables/544524/corp-announcements"
)
PDF_BASE_URL = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"


def fetch_announcements(
    start_date: date,
    end_date: date,
    scrip_code: int = 544524,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    params = {
        "pageno": 1,
        "strCat": -1,
        "strPrevDate": start_date.strftime("%Y%m%d"),
        "strScrip": scrip_code,
        "strSearch": "P",
        "strToDate": end_date.strftime("%Y%m%d"),
        "strType": "C",
        "subcategory": -1,
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "User-Agent": "bse-announcement-alert/1.0",
    }
    client = session or requests.Session()
    response = client.get(API_URL, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload.get("Table", [])


def fetch_today_announcements(
    scrip_code: int = 544524,
    today: date | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    current_date = today or date.today()
    return fetch_announcements(
        current_date, current_date, scrip_code=scrip_code, session=session
    )


def announcement_pdf_url(announcement: dict[str, Any]) -> str | None:
    filename = announcement.get("ATTACHMENTNAME")
    if not filename:
        return None
    return f"{PDF_BASE_URL}{filename}"


def announcement_page_url(_: dict[str, Any]) -> str:
    return ANNOUNCEMENT_PAGE

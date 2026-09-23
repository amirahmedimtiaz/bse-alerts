from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .http_client import get_json


API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
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
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected: int | None = None
    for page in range(1, 1001):
        params["pageno"] = page
        payload = get_json(API_URL, params=params, headers=headers, session=session)
        if not isinstance(payload, dict) or not isinstance(payload.get("Table"), list):
            raise ValueError("BSE response is missing its announcement table")
        rows = payload["Table"]
        totals = payload.get("Table1") or []
        if totals and totals[0].get("ROWCNT") is not None:
            expected = max(expected or 0, int(totals[0]["ROWCNT"]))
        if not rows:
            if expected is not None and len(result) < expected:
                raise ValueError(f"BSE incomplete pagination: {len(result)}/{expected}")
            return result
        before = len(result)
        for row in rows:
            if not isinstance(row, dict) or not row.get("NEWSID"):
                raise ValueError("BSE announcement is missing NEWSID")
            key = str(row["NEWSID"])
            if key not in seen:
                seen.add(key)
                result.append(row)
        if len(result) == before:
            raise ValueError("BSE repeated a page before pagination completed")
        if expected is not None and len(result) >= expected:
            return result
    raise ValueError("BSE pagination exceeded safety limit")


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


def announcement_page_url(announcement: dict[str, Any]) -> str:
    return announcement["_announcement_url"]

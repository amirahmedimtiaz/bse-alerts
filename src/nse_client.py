from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .http_client import get_json


API_URL = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"


def fetch_announcements(
    symbol: str,
    market_type: str = "sme",
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    params = {
        "functionName": "getCorporateAnnouncement",
        "symbol": symbol,
        "marketApiType": market_type,
    }
    headers = {
        "Accept": "*/*",
        "Referer": f"https://www.nseindia.com/get-quote/equity/{symbol}/",
        "User-Agent": "bse-announcement-alert/1.0",
    }
    payload = get_json(API_URL, params=params, headers=headers, session=session)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get("data", payload.get("Table"))
        if isinstance(rows, list):
            return rows
    raise ValueError("NSE response is missing its announcement table")


def fetch_today_announcements(
    symbol: str,
    today: date | None = None,
    market_type: str = "sme",
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    current_date = today or date.today()
    all_announcements = fetch_announcements(
        symbol=symbol, market_type=market_type, session=session
    )
    today_str = current_date.strftime("%Y-%m-%d")
    return [a for a in all_announcements if (a.get("sort_date") or "").startswith(today_str)]


def announcement_pdf_url(announcement: dict[str, Any]) -> str | None:
    url = announcement.get("attchmntFile")
    if not url or url.endswith("/-"):
        return None
    return url

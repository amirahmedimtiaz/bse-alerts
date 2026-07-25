from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import bse_client, nse_client
from .email_sender import send_announcement_email


COMPANIES_PATH = Path(__file__).resolve().parents[1] / "companies.json"
STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "seen_announcements.json"
INDIA_TZ = ZoneInfo("Asia/Kolkata")


def load_companies() -> list[dict[str, object]]:
    return json.loads(COMPANIES_PATH.read_text())


def load_state() -> dict[str, set[str]]:
    if not STATE_PATH.exists():
        return {}
    raw_state = json.loads(STATE_PATH.read_text())
    if isinstance(raw_state, list):
        return {"544524": {str(news_id) for news_id in raw_state}}
    return {
        str(key): {str(news_id) for news_id in news_ids}
        for key, news_ids in raw_state.items()
    }


def save_state(state: dict[str, set[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        key: sorted(news_ids)
        for key, news_ids in sorted(state.items())
    }
    STATE_PATH.write_text(json.dumps(serializable, indent=2) + "\n")


def state_key(company: dict[str, object]) -> str:
    exchange = str(company.get("exchange", "BSE"))
    if exchange == "NSE":
        return f"NSE:{company['symbol']}"
    return str(company["scrip_code"])


def add_company_details(
    announcement: dict[str, object], company: dict[str, object]
) -> dict[str, object]:
    exchange = str(company.get("exchange", "BSE"))
    base: dict[str, object] = {
        **announcement,
        "_exchange": exchange,
        "_company_name": company["name"],
        "_page_url": company["announcement_url"],
    }
    if exchange == "NSE":
        base["_subject"] = announcement.get("desc", "New NSE announcement")
        base["_published"] = announcement.get("sort_date") or announcement.get("dt", "Unknown")
        base["_category"] = announcement.get("desc", "Unknown")
        base["_headline"] = announcement.get("attchmntText", "")
        base["_pdf_url"] = nse_client.announcement_pdf_url(announcement)
    else:
        base["_subject"] = announcement.get("NEWSSUB", "New BSE announcement")
        base["_published"] = announcement.get("DT_TM", "Unknown")
        base["_category"] = announcement.get("CATEGORYNAME", "Unknown")
        base["_headline"] = (announcement.get("HEADLINE") or announcement.get("MORE") or "")
        base["_pdf_url"] = bse_client.announcement_pdf_url(announcement)
    return base


def get_announcement_id(announcement: dict[str, object], exchange: str) -> str:
    if exchange == "NSE":
        return str(announcement.get("seq_id", ""))
    return str(announcement.get("NEWSID", ""))


def fetch_for_company(
    company: dict[str, object], today: datetime.date
) -> list[dict[str, object]]:
    exchange = str(company.get("exchange", "BSE"))
    if exchange == "NSE":
        return nse_client.fetch_today_announcements(
            symbol=str(company["symbol"]),
            today=today,
            market_type=str(company.get("market_type", "sme")),
        )
    return bse_client.fetch_today_announcements(
        scrip_code=int(company["scrip_code"]), today=today
    )


def fetch_history_for_company(
    company: dict[str, object], today: datetime.date
) -> list[dict[str, object]]:
    exchange = str(company.get("exchange", "BSE"))
    if exchange == "NSE":
        return nse_client.fetch_announcements(
            symbol=str(company["symbol"]),
            market_type=str(company.get("market_type", "sme")),
        )
    return bse_client.fetch_announcements(
        start_date=today - timedelta(days=30),
        end_date=today,
        scrip_code=int(company["scrip_code"]),
    )


def run(send_alerts: bool = True) -> int:
    today = datetime.now(INDIA_TZ).date()
    state = load_state()
    alert_count = 0

    for company in load_companies():
        key = state_key(company)
        exchange = str(company.get("exchange", "BSE"))
        announcements = fetch_for_company(company, today)

        seen_in_response: set[str] = set()
        unique_announcements: list[dict[str, object]] = []
        for item in announcements:
            nid = get_announcement_id(item, exchange)
            if nid and nid not in seen_in_response:
                seen_in_response.add(nid)
                unique_announcements.append(item)
        announcements = unique_announcements

        current_ids = {
            get_announcement_id(item, exchange) for item in announcements
            if get_announcement_id(item, exchange)
        }
        seen_ids = state.get(key)

        new_announcements = [] if seen_ids is None else [
            add_company_details(item, company)
            for item in announcements
            if get_announcement_id(item, exchange) not in seen_ids
        ]
        if send_alerts:
            for announcement in reversed(new_announcements):
                try:
                    send_announcement_email(announcement)
                except Exception as exc:
                    print(f"Failed to send email for {company['name']}: {exc}")
        alert_count += len(new_announcements)
        state[key] = (seen_ids or set()) | current_ids

    save_state(state)
    return alert_count


def test_email() -> None:
    today = datetime.now(INDIA_TZ).date()
    available: list[dict[str, object]] = []
    for company in load_companies():
        announcements = fetch_for_company(company, today)
        if not announcements:
            announcements = fetch_history_for_company(company, today)
        available.extend(add_company_details(item, company) for item in announcements)

    if not available:
        raise RuntimeError("No announcement is available in the last 30 days")
    latest = max(
        available,
        key=lambda item: str(item.get("_published", "")),
    )
    send_announcement_email(latest)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "test-email"])
    args = parser.parse_args()
    if args.command == "test-email":
        test_email()
        print("Test email sent")
    else:
        count = run()
        print(f"Sent {count} new announcement alert(s)")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .bse_client import fetch_announcements, fetch_today_announcements
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
        # Migrate the original single-company state format for JD Cables.
        return {"544524": {str(news_id) for news_id in raw_state}}
    return {
        str(scrip_code): {str(news_id) for news_id in news_ids}
        for scrip_code, news_ids in raw_state.items()
    }


def save_state(state: dict[str, set[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        scrip_code: sorted(news_ids)
        for scrip_code, news_ids in sorted(state.items())
    }
    STATE_PATH.write_text(json.dumps(serializable, indent=2) + "\n")


def add_company_details(
    announcement: dict[str, object], company: dict[str, object]
) -> dict[str, object]:
    return {
        **announcement,
        "_company_name": company["name"],
        "_announcement_url": company["announcement_url"],
    }


def run(send_alerts: bool = True) -> int:
    today = datetime.now(INDIA_TZ).date()
    state = load_state()
    alert_count = 0

    for company in load_companies():
        scrip_code = str(company["scrip_code"])
        announcements = fetch_today_announcements(
            scrip_code=int(company["scrip_code"]), today=today
        )
        current_ids = {
            str(item["NEWSID"]) for item in announcements if item.get("NEWSID")
        }
        seen_ids = state.get(scrip_code)

        # A missing company entry is its initial baseline; do not email history.
        new_announcements = [] if seen_ids is None else [
            add_company_details(item, company)
            for item in announcements
            if str(item.get("NEWSID")) not in seen_ids
        ]
        if send_alerts:
            for announcement in reversed(new_announcements):
                send_announcement_email(announcement)
        alert_count += len(new_announcements)
        state[scrip_code] = (seen_ids or set()) | current_ids

    save_state(state)
    return alert_count


def test_email() -> None:
    today = datetime.now(INDIA_TZ).date()
    available: list[dict[str, object]] = []
    for company in load_companies():
        announcements = fetch_today_announcements(
            scrip_code=int(company["scrip_code"]), today=today
        )
        if not announcements:
            announcements = fetch_announcements(
                start_date=today - timedelta(days=30),
                end_date=today,
                scrip_code=int(company["scrip_code"]),
            )
        available.extend(add_company_details(item, company) for item in announcements)

    if not available:
        raise RuntimeError("No announcement is available in the last 30 days")
    latest = max(available, key=lambda item: str(item.get("DT_TM", "")))
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

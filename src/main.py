from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .bse_client import fetch_announcements, fetch_today_announcements
from .email_sender import send_announcement_email


STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "seen_announcements.json"
INDIA_TZ = ZoneInfo("Asia/Kolkata")


def load_state() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    return set(json.loads(STATE_PATH.read_text()))


def save_state(news_ids: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(news_ids), indent=2) + "\n")


def run(send_alerts: bool = True) -> int:
    announcements = fetch_today_announcements(today=datetime.now(INDIA_TZ).date())
    current_ids = {str(item["NEWSID"]) for item in announcements if item.get("NEWSID")}
    seen_ids = load_state()

    # An empty state is the initial baseline; do not email old announcements.
    new_announcements = [] if not seen_ids else [
        item for item in announcements if str(item.get("NEWSID")) not in seen_ids
    ]
    if send_alerts:
        for announcement in reversed(new_announcements):
            send_announcement_email(announcement)

    save_state(seen_ids | current_ids)
    return len(new_announcements)


def test_email() -> None:
    today = datetime.now(INDIA_TZ).date()
    announcements = fetch_today_announcements(today=today)
    if not announcements:
        announcements = fetch_announcements(
            start_date=today - timedelta(days=30), end_date=today
        )
    if not announcements:
        raise RuntimeError("No announcement is available in the last 30 days")
    send_announcement_email(announcements[0])


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

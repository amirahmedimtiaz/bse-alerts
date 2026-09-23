from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import bse_client, nse_client
from .alert_store import AlertStore, utc_now
from .email_sender import EmailSender
from .main import INDIA_TZ, add_company_details, get_announcement_id, load_companies, load_state, state_key


def fetch_window(company: dict, start: date, end: date) -> list[dict]:
    exchange = str(company.get("exchange", "BSE"))
    if exchange == "BSE":
        rows = bse_client.fetch_announcements(start, end, int(company["scrip_code"]))
    elif exchange == "NSE":
        rows = nse_client.fetch_announcements(str(company["symbol"]), str(company.get("market_type", "sme")))
    else:
        raise ValueError(f"Unsupported exchange {exchange}")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{exchange} response contains a malformed announcement")
        token = str(row.get("sort_date") or row.get("dt") or "") if exchange == "NSE" else str(row.get("DT_TM") or "")
        # Both live feeds expose ISO dates in their sorting/publication fields.
        try:
            published = date.fromisoformat(token[:10])
        except ValueError as exc:
            raise ValueError(f"{exchange} announcement has an invalid date: {token!r}") from exc
        if start <= published <= end:
            nid = get_announcement_id(row, exchange)
            if not nid or nid == "None":
                raise ValueError(f"{exchange} response has an in-window announcement without an ID")
            result.append({**add_company_details(row, company), "_id": nid})
    return sorted(result, key=lambda item: str(item["_published"]))


def collect(store: AlertStore, companies: list[dict], *, today: date | None = None,
            fetch: Callable = fetch_window, workers: int = 4) -> dict:
    today = today or datetime.now(INDIA_TZ).date()
    keys = [state_key(company) for company in companies]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate exchange/company keys in companies.json")
    if not 1 <= workers <= 16:
        raise ValueError("ALERT_WORKERS must be between 1 and 16")
    report = {"companies": len(companies), "collected": 0, "failed": [], "catching_up": []}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for company in companies:
            key = state_key(company)
            record = store.company(key)
            last = date.fromisoformat(record["last_success"] or store.migration_date() or today.isoformat()) if record else today
            # Always overlap one prior calendar day; long outages recover in
            # seven-day chunks, advancing only after a complete successful fetch.
            start = min(today, last) - timedelta(days=1)
            end = min(today, start + timedelta(days=6))
            futures[executor.submit(fetch, company, start, end)] = (company, key, end)
        for future in as_completed(futures):
            company, key, end = futures[future]
            try:
                rows = future.result()
                report["collected"] += store.collect(key, end.isoformat(), rows)
                if end < today:
                    report["catching_up"].append(key)
            except Exception as exc:
                report["failed"].append(key)
                print(f"::error::Collection failed for {company['name']}: {exc}", flush=True)
    return report


def deliver(store: AlertStore, checkpoint: Callable[[], None], *, sender=None,
            batch_size: int = 20, daily_limit: int = 450) -> dict:
    if not 1 <= batch_size <= 100 or not 1 <= daily_limit <= 450:
        raise ValueError("Email batch size must be 1–100; rolling daily limit must be 1–450")
    sent = 0
    emails = 0
    failed = False
    client = sender or EmailSender()
    try:
        while store.pending_count() and store.sent_recently() < daily_limit:
            batch = store.pending(batch_size)
            try:
                message_id = client.send([item["announcement"] for item in batch])
            except Exception as exc:
                print(f"::error::Email delivery failed; retained in outbox: {exc}", flush=True)
                failed = True
                break
            store.mark_sent(batch, message_id)
            # A checkpoint failure stops delivery immediately. SMTP may have
            # accepted this last batch; restart may repeat it (at-least-once).
            checkpoint()
            sent += len(batch)
            emails += 1
    finally:
        client.close()
    pending = store.pending_count()
    if pending and not failed:
        print(f"::warning::{pending} announcements queued until the rolling email allowance is available", flush=True)
    return {"sent": sent, "emails": emails, "email_failed": failed, "pending": pending}


def cycle(store: AlertStore, checkpoint: Callable[[], None], *, companies=None,
          today=None, fetch=fetch_window, sender=None) -> dict:
    started = time.monotonic()
    report = collect(store, load_companies() if companies is None else companies,
                     today=today, fetch=fetch, workers=int(os.getenv("ALERT_WORKERS", "4")))
    # Persist discovered payloads BEFORE any external delivery, including on
    # partial collection failure, so an exchange/date change cannot lose them.
    checkpoint()
    report.update(deliver(store, checkpoint, sender=sender,
                          batch_size=int(os.getenv("EMAIL_BATCH_SIZE", "20")),
                          daily_limit=int(os.getenv("EMAIL_DAILY_LIMIT", "450"))))
    report.update(finished_at=utc_now(), seconds=round(time.monotonic() - started, 2))
    store.health(report)
    checkpoint()
    print(f"Cycle complete: {report}", flush=True)
    return report


def run_local(send_alerts: bool = True) -> int:
    if not send_alerts:
        raise ValueError("Use validate for a read-only check; scan always persists its outbox")
    path = Path(os.getenv("ALERT_DB_PATH", "state/alerts.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    store = AlertStore(path)
    try:
        if fresh:
            store.seed_legacy(load_state())
        report = cycle(store, lambda: None)
        if report["failed"] or report["email_failed"]:
            raise RuntimeError("Alert cycle incomplete; pending deliveries and progress were retained")
        return report["sent"]
    finally:
        store.close()

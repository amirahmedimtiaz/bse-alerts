from __future__ import annotations

import json
import subprocess
from datetime import date
from unittest.mock import Mock, patch

import pytest
import responses

from src import bse_client, http_client, nse_client, scanner, worker
from src.alert_store import AlertStore, GitState
from src.email_sender import EmailSender


@pytest.fixture
def store(tmp_path):
    value = AlertStore(tmp_path / "state.sqlite3")
    yield value
    value.close()


@pytest.fixture(autouse=True)
def no_live_pacing(monkeypatch):
    monkeypatch.setattr(http_client, "_pace", lambda host: None)


def company(key=1):
    return {"name": f"Company {key}", "scrip_code": key, "announcement_url": "https://example.com/filings"}


def announcement(key="new"):
    return {"_id": key, "_company_name": "Company 1", "_exchange": "BSE", "_subject": f"Filing {key}",
            "_published": "2026-09-22T12:00:00", "_page_url": "https://example.com/filings",
            "_pdf_url": f"https://example.com/{key}.pdf"}


def sender(fail=False):
    result = Mock()
    if fail:
        result.send.side_effect = RuntimeError("mail unavailable")
    else:
        result.send.side_effect = lambda batch: ":".join(item["_id"] for item in batch)
    return result


def test_baseline_and_migration_deduplicate(store):
    assert store.collect("new-company", "2026-09-22", [announcement()]) == 0
    store.seed_legacy({"1": {"old"}})
    assert store.collect("1", "2026-09-22", [announcement("old"), announcement(), announcement()]) == 1
    assert store.pending_count() == 1


def test_cross_midnight_failed_mail_survives_restart(store):
    store.seed_legacy({"1": set()})
    store.collect("1", "2026-09-22", [announcement()])
    assert scanner.deliver(store, lambda: None, sender=sender(True))["email_failed"]
    snapshot = json.loads(json.dumps(store.export()))
    restored = AlertStore(":memory:")
    try:
        restored.restore(snapshot)
        # Next day the exchange no longer returns yesterday's announcement.
        scanner.collect(restored, [company()], today=date(2026, 9, 23), fetch=lambda *args: [])
        assert scanner.deliver(restored, lambda: None, sender=sender())["sent"] == 1
        assert restored.pending_count() == 0
    finally:
        restored.close()


def test_fetch_failure_does_not_advance_watermark_and_other_company_continues(store):
    store.seed_legacy({"1": set(), "2": set()})
    store.collect("1", "2026-09-20", [])
    windows = {}
    def fetch(c, start, end):
        windows[c["scrip_code"]] = (start, end)
        if c["scrip_code"] == 1:
            raise TimeoutError("exchange down")
        return [announcement()]
    result = scanner.collect(store, [company(), company(2)], today=date(2026, 9, 23), fetch=fetch)
    assert result["failed"] == ["1"]
    assert result["collected"] == 1
    assert store.company("1")["last_success"] == "2026-09-20"
    assert windows[1] == (date(2026, 9, 19), date(2026, 9, 23))


@responses.activate
def test_bse_access_denied_keeps_catch_up_window_while_nse_continues(store, monkeypatch):
    store.seed_legacy({"1": set(), "NSE:APS": set()}, as_of="2026-09-23")
    store.collect("1", "2026-09-23", [])
    store.collect("NSE:APS", "2026-09-23", [])
    responses.add(responses.GET, bse_client.API_URL, status=403)
    monkeypatch.setattr(nse_client, "fetch_announcements", lambda *args: [
        {"seq_id": "new-nse", "sort_date": "2026-09-25 12:00:00", "desc": "Filing"},
    ])
    nse_company = {**company(2), "exchange": "NSE", "symbol": "APS"}

    report = scanner.collect(store, [company(), nse_company],
                             today=date(2026, 9, 26), workers=1)

    assert report["failed"] == ["1"]
    assert report["collected"] == 1
    assert store.company("1")["last_success"] == "2026-09-23"
    assert store.company("NSE:APS")["last_success"] == "2026-09-26"
    assert store.pending_count() == 1


def test_long_outage_recovers_from_watermark_in_chunks(store):
    store.collect("1", "2026-08-01", [])
    windows = []
    result = scanner.collect(store, [company()], today=date(2026, 9, 23),
                             fetch=lambda c, start, end: windows.append((start, end)) or [])
    assert windows == [(date(2026, 7, 31), date(2026, 8, 6))]
    assert result["catching_up"] == ["1"]
    assert store.company("1")["last_success"] == "2026-08-06"


def test_migration_floor_does_not_move_forward_after_failed_first_scan(store):
    store.seed_legacy({"1": set()}, as_of="2026-09-01")
    windows = []
    def fetch(c, start, end):
        windows.append((start, end))
        raise TimeoutError("unavailable")
    for day in (22, 23):
        scanner.collect(store, [company()], today=date(2026, 9, day), fetch=fetch)
    assert windows == [(date(2026, 8, 31), date(2026, 9, 6))] * 2


def test_persistence_precedes_delivery_and_failure_stops_it(store):
    store.seed_legacy({"1": set()})
    mail = sender()
    checkpoint = Mock(side_effect=RuntimeError("cannot persist"))
    with pytest.raises(RuntimeError, match="persist"):
        scanner.cycle(store, checkpoint, companies=[company()], today=date(2026, 9, 23),
                      fetch=lambda *args: [announcement()], sender=mail)
    assert store.pending_count() == 1
    mail.send.assert_not_called()


def test_checkpoint_after_send_failure_does_not_send_next_batch(store):
    store.seed_legacy({"1": set()})
    store.collect("1", "2026-09-23", [announcement("a"), announcement("b")])
    mail = sender()
    with pytest.raises(RuntimeError):
        scanner.deliver(store, Mock(side_effect=RuntimeError("push failed")), sender=mail, batch_size=1)
    assert mail.send.call_count == 1
    assert store.pending_count() == 1


def test_quota_is_persistent_and_batches_keep_all_items(store):
    store.seed_legacy({"1": set()})
    store.collect("1", "2026-09-23", [announcement(str(i)) for i in range(5)])
    mail = sender()
    result = scanner.deliver(store, lambda: None, sender=mail, batch_size=2, daily_limit=2)
    assert result == {"sent": 4, "emails": 2, "email_failed": False, "pending": 1}
    restored = AlertStore(":memory:")
    try:
        restored.restore(store.export())
        assert scanner.deliver(restored, lambda: None, sender=sender(), daily_limit=2)["sent"] == 0
    finally:
        restored.close()


def test_quiet_rerun_does_not_email_twice(store):
    store.seed_legacy({"1": set()})
    mail = sender()
    for _ in range(2):
        scanner.cycle(store, lambda: None, companies=[company()], today=date(2026, 9, 23),
                      fetch=lambda *args: [announcement()], sender=mail)
    assert mail.send.call_count == 1


@responses.activate
def test_bse_follows_pages_and_deduplicates():
    for ids in (["a", "b"], ["b", "c"]):
        responses.add(responses.GET, bse_client.API_URL,
                      json={"Table": [{"NEWSID": i} for i in ids], "Table1": [{"ROWCNT": 3}]})
    result = bse_client.fetch_announcements(date(2026, 9, 22), date(2026, 9, 23))
    assert [row["NEWSID"] for row in result] == ["a", "b", "c"]
    assert "pageno=2" in responses.calls[1].request.url


@pytest.mark.parametrize("second", [[{"NEWSID": "a"}], []])
@responses.activate
def test_bse_rejects_repeated_or_incomplete_pages(second):
    for rows in ([{"NEWSID": "a"}], second):
        responses.add(responses.GET, bse_client.API_URL, json={"Table": rows, "Table1": [{"ROWCNT": 2}]})
    with pytest.raises(ValueError):
        bse_client.fetch_today_announcements()


@responses.activate
def test_bse_without_count_fetches_until_empty():
    responses.add(responses.GET, bse_client.API_URL, json={"Table": [{"NEWSID": "a"}]})
    responses.add(responses.GET, bse_client.API_URL, json={"Table": []})
    assert len(bse_client.fetch_today_announcements()) == 1


@responses.activate
def test_http_retries_transient_response_and_rejects_bad_schema():
    responses.add(responses.GET, nse_client.API_URL, status=503)
    responses.add(responses.GET, nse_client.API_URL, json={"error": "blocked"})
    with pytest.raises(ValueError, match="missing"):
        nse_client.fetch_announcements("APS")
    assert len(responses.calls) == 2


@responses.activate
def test_http_does_not_retry_forbidden():
    import requests
    responses.add(responses.GET, nse_client.API_URL, status=403)
    with pytest.raises(requests.HTTPError):
        nse_client.fetch_announcements("APS")
    assert len(responses.calls) == 1


def test_nse_window_and_date_validation(monkeypatch):
    c = {**company(), "exchange": "NSE", "symbol": "APS"}
    monkeypatch.setattr(nse_client, "fetch_announcements", lambda *args: [
        {"seq_id": None, "sort_date": "2025-05-16 15:56:20"},
        {"seq_id": "old", "sort_date": "2026-09-20 12:00:00"},
        {"seq_id": "new", "sort_date": "2026-09-22 12:00:00"},
    ])
    assert [row["_id"] for row in scanner.fetch_window(c, date(2026, 9, 21), date(2026, 9, 23))] == ["new"]
    monkeypatch.setattr(nse_client, "fetch_announcements", lambda *args: [{"seq_id": "a", "sort_date": "bad"}])
    with pytest.raises(ValueError, match="invalid date"):
        scanner.fetch_window(c, date(2026, 9, 21), date(2026, 9, 23))


def test_digest_reuses_connection_and_includes_every_link(monkeypatch):
    monkeypatch.setenv("EMAIL_SENDER", "sender@example.com")
    monkeypatch.setenv("EMAIL_RECEIVER", "receiver@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "test")
    with patch("src.email_sender.smtplib.SMTP_SSL") as smtp:
        connection = smtp.return_value.__enter__.return_value
        connection.send_message.return_value = {}
        client = EmailSender()
        client.send([announcement("a"), announcement("b")])
        client.send([announcement("c")])
        client.close()
        assert smtp.call_count == 1
        assert connection.login.call_count == 1
        first = connection.send_message.call_args_list[0].args[0]
        assert "a.pdf" in first.get_content() and "b.pdf" in first.get_content()
        assert first["Message-ID"]


def test_git_checkpoint_roundtrip_and_conflicting_writer(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("time.sleep", lambda _: None)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    first = GitState(tmp_path / "first", str(bare))
    store = AlertStore(":memory:")
    restored = AlertStore(":memory:")
    try:
        store.seed_legacy({"1": {"old"}})
        first.save(store)
        second = GitState(tmp_path / "second", str(bare))
        assert second.restore(restored)
        assert restored.export() == store.export()
        store.collect("1", "2026-09-23", [announcement("a")])
        first.save(store)
        restored.collect("1", "2026-09-23", [announcement("b")])
        with pytest.raises(subprocess.CalledProcessError):
            second.save(restored)
        latest = GitState(tmp_path / "latest", str(bare))
        latest.restore(restored)
        assert restored.pending()[0]["news_id"] == "a"
    finally:
        store.close()
        restored.close()


def test_worker_returns_failure_after_checkpointed_partial_cycle(monkeypatch):
    monkeypatch.setattr(worker.subprocess, "check_output", lambda *args, **kwargs: "unused")
    monkeypatch.setattr(worker, "GitState", Mock(return_value=Mock(restore=Mock(return_value=True))))
    monkeypatch.setattr(worker, "code_changed", lambda: False)
    monkeypatch.setattr(worker, "cycle", lambda *args: {"failed": ["1"], "email_failed": False})
    sleep = Mock()
    monkeypatch.setattr(worker.time, "sleep", sleep)
    assert worker.run_worker(1) == 1
    assert sleep.call_count == 1


def test_worker_yields_when_deployment_changes(monkeypatch):
    monkeypatch.setattr(worker.subprocess, "check_output", lambda *args, **kwargs: "unused")
    monkeypatch.setattr(worker, "GitState", Mock(return_value=Mock(restore=Mock(return_value=True))))
    monkeypatch.setattr(worker, "code_changed", lambda: True)
    run = Mock()
    monkeypatch.setattr(worker, "cycle", run)
    assert worker.run_worker(1) == 0
    run.assert_not_called()


def test_collection_concurrency_is_bounded(store):
    import threading
    barrier = threading.Barrier(4, timeout=5)
    active = 0
    peak = 0
    lock = threading.Lock()
    def fetch(*args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait()
        with lock:
            active -= 1
        return []
    result = scanner.collect(store, [company(i) for i in range(8)], fetch=fetch, workers=4)
    assert not result["failed"]
    assert peak == 4


def test_simulated_5000_company_collection_and_outbox():
    # Functional scale check only: no live HTTP or SMTP capacity claim.
    store = AlertStore(":memory:")
    try:
        store.seed_legacy({str(i): set() for i in range(5000)})
        result = scanner.collect(store, [company(i) for i in range(5000)],
                                 fetch=lambda c, *args: [announcement(str(c["scrip_code"]))])
        assert not result["failed"]
        assert result["collected"] == store.pending_count() == 5000
        delivery = scanner.deliver(store, lambda: None, sender=sender())
        assert delivery["sent"] == 5000
        assert delivery["emails"] == 250
        assert store.pending_count() == 0
    finally:
        store.close()

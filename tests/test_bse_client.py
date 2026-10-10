from datetime import date

import responses
import pytest

from src.bse_client import API_URL, announcement_pdf_url, fetch_today_announcements


@pytest.fixture(autouse=True)
def no_live_pacing(monkeypatch):
    monkeypatch.setattr("src.http_client._pace", lambda host: None)


@responses.activate
def test_fetches_only_today():
    responses.add(
        responses.GET,
        API_URL,
        json={"Table": [{"NEWSID": "one"}], "Table1": [{"ROWCNT": 1}]},
        status=200,
    )

    result = fetch_today_announcements(today=date(2026, 7, 19))

    assert result == [{"NEWSID": "one"}]
    assert responses.calls[0].request.url.endswith(
        "strToDate=20260719&strType=C&subcategory=-1"
    )
    request_headers = responses.calls[0].request.headers
    assert request_headers["Origin"] == "https://www.bseindia.com"
    assert request_headers["Referer"] == "https://www.bseindia.com/"
    assert '"Google Chrome";v="153"' in request_headers["Sec-CH-UA"]
    assert "Chrome/153" in request_headers["User-Agent"]


def test_pdf_url():
    assert announcement_pdf_url({"ATTACHMENTNAME": "file.pdf"}).endswith("file.pdf")


@responses.activate
def test_placeholder_retries_same_page_and_recovers_without_losing_rows(monkeypatch):
    from src import bse_client
    sleeps = []
    monkeypatch.setattr(bse_client.time, "sleep", sleeps.append)
    for payload in [
        {"Table": [{"NEWSID": "first"}], "Table1": [{"ROWCNT": 2}]},
        {"Table": [{"Column1": 1}]},
        {"Table": [{"NEWSID": "second"}], "Table1": [{"ROWCNT": 2}]},
    ]:
        responses.add(responses.GET, API_URL, json=payload)
    result = fetch_today_announcements(today=date(2026, 10, 10))
    assert [row["NEWSID"] for row in result] == ["first", "second"]
    assert [call.request.params["pageno"] for call in responses.calls] == ["1", "2", "2"]
    assert sleeps == [2]


@responses.activate
def test_persistent_placeholder_fails_instead_of_claiming_empty(monkeypatch):
    from src import bse_client
    monkeypatch.setattr(bse_client.time, "sleep", lambda _: None)
    responses.add(responses.GET, API_URL, json={"Table": [{"Column1": 1}]})
    with pytest.raises(ValueError, match="placeholder"):
        fetch_today_announcements(today=date(2026, 10, 10))
    assert len(responses.calls) == 3


@responses.activate
def test_invalid_json_can_recover(monkeypatch):
    from src import bse_client
    monkeypatch.setattr(bse_client.time, "sleep", lambda _: None)
    responses.add(responses.GET, API_URL, body="temporarily unavailable", status=200)
    responses.add(responses.GET, API_URL, json={"Table": [], "Table1": [{"ROWCNT": 0}]})
    assert fetch_today_announcements(today=date(2026, 10, 10)) == []
    assert len(responses.calls) == 2

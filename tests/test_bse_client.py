from datetime import date

import responses

from src.bse_client import API_URL, announcement_pdf_url, fetch_today_announcements


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
    assert request_headers["Referer"] == "https://www.bseindia.com/corporates/ann"
    assert "Mozilla/5.0" in request_headers["User-Agent"]


def test_pdf_url():
    assert announcement_pdf_url({"ATTACHMENTNAME": "file.pdf"}).endswith("file.pdf")

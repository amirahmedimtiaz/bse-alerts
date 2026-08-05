from unittest.mock import patch

from src import main


def test_run_continues_when_a_company_fetch_fails():
    companies = [
        {
            "name": "Unavailable Company",
            "scrip_code": 1,
            "announcement_url": "https://example.com/unavailable",
        },
        {
            "name": "Available Company",
            "scrip_code": 2,
            "announcement_url": "https://example.com/available",
        },
    ]
    announcement = {"NEWSID": "new-id"}
    saved_state = {}

    def fetch(company, today):
        if company["name"] == "Unavailable Company":
            raise TimeoutError("upstream timeout")
        return [announcement]

    with (
        patch.object(main, "load_companies", return_value=companies),
        patch.object(main, "load_state", return_value={"1": set(), "2": set()}),
        patch.object(main, "fetch_for_company", side_effect=fetch),
        patch.object(main, "save_state", side_effect=saved_state.update),
    ):
        assert main.run(send_alerts=False) == 1

    assert saved_state == {"1": set(), "2": {"new-id"}}


def test_failed_email_is_retried_on_a_later_run():
    company = {
        "name": "Available Company",
        "scrip_code": 2,
        "announcement_url": "https://example.com/available",
    }
    announcement = {"NEWSID": "new-id"}
    saved_state = {}

    with (
        patch.object(main, "load_companies", return_value=[company]),
        patch.object(main, "load_state", return_value={"2": {"old-id"}}),
        patch.object(main, "fetch_for_company", return_value=[announcement]),
        patch.object(main, "send_announcement_email", side_effect=RuntimeError("SMTP down")),
        patch.object(main, "save_state", side_effect=saved_state.update),
    ):
        assert main.run() == 0

    assert saved_state == {"2": {"old-id"}}

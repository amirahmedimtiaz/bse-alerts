from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.bse_client import fetch_today_announcements as bse_fetch
from src.nse_client import fetch_today_announcements as nse_fetch
from src.main import (
    add_company_details,
    get_announcement_id,
    load_companies,
    state_key,
)


COMPANIES_PATH = Path(__file__).resolve().parents[1] / "companies.json"
INDIA_TZ = ZoneInfo("Asia/Kolkata")


@pytest.mark.integration
class TestAllCompanies:
    @pytest.mark.parametrize(
        "company",
        [pytest.param(c, id=c.get("name", "unknown")) for c in json.loads(COMPANIES_PATH.read_text())],
    )
    def test_company_returns_announcements(self, company):
        exchange = str(company.get("exchange", "BSE"))
        today = datetime.now(INDIA_TZ).date()
        if exchange == "NSE":
            result = nse_fetch(
                symbol=str(company["symbol"]),
                today=today,
                market_type=str(company.get("market_type", "sme")),
            )
        else:
            result = bse_fetch(scrip_code=int(company["scrip_code"]), today=today)

        assert isinstance(result, list), f"Expected list, got {type(result)}"

        if result:
            first = result[0]
            announcement_id = get_announcement_id(first, exchange)
            assert announcement_id, f"No ID field found for {company['name']}"

            enriched = add_company_details(first, company)
            assert enriched["_company_name"] == company["name"]
            assert enriched.get("_subject"), "Missing _subject"
            assert enriched.get("_page_url"), "Missing _page_url"
            assert enriched.get("_exchange") == exchange

    @pytest.mark.parametrize(
        "company",
        [pytest.param(c, id=c.get("name", "unknown")) for c in json.loads(COMPANIES_PATH.read_text())],
    )
    def test_state_key_format(self, company):
        key = state_key(company)
        exchange = str(company.get("exchange", "BSE"))
        if exchange == "NSE":
            assert key.startswith("NSE:"), f"NSE state key should start with 'NSE:', got {key}"
            assert company["symbol"] in key
        else:
            assert str(company["scrip_code"]) == key


@pytest.mark.integration
class TestClients:
    def test_bse_client_returns_valid_format(self):
        result = bse_fetch(scrip_code=540795)
        assert isinstance(result, list)
        if result:
            item = result[0]
            assert "NEWSID" in item
            assert "NEWSSUB" in item
            assert "SLONGNAME" in item

    def test_nse_client_returns_valid_format(self):
        result = nse_fetch(symbol="VMARCIND", market_type="sme")
        assert isinstance(result, list)
        if result:
            item = result[0]
            assert "seq_id" in item
            assert "desc" in item
            assert "sm_name" in item
            assert "sort_date" in item


@pytest.mark.integration
class TestPipeline:
    def test_bse_pipeline(self):
        today = datetime.now(INDIA_TZ).date()
        company = {
            "name": "Dynamic Cables Ltd",
            "scrip_code": 540795,
            "announcement_url": "https://example.com",
        }
        announcements = bse_fetch(scrip_code=540795, today=today)
        if not announcements:
            pytest.skip("No announcements today")
        enriched = add_company_details(announcements[0], company)
        assert enriched["_exchange"] == "BSE"
        assert enriched["_company_name"] == "Dynamic Cables Ltd"
        assert enriched.get("_pdf_url") is not None

    def test_nse_pipeline(self):
        today = datetime.now(INDIA_TZ).date()
        company = {
            "name": "V-Marc India Limited",
            "exchange": "NSE",
            "symbol": "VMARCIND",
            "market_type": "sme",
            "announcement_url": "https://example.com",
        }
        announcements = nse_fetch(symbol="VMARCIND", today=today, market_type="sme")
        if not announcements:
            pytest.skip("No announcements today")
        enriched = add_company_details(announcements[0], company)
        assert enriched["_exchange"] == "NSE"
        assert enriched["_company_name"] == "V-Marc India Limited"
        assert enriched.get("_pdf_url") is not None

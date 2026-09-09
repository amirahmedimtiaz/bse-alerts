import json
from pathlib import Path

from scripts.download_ganesh_green_bharat_earnings_call_transcripts import (
    is_earnings_call_transcript,
    period_label_from_text,
    save_pdf,
)
from src.main import state_key


def test_period_parser_normalizes_half_year_calls_to_quarter_labels():
    assert period_label_from_text("H1 FY '25 Earnings Conference Call") == "Q2_FY2025"
    assert period_label_from_text("H2 & FY26 Earnings Conference Call") == "Q4_FY2026"
    assert period_label_from_text("Q1 FY27 Earnings Conference Call") == "Q1_FY2027"


def test_transcript_filter_excludes_schedule_and_recording_filings():
    assert is_earnings_call_transcript(
        {
            "desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
            "attchmntText": "Ganesh Green Bharat Limited has informed the Exchange about Transcript",
            "attchmntFile": "https://example.test/transcriptofearningcall.pdf",
        }
    )
    assert not is_earnings_call_transcript(
        {
            "desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
            "attchmntText": "Audio recording of the Earnings Conference Call is available",
            "attchmntFile": "https://example.test/audio.mp3",
        }
    )


def test_save_pdf_never_overwrites_a_different_existing_file(tmp_path):
    destination = tmp_path / "Q1_Ganesh_Green_Bharat_Earnings_Call_Transcript.pdf"
    destination.write_bytes(b"%PDF-original")

    status, saved = save_pdf(b"%PDF-original", destination)
    assert status == "already exists"
    assert saved == destination

    status, saved = save_pdf(b"%PDF-new", destination)
    assert status == "downloaded"
    assert saved.name == "Q1_Ganesh_Green_Bharat_Earnings_Call_Transcript_2.pdf"
    assert destination.read_bytes() == b"%PDF-original"


def test_ganesh_green_bharat_is_in_the_nse_alert_configuration():
    companies_path = Path(__file__).resolve().parents[1] / "companies.json"
    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    matches = [company for company in companies if company.get("symbol") == "GGBL"]

    assert len(matches) == 1
    company = matches[0]
    assert company["name"] == "Ganesh Green Bharat Limited"
    assert company["exchange"] == "NSE"
    assert company["market_type"] == "sme"
    assert state_key(company) == "NSE:GGBL"

from scripts.download_insolation_energy_earnings_call_transcripts import (
    extract_period,
    is_earnings_call_transcript,
)


def test_extracts_insolation_energy_transcript_periods():
    assert extract_period("Q4 FY26 Earnings Conference Call") == (2026, "Q4")
    assert extract_period("Q3 9MFY26 Earnings Conference Call") == (2026, "Q3")
    assert extract_period("H2 & FY25 Earnings Conference Call") == (2025, "H2")


def test_period_parser_uses_header_period_before_later_outlook_period():
    text = "H2 & FY25 Earnings Conference Call\nThe outlook includes Q1FY26."
    assert extract_period(text) == (2025, "H2")


def test_excludes_audio_outcomes_from_transcript_matches():
    assert is_earnings_call_transcript(
        {
            "NEWSSUB": "Announcement under Regulation 30 (LODR)-Earnings Call Transcript",
            "SUBCATNAME": "Earnings Call Transcript",
        }
    )
    assert not is_earnings_call_transcript(
        {
            "NEWSSUB": "Announcement under Regulation 30 (LODR)-Analyst / Investor Meet - Outcome",
            "MORE": "The audio recording of the Earnings Conference Call is available.",
        }
    )

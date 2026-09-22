from datetime import date

from scripts.download_mv_electrosystems_investor_materials import (
    MAX_WINDOW_DAYS,
    date_windows,
    is_annual_report,
    is_earnings_call_transcript,
    is_investor_presentation,
    period_label_from_text,
)


def test_bse_windows_are_bounded_and_cover_the_requested_range():
    windows = date_windows(date(2024, 1, 1), date(2026, 1, 1))

    assert windows[0][1] == date(2026, 1, 1)
    assert windows[-1][0] == date(2024, 1, 1)
    assert all((end - start).days <= MAX_WINDOW_DAYS for start, end in windows)
    assert all(
        earlier[0] == later[1]
        for earlier, later in zip(windows, windows[1:])
    )


def test_material_filters_use_bse_categories_and_exclude_audio_only_filings():
    assert is_annual_report(
        {"SUBCATNAME": "Reg. 34 (1) Annual Report"}
    )
    assert is_investor_presentation(
        {"SUBCATNAME": "Investor Presentation"}
    )
    assert is_earnings_call_transcript(
        {"SUBCATNAME": "Earnings Call Transcript"}
    )
    assert not is_earnings_call_transcript(
        {
            "SUBCATNAME": "Analyst / Investor Meet",
            "MORE": "Audio recording of the Earnings Conference Call is available.",
        }
    )


def test_period_parser_handles_annual_and_quarter_labels():
    assert period_label_from_text("MV Electrosystems Annual Report 2025-26") == "FY2026"
    assert period_label_from_text("Q1 FY2027 Earnings Call Transcript") == "Q1_FY2027"

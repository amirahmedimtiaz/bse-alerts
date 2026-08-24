#!/usr/bin/env python3
"""Download Premier Energies' BSE financial-results filings from FY2021 onward.

The BSE result feed contains the official quarterly and year-end financial
results.  This script reuses the repository's paginated BSE history/downloader
helpers, identifies the fiscal period from each PDF, and saves the filings to
``~/Investing/Premier Energies Ltd`` by default.

Interim filings are labelled ``Unaudited`` and Q4/year-end filings are labelled
``Audited`` based on the filing's opening pages.  To download only audited
filings, pass ``--only-audited``.

Run from the repository root::

    python scripts/download_premier_energies_financial_results.py
    python scripts/download_premier_energies_financial_results.py --only-audited
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .download_premier_energies_earnings_call_transcripts import (
        ANNOUNCEMENTS_URL,
        API_URL,
        DEFAULT_OUTPUT_DIR,
        SCRIP_CODE,
        announcement_date,
        announcement_text,
        attachment_urls,
        build_session,
        download_pdf,
        extract_period,
        extract_pdf_text,
        fetch_announcements,
        save_pdf,
        sha256,
    )
except ImportError:
    # Supports direct execution with ``python scripts/<this-file>.py``.
    from download_premier_energies_earnings_call_transcripts import (
        ANNOUNCEMENTS_URL,
        API_URL,
        DEFAULT_OUTPUT_DIR,
        SCRIP_CODE,
        announcement_date,
        announcement_text,
        attachment_urls,
        build_session,
        download_pdf,
        extract_period,
        extract_pdf_text,
        fetch_announcements,
        save_pdf,
        sha256,
    )


DEFAULT_START_FISCAL_YEAR = 2021
AUDIT_STATUS_PATTERN = re.compile(r"\bun[\s-]*audited\b", re.IGNORECASE)
AUDITED_PATTERN = re.compile(r"\baudited\b", re.IGNORECASE)


@dataclass(frozen=True)
class FinancialResult:
    announcement: dict[str, Any]
    content: bytes
    source_url: str
    fiscal_year: int
    quarter: str
    audit_status: str
    page_count: int

    @property
    def period_label(self) -> str:
        return f"{self.quarter}_FY{self.fiscal_year}"


def is_financial_results_filing(announcement: dict[str, Any]) -> bool:
    """Select BSE result filings, excluding board notices and presentations."""

    category = str(announcement.get("CATEGORYNAME") or "").strip().lower()
    subcategory = str(announcement.get("SUBCATNAME") or "").strip().lower()
    return category == "result" or subcategory == "financial results"


def classify_audit_status(
    announcement: dict[str, Any], pdf_text: str
) -> str:
    """Classify the filing from the first audit-status statement."""

    evidence = "\n".join(
        (
            pdf_text[:6_000],
            announcement_text(announcement),
        )
    )
    unaudited_match = AUDIT_STATUS_PATTERN.search(evidence)
    audited_match = AUDITED_PATTERN.search(evidence)
    if unaudited_match and (
        audited_match is None or unaudited_match.start() < audited_match.start()
    ):
        return "Unaudited"
    if audited_match:
        return "Audited"
    return "Unspecified"


def make_result(
    session: Any,
    announcement: dict[str, Any],
    start_fiscal_year: int,
) -> FinancialResult | None:
    urls = attachment_urls(announcement)
    if not urls:
        return None

    content, source_url = download_pdf(session, urls)
    pdf_text, page_count = extract_pdf_text(content)
    fiscal_year, quarter = extract_period(pdf_text)
    if fiscal_year is None or quarter is None:
        fallback_year, fallback_quarter = extract_period(announcement_text(announcement))
        fiscal_year = fiscal_year or fallback_year
        quarter = quarter or fallback_quarter

    if fiscal_year is None or quarter is None or fiscal_year < start_fiscal_year:
        return None

    return FinancialResult(
        announcement=announcement,
        content=content,
        source_url=source_url,
        fiscal_year=fiscal_year,
        quarter=quarter,
        audit_status=classify_audit_status(announcement, pdf_text),
        page_count=page_count,
    )


def destination_for(result: FinancialResult, output_dir: Path) -> Path:
    return output_dir / (
        f"{result.period_label}_Premier_Energies_Financial_Results_"
        f"{result.audit_status}.pdf"
    )


def write_manifest(
    output_dir: Path,
    start_fiscal_year: int,
    only_audited: bool,
    announcements_count: int,
    candidates_count: int,
    results: list[dict[str, Any]],
    skipped_candidates: list[dict[str, str]],
) -> None:
    notes = [
        "Fiscal-year labels use the ending year; for example, FY2021 means the fiscal year ended March 31, 2021.",
        "Interim quarterly filings are retained and labelled Unaudited; year-end Q4 filings are labelled Audited.",
        "The result PDF is the BSE filing and may contain both standalone and consolidated statements plus review/audit reports.",
    ]
    earliest_fiscal_year = min(
        (int(item["fiscal_year"]) for item in results),
        default=None,
    )
    if earliest_fiscal_year is None:
        notes.append(
            f"No BSE financial-results filing was returned from FY{start_fiscal_year} onward."
        )
    elif earliest_fiscal_year > start_fiscal_year:
        notes.append(
            f"No BSE Result/Financial Results filing was returned before FY{earliest_fiscal_year} for the requested range."
        )

    manifest = {
        "company": "Premier Energies Ltd",
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "api_url": API_URL,
        "retrieved_on": date.today().isoformat(),
        "start_fiscal_year": start_fiscal_year,
        "only_audited": only_audited,
        "selection": "CATEGORYNAME=Result or SUBCATNAME=Financial Results",
        "historical_announcements": announcements_count,
        "financial_results_candidates": candidates_count,
        "matched_results": len(results),
        "results": results,
        "skipped_candidates": skipped_candidates,
        "notes": notes,
    }
    (output_dir / "financial_results_sources.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )


def write_html_index(
    output_dir: Path,
    result_rows: list[dict[str, Any]],
    start_fiscal_year: int,
) -> None:
    rows = []
    for item in result_rows:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['period'])}</td>"
            f"<td>{html.escape(item['audit_status'])}</td>"
            f"<td>{html.escape(item['filed_date'] or 'Unknown')}</td>"
            f"<td><a href=\"{html.escape(item['local_file'])}\">PDF</a></td>"
            f"<td><a href=\"{html.escape(item['source_url'])}\">BSE attachment</a></td>"
            "</tr>"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Premier Energies financial results</title>
  <style>body {{ font: 16px system-ui, sans-serif; margin: 2rem; }} table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #ccc; padding: .5rem .75rem; text-align: left; }} </style>
</head>
<body>
  <h1>Premier Energies Ltd — financial results</h1>
  <p>Collected from the <a href="{html.escape(ANNOUNCEMENTS_URL)}">BSE corporate-announcements page</a> on {html.escape(date.today().isoformat())}. Coverage starts at FY{start_fiscal_year} and includes the latest available result filing.</p>
  <table>
    <thead><tr><th>Period</th><th>Status</th><th>BSE filing date</th><th>Local file</th><th>Source</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p>The machine-readable source log is in <a href="financial_results_sources.json">financial_results_sources.json</a>.</p>
</body>
</html>
"""
    (output_dir / "financial_results.html").write_text(document)


def run(
    start_fiscal_year: int,
    output_dir: Path,
    only_audited: bool,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = build_session()
    announcements = fetch_announcements(session)
    candidates = [a for a in announcements if is_financial_results_filing(a)]
    print(
        f"BSE returned {len(announcements)} historical announcements; "
        f"{len(candidates)} are financial-results filings."
    )

    result_rows: list[dict[str, Any]] = []
    skipped_candidates: list[dict[str, str]] = []
    seen_attachments: set[str] = set()
    for announcement in candidates:
        attachment_name = str(announcement.get("ATTACHMENTNAME") or "").strip()
        if not attachment_name or attachment_name in seen_attachments:
            continue
        seen_attachments.add(attachment_name)

        try:
            result = make_result(session, announcement, start_fiscal_year)
        except Exception as exc:
            skipped_candidates.append(
                {"attachment": attachment_name, "reason": str(exc)}
            )
            print(f"Could not inspect {attachment_name}: {exc}")
            continue
        if result is None:
            skipped_candidates.append(
                {
                    "attachment": attachment_name,
                    "reason": "PDF period was unavailable or before the requested fiscal year",
                }
            )
            continue
        if only_audited and result.audit_status != "Audited":
            skipped_candidates.append(
                {
                    "attachment": attachment_name,
                    "reason": f"Filing classified as {result.audit_status}; --only-audited was requested",
                }
            )
            continue

        destination = destination_for(result, output_dir)
        status, saved_to = save_pdf(result.content, destination)
        filed = announcement_date(announcement)
        result_rows.append(
            {
                "period": result.period_label,
                "fiscal_year": result.fiscal_year,
                "quarter": result.quarter,
                "audit_status": result.audit_status,
                "filed_date": filed.isoformat() if filed else None,
                "announcement_id": str(announcement.get("NEWSID") or ""),
                "announcement_subject": announcement.get("NEWSSUB", ""),
                "headline": announcement.get("HEADLINE", ""),
                "attachment_name": attachment_name,
                "source_url": result.source_url,
                "local_file": saved_to.name,
                "sha256": sha256(result.content),
                "pages": result.page_count,
            }
        )
        filed_label = f" filed {filed:%Y-%m-%d}" if filed else ""
        print(
            f"{status}: {saved_to} [{result.audit_status}]{filed_label}"
        )

    result_rows.sort(key=lambda item: (item["fiscal_year"], item["quarter"]))
    write_manifest(
        output_dir,
        start_fiscal_year,
        only_audited,
        len(announcements),
        len(candidates),
        result_rows,
        skipped_candidates,
    )
    write_html_index(output_dir, result_rows, start_fiscal_year)
    print(f"Matched {len(result_rows)} financial-results filing(s) in {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-fiscal-year",
        type=int,
        default=DEFAULT_START_FISCAL_YEAR,
        help=f"First fiscal year to include (default: {DEFAULT_START_FISCAL_YEAR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--only-audited",
        action="store_true",
        help="Save only filings classified as audited",
    )
    args = parser.parse_args()
    if args.start_fiscal_year < 2000:
        parser.error("--start-fiscal-year must be a four-digit year from 2000 onward")
    return run(
        args.start_fiscal_year,
        args.output_dir.expanduser(),
        args.only_audited,
    )


if __name__ == "__main__":
    raise SystemExit(main())

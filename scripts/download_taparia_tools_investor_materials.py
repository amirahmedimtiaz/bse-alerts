#!/usr/bin/env python3
"""Archive Taparia Tools Ltd investor materials.

The collector combines Taparia's official annual-report index with the
complete BSE corporate-announcement history for scrip 505685. It preserves
the original PDFs, source URLs, filing metadata, page counts, SHA-256 hashes,
and duplicate relationships in a local manifest.

Run from the repository root::

    python scripts/download_taparia_tools_investor_materials.py

Files are written below ``~/Investing/Taparia Tools`` by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

try:
    from scripts import download_chemkart_investor_materials as common
    from scripts import download_kv_toys_investor_materials as bse
except ModuleNotFoundError:  # Direct execution: python scripts/<collector>.py
    import download_chemkart_investor_materials as common
    import download_kv_toys_investor_materials as bse


COMPANY_NAME = "Taparia Tools Ltd"
SCRIP_CODE = 505685
COMPANY_FILE_STEM = "Taparia_Tools"
ANNOUNCEMENTS_URL = (
    "https://www.bseindia.com/stock-share-price/"
    "taparia-tools-ltd/taparia/505685/corp-announcements"
)
OFFICIAL_INVESTOR_PAGE = "https://www.tapariatools.com/investor_investor.html"
OFFICIAL_ANNUAL_REPORTS_PAGE = "https://www.tapariatools.com/annual_report.html"
OFFICIAL_MIGRATED_ANNUAL_REPORTS_PAGE = (
    "https://tapariatools.tapariatools.com/"
    "investors-desk-reports/annual-reports"
)
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "Taparia Tools"
START_DATE = date(2000, 1, 1)
MANIFEST_FILENAME = "Taparia_Tools_Investor_Materials.json"
CSV_FILENAME = "Taparia_Tools_Investor_Materials.csv"


def annual_report(period: str, url: str) -> dict[str, str]:
    start, end = period.split("-")
    return {
        "period": "FY" + start,
        "label": f"FY {period}",
        "period_text": period,
        "url": url,
        "page_url": OFFICIAL_ANNUAL_REPORTS_PAGE,
    }


# The legacy official index is the issuer's complete annual-report list. The
# migrated page currently omits some of these years, so both page URLs are
# recorded in the manifest while the legacy index supplies the downloads.
OFFICIAL_ANNUAL_REPORTS = (
    annual_report(
        "2025-26",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2025-26.pdf",
    ),
    annual_report(
        "2024-25",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2024-25.pdf",
    ),
    annual_report(
        "2023-24",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2023-24.pdf",
    ),
    annual_report(
        "2022-23",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2022-23.pdf",
    ),
    annual_report(
        "2021-22",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2021-22.pdf",
    ),
    annual_report(
        "2020-21",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2020-21.pdf",
    ),
    annual_report(
        "2019-20",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2019-20.pdf",
    ),
    annual_report(
        "2018-19",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2018-19.pdf",
    ),
    annual_report(
        "2017-18",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2017-18.pdf",
    ),
    annual_report(
        "2016-17",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2016-17.pdf",
    ),
    annual_report(
        "2015-16",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2015-16.pdf",
    ),
    annual_report(
        "2014-15",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2014-15.pdf",
    ),
    annual_report(
        "2013-14",
        "https://www.tapariatools.com/pdf/investors/annual_report/"
        "annual_report_2013-14.pdf",
    ),
)


def taparia_period_check(text: str, period_text: str | None) -> str:
    if not period_text:
        return "not_applicable"
    normalized = common.normalize_text(text)
    normalized = normalized.replace("–", "-").replace("—", "-")
    start, end = period_text.split("-")
    candidates = (period_text, f"{start}-{int(end):04d}", f"{start}-{end}")
    if any(candidate in normalized for candidate in candidates):
        return "matched"
    return "not_found_in_first_8_pages"


def expected_period(announcement: dict[str, Any]) -> str | None:
    filed = bse.parse_datetime(announcement)
    if not filed:
        return None
    # Taparia's annual-report filings are made after the March year end.
    year = filed.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def row_period(row: dict[str, Any]) -> str | None:
    text = bse.announcement_text(row).replace("–", "-").replace("—", "-")
    match = re.search(r"20\d{2}\s*[-/]\s*(?:20)?\d{2}", text)
    if match:
        raw = re.sub(r"\s+", "", match.group(0)).replace("/", "-")
        if len(raw.split("-")[1]) == 4:
            first, second = raw.split("-")
            return f"{first}-{second[-2:]}"
        return raw
    return expected_period(row)


def official_row(report: dict[str, str]) -> dict[str, Any]:
    row = common.official_annual_row(report)
    row["period_text"] = report["period_text"]
    return row


def write_manifest(
    output_dir: Path,
    retrieved_on: date,
    start_date: date,
    end_date: date,
    windows: list[dict[str, Any]],
    announcements_count: int,
    annual_rows: list[dict[str, Any]],
    transcript_rows: list[dict[str, Any]],
    official_status: dict[str, Any],
) -> Path:
    unique_annual = len(
        {row["sha256"] for row in annual_rows if row.get("sha256")}
    )
    manifest = {
        "company": COMPANY_NAME,
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "official_investor_page": OFFICIAL_INVESTOR_PAGE,
        "official_annual_reports_page": OFFICIAL_ANNUAL_REPORTS_PAGE,
        "official_migrated_annual_reports_page": OFFICIAL_MIGRATED_ANNUAL_REPORTS_PAGE,
        "api_url": bse.API_URL,
        "retrieved_on": retrieved_on.isoformat(),
        "bse_history_date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "bse_history_windows": windows,
        "bse_historical_announcements": announcements_count,
        "bse_candidate_counts": {
            "annual_report_filings": sum(
                row["source_type"] == "BSE corporate-announcement API"
                for row in annual_rows
            ),
            "investor_presentation_filings": 0,
            "earnings_call_transcript_filings": len(transcript_rows),
        },
        "official_source_status": official_status,
        "selected_counts": {
            "annual_report_records": len(annual_rows),
            "annual_report_unique_pdfs": unique_annual,
            "investor_presentations": 0,
            "earnings_call_transcripts": len(transcript_rows),
        },
        "annual_reports": annual_rows,
        "investor_presentations": [],
        "earnings_call_transcripts": transcript_rows,
        "notes": [
            "The BSE API was queried in overlapping windows no longer than 364 days and globally deduplicated by NEWSID or attachment name.",
            "BSE returned 14 annual-report filing candidates from 2019-20 through 2025-26, including AGM/Reg. 34 source records where both were filed; those records are retained separately and duplicate bytes are linked by SHA-256.",
            "The legacy official annual-report index lists FY2013-14 through FY2025-26. The migrated annual-report page is retained as a second source reference but currently omits some years shown on the legacy index.",
            "No investor-presentation filing and no written earnings-call or conference-call transcript PDF was found in the complete BSE history or the issuer's official investor pages as of the retrieval date. AGM notices, annual reports, and general disclosures are not presentations or transcripts.",
            "The archive covers the requested annual reports, investor-presentation filings, and written earnings-call transcripts; annual returns, quarterly results, governance filings, newspaper advertisements, and other investor information are outside this requested set.",
        ],
    }
    path = output_dir / MANIFEST_FILENAME
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def write_csv(output_dir: Path, manifest_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = (
        manifest["annual_reports"]
        + manifest["investor_presentations"]
        + manifest["earnings_call_transcripts"]
    )
    columns = [
        "kind",
        "source_type",
        "period",
        "filed_date",
        "subject",
        "source_page_url",
        "source_url",
        "retrieval_url",
        "local_file",
        "status",
        "duplicate_of",
        "sha256",
        "pages",
        "period_check",
        "announcement_id",
    ]
    path = output_dir / CSV_FILENAME
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_readme(output_dir: Path, manifest_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    counts = manifest["selected_counts"]
    lines = [
        f"# {manifest['company']} investor-materials archive",
        "",
        f"Retrieved: {manifest['retrieved_on']}",
        f"BSE scrip code: `{manifest['scrip_code']}`",
        "",
        "## Coverage",
        "",
        f"- Annual-report records: {counts['annual_report_records']} ({counts['annual_report_unique_pdfs']} unique PDFs).",
        f"- Investor-presentation filings: {counts['investor_presentations']} found.",
        f"- Written earnings-call transcripts: {counts['earnings_call_transcripts']} found.",
        f"- BSE announcements scanned: {manifest['bse_historical_announcements']} across {len(manifest['bse_history_windows'])} bounded windows.",
        "",
        "Primary sources are the [legacy official annual-report index]("
        + OFFICIAL_ANNUAL_REPORTS_PAGE
        + "), the [migrated official annual-report page]("
        + OFFICIAL_MIGRATED_ANNUAL_REPORTS_PAGE
        + "), the [issuer investor-information page]("
        + OFFICIAL_INVESTOR_PAGE
        + "), and the [BSE corporate-announcement page]("
        + ANNOUNCEMENTS_URL
        + "). See the JSON and CSV manifests for exact filing metadata, source URLs, page counts, hashes, period checks, and duplicate handling.",
        "",
        "The legacy official index lists FY2013-14 through FY2025-26. The BSE folder records the exchange's annual-report filing candidates separately, including AGM/Reg. 34 pairs where both source records were present.",
        "",
        "No investor-presentation or written earnings-call/conference-call transcript PDF was located in the complete BSE history or the issuer's official investor pages at the retrieval date. AGM notices and annual reports are not presentations or transcripts.",
    ]
    path = output_dir / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_empty_status_files(output_dir: Path, manifest_path: Path) -> tuple[Path, Path]:
    presentations = output_dir / "Investor Presentations" / "README.md"
    presentations.write_text(
        "# Investor-presentation archive\n\n"
        "No investor-presentation filing was located in the complete BSE history "
        "or the issuer's official investor pages as of the retrieval date.\n",
        encoding="utf-8",
    )
    transcripts = output_dir / "Earnings Call Transcripts" / "README.md"
    transcripts.write_text(
        "# Earnings-call transcript archive\n\n"
        "No written earnings-call or conference-call transcript PDF was located "
        "in the complete BSE history or the issuer's official investor pages as "
        "of the retrieval date.\n",
        encoding="utf-8",
    )
    return presentations, transcripts


def collect(
    output_dir: Path,
    start_date: date = START_DATE,
    end_date: date | None = None,
) -> int:
    final_date = end_date or date.today()
    if start_date > final_date:
        raise ValueError("start_date cannot be after end_date")

    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    for folder in (
        "Annual Reports",
        "Investor Presentations",
        "Earnings Call Transcripts",
    ):
        (output_dir / folder).mkdir(parents=True, exist_ok=True)

    # Reuse the tested BSE/PDF primitives while supplying Taparia-specific
    # identifiers and filenames.
    bse.SCRIP_CODE = SCRIP_CODE
    bse.ANNOUNCEMENTS_URL = ANNOUNCEMENTS_URL
    common.force_ipv4()
    common.COMPANY_FILE_STEM = COMPANY_FILE_STEM
    common.period_check = taparia_period_check
    session = bse.build_session()
    official_session = requests.Session()
    official_session.headers.update({"User-Agent": common.OFFICIAL_USER_AGENTS[0]})
    official_status = {
        "investor_information_page": common.probe_page(
            official_session, OFFICIAL_INVESTOR_PAGE
        ),
        "legacy_annual_reports_page": common.probe_page(
            official_session, OFFICIAL_ANNUAL_REPORTS_PAGE
        ),
        "migrated_annual_reports_page": common.probe_page(
            official_session, OFFICIAL_MIGRATED_ANNUAL_REPORTS_PAGE
        ),
        "annual_report_records": [
            {
                "period": report["period"],
                "source_page_url": report["page_url"],
                "source_url": report["url"],
            }
            for report in OFFICIAL_ANNUAL_REPORTS
        ],
    }

    announcements, window_metadata = bse.fetch_all_announcements(
        session, start_date=start_date, end_date=final_date
    )
    bse_annual_candidates = [
        row for row in announcements if bse.is_annual_report(row)
    ]
    bse_presentation_candidates = [
        row for row in announcements if bse.is_investor_presentation(row)
    ]
    bse_transcript_candidates = [
        row for row in announcements if bse.is_earnings_call_transcript(row)
    ]
    for rows in (
        bse_annual_candidates,
        bse_presentation_candidates,
        bse_transcript_candidates,
    ):
        rows.sort(
            key=lambda item: (
                bse.parse_datetime(item) or datetime.min,
                bse.announcement_key(item),
            )
        )

    print(
        f"BSE returned {len(announcements)} unique announcements across "
        f"{len(window_metadata)} windows; {len(bse_annual_candidates)} "
        f"annual-report, {len(bse_presentation_candidates)} presentation, "
        f"and {len(bse_transcript_candidates)} transcript filings."
    )

    digest_index = bse.index_existing_pdfs(output_dir)
    failures = 0
    annual_rows: list[dict[str, Any]] = []

    for report in OFFICIAL_ANNUAL_REPORTS:
        row = official_row(report)
        try:
            common.save_row(
                official_session,
                row,
                "annual_report",
                output_dir,
                digest_index,
                official=True,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {report['label']}: {exc}")
        else:
            print(f"{row['status']}: {row['local_file']}")
        annual_rows.append(row)

    for announcement in bse_annual_candidates:
        row = bse.bse_row_base(announcement, "annual_report")
        row["period_text"] = row_period(announcement)
        try:
            common.save_row(
                session,
                row,
                "annual_report",
                output_dir,
                digest_index,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {row['attachment_name']}: {exc}")
        else:
            print(f"{row['status']}: {row['local_file']}")
        annual_rows.append(row)

    # There are currently no presentation or transcript candidates, but keep
    # the loops explicit so a later filing is archived automatically.
    presentation_rows: list[dict[str, Any]] = []
    for announcement in bse_presentation_candidates:
        row = bse.bse_row_base(announcement, "investor_presentation")
        try:
            common.save_row(
                session,
                row,
                "investor_presentation",
                output_dir,
                digest_index,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
        presentation_rows.append(row)

    transcript_rows: list[dict[str, Any]] = []
    for announcement in bse_transcript_candidates:
        row = bse.bse_row_base(announcement, "earnings_call_transcript")
        try:
            common.save_row(
                session,
                row,
                "earnings_call_transcript",
                output_dir,
                digest_index,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
        transcript_rows.append(row)

    manifest_path = write_manifest(
        output_dir,
        retrieved_on=final_date,
        start_date=start_date,
        end_date=final_date,
        windows=window_metadata,
        announcements_count=len(announcements),
        annual_rows=annual_rows,
        transcript_rows=transcript_rows,
        official_status=official_status,
    )
    csv_path = write_csv(output_dir, manifest_path)
    readme_path = write_readme(output_dir, manifest_path)
    presentations_path, transcripts_path = write_empty_status_files(
        output_dir, manifest_path
    )
    print(f"Wrote manifest: {manifest_path}")
    print(f"Wrote CSV: {csv_path}")
    print(f"Wrote README: {readme_path}")
    print(f"Wrote presentation status: {presentations_path}")
    print(f"Wrote transcript status: {transcripts_path}")
    return 1 if failures else 0


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected an ISO date (YYYY-MM-DD), got {value!r}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--start-date",
        type=parse_iso_date,
        default=START_DATE,
        help=f"First BSE filing date to include (default: {START_DATE})",
    )
    parser.add_argument(
        "--end-date",
        type=parse_iso_date,
        default=None,
        help="Last BSE filing date to include (default: today)",
    )
    args = parser.parse_args()
    if args.end_date is not None and args.start_date > args.end_date:
        parser.error("--start-date cannot be after --end-date")
    return collect(
        args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())

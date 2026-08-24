#!/usr/bin/env python3
"""Download Premier Energies' BSE earnings-call transcripts from FY2025 onward.

The BSE corporate-announcement API is paginated.  This script walks the full
announcement history, selects transcript filings, identifies the fiscal period
from the PDF text, and saves the PDFs under ``~/Investing/Premier Energies Ltd``
by default.

Run from the repository root::

    python scripts/download_premier_energies_earnings_call_transcripts.py

Use ``--output-dir`` to choose another destination or ``--start-fiscal-year``
to reuse the downloader for a later starting year.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader


SCRIP_CODE = 544238
API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
PDF_BASE_URLS = (
    "https://www.bseindia.com/xml-data/corpfiling/AttachLive/",
    "https://www.bseindia.com/xml-data/corpfiling/AttachHis/",
)
ANNOUNCEMENTS_URL = (
    "https://www.bseindia.com/stock-share-price/"
    "premier-energies-ltd/premierene/544238/corp-announcements"
)
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "Premier Energies Ltd"
COMPANY_FILE_STEM = "Premier_Energies"
DEFAULT_START_FISCAL_YEAR = 2025
START_DATE = date(2000, 1, 1)
USER_AGENT = "premier-energies-earnings-call-downloader/1.0"
PDF_INSPECTION_CHARS = 12_000

MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
MONTH_PATTERN = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"

QUARTER_FY_PATTERN = re.compile(
    r"\bQ([1-4])\s*(?:(?:&|and)\s*)?"
    r"(?:(?:H[12]|[369]M|(?:nine|six|three)\s+months?)\s+)?"
    r"F\.?\s*Y\.?\s*['’]?(20\d{2}|\d{2})(?:\s*[-/]\s*(\d{2,4}))?\b",
    re.IGNORECASE,
)
FISCAL_YEAR_PATTERN = re.compile(
    r"\bF\.?\s*Y\.?\s*['’]?(20\d{2}|\d{2})(?:\s*[-/]\s*(\d{2,4}))?\b",
    re.IGNORECASE,
)
PERIOD_END_PATTERN = re.compile(
    rf"\b(?:quarter|half[- ]year|nine months|year)?\s*ended\s+"
    rf"(?:on\s+)?(?:({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})|"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})[,]?\s+(20\d{{2}}))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Transcript:
    announcement: dict[str, Any]
    content: bytes
    source_url: str
    fiscal_year: int
    quarter: str | None
    page_count: int

    @property
    def period_label(self) -> str:
        return f"{self.quarter}_FY{self.fiscal_year}" if self.quarter else f"FY{self.fiscal_year}"


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Referer": ANNOUNCEMENTS_URL,
            "User-Agent": USER_AGENT,
        }
    )
    return session


def fetch_announcements(session: requests.Session) -> list[dict[str, Any]]:
    """Fetch every historical BSE announcement for the company."""

    base_params = {
        "strCat": -1,
        "strPrevDate": START_DATE.strftime("%Y%m%d"),
        "strScrip": SCRIP_CODE,
        "strSearch": "P",
        "strToDate": date.today().strftime("%Y%m%d"),
        "strType": "C",
        "subcategory": -1,
    }
    announcements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    expected_count: int | None = None
    page = 1

    while True:
        response = session.get(
            API_URL,
            params={**base_params, "pageno": page},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("Table", [])
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected BSE response: Table is not a list")

        if expected_count is None:
            table1 = payload.get("Table1") or []
            if table1 and isinstance(table1[0], dict):
                try:
                    expected_count = int(table1[0].get("ROWCNT", 0))
                except (TypeError, ValueError):
                    expected_count = None

        before_count = len(announcements)
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("NEWSID") or row.get("ATTACHMENTNAME") or "")
            if row_id and row_id in seen_ids:
                continue
            if row_id:
                seen_ids.add(row_id)
            announcements.append(row)

        if (
            not rows
            or len(announcements) == before_count
            or (expected_count is not None and len(announcements) >= expected_count)
        ):
            break
        page += 1

    return announcements


def announcement_text(announcement: dict[str, Any]) -> str:
    return " ".join(
        str(announcement.get(key) or "")
        for key in ("NEWSSUB", "HEADLINE", "MORE", "SUBCATNAME")
    )


def is_transcript_filing(announcement: dict[str, Any]) -> bool:
    text = announcement_text(announcement).lower()
    if "transcript" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "earnings",
            "conference",
            "concall",
            "financial result",
            "financial performance",
            "quarter",
            "year ended",
        )
    )


def normalize_year(value: str) -> int:
    year = int(value)
    return year + 2000 if year < 100 else year


def extract_period(text: str) -> tuple[int | None, str | None]:
    """Extract the fiscal year and quarter from the PDF's opening text."""

    head = text[:PDF_INSPECTION_CHARS]
    quarter_match = QUARTER_FY_PATTERN.search(head)
    if quarter_match:
        fiscal_year = normalize_year(quarter_match.group(3) or quarter_match.group(2))
        return fiscal_year, f"Q{quarter_match.group(1)}"

    fiscal_year_match = FISCAL_YEAR_PATTERN.search(head)
    if fiscal_year_match:
        fiscal_year = normalize_year(fiscal_year_match.group(2) or fiscal_year_match.group(1))
        return fiscal_year, None

    period_end = PERIOD_END_PATTERN.search(head)
    if not period_end:
        return None, None

    if period_end.group(1):
        month = MONTHS[period_end.group(1).lower()]
        year = int(period_end.group(3))
    else:
        month = MONTHS[period_end.group(5).lower()]
        year = int(period_end.group(6))

    quarter_by_month = {3: "Q4", 6: "Q1", 9: "Q2", 12: "Q3"}
    quarter = quarter_by_month.get(month)
    if quarter is None:
        return None, None
    fiscal_year = year if month <= 3 else year + 1
    return fiscal_year, quarter


def extract_pdf_text(content: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)


def attachment_urls(announcement: dict[str, Any]) -> list[str]:
    filename = str(announcement.get("ATTACHMENTNAME") or "").strip()
    if not filename or filename == "-":
        return []
    if filename.startswith(("http://", "https://")):
        return [filename]
    return [f"{base_url}{filename}" for base_url in PDF_BASE_URLS]


def download_pdf(session: requests.Session, urls: list[str]) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for url in urls:
        try:
            response = session.get(url, timeout=90)
            response.raise_for_status()
            content = response.content
            if not content.lstrip().startswith(b"%PDF"):
                content_type = response.headers.get("content-type", "unknown")
                raise RuntimeError(f"BSE attachment is not a PDF ({content_type})")
            return content, url
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(f"Could not download attachment from {urls}: {last_error}")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_pdf(content: bytes, destination: Path) -> tuple[str, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination.read_bytes()) == sha256(content):
            return "already exists", destination

        stem, suffix = destination.stem, destination.suffix
        index = 2
        while True:
            candidate = destination.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                destination = candidate
                break
            index += 1

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return "downloaded", destination


def destination_for(transcript: Transcript, output_dir: Path) -> Path:
    return output_dir / (
        f"{transcript.period_label}_{COMPANY_FILE_STEM}_"
        "Earnings_Call_Transcript.pdf"
    )


def announcement_date(announcement: dict[str, Any]) -> date | None:
    for key in ("NEWS_DT", "DT_TM", "News_submission_dt", "DissemDT"):
        value = str(announcement.get(key) or "")
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            continue
    return None


def make_transcript(
    session: requests.Session,
    announcement: dict[str, Any],
    start_fiscal_year: int,
) -> Transcript | None:
    urls = attachment_urls(announcement)
    if not urls:
        return None

    content, source_url = download_pdf(session, urls)
    text, page_count = extract_pdf_text(content)
    fiscal_year, quarter = extract_period(text)

    # A few filings put the period in the announcement rather than in the PDF.
    if fiscal_year is None:
        fiscal_year, quarter = extract_period(announcement_text(announcement))
    if fiscal_year is None or fiscal_year < start_fiscal_year:
        return None

    return Transcript(
        announcement=announcement,
        content=content,
        source_url=source_url,
        fiscal_year=fiscal_year,
        quarter=quarter,
        page_count=page_count,
    )


def write_manifest(
    output_dir: Path,
    start_fiscal_year: int,
    announcements_count: int,
    candidates_count: int,
    transcripts: list[dict[str, Any]],
    skipped_candidates: list[dict[str, str]],
) -> None:
    manifest = {
        "company": "Premier Energies Ltd",
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "api_url": API_URL,
        "retrieved_on": date.today().isoformat(),
        "start_fiscal_year": start_fiscal_year,
        "historical_announcements": announcements_count,
        "transcript_candidates": candidates_count,
        "matched_transcripts": len(transcripts),
        "transcripts": transcripts,
        "skipped_candidates": skipped_candidates,
        "notes": [
            "Fiscal year is the ending year: FY2025 means the year ended March 31, 2025.",
            "The BSE API history contained no Q1 FY2025 transcript filing; the first matching filing is Q2 FY2025.",
            "PDFs were checked against both BSE AttachLive and AttachHis attachment paths.",
        ],
    }
    (output_dir / "sources.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )


def write_html_index(output_dir: Path, transcript_rows: list[dict[str, Any]]) -> None:
    rows = []
    for item in transcript_rows:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['period'])}</td>"
            f"<td>{html.escape(item['filed_date'] or 'Unknown')}</td>"
            f"<td><a href=\"{html.escape(item['local_file'])}\">PDF</a></td>"
            f"<td><a href=\"{html.escape(item['source_url'])}\">BSE attachment</a></td>"
            "</tr>"
        )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Premier Energies earnings-call transcripts</title>
  <style>body {{ font: 16px system-ui, sans-serif; margin: 2rem; }} table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #ccc; padding: .5rem .75rem; text-align: left; }} </style>
</head>
<body>
  <h1>Premier Energies Ltd — earnings-call transcripts</h1>
  <p>Collected from the <a href="{html.escape(ANNOUNCEMENTS_URL)}">BSE corporate-announcements page</a> on {html.escape(date.today().isoformat())}. Coverage starts at FY2025 and includes all matched filings through the latest available filing.</p>
  <table>
    <thead><tr><th>Period</th><th>BSE filing date</th><th>Local file</th><th>Source</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p>The machine-readable source log is in <a href="sources.json">sources.json</a>.</p>
</body>
</html>
"""
    (output_dir / "index.html").write_text(document)


def run(start_fiscal_year: int, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = build_session()
    announcements = fetch_announcements(session)
    candidates = [a for a in announcements if is_transcript_filing(a)]
    print(
        f"BSE returned {len(announcements)} historical announcements; "
        f"{len(candidates)} are transcript candidates."
    )

    transcript_rows: list[dict[str, Any]] = []
    skipped_candidates: list[dict[str, str]] = []
    seen_attachments: set[str] = set()
    for announcement in candidates:
        attachment_name = str(announcement.get("ATTACHMENTNAME") or "").strip()
        if not attachment_name or attachment_name in seen_attachments:
            continue
        seen_attachments.add(attachment_name)

        try:
            transcript = make_transcript(session, announcement, start_fiscal_year)
        except Exception as exc:
            skipped_candidates.append(
                {"attachment": attachment_name, "reason": str(exc)}
            )
            print(f"Could not inspect {attachment_name}: {exc}")
            continue
        if transcript is None:
            skipped_candidates.append(
                {
                    "attachment": attachment_name,
                    "reason": "PDF period was unavailable or before the requested fiscal year",
                }
            )
            continue

        destination = destination_for(transcript, output_dir)
        status, saved_to = save_pdf(transcript.content, destination)
        filed = announcement_date(announcement)
        source_file = saved_to.name
        transcript_rows.append(
            {
                "period": transcript.period_label,
                "fiscal_year": transcript.fiscal_year,
                "quarter": transcript.quarter,
                "filed_date": filed.isoformat() if filed else None,
                "announcement_id": str(announcement.get("NEWSID") or ""),
                "announcement_subject": announcement.get("NEWSSUB", ""),
                "headline": announcement.get("HEADLINE", ""),
                "attachment_name": attachment_name,
                "source_url": transcript.source_url,
                "local_file": source_file,
                "sha256": sha256(transcript.content),
                "pages": transcript.page_count,
            }
        )
        print(f"{status}: {saved_to}{f' filed {filed:%Y-%m-%d}' if filed else ''}")

    transcript_rows.sort(key=lambda item: (item["fiscal_year"], item["quarter"] or ""))
    write_manifest(
        output_dir,
        start_fiscal_year,
        len(announcements),
        len(candidates),
        transcript_rows,
        skipped_candidates,
    )
    write_html_index(output_dir, transcript_rows)
    print(f"Matched {len(transcript_rows)} transcript(s) in {output_dir}")
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
    args = parser.parse_args()
    if args.start_fiscal_year < 2000:
        parser.error("--start-fiscal-year must be a four-digit year from 2000 onward")
    return run(args.start_fiscal_year, args.output_dir.expanduser())


if __name__ == "__main__":
    raise SystemExit(main())

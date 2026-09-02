#!/usr/bin/env python3
"""Download all Insolation Energy BSE earnings-call transcript PDFs.

The BSE corporate-announcement history is the source of truth.  This script
walks every history page, selects only filings identified as earnings-call
transcripts, downloads each PDF from BSE's live or historical attachment
archive, and writes a JSON audit manifest beside the PDFs.

Run from the repository root::

    python scripts/download_insolation_energy_earnings_call_transcripts.py

Files are saved to ``~/Investing/Insolation Energy`` by default.  Use
``--output-dir`` to choose another destination.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader


SCRIP_CODE = 543620
API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
PDF_BASE_URLS = (
    "https://www.bseindia.com/xml-data/corpfiling/AttachLive/",
    "https://www.bseindia.com/xml-data/corpfiling/AttachHis/",
)
ANNOUNCEMENTS_URL = (
    "https://www.bseindia.com/stock-share-price/"
    "insolation-energy-ltd/INA/543620/corp-announcements"
)
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "Insolation Energy"
COMPANY_FILE_STEM = "Insolation_Energy"
START_DATE = date(2000, 1, 1)
USER_AGENT = "insolation-energy-earnings-call-downloader/1.0"
PDF_INSPECTION_CHARS = 12_000
MANIFEST_FILENAME = "Insolation_Energy_Earnings_Call_Transcripts.json"

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
    r"(?:(?:H[12]|[369]M|(?:nine|six|three)\s+months?)\s*)?"
    r"F\.?\s*Y\.?\s*['’]?(20\d{2}|\d{2})"
    r"(?:\s*[-/]\s*(\d{2,4}))?\b",
    re.IGNORECASE,
)
HALF_FY_PATTERN = re.compile(
    r"\bH([12])\s*(?:(?:&|and)\s*)?F\.?\s*Y\.?\s*"
    r"['’]?(20\d{2}|\d{2})(?:\s*[-/]\s*(\d{2,4}))?\b",
    re.IGNORECASE,
)
FISCAL_YEAR_PATTERN = re.compile(
    r"\bF\.?\s*Y\.?\s*['’]?(20\d{2}|\d{2})\b",
    re.IGNORECASE,
)
PERIOD_END_PATTERN = re.compile(
    rf"\b(?:ended|ending)\s+(?:on\s+)?"
    rf"(?:({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s*(20\d{{2}})|"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})[,]?\s*(20\d{{2}}))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Transcript:
    """A downloaded BSE transcript with period and source metadata."""

    announcement: dict[str, Any]
    content: bytes
    source_url: str
    fiscal_year: int | None
    quarter: str | None
    page_count: int | None
    inspection_error: str | None = None

    @property
    def period_label(self) -> str:
        if self.fiscal_year is None:
            return "UNKNOWN_PERIOD"
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


def fetch_announcements(
    session: requests.Session,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Fetch every historical BSE announcement for the company."""

    base_params = {
        "strCat": -1,
        "strPrevDate": START_DATE.strftime("%Y%m%d"),
        "strScrip": SCRIP_CODE,
        "strSearch": "P",
        "strToDate": (end_date or date.today()).strftime("%Y%m%d"),
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
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected BSE response: expected an object")

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
        for row_number, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            row_id = str(
                row.get("NEWSID")
                or row.get("ATTACHMENTNAME")
                or f"page-{page}-row-{row_number}"
            )
            if row_id in seen_ids:
                continue
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
        for key in ("NEWSSUB", "HEADLINE", "MORE", "CATEGORYNAME", "SUBCATNAME")
    )


def is_earnings_call_transcript(announcement: dict[str, Any]) -> bool:
    """Return whether a BSE filing appears to be an earnings-call transcript."""

    text = announcement_text(announcement).casefold()
    return "transcript" in text and any(
        marker in text
        for marker in ("earnings call", "conference call", "concall", "earnings")
    )


def normalize_fiscal_year(value: str) -> int:
    year = int(value)
    return year + 2000 if year < 100 else year


def extract_period(text: str) -> tuple[int | None, str | None]:
    """Extract fiscal year and quarter/half-year from opening transcript text."""

    head = text[:PDF_INSPECTION_CHARS]

    # The transcript body may mention a later quarter while discussing the
    # outlook.  Select the earliest explicit quarter/half-year label, which
    # is normally the title on the opening transcript page.
    explicit_periods: list[tuple[int, int, str]] = []
    for match in QUARTER_FY_PATTERN.finditer(head):
        fiscal_year = normalize_fiscal_year(match.group(3) or match.group(2))
        explicit_periods.append((match.start(), fiscal_year, f"Q{match.group(1)}"))
    for match in HALF_FY_PATTERN.finditer(head):
        fiscal_year = normalize_fiscal_year(match.group(3) or match.group(2))
        explicit_periods.append((match.start(), fiscal_year, f"H{match.group(1)}"))
    if explicit_periods:
        _, fiscal_year, period = min(explicit_periods, key=lambda item: item[0])
        return fiscal_year, period

    fiscal_year_match = FISCAL_YEAR_PATTERN.search(head)
    if fiscal_year_match:
        return normalize_fiscal_year(fiscal_year_match.group(1)), None

    period_end = PERIOD_END_PATTERN.search(head)
    if not period_end:
        return None, None

    if period_end.group(1):
        month = MONTHS[period_end.group(1).casefold()]
        year = int(period_end.group(3))
    else:
        month = MONTHS[period_end.group(5).casefold()]
        year = int(period_end.group(6))

    context = head[max(0, period_end.start() - 120) : period_end.start()].casefold()
    if "half" in context:
        quarter = "H1" if month == 9 else "H2" if month == 3 else None
    elif "nine" in context or "9m" in context:
        quarter = "Q3" if month == 12 else None
    else:
        quarter = {3: "Q4", 6: "Q1", 9: "Q2", 12: "Q3"}.get(month)

    fiscal_year = year if month <= 3 else year + 1
    return fiscal_year, quarter


def parse_datetime(announcement: dict[str, Any]) -> datetime | None:
    for key in ("DT_TM", "NEWS_DT", "News_submission_dt", "DissemDT"):
        raw_value = str(announcement.get(key) or "").strip()
        if not raw_value:
            continue
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def attachment_urls(announcement: dict[str, Any]) -> list[str]:
    filename = str(announcement.get("ATTACHMENTNAME") or "").strip()
    if not filename or filename == "-":
        return []
    if filename.startswith(("http://", "https://")):
        return [filename]
    return [f"{base_url}{filename}" for base_url in PDF_BASE_URLS]


def download_pdf(
    session: requests.Session,
    urls: list[str],
) -> tuple[bytes, str]:
    if not urls:
        raise RuntimeError("Announcement has no PDF attachment")

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


def inspect_pdf(content: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text, len(reader.pages)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_pdf(content: bytes, destination: Path) -> tuple[str, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256(content)
    if destination.exists():
        if sha256(destination.read_bytes()) == digest:
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


def make_transcript(
    session: requests.Session,
    announcement: dict[str, Any],
) -> Transcript:
    content, source_url = download_pdf(session, attachment_urls(announcement))
    inspection_error: str | None = None
    page_count: int | None = None
    pdf_text = ""
    try:
        pdf_text, page_count = inspect_pdf(content)
    except Exception as exc:
        inspection_error = str(exc)

    fiscal_year, quarter = extract_period(pdf_text)
    if fiscal_year is None:
        fiscal_year, quarter = extract_period(announcement_text(announcement))

    return Transcript(
        announcement=announcement,
        content=content,
        source_url=source_url,
        fiscal_year=fiscal_year,
        quarter=quarter,
        page_count=page_count,
        inspection_error=inspection_error,
    )


def destination_for(transcript: Transcript, output_dir: Path) -> Path:
    label = transcript.period_label
    if label == "UNKNOWN_PERIOD":
        filed = parse_datetime(transcript.announcement)
        label = filed.date().isoformat() if filed else "UNKNOWN_DATE"
    filename = f"{label}_{COMPANY_FILE_STEM}_Earnings_Call_Transcript.pdf"
    return output_dir / filename


def manifest_row_base(announcement: dict[str, Any]) -> dict[str, Any]:
    filed = parse_datetime(announcement)
    return {
        "announcement_id": str(announcement.get("NEWSID") or ""),
        "filed_at": str(announcement.get("DT_TM") or ""),
        "filed_date": filed.date().isoformat() if filed else None,
        "period": "UNKNOWN_PERIOD",
        "subject": str(announcement.get("NEWSSUB") or ""),
        "subcategory": str(announcement.get("SUBCATNAME") or ""),
        "attachment_name": str(announcement.get("ATTACHMENTNAME") or ""),
        "source_url_candidates": attachment_urls(announcement),
    }


def write_manifest(
    output_dir: Path,
    announcements_count: int,
    candidates_count: int,
    rows: list[dict[str, Any]],
) -> Path:
    manifest = {
        "company": "Insolation Energy Ltd",
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "api_url": API_URL,
        "retrieved_on": date.today().isoformat(),
        "historical_announcements": announcements_count,
        "transcript_candidates": candidates_count,
        "matched_transcripts": sum("error" not in row for row in rows),
        "transcripts": rows,
        "notes": [
            "BSE corporate-announcements history is the source of truth and is fetched across all available pages.",
            "Only announcements containing transcript and earnings-call language are selected; call intimations and audio-recording outcomes are excluded.",
            "Each attachment is tried against both BSE AttachLive and AttachHis paths because older filings may have moved to the historical archive.",
            "Fiscal year labels use the year ending in March: FY2026 means the financial year ended March 31, 2026.",
        ],
    }
    path = output_dir / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run(output_dir: Path) -> int:
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    session = build_session()
    announcements = fetch_announcements(session)
    candidates: list[dict[str, Any]] = []
    seen_attachments: set[str] = set()
    for announcement in announcements:
        if not is_earnings_call_transcript(announcement):
            continue
        attachment_name = str(announcement.get("ATTACHMENTNAME") or "").strip()
        if not attachment_name or attachment_name in seen_attachments:
            continue
        seen_attachments.add(attachment_name)
        candidates.append(announcement)

    candidates.sort(
        key=lambda item: (
            parse_datetime(item) or datetime.min,
            str(item.get("NEWSID") or ""),
        ),
        reverse=True,
    )
    print(
        f"BSE returned {len(announcements)} historical announcements; "
        f"{len(candidates)} are earnings-call transcript filings."
    )

    rows: list[dict[str, Any]] = []
    failures = 0
    for announcement in candidates:
        row = manifest_row_base(announcement)
        try:
            transcript = make_transcript(session, announcement)
            destination = destination_for(transcript, output_dir)
            status, saved_to = save_pdf(transcript.content, destination)
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"Could not download {row['attachment_name']}: {exc}")
        else:
            row.update(
                {
                    "period": transcript.period_label,
                    "fiscal_year": transcript.fiscal_year,
                    "quarter": transcript.quarter,
                    "local_file": saved_to.name,
                    "source_url": transcript.source_url,
                    "status": status,
                    "sha256": sha256(transcript.content),
                    "pages": transcript.page_count,
                }
            )
            if transcript.inspection_error:
                row["inspection_error"] = transcript.inspection_error
            print(f"{status}: {saved_to}")
        rows.append(row)

    manifest_path = write_manifest(
        output_dir,
        len(announcements),
        len(candidates),
        rows,
    )
    print(f"Wrote manifest: {manifest_path}")
    print(
        f"Finished: {len(candidates) - failures} transcript PDF(s) in {output_dir}"
    )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()
    return run(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())

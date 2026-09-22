#!/usr/bin/env python3
"""Archive MV Electrosystems' BSE annual reports and investor materials.

The BSE corporate-announcement history is queried across overlapping windows,
then every matching annual report, investor presentation, and written
earnings-call transcript PDF is downloaded.  The output is source-preserving:
the original PDF, source URL candidates, filing metadata, SHA-256 hash, page
count, and any download failure are recorded in a JSON manifest.

Run from the repository root::

    python scripts/download_mv_electrosystems_investor_materials.py

By default files are written below ``~/Investing/MV Electrosystems`` in
separate ``Annual Reports``, ``Investor Presentations``, and ``Earnings Call
Transcripts`` folders.  Use ``--output-dir`` or the date options to override
the defaults.

"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import requests
from pypdf import PdfReader


COMPANY_NAME = "MV Electrosystems Ltd"
SCRIP_CODE = 544851
COMPANY_FILE_STEM = "MV_Electrosystems"
API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ANNOUNCEMENTS_URL = (
    "https://www.bseindia.com/stock-share-price/"
    "mv-electrosystems-ltd/mvelectro/544851/corp-announcements"
)
PDF_BASE_URLS = (
    "https://www.bseindia.com/xml-data/corpfiling/AttachLive/",
    "https://www.bseindia.com/xml-data/corpfiling/AttachHis/",
)
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "MV Electrosystems"
START_DATE = date(2000, 1, 1)
MAX_WINDOW_DAYS = 364
USER_AGENT = "mv-electrosystems-investor-materials-downloader/1.0"
PDF_INSPECTION_PAGES = 8
MANIFEST_FILENAME = "MV_Electrosystems_Investor_Materials.json"

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
    r"F\.?\s*Y\.?\s*['’]?(\d{2,4})"
    r"(?:\s*[-/]\s*['’]?(\d{2,4}))?\b",
    re.IGNORECASE,
)
HALF_FY_PATTERN = re.compile(
    r"\bH([12])\s*(?:(?:&|and)\s*)?F\.?\s*Y\.?\s*"
    r"['’]?(\d{2,4})(?:\s*[-/]\s*['’]?(\d{2,4}))?\b",
    re.IGNORECASE,
)
ANNUAL_RANGE_PATTERN = re.compile(
    r"\b(?:annual\s+report|financial\s+year|f\.?\s*y\.?)\s*"
    r"(?:for\s*)?['’]?(20\d{2})\s*[-–/]\s*['’]?(\d{2,4})\b",
    re.IGNORECASE,
)
FISCAL_YEAR_PATTERN = re.compile(
    r"\bF\.?\s*Y\.?\s*['’]?(20\d{2}|\d{2})\b",
    re.IGNORECASE,
)
MONTH_PERIOD_END_PATTERN = re.compile(
    rf"\b(?:ended|ending)\s+(?:on\s+)?"
    rf"(?:({MONTH_PATTERN})\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s*(20\d{{2}})|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+({MONTH_PATTERN})[,]?\s*(20\d{{2}}))\b",
    re.IGNORECASE,
)
NUMERIC_PERIOD_END_PATTERN = re.compile(
    r"\b(?:ended|ending)\s+(?:on\s+)?"
    r"(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b",
    re.IGNORECASE,
)


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


def parse_datetime(announcement: dict[str, Any]) -> datetime | None:
    """Parse the first usable BSE filing timestamp."""

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


def announcement_key(announcement: dict[str, Any]) -> str:
    return str(
        announcement.get("NEWSID")
        or announcement.get("ATTACHMENTNAME")
        or ""
    ).strip()


def attachment_name(announcement: dict[str, Any]) -> str:
    return str(announcement.get("ATTACHMENTNAME") or "").strip()


def attachment_urls(announcement: dict[str, Any]) -> list[str]:
    filename = attachment_name(announcement)
    if not filename or filename == "-":
        return []
    if filename.startswith(("http://", "https://")):
        return [filename]
    return [f"{base_url}{filename}" for base_url in PDF_BASE_URLS]


def announcement_text(announcement: dict[str, Any]) -> str:
    return " ".join(
        str(announcement.get(key) or "")
        for key in (
            "NEWSSUB",
            "HEADLINE",
            "MORE",
            "CATEGORYNAME",
            "SUBCATNAME",
        )
    )


def normalized_subcategory(announcement: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(announcement.get("SUBCATNAME") or "")).strip().casefold()


def is_annual_report(announcement: dict[str, Any]) -> bool:
    subcategory = normalized_subcategory(announcement)
    if subcategory in {"reg. 34 (1) annual report", "annual report"}:
        return True
    text = announcement_text(announcement).casefold()
    return bool(re.search(r"reg\.?\s*34\s*\(\s*1\s*\).*annual report", text))


def is_investor_presentation(announcement: dict[str, Any]) -> bool:
    subcategory = normalized_subcategory(announcement)
    if subcategory == "investor presentation":
        return True
    text = announcement_text(announcement).casefold()
    return "investor presentation" in text and "audio" not in text


def is_earnings_call_transcript(announcement: dict[str, Any]) -> bool:
    """Identify written transcripts while excluding schedules/audio outcomes."""

    subcategory = normalized_subcategory(announcement)
    if subcategory == "earnings call transcript":
        return True
    text = announcement_text(announcement).casefold()
    if "transcript" not in text:
        return False
    return any(
        marker in text
        for marker in ("earnings call", "conference call", "concall", "earnings")
    )


def fetch_window(
    session: requests.Session,
    window_start: date,
    window_end: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and paginate one BSE date window."""

    if window_start > window_end:
        raise ValueError("window_start must not be after window_end")
    if (window_end - window_start).days > MAX_WINDOW_DAYS:
        raise ValueError("BSE window exceeds the supported date-range limit")

    base_params = {
        "strCat": -1,
        "strPrevDate": window_start.strftime("%Y%m%d"),
        "strScrip": SCRIP_CODE,
        "strSearch": "P",
        "strToDate": window_end.strftime("%Y%m%d"),
        "strType": "C",
        "subcategory": -1,
    }
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    expected_count: int | None = None
    page = 1

    while True:
        if page > 1000:
            raise RuntimeError("BSE pagination exceeded 1000 pages")
        response = session.get(
            API_URL,
            params={**base_params, "pageno": page},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected BSE response: expected an object")

        page_rows = payload.get("Table", [])
        if not isinstance(page_rows, list):
            message = str(payload.get("Message") or "unknown BSE response")
            raise RuntimeError(f"Unexpected BSE response: {message}")

        if expected_count is None:
            table1 = payload.get("Table1") or []
            if table1 and isinstance(table1[0], dict):
                try:
                    expected_count = int(table1[0].get("ROWCNT", 0))
                except (TypeError, ValueError):
                    expected_count = None

        before_count = len(rows)
        for row_number, row in enumerate(page_rows):
            if not isinstance(row, dict):
                continue
            row_id = announcement_key(row) or f"page-{page}-row-{row_number}"
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            rows.append(row)

        if not page_rows:
            if expected_count is not None and len(rows) < expected_count:
                raise RuntimeError(
                    f"BSE pagination ended at page {page} with "
                    f"{len(rows)} of {expected_count} rows"
                )
            break
        if len(rows) == before_count:
            raise RuntimeError(f"BSE pagination repeated page {page}")
        if expected_count is not None and len(rows) >= expected_count:
            break
        page += 1

    return rows, {
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "pages": page,
        "rows": len(rows),
        "reported_rows": expected_count,
    }


def date_windows(start_date: date, end_date: date) -> list[tuple[date, date]]:
    """Return overlapping windows within BSE's date-range limit."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    windows: list[tuple[date, date]] = []
    current_end = end_date
    while current_end >= start_date:
        current_start = max(
            start_date,
            current_end - timedelta(days=MAX_WINDOW_DAYS),
        )
        windows.append((current_start, current_end))
        if current_start == start_date:
            break
        # Retain one boundary date in the adjacent window and deduplicate by
        # NEWSID/attachment globally so neither inclusive-bound convention can
        # omit a filing.
        current_end = current_start
    return windows


def fetch_all_announcements(
    session: requests.Session,
    start_date: date = START_DATE,
    end_date: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch and globally deduplicate the complete requested history."""

    final_date = end_date or date.today()
    merged: dict[str, dict[str, Any]] = {}
    window_metadata: list[dict[str, Any]] = []
    fallback_index = 0

    for window_start, window_end in date_windows(start_date, final_date):
        rows, metadata = fetch_window(session, window_start, window_end)
        window_metadata.append(metadata)
        for row in rows:
            key = announcement_key(row)
            if not key:
                fallback_index += 1
                key = f"anonymous-row-{fallback_index}"
            merged.setdefault(key, row)

    announcements = sorted(
        merged.values(),
        key=lambda item: (
            parse_datetime(item) or datetime.min,
            announcement_key(item),
        ),
        reverse=True,
    )
    return announcements, window_metadata


def normalize_fiscal_year(value: str) -> int:
    year = int(value)
    return year + 2000 if year < 100 else year


def period_label_from_text(text: str) -> str:
    """Extract a fiscal-period label from filing or PDF text."""

    head = text[:16_000]
    explicit: list[tuple[int, str]] = []
    for match in QUARTER_FY_PATTERN.finditer(head):
        fiscal_year = normalize_fiscal_year(match.group(3) or match.group(2))
        explicit.append((match.start(), f"Q{match.group(1)}_FY{fiscal_year}"))
    for match in HALF_FY_PATTERN.finditer(head):
        fiscal_year = normalize_fiscal_year(match.group(3) or match.group(2))
        explicit.append((match.start(), f"H{match.group(1)}_FY{fiscal_year}"))
    if explicit:
        return min(explicit, key=lambda item: item[0])[1]

    annual_range = ANNUAL_RANGE_PATTERN.search(head)
    if annual_range:
        return f"FY{normalize_fiscal_year(annual_range.group(2))}"

    fiscal_year = FISCAL_YEAR_PATTERN.search(head)
    if fiscal_year:
        return f"FY{normalize_fiscal_year(fiscal_year.group(1))}"

    period_end = MONTH_PERIOD_END_PATTERN.search(head)
    if period_end:
        if period_end.group(1):
            month = MONTHS[period_end.group(1).casefold()]
            year = int(period_end.group(2))
        else:
            month = MONTHS[period_end.group(3).casefold()]
            year = int(period_end.group(4))
        quarter = {3: 4, 6: 1, 9: 2, 12: 3}.get(month)
        if quarter:
            fiscal_year = year if month <= 3 else year + 1
            return f"Q{quarter}_FY{fiscal_year}"

    numeric_end = NUMERIC_PERIOD_END_PATTERN.search(head)
    if numeric_end:
        month = int(numeric_end.group(2))
        year = int(numeric_end.group(3))
        quarter = {3: 4, 6: 1, 9: 2, 12: 3}.get(month)
        if quarter:
            fiscal_year = year if month <= 3 else year + 1
            return f"Q{quarter}_FY{fiscal_year}"

    return "UNKNOWN_PERIOD"


def inspect_pdf(content: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(content))
    text = "\n".join(
        (page.extract_text() or "") for page in reader.pages[:PDF_INSPECTION_PAGES]
    )
    return text, len(reader.pages)


def period_label(
    announcement: dict[str, Any],
    content: bytes | None = None,
) -> str:
    if content:
        try:
            pdf_text, _ = inspect_pdf(content)
            label = period_label_from_text(pdf_text)
            if label != "UNKNOWN_PERIOD":
                return label
        except Exception:
            pass

    label = period_label_from_text(announcement_text(announcement))
    if label != "UNKNOWN_PERIOD":
        return label

    filed = parse_datetime(announcement)
    return filed.date().isoformat() if filed else "UNKNOWN_DATE"


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


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_pdf_idempotently(content: bytes, destination: Path) -> tuple[str, Path]:
    """Save a PDF without duplicating an identical prior archive file."""

    digest = sha256(content)
    destination.parent.mkdir(parents=True, exist_ok=True)
    candidates = [destination]
    candidates.extend(sorted(destination.parent.glob("*.pdf")))
    seen_paths: set[Path] = set()
    for candidate in candidates:
        if candidate in seen_paths or not candidate.is_file():
            continue
        seen_paths.add(candidate)
        if sha256(candidate.read_bytes()) == digest:
            return "already exists", candidate

    if destination.exists():
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


def select_candidates(
    announcements: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_attachments: set[str] = set()
    for announcement in announcements:
        if not predicate(announcement):
            continue
        key = attachment_name(announcement) or announcement_key(announcement)
        if not key or not attachment_urls(announcement) or key in seen_attachments:
            continue
        seen_attachments.add(key)
        candidates.append(announcement)
    candidates.sort(
        key=lambda item: (
            parse_datetime(item) or datetime.min,
            announcement_key(item),
        ),
        reverse=True,
    )
    return candidates


def safe_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "UNKNOWN"


def destination_for(
    announcement: dict[str, Any],
    kind: str,
    content: bytes,
    output_dir: Path,
) -> Path:
    folder_by_kind = {
        "annual_report": "Annual Reports",
        "investor_presentation": "Investor Presentations",
        "transcript": "Earnings Call Transcripts",
    }
    suffix_by_kind = {
        "annual_report": "Annual_Report",
        "investor_presentation": "Investor_Presentation",
        "transcript": "Earnings_Call_Transcript",
    }
    label = safe_filename_part(period_label(announcement, content))
    suffix = suffix_by_kind[kind]
    return (
        output_dir
        / folder_by_kind[kind]
        / f"{label}_{COMPANY_FILE_STEM}_{suffix}.pdf"
    )


def manifest_row_base(
    announcement: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    filed = parse_datetime(announcement)
    return {
        "kind": kind,
        "announcement_id": str(announcement.get("NEWSID") or ""),
        "filed_at": str(announcement.get("DT_TM") or ""),
        "filed_date": filed.date().isoformat() if filed else None,
        "period": period_label(announcement),
        "subject": str(announcement.get("NEWSSUB") or ""),
        "headline": str(announcement.get("HEADLINE") or ""),
        "category": str(announcement.get("CATEGORYNAME") or ""),
        "subcategory": str(announcement.get("SUBCATNAME") or ""),
        "attachment_name": attachment_name(announcement),
        "source_url_candidates": attachment_urls(announcement),
        "raw_announcement": announcement,
    }


def write_manifest(
    output_dir: Path,
    start_date: date,
    end_date: date,
    window_metadata: list[dict[str, Any]],
    announcements_count: int,
    candidates: dict[str, list[dict[str, Any]]],
    rows: dict[str, list[dict[str, Any]]],
) -> Path:
    manifest = {
        "company": COMPANY_NAME,
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "api_url": API_URL,
        "retrieved_on": date.today().isoformat(),
        "history_date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "history_windows": window_metadata,
        "historical_announcements": announcements_count,
        "candidate_counts": {
            "annual_reports": len(candidates["annual_reports"]),
            "investor_presentations": len(candidates["investor_presentations"]),
            "transcripts": len(candidates["transcripts"]),
        },
        "selected_counts": {key: len(value) for key, value in rows.items()},
        "annual_reports": rows["annual_reports"],
        "investor_presentations": rows["investor_presentations"],
        "transcripts": rows["transcripts"],
        "notes": [
            "Coverage is limited to written PDF filings returned by the BSE corporate-announcement history for scrip 544851.",
            "The BSE API date-range limit is treated as 364 days; overlapping windows are globally deduplicated by NEWSID or attachment name.",
            "Each window is paginated until the BSE-reported row count is reached; a short response is treated as an extraction failure.",
            "Attachments are tried against both BSE AttachLive and AttachHis paths because older filings may use the historical archive.",
            "Annual reports are selected from the Reg. 34 (1) Annual Report classification; presentations and transcripts are selected from their matching BSE classifications or explicit filing text.",
            "Schedules, meeting intimations, audio-only outcomes, and links without a PDF attachment are not counted as written transcripts.",
            "Fiscal-year labels use the year ending in March: FY2026 means the financial year ended March 31, 2026.",
        ],
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".part")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path


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

    session = build_session()
    announcements, window_metadata = fetch_all_announcements(
        session,
        start_date=start_date,
        end_date=final_date,
    )
    candidates = {
        "annual_reports": select_candidates(announcements, is_annual_report),
        "investor_presentations": select_candidates(
            announcements, is_investor_presentation
        ),
        "transcripts": select_candidates(
            announcements, is_earnings_call_transcript
        ),
    }
    print(
        f"BSE returned {len(announcements)} unique historical announcements "
        f"across {len(window_metadata)} windows; "
        f"{len(candidates['annual_reports'])} annual report, "
        f"{len(candidates['investor_presentations'])} presentation, and "
        f"{len(candidates['transcripts'])} transcript filings."
    )

    rows: dict[str, list[dict[str, Any]]] = {
        "annual_reports": [],
        "investor_presentations": [],
        "transcripts": [],
    }
    failures = 0
    selections = (
        ("annual_report", candidates["annual_reports"], "annual_reports"),
        (
            "investor_presentation",
            candidates["investor_presentations"],
            "investor_presentations",
        ),
        ("transcript", candidates["transcripts"], "transcripts"),
    )
    for kind, selected, group in selections:
        for announcement in selected:
            row = manifest_row_base(announcement, kind)
            try:
                content, source_url = download_pdf(
                    session,
                    attachment_urls(announcement),
                )
                _, page_count = inspect_pdf(content)
                destination = destination_for(
                    announcement,
                    kind,
                    content,
                    output_dir,
                )
                status, saved_to = save_pdf_idempotently(content, destination)
            except Exception as exc:
                failures += 1
                row.update({"status": "unavailable", "error": str(exc)})
                print(f"unavailable: {row['attachment_name']}: {exc}")
            else:
                row.update(
                    {
                        "period": period_label(announcement, content),
                        "source_url": source_url,
                        "local_file": str(saved_to.relative_to(output_dir)),
                        "status": status,
                        "sha256": sha256(content),
                        "pages": page_count,
                    }
                )
                filed = parse_datetime(announcement)
                filed_label = f" filed {filed:%Y-%m-%d}" if filed else ""
                print(f"{status}: {saved_to}{filed_label}")
            rows[group].append(row)

    manifest_path = write_manifest(
        output_dir,
        start_date,
        final_date,
        window_metadata,
        len(announcements),
        candidates,
        rows,
    )
    print(f"Wrote manifest: {manifest_path}")
    downloaded_count = sum(
        1 for group in rows.values() for row in group if row.get("status") != "unavailable"
    )
    print(f"Finished: {downloaded_count} PDF(s) in {output_dir}")
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
        help=f"First filing date to include (default: {START_DATE})",
    )
    parser.add_argument(
        "--end-date",
        type=parse_iso_date,
        default=None,
        help="Last filing date to include (default: today)",
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

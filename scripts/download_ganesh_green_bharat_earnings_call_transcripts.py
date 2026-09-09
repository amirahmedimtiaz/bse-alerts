#!/usr/bin/env python3
"""Download Ganesh Green Bharat's complete NSE earnings-call archive.

The NSE corporate-announcement history is the source of truth.  The script
queries both the current ``equities`` view and the legacy ``sme`` view, merges
duplicate attachment URLs, downloads every matching transcript PDF, and
writes an audit manifest beside the files.

Existing transcript files are preserved.  When a prior manifest identifies a
matching local file, the file is reused without downloading or rewriting it.
If a new source has the same period label as an existing file, ``save_pdf``
also preserves the old file and uses a suffixed filename when necessary.

Run from the repository root::

    python scripts/download_ganesh_green_bharat_earnings_call_transcripts.py

Files are saved to ``~/Investing/Ganesh Green Bharat`` by default.  Use
``--output-dir`` to choose another destination.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader


COMPANY_NAME = "Ganesh Green Bharat Limited"
SYMBOL = "GGBL"
MARKET_TYPES = ("sme", "equities")
ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/get-quote/equity/"
    "GGBL/Ganesh-Green-Bharat-Limited"
)
API_URL = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "Ganesh Green Bharat"
COMPANY_FILE_STEM = "Ganesh_Green_Bharat"
MANIFEST_FILENAME = "Ganesh_Green_Bharat_Earnings_Call_Transcripts.json"
LEGACY_MANIFEST_FILENAME = "Ganesh_Green_Bharat_Investor_Materials.json"
USER_AGENT = "ganesh-green-bharat-earnings-call-downloader/1.0"
PDF_INSPECTION_PAGES = 6

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
    r"(?:\s*[-/]\s*['’]?(\d{2,4}))?\b",
    re.IGNORECASE,
)
HALF_FY_PATTERN = re.compile(
    r"\bH([12])\s*(?:(?:&|and)\s*)?F\.?\s*Y\.?\s*"
    r"['’]?(20\d{2}|\d{2})(?:\s*[-/]\s*['’]?(\d{2,4}))?\b",
    re.IGNORECASE,
)
FISCAL_YEAR_PATTERN = re.compile(
    r"\bF\.?\s*Y\.?\s*['’]?(20\d{2}|\d{2})"
    r"(?:\s*[-/]\s*['’]?(\d{2,4}))?\b",
    re.IGNORECASE,
)
PERIOD_END_PATTERN = re.compile(
    rf"\b(?:ended|ending)\s+(?:on\s+)?"
    rf"(?:({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s*(20\d{{2}})|"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})[,]?\s*(20\d{{2}}))\b",
    re.IGNORECASE,
)


def build_session() -> requests.Session:
    """Create an NSE session with the quote-page request context."""

    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Referer": ANNOUNCEMENTS_URL,
            "User-Agent": USER_AGENT,
        }
    )
    try:
        session.get(ANNOUNCEMENTS_URL, timeout=30)
    except requests.RequestException:
        # The API normally works without the HTML page; do not make a page
        # rendering failure look like missing announcement history.
        pass
    return session


def announcement_text(announcement: dict[str, Any]) -> str:
    return " ".join(
        str(announcement.get(key) or "")
        for key in ("desc", "attchmntText", "attchmntFile", "dt", "an_dt")
    )


def attachment_url(announcement: dict[str, Any]) -> str:
    value = str(announcement.get("attchmntFile") or "").strip()
    return "" if not value or value.endswith("/-") else value


def attachment_name(announcement: dict[str, Any]) -> str:
    return attachment_url(announcement).rsplit("/", 1)[-1]


def attachment_key(announcement: dict[str, Any]) -> str:
    """Return a stable key that also works for old rows without seq_id."""

    return attachment_url(announcement) or str(announcement.get("seq_id") or "")


def is_earnings_call_transcript(announcement: dict[str, Any]) -> bool:
    """Identify transcript filings while excluding call schedules/outcomes."""

    text = announcement_text(announcement).casefold()
    return "transcript" in text and any(
        marker in text
        for marker in ("earnings", "earning", "conference call", "concall", "call")
    )


def parse_datetime(announcement: dict[str, Any]) -> datetime | None:
    for key in ("sort_date", "dt", "an_dt"):
        raw_value = str(announcement.get(key) or "").strip()
        if not raw_value:
            continue
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for pattern in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
                try:
                    parsed = datetime.strptime(raw_value, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def filed_value(announcement: dict[str, Any]) -> str:
    return next(
        (
            str(announcement.get(key) or "")
            for key in ("sort_date", "dt", "an_dt")
            if announcement.get(key)
        ),
        "",
    )


def _fiscal_year(first: str, second: str | None = None) -> int:
    value = second or first
    year = int(value)
    return year + 2000 if year < 100 else year


def period_from_text(text: str) -> tuple[int | None, str | None]:
    """Read the earliest explicit call-period label from transcript text."""

    head = text[:40_000]
    explicit: list[tuple[int, int, str]] = []
    for match in QUARTER_FY_PATTERN.finditer(head):
        explicit.append(
            (
                match.start(),
                _fiscal_year(match.group(2), match.group(3)),
                f"Q{match.group(1)}",
            )
        )
    for match in HALF_FY_PATTERN.finditer(head):
        quarter = "Q2" if match.group(1) == "1" else "Q4"
        explicit.append(
            (
                match.start(),
                _fiscal_year(match.group(2), match.group(3)),
                quarter,
            )
        )
    if explicit:
        _, fiscal_year, quarter = min(explicit, key=lambda item: item[0])
        return fiscal_year, quarter

    fiscal_year_match = FISCAL_YEAR_PATTERN.search(head)
    if fiscal_year_match:
        return _fiscal_year(fiscal_year_match.group(1), fiscal_year_match.group(2)), None

    period_end = PERIOD_END_PATTERN.search(head)
    if not period_end:
        return None, None

    if period_end.group(1):
        month = MONTHS[period_end.group(1).casefold()]
        year = int(period_end.group(3))
    else:
        month = MONTHS[period_end.group(5).casefold()]
        year = int(period_end.group(6))

    context = head[max(0, period_end.start() - 160) : period_end.start()].casefold()
    if "half" in context or "h1" in context or "h2" in context:
        quarter = "Q2" if month == 9 else "Q4" if month == 3 else None
    elif "nine" in context or "9m" in context:
        quarter = "Q3" if month == 12 else None
    else:
        quarter = {3: "Q4", 6: "Q1", 9: "Q2", 12: "Q3"}.get(month)
    fiscal_year = year if month <= 3 else year + 1
    return fiscal_year, quarter


def period_label_from_text(text: str) -> str:
    fiscal_year, quarter = period_from_text(text)
    if fiscal_year is None:
        return "UNKNOWN_PERIOD"
    return f"{quarter}_FY{fiscal_year}" if quarter else f"FY{fiscal_year}"


def inspect_pdf(content: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(content))
    pages = len(reader.pages)
    text = "\n".join(
        (page.extract_text() or "") for page in reader.pages[:PDF_INSPECTION_PAGES]
    )
    return text, pages


def download_pdf(session: requests.Session, url: str) -> bytes:
    if not url:
        raise RuntimeError("Announcement has no PDF attachment")
    response = session.get(url, timeout=90)
    response.raise_for_status()
    content = response.content
    if not content.lstrip().startswith(b"%PDF"):
        content_type = response.headers.get("content-type", "unknown")
        raise RuntimeError(f"NSE attachment is not a PDF ({content_type})")
    return content


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_pdf(content: bytes, destination: Path) -> tuple[str, Path]:
    """Save without overwriting an existing file."""

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


def fetch_all_nse_announcements(session: requests.Session) -> list[dict[str, Any]]:
    """Merge both NSE announcement views by attachment URL or sequence ID."""

    merged: dict[str, dict[str, Any]] = {}
    for market_type in MARKET_TYPES:
        response = session.get(
            API_URL,
            params={
                "functionName": "getCorporateAnnouncement",
                "symbol": SYMBOL,
                "marketApiType": market_type,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("data", payload.get("Table", []))
        else:
            raise RuntimeError(
                f"Unexpected NSE response shape for {market_type}: "
                f"{type(payload).__name__}"
            )
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected NSE rows for {market_type}: not a list")

        for row in rows:
            if not isinstance(row, dict):
                continue
            key = attachment_key(row)
            if not key:
                key = f"{market_type}:{row.get('dt')}:{row.get('desc')}"
            current = merged.get(key)
            if current is None:
                merged[key] = {**row, "nse_market_views": [market_type]}
            elif market_type not in current["nse_market_views"]:
                current["nse_market_views"].append(market_type)

    return sorted(
        merged.values(),
        key=lambda item: (
            parse_datetime(item) or datetime.min,
            attachment_key(item),
        ),
        reverse=True,
    )


def transcript_candidates(
    announcements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for announcement in announcements:
        if not is_earnings_call_transcript(announcement):
            continue
        key = attachment_key(announcement)
        if not key or not attachment_url(announcement) or key in seen:
            continue
        seen.add(key)
        candidates.append(announcement)
    return candidates


def manifest_row_base(announcement: dict[str, Any]) -> dict[str, Any]:
    filed = parse_datetime(announcement)
    return {
        "announcement_id": str(announcement.get("seq_id") or ""),
        "attachment_key": attachment_key(announcement),
        "attachment_name": attachment_name(announcement),
        "filed_at": filed_value(announcement),
        "filed_date": filed.date().isoformat() if filed else None,
        "period": "UNKNOWN_PERIOD",
        "subject": str(announcement.get("desc") or ""),
        "attachment_text": str(announcement.get("attchmntText") or ""),
        "source_url_candidates": [attachment_url(announcement)],
        "nse_market_views": announcement.get("nse_market_views", []),
    }


def load_existing_rows(output_dir: Path) -> dict[str, dict[str, Any]]:
    """Index current and legacy manifests so prior PDFs can be reused."""

    rows_by_key: dict[str, dict[str, Any]] = {}
    for filename in (MANIFEST_FILENAME, LEGACY_MANIFEST_FILENAME):
        path = output_dir / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Warning: could not read prior manifest {path}: {exc}")
            continue
        rows = payload.get("transcripts", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            keys = {
                str(row.get(key) or "").strip()
                for key in ("attachment_key", "attachment_name", "source_url", "attachment_url")
            }
            keys.discard("")
            for key in keys:
                rows_by_key.setdefault(key, row)
    return rows_by_key


def local_file_from_row(output_dir: Path, row: dict[str, Any]) -> Path | None:
    value = str(row.get("local_file") or "").strip()
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = output_dir / candidate
    try:
        candidate.resolve().relative_to(output_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def existing_row_for(
    output_dir: Path,
    announcement: dict[str, Any],
    rows_by_key: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], Path] | None:
    keys = (attachment_key(announcement), attachment_name(announcement), attachment_url(announcement))
    for key in keys:
        if not key:
            continue
        row = rows_by_key.get(key)
        if row is None:
            continue
        local_file = local_file_from_row(output_dir, row)
        if local_file is not None:
            expected_hash = str(row.get("sha256") or "").strip()
            if expected_hash and sha256(local_file.read_bytes()) != expected_hash:
                print(
                    f"Warning: {local_file} differs from its prior manifest hash; "
                    "preserving it and refreshing the source into a suffixed file."
                )
                return None
            return row, local_file
    return None


def destination_for(
    announcement: dict[str, Any],
    content: bytes,
    output_dir: Path,
) -> tuple[Path, str, int | None, str | None, int]:
    text, pages = inspect_pdf(content)
    fiscal_year, quarter = period_from_text(text)
    if fiscal_year is None:
        fiscal_year, quarter = period_from_text(announcement_text(announcement))
    if fiscal_year is None:
        filed = parse_datetime(announcement)
        label = filed.date().isoformat() if filed else "UNKNOWN_DATE"
    else:
        label = f"{quarter}_FY{fiscal_year}" if quarter else f"FY{fiscal_year}"
    destination = output_dir / f"{label}_{COMPANY_FILE_STEM}_Earnings_Call_Transcript.pdf"
    return destination, label, fiscal_year, quarter, pages


def write_manifest(
    output_dir: Path,
    announcements_count: int,
    candidates: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> Path:
    manifest = {
        "company": COMPANY_NAME,
        "exchange": "NSE",
        "symbol": SYMBOL,
        "market_types_queried": list(MARKET_TYPES),
        "announcements_url": ANNOUNCEMENTS_URL,
        "api_url": API_URL,
        "retrieved_on": date.today().isoformat(),
        "historical_announcements": announcements_count,
        "transcript_candidates": len(candidates),
        "matched_transcripts": sum(row.get("status") not in {"unavailable", "error"} for row in rows),
        "transcripts": rows,
        "notes": [
            "NSE corporate-announcement history is the source of truth; both the current equities and legacy SME views are queried.",
            "Rows are deduplicated by attachment URL or sequence ID, then sorted by filing timestamp, newest first.",
            "Only PDF filings containing transcript and earnings/conference-call language are selected; schedules and audio-recording outcomes are excluded.",
            "Existing files identified by the prior investor-materials or transcript manifest are reused without rewriting.",
            "Fiscal-year labels use the year ending in March: FY2026 means the financial year ended March 31, 2026.",
        ],
    }
    path = output_dir / MANIFEST_FILENAME
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def run(output_dir: Path) -> int:
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    prior_rows = load_existing_rows(output_dir)
    session = build_session()
    announcements = fetch_all_nse_announcements(session)
    candidates = transcript_candidates(announcements)
    print(
        f"NSE returned {len(announcements)} merged historical announcements; "
        f"{len(candidates)} are earnings-call transcript filings."
    )

    rows: list[dict[str, Any]] = []
    failures = 0
    for announcement in candidates:
        row = manifest_row_base(announcement)
        prior = existing_row_for(output_dir, announcement, prior_rows)
        if prior is not None:
            prior_row, local_file = prior
            period = str(prior_row.get("period") or "UNKNOWN_PERIOD")
            fiscal_year = prior_row.get("fiscal_year")
            quarter = prior_row.get("quarter")
            pages = prior_row.get("pages")
            try:
                existing_text, existing_pages = inspect_pdf(local_file.read_bytes())
                existing_fiscal_year, existing_quarter = period_from_text(existing_text)
            except Exception as exc:
                print(f"Warning: could not inspect reused PDF {local_file}: {exc}")
            else:
                pages = existing_pages
                if existing_fiscal_year is not None:
                    fiscal_year = existing_fiscal_year
                    quarter = existing_quarter
                    period = (
                        f"{existing_quarter}_FY{existing_fiscal_year}"
                        if existing_quarter
                        else f"FY{existing_fiscal_year}"
                    )
            row.update(
                {
                    "period": period,
                    "fiscal_year": fiscal_year,
                    "quarter": quarter,
                    "local_file": str(local_file.relative_to(output_dir)),
                    "status": "already exists",
                    "sha256": sha256(local_file.read_bytes()),
                    "pages": pages,
                    "source_url": attachment_url(announcement),
                    "reused_from_manifest": True,
                }
            )
            print(f"already exists: {local_file}")
            rows.append(row)
            continue

        try:
            source_url = attachment_url(announcement)
            content = download_pdf(session, source_url)
            destination, label, fiscal_year, quarter, pages = destination_for(
                announcement, content, output_dir
            )
            status, saved_to = save_pdf(content, destination)
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {row['attachment_name']}: {exc}")
        else:
            row.update(
                {
                    "period": label,
                    "fiscal_year": fiscal_year,
                    "quarter": quarter,
                    "source_url": source_url,
                    "local_file": str(saved_to.relative_to(output_dir)),
                    "status": status,
                    "sha256": sha256(content),
                    "pages": pages,
                }
            )
            print(f"{status}: {saved_to}")
        rows.append(row)

    manifest_path = write_manifest(output_dir, len(announcements), candidates, rows)
    print(f"Wrote manifest: {manifest_path}")
    print(f"Finished: {len(candidates) - failures} transcript PDF(s) in {output_dir}")
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

#!/usr/bin/env python3
"""Archive K. V. Toys India Ltd. investor materials.

The collector combines the issuer's official investor page with the complete
BSE corporate-announcement history for scrip 544641.  It preserves original
PDFs, source URLs, filing metadata, page counts, SHA-256 hashes, and duplicate
relationships in a local manifest.

Run from the repository root::

    python scripts/download_kv_toys_investor_materials.py

Files are written below ``~/Investing/K V Toys India`` by default.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader


COMPANY_NAME = "K. V. Toys India Ltd"
SCRIP_CODE = 544641
COMPANY_FILE_STEM = "K_V_Toys_India"
ANNOUNCEMENTS_URL = (
    "https://www.bseindia.com/stock-share-price/"
    "k-v-toys-india-ltd/kvtoys/544641/corp-announcements"
)
OFFICIAL_INVESTOR_PAGE = "https://kvtoys.com/investor/"
API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
PDF_BASE_URLS = (
    "https://www.bseindia.com/xml-data/corpfiling/AttachLive/",
    "https://www.bseindia.com/xml-data/corpfiling/AttachHis/",
)
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "K V Toys India"
START_DATE = date(2000, 1, 1)
MAX_WINDOW_DAYS = 364
USER_AGENT = "kv-toys-investor-materials-downloader/1.0"
MANIFEST_FILENAME = "K_V_Toys_India_Investor_Materials.json"
CSV_FILENAME = "K_V_Toys_India_Investor_Materials.csv"

OFFICIAL_ANNUAL_REPORTS = (
    {
        "period": "FY2024",
        "label": "FY 2023-24",
        "url": "https://kvtoys.com/wp-content/uploads/2025/12/"
        "Annual-Report-FY-23-24-KV-Toys.pdf",
    },
    {
        "period": "FY2025",
        "label": "FY 2024-25",
        "url": "https://kvtoys.com/wp-content/uploads/2025/12/"
        "AR-FS_KV-Toys-Mar_25.pdf",
    },
    {
        "period": "FY2026",
        "label": "FY 2025-26",
        "url": "https://kvtoys.com/wp-content/uploads/2026/08/"
        "Annual-Report.pdfsigned.pdf",
    },
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
    return re.sub(
        r"\s+", " ", str(announcement.get("SUBCATNAME") or "")
    ).strip().casefold()


def is_annual_report(announcement: dict[str, Any]) -> bool:
    """Include both the report package and its later Reg. 34 filing."""

    return "annual report" in announcement_text(announcement).casefold()


def is_investor_presentation(announcement: dict[str, Any]) -> bool:
    subcategory = normalized_subcategory(announcement)
    if subcategory == "investor presentation":
        return True
    text = announcement_text(announcement).casefold()
    return "investor presentation" in text and "audio" not in text


def is_earnings_call_transcript(announcement: dict[str, Any]) -> bool:
    """Identify written transcripts and exclude meeting schedules/outcomes."""

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
        current_end = current_start
    return windows


def fetch_all_announcements(
    session: requests.Session,
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[str, dict[str, Any]] = {}
    window_metadata: list[dict[str, Any]] = []
    fallback_index = 0

    for window_start, window_end in date_windows(start_date, end_date):
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


def download_pdf(
    session: requests.Session,
    urls: list[str],
) -> tuple[bytes, str]:
    if not urls:
        raise RuntimeError("Announcement has no PDF attachment")

    last_error: Exception | None = None
    for url in urls:
        try:
            response = session.get(url, timeout=120)
            response.raise_for_status()
            content = response.content
            if not content.lstrip().startswith(b"%PDF"):
                content_type = response.headers.get("content-type", "unknown")
                raise RuntimeError(f"Attachment is not a PDF ({content_type})")
            return content, url
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(f"Could not download PDF from {urls}: {last_error}")


def inspect_pdf(content: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(content))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:8])
    return text, len(reader.pages)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_filed_date(announcement: dict[str, Any]) -> str | None:
    parsed = parse_datetime(announcement)
    return parsed.date().isoformat() if parsed else None


def safe_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "UNKNOWN"


def save_pdf_idempotently(
    content: bytes,
    destination: Path,
    digest_index: dict[str, Path],
) -> tuple[str, Path]:
    digest = sha256(content)
    if digest in digest_index:
        return "duplicate", digest_index[digest]

    destination.parent.mkdir(parents=True, exist_ok=True)
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
    digest_index[digest] = destination
    return "downloaded", destination


def index_existing_pdfs(output_dir: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in sorted(output_dir.rglob("*.pdf")) if output_dir.exists() else []:
        index.setdefault(sha256(path.read_bytes()), path)
    return index


def bse_row_base(announcement: dict[str, Any], kind: str) -> dict[str, Any]:
    filed = parse_datetime(announcement)
    return {
        "kind": kind,
        "source_type": "BSE corporate-announcement API",
        "announcement_id": str(announcement.get("NEWSID") or ""),
        "filed_at": str(announcement.get("DT_TM") or ""),
        "filed_date": filed.date().isoformat() if filed else None,
        "period": None,
        "subject": str(announcement.get("NEWSSUB") or ""),
        "headline": str(announcement.get("HEADLINE") or ""),
        "category": str(announcement.get("CATEGORYNAME") or ""),
        "subcategory": str(announcement.get("SUBCATNAME") or ""),
        "attachment_name": attachment_name(announcement),
        "source_url_candidates": attachment_urls(announcement),
        "source_url": None,
        "local_file": None,
        "status": None,
        "sha256": None,
        "pages": None,
        "raw_announcement": announcement,
    }


def official_row_base(report: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "annual_report",
        "source_type": "issuer official investor page",
        "announcement_id": None,
        "filed_at": None,
        "filed_date": None,
        "period": report["period"],
        "subject": report["label"],
        "headline": "Annual report linked from the official investor page",
        "category": "Annual Report",
        "subcategory": "Annual Report",
        "attachment_name": report["url"].rsplit("/", 1)[-1],
        "source_url_candidates": [report["url"]],
        "source_url": None,
        "official_investor_page": OFFICIAL_INVESTOR_PAGE,
        "official_link_verified": None,
        "local_file": None,
        "status": None,
        "sha256": None,
        "pages": None,
    }


def filename_for(row: dict[str, Any], kind: str) -> Path:
    filed_date = row.get("filed_date") or "undated"
    if row["source_type"] == "issuer official investor page":
        filename = f"{row['period']}_{COMPANY_FILE_STEM}_Annual_Report.pdf"
        return Path("Annual Reports") / filename

    prefix = safe_filename_part(str(filed_date))
    if kind == "annual_report":
        filename = f"{prefix}_{COMPANY_FILE_STEM}_Annual_Report_Filing.pdf"
        return Path("Annual Reports") / filename
    if kind == "investor_presentation":
        filename = f"{prefix}_{COMPANY_FILE_STEM}_Investor_Presentation_Filing.pdf"
        return Path("Investor Presentations") / filename
    filename = f"{prefix}_{COMPANY_FILE_STEM}_Earnings_Call_Transcript.pdf"
    return Path("Earnings Call Transcripts") / filename


def save_document(
    session: requests.Session,
    row: dict[str, Any],
    kind: str,
    output_dir: Path,
    digest_index: dict[str, Path],
) -> None:
    content, source_url = download_pdf(session, row["source_url_candidates"])
    pdf_text, page_count = inspect_pdf(content)
    destination = output_dir / filename_for(row, kind)
    status, saved_to = save_pdf_idempotently(content, destination, digest_index)
    row.update(
        {
            "source_url": source_url,
            "local_file": str(saved_to.relative_to(output_dir)),
            "status": status,
            "sha256": sha256(content),
            "pages": page_count,
        }
    )
    if kind == "investor_presentation":
        if page_count <= 2 and "meeting" in pdf_text.casefold():
            row["document_variant"] = "analyst/institutional investor meeting outcome notice"
        else:
            row["document_variant"] = "presentation or presentation filing"


def write_manifest(
    output_dir: Path,
    retrieved_on: date,
    start_date: date,
    end_date: date,
    window_metadata: list[dict[str, Any]],
    announcements_count: int,
    bse_candidates: dict[str, list[dict[str, Any]]],
    official_rows: list[dict[str, Any]],
    presentation_rows: list[dict[str, Any]],
    transcript_rows: list[dict[str, Any]],
    official_page_status: dict[str, Any],
) -> Path:
    manifest = {
        "company": COMPANY_NAME,
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "official_investor_page": OFFICIAL_INVESTOR_PAGE,
        "api_url": API_URL,
        "retrieved_on": retrieved_on.isoformat(),
        "bse_history_date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "bse_history_windows": window_metadata,
        "bse_historical_announcements": announcements_count,
        "bse_candidate_counts": {
            "annual_report_filings": len(bse_candidates["annual_reports"]),
            "investor_presentation_filings": len(
                bse_candidates["investor_presentations"]
            ),
            "earnings_call_transcript_filings": len(
                bse_candidates["transcripts"]
            ),
        },
        "official_investor_page_status": official_page_status,
        "selected_counts": {
            "annual_reports": len(official_rows)
            + len(bse_candidates["annual_reports"]),
            "investor_presentations": len(presentation_rows),
            "earnings_call_transcripts": len(transcript_rows),
        },
        "annual_reports": official_rows + bse_candidates["annual_reports"],
        "investor_presentations": presentation_rows,
        "earnings_call_transcripts": transcript_rows,
        "notes": [
            "The BSE API was queried in overlapping windows no longer than 364 days and globally deduplicated by NEWSID or attachment name.",
            "BSE attachments were tried against both AttachLive and AttachHis; the historical archive supplied these older filings.",
            "The three annual-report PDFs linked on the issuer's official investor page are preserved. The FY2025-26 website PDF has the same SHA-256 as the BSE August 10, 2026 filing, so the BSE row is recorded as a duplicate rather than saved twice.",
            "BSE returned three filings classified as Investor Presentation. The March 9 and March 23 PDFs are one-page analyst/institutional investor meeting outcome notices; they are retained and labeled accordingly. The June 22 PDF is the substantive H2 FY26/FY26 presentation.",
            "No written earnings-call or conference-call transcript PDF was found in the complete BSE history or on the official investor page as of the retrieval date. Analyst/investor meeting intimations and outcomes are not transcripts.",
            "This archive covers the requested annual reports, investor-presentation filings, and written earnings-call transcripts; prospectuses, policies, annual returns, and separate audited-financial attachments on the issuer website are outside this requested set.",
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
        "subcategory",
        "document_variant",
        "source_url",
        "local_file",
        "status",
        "sha256",
        "pages",
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
    annual = manifest["annual_reports"]
    presentations = manifest["investor_presentations"]
    transcripts = manifest["earnings_call_transcripts"]
    lines = [
        f"# {manifest['company']} investor-materials archive",
        "",
        f"Retrieved: {manifest['retrieved_on']}",
        f"BSE scrip code: `{manifest['scrip_code']}`",
        "",
        "## Coverage",
        "",
        f"- Annual-report records: {len(annual)} (three official annual-report PDFs, plus BSE filing records).",
        f"- Investor-presentation filings: {len(presentations)}.",
        f"- Written earnings-call transcripts: {len(transcripts)} found.",
        f"- BSE announcements scanned: {manifest['bse_historical_announcements']} across {len(manifest['bse_history_windows'])} bounded windows.",
        "",
        "The official issuer page is [K.V. Toys Investor Relations]("
        + OFFICIAL_INVESTOR_PAGE
        + "). The exchange source is the [BSE corporate-announcement page]("
        + ANNOUNCEMENTS_URL
        + "). See the JSON and CSV manifests for exact filing metadata, source URLs, page counts, hashes, and duplicate handling.",
        "",
        "No written earnings-call/conference-call transcript PDF was located in the complete BSE history or on the official investor page at the retrieval date. Meeting intimations and meeting outcomes are not transcripts.",
        "",
        "## Notes",
        "",
        "- The March 9 and March 23 BSE items classified as Investor Presentation are one-page meeting-outcome notices; they remain archived but are labeled in the manifest.",
        "- The FY2025-26 official website annual report and the BSE August 10, 2026 filing have identical SHA-256 hashes; only one PDF copy is stored.",
    ]
    path = output_dir / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_transcript_status(output_dir: Path, manifest_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = output_dir / "Earnings Call Transcripts" / "README.md"
    if manifest["earnings_call_transcripts"]:
        text = (
            "# Earnings-call transcript archive\n\n"
            "See the parent manifest for the archived transcript records.\n"
        )
    else:
        text = (
            "# Earnings-call transcript archive\n\n"
            "No written earnings-call or conference-call transcript PDF was "
            "located in the complete BSE history or on the issuer's official "
            "investor page as of the retrieval date. Analyst/investor meeting "
            "intimations and outcomes are not transcripts.\n"
        )
    path.write_text(text, encoding="utf-8")
    return path


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
    page_response = session.get(OFFICIAL_INVESTOR_PAGE, timeout=60)
    page_response.raise_for_status()
    page_html = page_response.text
    official_page_status = {
        "status_code": page_response.status_code,
        "final_url": page_response.url,
        "content_type": page_response.headers.get("content-type", ""),
        "annual_report_links_verified": {
            report["period"]: report["url"] in page_html
            for report in OFFICIAL_ANNUAL_REPORTS
        },
    }

    announcements, window_metadata = fetch_all_announcements(
        session,
        start_date=start_date,
        end_date=final_date,
    )
    bse_candidates = {
        "annual_reports": [row for row in announcements if is_annual_report(row)],
        "investor_presentations": [
            row for row in announcements if is_investor_presentation(row)
        ],
        "transcripts": [
            row for row in announcements if is_earnings_call_transcript(row)
        ],
    }
    for rows in bse_candidates.values():
        rows.sort(
            key=lambda item: (
                parse_datetime(item) or datetime.min,
                announcement_key(item),
            )
        )

    print(
        f"BSE returned {len(announcements)} unique announcements across "
        f"{len(window_metadata)} windows; "
        f"{len(bse_candidates['annual_reports'])} annual-report, "
        f"{len(bse_candidates['investor_presentations'])} presentation, and "
        f"{len(bse_candidates['transcripts'])} transcript filings."
    )

    digest_index = index_existing_pdfs(output_dir)
    failures = 0
    official_rows: list[dict[str, Any]] = []
    for report in OFFICIAL_ANNUAL_REPORTS:
        row = official_row_base(report)
        try:
            row["official_link_verified"] = official_page_status[
                "annual_report_links_verified"
            ][report["period"]]
            save_document(
                session,
                row,
                "annual_report",
                output_dir,
                digest_index,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {report['label']}: {exc}")
        else:
            print(f"{row['status']}: {row['local_file']}")
        official_rows.append(row)

    bse_annual_rows: list[dict[str, Any]] = []
    for announcement in bse_candidates["annual_reports"]:
        row = bse_row_base(announcement, "annual_report")
        try:
            save_document(
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
        bse_annual_rows.append(row)

    presentation_rows: list[dict[str, Any]] = []
    for announcement in bse_candidates["investor_presentations"]:
        row = bse_row_base(announcement, "investor_presentation")
        try:
            save_document(
                session,
                row,
                "investor_presentation",
                output_dir,
                digest_index,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {row['attachment_name']}: {exc}")
        else:
            print(f"{row['status']}: {row['local_file']}")
        presentation_rows.append(row)

    transcript_rows: list[dict[str, Any]] = []
    for announcement in bse_candidates["transcripts"]:
        row = bse_row_base(announcement, "earnings_call_transcript")
        try:
            save_document(
                session,
                row,
                "earnings_call_transcript",
                output_dir,
                digest_index,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {row['attachment_name']}: {exc}")
        else:
            print(f"{row['status']}: {row['local_file']}")
        transcript_rows.append(row)

    manifest_path = write_manifest(
        output_dir,
        retrieved_on=final_date,
        start_date=start_date,
        end_date=final_date,
        window_metadata=window_metadata,
        announcements_count=len(announcements),
        bse_candidates={
            "annual_reports": bse_annual_rows,
            "investor_presentations": bse_candidates["investor_presentations"],
            "transcripts": bse_candidates["transcripts"],
        },
        official_rows=official_rows,
        presentation_rows=presentation_rows,
        transcript_rows=transcript_rows,
        official_page_status=official_page_status,
    )
    csv_path = write_csv(output_dir, manifest_path)
    readme_path = write_readme(output_dir, manifest_path)
    transcript_status_path = write_transcript_status(output_dir, manifest_path)
    print(f"Wrote manifest: {manifest_path}")
    print(f"Wrote CSV: {csv_path}")
    print(f"Wrote README: {readme_path}")
    print(f"Wrote transcript status: {transcript_status_path}")
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

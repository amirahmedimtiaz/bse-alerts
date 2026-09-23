#!/usr/bin/env python3
"""Archive Chemkart India Ltd investor materials.

The collector combines the issuer's official document records with the
complete BSE corporate-announcement history for scrip 544442. It preserves
the original PDFs, source URLs, filing metadata, page counts, SHA-256 hashes,
and duplicate relationships in a local manifest.

Run from the repository root::

    python scripts/download_chemkart_investor_materials.py

Files are written below ``~/Investing/Chemkart India`` by default.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import socket
import subprocess
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
import urllib3.util.connection as urllib3_connection
from pypdf import PdfReader

try:
    from scripts import download_kv_toys_investor_materials as bse
except ModuleNotFoundError:  # Direct execution: python scripts/<collector>.py
    import download_kv_toys_investor_materials as bse


COMPANY_NAME = "Chemkart India Ltd"
SCRIP_CODE = 544442
COMPANY_FILE_STEM = "Chemkart_India"
ANNOUNCEMENTS_URL = (
    "https://www.bseindia.com/stock-share-price/"
    "chemkart-india-ltd/CHEMKART/544442/"
)
OFFICIAL_INVESTOR_PAGE = "https://chemkart.com/investor-relation/"
OFFICIAL_ANNUAL_REPORTS_PAGE = "https://chemkart.com/cat_doc/annual-reports/"
DEFAULT_OUTPUT_DIR = Path.home() / "Investing" / "Chemkart India"
START_DATE = date(2000, 1, 1)
MANIFEST_FILENAME = "Chemkart_India_Investor_Materials.json"
CSV_FILENAME = "Chemkart_India_Investor_Materials.csv"
OFFICIAL_USER_AGENTS = (
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/125.0 Safari/537.36",
    "curl/8.0",
)


def force_ipv4() -> None:
    """Avoid occasional stalled IPv6 keep-alives on the BSE API host."""

    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET


# The older reports are listed in the issuer's Annual Reports category. The
# FY2023-24 document record currently points at a duplicated FY2022-23
# filename; the correctly named FY2023-24 media attachment is preserved here.
OFFICIAL_ANNUAL_REPORTS = (
    {
        "period": "FY2022",
        "label": "FY 2021-22",
        "period_text": "2021-22",
        "url": (
            "https://chemkart.com/wp-content/uploads/2025/10/"
            "CIPL-Annual-Report-FY-2021-22-1.pdf"
        ),
        "page_url": "https://chemkart.com/ova_doc/cipl-annual-report-fy-2021-22/",
    },
    {
        "period": "FY2023",
        "label": "FY 2022-23",
        "period_text": "2022-23",
        "url": (
            "https://chemkart.com/wp-content/uploads/2025/10/"
            "CIPL-Annual-Report-FY-2022-23-1.pdf"
        ),
        "page_url": "https://chemkart.com/ova_doc/cipl-annual-report-fy-2022-23/",
    },
    {
        "period": "FY2024",
        "label": "FY 2023-24",
        "period_text": "2023-24",
        "url": (
            "https://chemkart.com/wp-content/uploads/2025/10/"
            "CIPL-Annual-Report-FY-2023-24-1.pdf"
        ),
        "page_url": "https://chemkart.com/ova_doc/cipl-annual-report-fy-2023-24/",
        "page_link_url": (
            "https://chemkart.com/wp-content/uploads/2025/10/"
            "CIPL-Annual-Report-FY-2022-23-1-1.pdf"
        ),
        "source_note": (
            "The FY2023-24 issuer record currently displays the FY2022-23-1-1 "
            "filename; the correctly named FY2023-24-1.pdf media attachment "
            "was used and its period was checked from the PDF."
        ),
    },
    {
        "period": "FY2025",
        "label": "FY 2024-25",
        "period_text": "2024-25",
        "url": (
            "https://chemkart.com/wp-content/uploads/2026/04/"
            "CIL-Annual-Report-FY-2024-25.pdf"
        ),
        "page_url": OFFICIAL_ANNUAL_REPORTS_PAGE,
    },
    {
        "period": "FY2026",
        "label": "FY 2025-26",
        "period_text": "2025-26",
        "url": (
            "https://chemkart.com/wp-content/uploads/2026/09/"
            "Chamkart-Annual-Report-2025-26-31082026.pdf"
        ),
        "page_url": (
            "https://chemkart.com/ova_doc/"
            "chemkart-annual-report-2025-26/"
        ),
    },
)


# These are the four presentation records returned by both the BSE history and
# the issuer's official document records. The BSE filing remains the primary
# exchange source; the official URL is retained for independent comparison.
OFFICIAL_PRESENTATIONS = (
    {
        "filed_date": "2025-09-11",
        "label": "Investor Presentation - 11 September 2025",
        "url": (
            "https://chemkart.com/wp-content/uploads/2026/03/"
            "2025-09-11_Investor-Presentation-under-Reg-30-of-SEBI-LODR.pdf"
        ),
        "page_url": (
            "https://chemkart.com/ova_doc/"
            "2025-09-11_investor-presentation-under-reg-30-of-sebi-lodr/"
        ),
    },
    {
        "filed_date": "2025-11-12",
        "label": "Investor Presentation - 12 November 2025",
        "url": (
            "https://chemkart.com/wp-content/uploads/2026/03/"
            "2025-11-12_Investor-Presentation-under-Reg-30-of-SEBI-LODR.pdf"
        ),
        "page_url": (
            "https://chemkart.com/ova_doc/"
            "2025-11-12_investor-presentation-under-reg-30-of-sebi-lodr/"
        ),
    },
    {
        "filed_date": "2026-05-18",
        "label": "Investor Presentation - 18 May 2026",
        "url": (
            "https://chemkart.com/wp-content/uploads/2026/05/"
            "2026-05-18_Investor-Presentation-under-Reg-30-of-SEBI-LODR.pdf"
        ),
        "page_url": (
            "https://chemkart.com/ova_doc/"
            "2026-05-18_investor-presentation-under-reg-30-of-sebi-lodr/"
        ),
    },
    {
        "filed_date": "2026-07-15",
        "label": "Investor Presentation - 15 July 2026",
        "url": (
            "https://chemkart.com/wp-content/uploads/2026/07/"
            "2026-07-15_Investors-Presentation_CIL.pdf"
        ),
        "page_url": (
            "https://chemkart.com/ova_doc/"
            "2026-07-15_investors-presentation_cil/"
        ),
    },
)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def safe_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "UNKNOWN"


def official_download_candidates(url: str) -> list[str]:
    candidates = [f"{url}?download=1", url]
    if "?" in url:
        candidates = [url, url.split("?", 1)[0]]
    return list(dict.fromkeys(candidates))


def download_official_pdf(
    session: requests.Session,
    url: str,
) -> tuple[bytes, str]:
    errors: list[str] = []
    for candidate in official_download_candidates(url):
        for user_agent in OFFICIAL_USER_AGENTS:
            try:
                curl = subprocess.run(
                    [
                        "curl",
                        "--fail",
                        "--location",
                        "--silent",
                        "--show-error",
                        "--connect-timeout",
                        "30",
                        "--max-time",
                        "180",
                        "-A",
                        user_agent,
                        "-H",
                        "Accept: application/pdf,*/*",
                        candidate,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=210,
                    check=False,
                )
                content = curl.stdout
                if curl.returncode == 0 and content.lstrip().startswith(b"%PDF"):
                    return content, candidate
                errors.append(
                    f"curl {curl.returncode} {candidate}: "
                    f"{curl.stderr.decode(errors='replace')[-200:]}"
                )
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"curl {type(exc).__name__} {candidate}: {exc}")
            try:
                response = session.get(
                    candidate,
                    headers={
                        "Accept": "application/pdf,*/*",
                        "User-Agent": user_agent,
                    },
                    timeout=(30, 60),
                    stream=True,
                )
                try:
                    if response.status_code >= 400:
                        errors.append(f"{response.status_code} {candidate}")
                        continue
                    # This host can stall when requests asks for 1 MB chunks;
                    # 64 KB preserves the bytes and completes reliably.
                    chunks = response.iter_content(chunk_size=64 * 1024)
                    content = b"".join(chunks)
                    if not content.lstrip().startswith(b"%PDF"):
                        errors.append(
                            f"non-PDF {response.headers.get('content-type', 'unknown')} "
                            f"{candidate}"
                        )
                        continue
                    return content, candidate
                finally:
                    response.close()
            except requests.RequestException as exc:
                errors.append(f"{type(exc).__name__} {candidate}: {exc}")
    raise RuntimeError("; ".join(errors[-8:]))


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def inspect_pdf(content: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(content))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:8])
    return text, len(reader.pages)


def period_check(text: str, period_text: str | None) -> str:
    if not period_text:
        return "not_applicable"
    normalized = normalize_text(text).replace("–", "-").replace("—", "-")
    if period_text in normalized or f"FY {period_text}" in normalized:
        return "matched"
    return "not_found_in_first_8_pages"


def official_annual_row(report: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": "annual_report",
        "source_type": "issuer official annual-reports record",
        "period": report["period"],
        "subject": report["label"],
        "filed_date": None,
        "source_page_url": report["page_url"],
        "source_url": report["url"],
        "source_url_candidates": [report["url"]],
        "local_file": None,
        "status": None,
        "sha256": None,
        "pages": None,
        "period_check": None,
    }
    for key in ("page_link_url", "source_note"):
        if key in report:
            row[key] = report[key]
    return row


def official_presentation_row(presentation: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "investor_presentation",
        "source_type": "issuer official investor-presentation record",
        "period": presentation["filed_date"],
        "subject": presentation["label"],
        "filed_date": presentation["filed_date"],
        "source_page_url": presentation["page_url"],
        "source_url": presentation["url"],
        "source_url_candidates": [presentation["url"]],
        "local_file": None,
        "status": None,
        "sha256": None,
        "pages": None,
        "period_check": "not_applicable",
    }


def filename_for(row: dict[str, Any], kind: str) -> Path:
    if row["source_type"].startswith("issuer official"):
        date_part = row.get("period") or "undated"
        if kind == "annual_report":
            return Path("Annual Reports") / (
                f"{safe_filename_part(str(date_part))}_"
                f"{COMPANY_FILE_STEM}_Annual_Report.pdf"
            )
        if kind == "investor_presentation":
            return Path("Investor Presentations") / (
                f"{safe_filename_part(str(date_part))}_"
                f"{COMPANY_FILE_STEM}_Investor_Presentation.pdf"
            )
    filed_date = row.get("filed_date") or "undated"
    if kind == "annual_report":
        return Path("Annual Reports") / (
            f"{safe_filename_part(str(filed_date))}_"
            f"{COMPANY_FILE_STEM}_Annual_Report_Filing.pdf"
        )
    if kind == "investor_presentation":
        return Path("Investor Presentations") / (
            f"{safe_filename_part(str(filed_date))}_"
            f"{COMPANY_FILE_STEM}_Investor_Presentation_Filing.pdf"
        )
    return Path("Earnings Call Transcripts") / (
        f"{safe_filename_part(str(filed_date))}_"
        f"{COMPANY_FILE_STEM}_Earnings_Call_Transcript.pdf"
    )


def save_row(
    session: requests.Session,
    row: dict[str, Any],
    kind: str,
    output_dir: Path,
    digest_index: dict[str, Path],
    official: bool = False,
) -> None:
    if official:
        content, retrieval_url = download_official_pdf(
            session, row["source_url"]
        )
    else:
        content, retrieval_url = bse.download_pdf(
            session, row["source_url_candidates"]
        )
    text, pages = inspect_pdf(content)
    destination = output_dir / filename_for(row, kind)
    status, saved_to = bse.save_pdf_idempotently(
        content, destination, digest_index
    )
    row.update(
        {
            "retrieval_url": retrieval_url,
            "local_file": str(saved_to.relative_to(output_dir)),
            "status": status,
            "sha256": sha256(content),
            "pages": pages,
            "period_check": period_check(text, row.get("period_text")),
            "text_preview": normalize_text(text[:600]),
        }
    )
    if status == "duplicate":
        row["duplicate_of"] = str(saved_to.relative_to(output_dir))


def probe_page(session: requests.Session, url: str) -> dict[str, Any]:
    try:
        response = session.get(url, timeout=(20, 30), stream=True)
        try:
            return {
                "status_code": response.status_code,
                "final_url": response.url,
                "content_type": response.headers.get("content-type", ""),
            }
        finally:
            response.close()
    except requests.RequestException as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def write_manifest(
    output_dir: Path,
    retrieved_on: date,
    start_date: date,
    end_date: date,
    windows: list[dict[str, Any]],
    announcements_count: int,
    annual_rows: list[dict[str, Any]],
    presentation_rows: list[dict[str, Any]],
    transcript_rows: list[dict[str, Any]],
    excluded_meetings: list[dict[str, Any]],
    official_status: dict[str, Any],
) -> Path:
    unique_annual = len(
        {row["sha256"] for row in annual_rows if row.get("sha256")}
    )
    unique_presentations = len(
        {
            row["sha256"]
            for row in presentation_rows
            if row.get("sha256")
        }
    )
    manifest = {
        "company": COMPANY_NAME,
        "exchange": "BSE",
        "scrip_code": SCRIP_CODE,
        "announcements_url": ANNOUNCEMENTS_URL,
        "official_investor_page": OFFICIAL_INVESTOR_PAGE,
        "official_annual_reports_page": OFFICIAL_ANNUAL_REPORTS_PAGE,
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
            "investor_presentation_filings": sum(
                row["source_type"] == "BSE corporate-announcement API"
                for row in presentation_rows
            ),
            "earnings_call_transcript_filings": len(transcript_rows),
        },
        "official_source_status": official_status,
        "selected_counts": {
            "annual_report_records": len(annual_rows),
            "annual_report_unique_pdfs": unique_annual,
            "investor_presentation_records": len(presentation_rows),
            "investor_presentation_unique_pdfs": unique_presentations,
            "earnings_call_transcripts": len(transcript_rows),
        },
        "annual_reports": annual_rows,
        "investor_presentations": presentation_rows,
        "earnings_call_transcripts": transcript_rows,
        "excluded_analyst_investor_meeting_intimations": excluded_meetings,
        "notes": [
            "The BSE API was queried in overlapping windows no longer than 364 days and globally deduplicated by NEWSID or attachment name.",
            "BSE attachments were tried against both AttachLive and AttachHis; the historical archive supplied the selected filing PDFs.",
            "The issuer's Annual Reports category lists FY2021-22 through FY2025-26. The FY2023-24 record currently points at a FY2022-23-1-1 filename; the correctly named FY2023-24-1.pdf media attachment is archived and its first eight pages were checked for the FY2023-24 period.",
            "The FY2025-26 initial BSE filing and the later revised BSE filing are retained as separate records; identical content is stored once and linked by SHA-256 when applicable.",
            "The four BSE investor-presentation filings are paired with the four issuer official document records. Duplicate bytes are stored once and both source records remain in the manifest.",
            "No written earnings-call or conference-call transcript PDF was found in the complete BSE history or the issuer's official investor-document records as of the retrieval date. Analyst/investor meeting intimations are recorded separately and are not transcripts.",
            "The archive covers the requested annual reports, investor-presentation filings, and written earnings-call transcripts; prospectuses, annual returns, policies, and separate financial-result attachments are outside this requested set.",
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
        f"- Investor-presentation records: {counts['investor_presentation_records']} ({counts['investor_presentation_unique_pdfs']} unique PDFs).",
        f"- Written earnings-call transcripts: {counts['earnings_call_transcripts']} found.",
        f"- BSE announcements scanned: {manifest['bse_historical_announcements']} across {len(manifest['bse_history_windows'])} bounded windows.",
        "",
        "Primary sources are the [issuer investor-relations page]("
        + OFFICIAL_INVESTOR_PAGE
        + "), [issuer annual-reports category]("
        + OFFICIAL_ANNUAL_REPORTS_PAGE
        + "), and the [BSE quote/corporate-announcement page]("
        + ANNOUNCEMENTS_URL
        + "). See the JSON and CSV manifests for exact filing metadata, source URLs, page counts, hashes, period checks, and duplicate handling.",
        "",
        "No written earnings-call or conference-call transcript PDF was located in the complete BSE history or the issuer's official investor-document records at the retrieval date. The six BSE analyst/investor meeting intimations are recorded in the JSON manifest but are not transcripts.",
        "",
        "The issuer's FY2023-24 document record currently displays a FY2022-23-1-1 filename. The archive uses the correctly named FY2023-24-1.pdf media attachment and records the mismatch in the manifest.",
        "",
        "The initial and revised FY2025-26 BSE annual-report filings are retained as separate source records. When source bytes match, the manifest points both records to the one stored PDF by SHA-256.",
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
            "located in the complete BSE history or the issuer's official "
            "investor-document records as of the retrieval date. Analyst/"
            "investor meeting intimations and outcomes are not transcripts.\n"
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

    bse.SCRIP_CODE = SCRIP_CODE
    bse.ANNOUNCEMENTS_URL = ANNOUNCEMENTS_URL
    force_ipv4()
    session = bse.build_session()
    official_session = requests.Session()
    official_session.headers.update({"User-Agent": OFFICIAL_USER_AGENTS[0]})
    official_status = {
        "investor_relation_page": probe_page(
            official_session, OFFICIAL_INVESTOR_PAGE
        ),
        "annual_reports_page": probe_page(
            official_session, OFFICIAL_ANNUAL_REPORTS_PAGE
        ),
        "annual_report_records": [
            {
                "period": report["period"],
                "page_url": report["page_url"],
                "source_url": report["url"],
                "page_link_url": report.get("page_link_url"),
            }
            for report in OFFICIAL_ANNUAL_REPORTS
        ],
        "presentation_records": [
            {
                "filed_date": presentation["filed_date"],
                "page_url": presentation["page_url"],
                "source_url": presentation["url"],
            }
            for presentation in OFFICIAL_PRESENTATIONS
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
    excluded_meetings = [
        bse.bse_row_base(row, "excluded_analyst_investor_meeting_intimation")
        for row in announcements
        if bse.normalized_subcategory(row) == "analyst / investor meet"
    ]
    for rows in (
        bse_annual_candidates,
        bse_presentation_candidates,
        bse_transcript_candidates,
        excluded_meetings,
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
    print(f"Excluded analyst/investor meeting intimations: {len(excluded_meetings)}")

    digest_index = bse.index_existing_pdfs(output_dir)
    failures = 0
    annual_rows: list[dict[str, Any]] = []

    for report in OFFICIAL_ANNUAL_REPORTS:
        row = official_annual_row(report)
        row["period_text"] = report["period_text"]
        try:
            save_row(
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

    for announcement in sorted(
        bse_annual_candidates,
        key=lambda item: bse.parse_datetime(item) or datetime.min,
    ):
        row = bse.bse_row_base(announcement, "annual_report")
        row["period_text"] = "2025-26"
        try:
            save_row(
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

    presentation_rows: list[dict[str, Any]] = []
    for announcement in sorted(
        bse_presentation_candidates,
        key=lambda item: bse.parse_datetime(item) or datetime.min,
    ):
        row = bse.bse_row_base(announcement, "investor_presentation")
        try:
            save_row(
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

    for presentation in OFFICIAL_PRESENTATIONS:
        row = official_presentation_row(presentation)
        try:
            save_row(
                official_session,
                row,
                "investor_presentation",
                output_dir,
                digest_index,
                official=True,
            )
        except Exception as exc:
            failures += 1
            row.update({"status": "unavailable", "error": str(exc)})
            print(f"unavailable: {presentation['label']}: {exc}")
        else:
            print(f"{row['status']}: {row['local_file']}")
        presentation_rows.append(row)

    transcript_rows: list[dict[str, Any]] = []
    for announcement in bse_transcript_candidates:
        row = bse.bse_row_base(announcement, "earnings_call_transcript")
        try:
            save_row(
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
        windows=window_metadata,
        announcements_count=len(announcements),
        annual_rows=annual_rows,
        presentation_rows=presentation_rows,
        transcript_rows=transcript_rows,
        excluded_meetings=excluded_meetings,
        official_status=official_status,
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

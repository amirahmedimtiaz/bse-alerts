from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any


def send_announcement_email(announcement: dict[str, Any]) -> None:
    sender = os.environ["EMAIL_SENDER"]
    password = os.environ["EMAIL_PASSWORD"]
    receiver = os.environ["EMAIL_RECEIVER"]
    company_name = announcement["_company_name"]
    subject = announcement["_subject"]
    published = announcement.get("_published", "Unknown")
    category = announcement.get("_category", "Unknown")
    headline = announcement.get("_headline", "")
    page_url = announcement.get("_page_url", "")
    pdf_url = announcement.get("_pdf_url")

    exchange = announcement.get("_exchange", "BSE")
    links = [f"{exchange} announcement page: {page_url}"]
    if pdf_url:
        links.append(f"PDF: {pdf_url}")
    body = "\n".join(
        [
            f"New {exchange} corporate announcement",
            "",
            f"Company: {company_name}",
            f"Published: {published}",
            f"Category: {category}",
            f"Subject: {subject}",
            "",
            headline.strip(),
            "",
            *links,
        ]
    )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = receiver
    message["Subject"] = f"[{exchange}] {company_name} — {subject}"
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


def send_test_email(announcement: dict[str, Any]) -> None:
    send_announcement_email(announcement)

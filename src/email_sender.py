from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from .bse_client import announcement_page_url, announcement_pdf_url


def send_announcement_email(announcement: dict[str, Any]) -> None:
    sender = os.environ["EMAIL_SENDER"]
    password = os.environ["EMAIL_PASSWORD"]
    receiver = os.environ["EMAIL_RECEIVER"]
    subject = announcement.get("NEWSSUB", "New BSE announcement")
    published = announcement.get("DT_TM", "Unknown")
    category = announcement.get("CATEGORYNAME", "Unknown")
    headline = announcement.get("HEADLINE") or announcement.get("MORE") or ""
    pdf_url = announcement_pdf_url(announcement)

    links = [f"BSE announcement page: {announcement_page_url(announcement)}"]
    if pdf_url:
        links.append(f"PDF: {pdf_url}")
    body = "\n".join(
        [
            "New BSE corporate announcement",
            "",
            f"Company: {announcement.get('_company_name', announcement.get('SLONGNAME', 'Unknown'))}",
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
    message["Subject"] = f"BSE alert: {subject}"
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


def send_test_email(announcement: dict[str, Any]) -> None:
    send_announcement_email(announcement)

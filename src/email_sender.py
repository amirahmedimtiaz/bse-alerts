from __future__ import annotations

import os
import hashlib
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any


def _single_message(announcement: dict[str, Any]) -> EmailMessage:
    sender = os.environ["EMAIL_SENDER"]
    receiver = os.environ["EMAIL_RECEIVER"]
    company_name = " ".join(str(announcement["_company_name"]).split())
    subject = " ".join(str(announcement["_subject"]).split())
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

    return message


class EmailSender:
    """Reuse one authenticated connection for a cycle; retain all filing links."""
    def __init__(self):
        self.smtp = None
        self.connection = None

    def send(self, announcements: list[dict[str, Any]]) -> str:
        if not announcements:
            raise ValueError("Cannot send an empty announcement batch")
        if len(announcements) == 1:
            message = _single_message(announcements[0])
        else:
            message = EmailMessage()
            message["From"] = os.environ["EMAIL_SENDER"]
            message["To"] = os.environ["EMAIL_RECEIVER"]
            message["Subject"] = f"[BSE/NSE] {len(announcements)} new corporate announcements"
            message.set_content("\n\n".join(_single_message(item).get_content() for item in announcements))
        identity = "|".join(sorted(
            f"{item.get('_exchange')}:{item.get('_company_name')}:{item.get('_id') or item.get('NEWSID') or item.get('seq_id')}"
            for item in announcements
        ))
        digest = hashlib.sha256(identity.encode()).hexdigest()
        domain = os.environ["EMAIL_SENDER"].rsplit("@", 1)[-1]
        message_id = f"<{digest}@{domain}>"
        message["Message-ID"] = message_id
        if self.smtp is None:
            self.connection = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30)
            self.smtp = self.connection.__enter__()
            self.smtp.login(os.environ["EMAIL_SENDER"], os.environ["EMAIL_PASSWORD"])
        refused = self.smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        return message_id

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.__exit__(None, None, None)
            except (smtplib.SMTPException, OSError):
                pass
            finally:
                self.connection = self.smtp = None


def send_announcement_email(announcement: dict[str, Any]) -> None:
    sender = EmailSender()
    try:
        sender.send([announcement])
    finally:
        sender.close()


def send_test_email(announcement: dict[str, Any]) -> None:
    send_announcement_email(announcement)

from unittest.mock import patch

from src.email_sender import EmailSender, send_announcement_email


@patch.dict(
    "os.environ",
    {
        "EMAIL_SENDER": "sender@example.com",
        "EMAIL_PASSWORD": "password",
        "EMAIL_RECEIVER": "receiver@example.com",
    },
)
@patch("src.email_sender.smtplib.SMTP_SSL")
def test_email_contains_links(smtp_ssl):
    smtp_ssl.return_value.__enter__.return_value.send_message.return_value = {}
    send_announcement_email(
        {
            "_company_name": "Test Company Ltd",
            "_subject": "Test announcement",
            "_published": "2026-01-01 10:00:00",
            "_category": "General",
            "_headline": "Headline text",
            "_page_url": "https://example.com/announcements",
            "_pdf_url": "https://example.com/file.pdf",
            "_exchange": "BSE",
        }
    )

    message = smtp_ssl.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "Test announcement" in message["Subject"]
    assert "file.pdf" in message.get_content()
    assert "Test Company Ltd" in message.get_content()
    assert "https://example.com/announcements" in message.get_content()


@patch.dict(
    "os.environ",
    {
        "EMAIL_SENDER": "sender@example.com",
        "EMAIL_PASSWORD": "password",
        "EMAIL_RECEIVER": "receiver@example.com",
    },
)
@patch("src.email_sender.smtplib.SMTP_SSL")
def test_batch_accepts_bse_subject_with_embedded_newline(smtp_ssl):
    smtp_ssl.return_value.__enter__.return_value.send_message.return_value = {}
    item = {
        "_id": "filing-one",
        "_company_name": "Test Company Ltd",
        "_subject": "Allotment by West Bengal Industrial Development \r\nCorporation",
        "_published": "2026-09-26 10:00:00",
        "_category": "General",
        "_headline": "Filing details",
        "_page_url": "https://example.com/announcements",
        "_pdf_url": None,
        "_exchange": "BSE",
    }
    client = EmailSender()
    try:
        client.send([item, {**item, "_id": "filing-two"}])
    finally:
        client.close()

    message = smtp_ssl.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "Development Corporation" in message.get_content()
    assert "\r" not in message.get_content()

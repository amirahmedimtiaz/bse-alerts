from unittest.mock import patch

from src.email_sender import send_announcement_email


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
    send_announcement_email(
        {
            "NEWSID": "one",
            "NEWSSUB": "Test announcement",
            "ATTACHMENTNAME": "file.pdf",
            "_company_name": "Test Company Ltd",
            "_announcement_url": "https://example.com/announcements",
        }
    )

    message = smtp_ssl.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "Test announcement" in message["Subject"]
    assert "file.pdf" in message.get_content()
    assert "Test Company Ltd" in message.get_content()
    assert "https://example.com/announcements" in message.get_content()

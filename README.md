# BSE Announcement Alert

This service checks the companies listed in `companies.json` for corporate
announcements on BSE and emails links to new announcements.

It checks only announcements published on the current date in India Standard
Time. The first scheduled run creates a baseline and does not send historical
alerts. Later runs email only unseen announcements.

## Local commands

Install dependencies and run either command from the repository root:

```text
pip install -r requirements.txt
python -m src.main scan
python -m src.main test-email
```

Required environment variables are `EMAIL_SENDER`, `EMAIL_PASSWORD`, and
`EMAIL_RECEIVER`. For Gmail, `EMAIL_PASSWORD` must be an App Password.

GitHub Actions runs the scan every five minutes. Use the **Send Test BSE
Email** workflow under the Actions tab to send the latest available
announcement immediately after deployment. The test workflow can use a recent
announcement when BSE has none published today; this does not change the
today-only behavior of the scheduled scanner.

## Adding a company

Add its name, BSE scrip code, and corporate-announcements URL to
`companies.json`. The next scheduled run will create a baseline for that
company without sending historical alerts.

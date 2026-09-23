# BSE Announcement Alert

This service checks the companies listed in `companies.json` for corporate
announcements on BSE/NSE and emails links to new announcements.

It tracks successful collection dates, overlaps yesterday, and catches up after
outages. Newly added companies are baselined without historical email. Pending
emails survive failures and date changes. See [Alert operations](ALERT_OPERATIONS.md)
for state migration, deployment, pause/resume, email batching, and scaling limits.

## Local commands

Install dependencies and run either command from the repository root:

```text
pip install -r requirements.txt
python -m src.main scan
python -m src.main test-email
```

Required environment variables are `EMAIL_SENDER`, `EMAIL_PASSWORD`, and
`EMAIL_RECEIVER`. For Gmail, `EMAIL_PASSWORD` must be an App Password.

GitHub Actions runs a worker that polls every five minutes and dispatches its
successor before its time limit, with cron as a recovery watchdog. Transactional
state is checkpointed on the separate `alert-state` branch. Production verification
should use the announcement workflow; local `scan` maintains a separate SQLite
ledger and sends real emails. The **Send Test BSE Email** workflow sends a recent
filing explicitly and does not use the production delivery ledger or quota.

## Adding a company

Add its name, BSE scrip code, and corporate-announcements URL to
`companies.json`. The next scheduled run will create a baseline for that
company without sending historical alerts.

Insolation Energy Ltd is tracked as BSE scrip `543620`. To download all
currently available BSE earnings-call transcript PDFs into
`~/Investing/Insolation Energy`, run:

```text
python scripts/download_insolation_energy_earnings_call_transcripts.py
```

The downloader walks the complete BSE announcement history, excludes call
intimations and audio-recording outcomes, tries both BSE attachment archives,
and writes `Insolation_Energy_Earnings_Call_Transcripts.json` as an audit
manifest beside the PDFs. Use `--output-dir` to choose another destination.

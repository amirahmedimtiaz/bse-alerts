# Alert operations

The production watchlist remains `companies.json`. The GitHub Actions worker
polls every five minutes for up to five hours, then explicitly dispatches its
successor. A cron watchdog also starts recovery runs. One concurrency group
prevents overlapping workers. Queued runs check out current main; active workers
notice changes to code or the watchlist before their next scan and hand off.
This substantially reduces dependence on cron dispatch, but does not guarantee
uptime during GitHub outages. The workflow has a 330-minute safety timeout.

To deploy: push the tested code to main, then dispatch `announcement-alert.yml`.
For an initial short verification run set `cycles=1`; its successor uses the
normal 60-cycle duration. Inspect the workflow summary and the `alert-state`
branch's `alerts.json`, including its `metadata` health entry. A running worker
is expected to stay in progress for hours. A cycle with fetch/send failures
checkpoints successful work and finishes with a failed job. The successor is
dispatched only after a successful worker. A failed job retains successful
collection and queued email state. The watchdog retries on its next scheduled
dispatch (nominally every twenty minutes, subject to GitHub schedule delays).
This avoids repeated self-dispatch when an exchange blocks every request. For
a checkpoint failure, external delivery stops immediately.

To pause: set repository Actions variable `ALERTS_PAUSED` to `true`, then cancel
the current run. Cancellation intentionally does not dispatch a successor.
Setting the variable alone affects future jobs; the active job continues until
it is cancelled or finishes. Remove the variable or set it to `false` and
dispatch the workflow to resume. Do not delete the state branch.

## Collection and recovery

The scanner uses four threads, pooled per-thread HTTP sessions, at most two
requests/second per exchange host, bounded transient retries, and Retry-After
handling. BSE pagination checks the reported ROWCNT and detects repeated or
incomplete pages. An invalid response fails collection instead of marking it
as empty. NSE still relies on the history returned by its existing quote API;
upstream truncation/completeness is not proven by this application.

BSE's announcement API requires requests to carry the current website's
browser context (`Origin`, announcement-page `Referer`, and a browser user
agent). The default user agent was verified against the live API on 26 September
2026. If BSE changes its access checks, set `BSE_BROWSER_USER_AGENT` to a
verified working value and confirm a complete read-only scan before treating
the worker as recovered. A 403 does not advance the collection watermark.

Successful collection dates are stored per company. Every scan overlaps the
previous date; an outage spanning many days is recovered in seven-day chunks.
The watermark advances only for successfully collected companies. New companies
are baselined without historical email. On first migration, existing legacy IDs
are preserved and existing companies are checked from yesterday onward, so
previously missed recent filings can generate catch-up alerts. Earlier unknown
gaps are not automatically reconstructed. Corrected filings with the same ID
are not treated as new announcements.

## State and delivery

SQLite transactions hold the deduplication ledger, collection dates, outbox and
rolling email counts. Hosted workers restore/export a JSON snapshot on the
separate `alert-state` branch; production state no longer creates commits on
main. The original `state/seen_announcements.json` is retained for migration
and historical reference; it is no longer the live alert ledger. Git fast-forward
pushes provide durable checkpoints; conflicts stop delivery rather than
overwriting another writer. Do not run an independent production sender against
the same watchlist concurrently.

The outbox is persisted before sending. Each successful batch is checkpointed
before sending the next. Failed mail stays queued even after midnight and is
retried in subsequent cycles. A maximum of 20 announcements share an email,
each with its company, headline, date and source/PDF links. A single announcement
retains the original subject format. One authenticated SMTP connection is reused
per cycle. At most 450 emails are sent in a rolling 24 hours; excess batches stay
queued. This conservative cap does not account for messages sent outside this
worker or guarantee that Gmail will accept every message. SMTP errors remain
visible and retryable. Quota deferral is reported separately from an error.

Delivery is at-least-once. A crash after SMTP accepts a message but before its
remote checkpoint can repeat that batch. Stable Message-ID values help identify
duplicates but do not guarantee recipient-side suppression. Sent IDs are retained;
the snapshot grows over time. This is a practical persistent checkpoint backend
for the present deployment, not a demonstrated 5,000-company storage/feed design.

## Commands and limits

`python -m src.main validate` is read-only. `python -m src.main scan` sends a
single local cycle using `state/alerts.sqlite3`; it does not import live GitHub
state automatically. Do not use it to verify production delivery; use the
workflow. `python -m src.worker --cycles 1` uses the remote state branch and sends
real queued alerts. Secrets remain environment variables.

`python -m pytest -q -m 'not integration'` runs offline regression tests.
API integration tests are explicitly separate. More companies can be configured,
but 1,000–5,000-company five-minute coverage has not been load-tested. Default
request pacing alone requires at least ~8.3 minutes for 1,000 companies on one
exchange, before pagination and retries. For that scale, first verify an
exchange-wide feed or licensed provider and its allowed volume; then size the
worker and persistent database from measured latency/filing volume. Do not simply
raise concurrency to evade exchange limits.

GitHub references: [workflow dispatch](https://docs.github.com/en/actions/concepts/security/github_token),
[job limits](https://docs.github.com/en/actions/reference/limits), and
[public-repository billing](https://docs.github.com/en/actions/concepts/billing-and-usage).

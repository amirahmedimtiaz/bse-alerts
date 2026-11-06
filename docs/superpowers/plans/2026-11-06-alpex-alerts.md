# Alpex ownership alerts implementation
Implement the approved conversation plan: official NSE bulk/block, PIT, SAST29/31, shareholding and capital filings; five-minute detection; initial 30 calendar days summary; durable outbox; feed outage notices; source guide/CSV/JSON; deploy and verify persisted state and mailbox.

## Progress ledger
- Baseline: 40 focused tests passed. Native worktree failed because conversation cwd is not a repo; fallback /tmp/alpex-trade-alerts on feat/alpex-trade-alerts.
- Task 1: verify live endpoints and normalize source records.
- Task 2: durable collection, corrections, initial summary, health and email integration.
- Task 3: tests, source report, review and production rollout.
- Ruling: keep schema version 1 and use namespaced metadata and announcement keys, preserving all existing state and deliveries.
- Task 1 complete: current endpoints discovered from NSE's own corporate-filings.js and bulk-block-deals-short-selling.js; seven live feeds returned valid Alpex-filtered responses. Screenshot verified with full CSV precision.
- Task 2 complete: schema-v1 namespaced archive/outbox, corrections including reversions, same-ID capital correction routing, transactional per-source rendering, warning/recovery and report exporter implemented.
- Ruling: anonymous typed XBRL contexts without a shareholder name are retained as unmatched-context markers and excluded from comparisons; row numbers do not establish identity.
- Ruling: use bounded curl subprocess transport for new adapters; requests timed out locally while curl retrieved official endpoints. No shell interpolation, host whitelist, HTTPS-only redirects, size/time limits and exchange pacing.
- Independent review: Important findings fixed with failing-then-passing regression tests: render failure consuming unsent event; same-ID capital corrections; unchanged-timestamp prior shareholding revisions. Source reversions also tested and fixed. No deferred Critical/Important findings.
- Task 3 verification: 78 offline tests passed, 106 integration tests deselected; diff whitespace checks clean. Read-only live report: six archived events (two bulk rows, four shareholding snapshots), all seven feed responses successful. Production deployment verification follows.
- Task 3 complete / production verification: code c201e120 deployed on main. Old worker handed off safely to run42208398474; hosted pipeline passed66 tests. First production cycle finished2026-11-06T11:13:23Z with seven successful source feeds, six archived events, one initial summary sent and zero pending. Gmail inbox readback verified message1a1b0902db4c94a6, both full-precision prices and the June/September comparison. Follow-up cycle finished11:17:50Z with zero Alpex events queued and no pending deliveries; the initial summary remained the sole ALPEX:ownership sent record. Existing announcement delivery continued. Exported HTML/CSV/JSON and verification JSON are in reports/Alpex_Solar_Trade_Ownership_*; regenerate with src.alpex_report --remote. No deferred review findings.

## HTML email revision
User rejected the plain-text filing dump and requested a beautiful formatted email.
Replaced the Alpex visible layout with a responsive table-based HTML newsletter:
navy/teal header, two summary tiles, buy/sell trade cards, exact prices, Indian
share counts, approximate crore values, a compact ownership comparison and
labelled source links. All source text is escaped; no remote fonts/images/scripts.
Plain-text MIME alternatives and existing delivery identities remain available.
Structured view data is persisted with new outbox items; older pending payloads
render safely through a fallback. Capital filings use the same layout.

Verification:85 offline tests passed. Desktop and390px previews visually inspected;
390px body had zero overflowing content. Independent review found missing newly
or no-longer-disclosed holder names; fixed with a failing/passing test and explicit
qualification that changes do not prove investment entry/exit. Reported monetary
values retain paise. HTML resend requested by user was sent through Gmail and read
back in the inbox: message1a1b0d32baad80f6; multipart/alternative includes12998-byte
HTML, source links and both exact prices. Preview saved in reports/Alpex_Solar_Email_Preview.html.

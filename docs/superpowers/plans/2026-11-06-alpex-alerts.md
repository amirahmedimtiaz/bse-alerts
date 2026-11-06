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

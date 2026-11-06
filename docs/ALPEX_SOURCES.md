# Alpex official-source verification — 6 November 2026

Issuer: NSE SME ALPEXSOLAR, ISIN INE0R4701017.

The adapters follow the exchange's own public website requests, inspected at:
- https://www.nseindia.com/dist/js/sections/bulk-block-deals-short-selling.js
- https://www.nseindia.com/dist/js/sections/corporate-filings.js

| Feed | Public request path | Live verification |
| --- | --- | --- |
| Bulk | /api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&symbol=ALPEXSOLAR&from=08-10-2026&to=06-11-2026&csv=true | Two full CSV rows; Faraday 148000 BUY @663.26 and Zeta155000 SELL @663.28 on4Nov |
| Block | Same path, optionType=block_deals | Valid header, zero matching rows |
| Insider | /api/corporates-pit-gg?index=sme&symbol=ALPEXSOLAR&from_date=08-10-2026&to_date=06-11-2026 | Valid empty data table; independently checked populated SME feed (594 records without issuer filter), downloaded populated PIT XML to verify multi-person transaction fields |
| SAST29 | /api/corporate-sast-reg29?index=sme&symbol=ALPEXSOLAR&from_date=08-10-2026&to_date=06-11-2026 | Valid empty data table; unfiltered SME response populated |
| Encumbrances | /api/corporate-pledgedata-sast3132?index=sme&symbol=ALPEXSOLAR&from_date=08-10-2026&to_date=06-11-2026 | Valid empty data table; unfiltered response populated; negative result is not absence of existing pledges |
| Shareholding | /api/corporate-share-holdings-master?index=sme&symbol=ALPEXSOLAR&from_date=06-11-2025&to_date=06-11-2026 | Four snapshots with XBRL financial facts; September snapshot published16October compares with June |
| Capital | /api/NextApi/apiClient/GetQuoteApi?functionName=getCorporateAnnouncement&symbol=ALPEXSOLAR&marketApiType=sme | Valid issuer history; no matching capital filings in initial30-day window |

The retired historical deal API returned HTML. Equity-only legacy insider requests
must not be used to infer SME coverage. Daily bulk.csv/block.csv are latest-session
files and cannot establish historical completeness. The on-page historical deal
table is limited to70 rows; the adapter uses the full CSV download.

`--output-dir` report exports preserve exact source URLs, statement dates, detection
times and original/revised facts. API availability is not a permanent guarantee;
each cycle validates responses and leaves failed-source watermarks unchanged.
Source publications can be late or revised. No observer can infer complete current
holdings by adding publicly reported trades. Shareholding comparisons exclude
anonymous typed rows and do not establish open-market trading. Original and revised
snapshot rows coexist; latest detected revision wins timestamp ties.

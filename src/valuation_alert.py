"""Trailing-twelve-month P/E alerts for NSE-listed stocks.

P/E = NSE last price x NSE issued shares / configured TTM PAT (Rs crore).
One email when P/E first drops below ``max_pe``; re-armed only after P/E
recovers to ``rearm_pe`` (hysteresis prevents repeated mails near the line).
A newer financial-results filing than ``last_results_filed`` triggers a
one-time "TTM earnings out of date" email; alerts continue on the stale basis
and say so. Repeated price-fetch failures trigger a one-time health email.

Usage: python -m src.valuation_alert --config valuation_alerts.json --state STATE.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

import requests

IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
QUOTE_URL = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"
ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"
FAILURE_NOTICE_AFTER = 6  # consecutive failed checks (~3 market hours at 30-minute cadence)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
    return session


def _get_json(session: requests.Session, url: str, params: dict, symbol: str) -> Any:
    referer = f"https://www.nseindia.com/get-quote/equity/{symbol}"
    last: Exception | None = None
    for _ in range(3):
        try:
            response = session.get(url, params=params, headers={"Referer": referer}, timeout=(10, 30))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
    raise RuntimeError(f"NSE request failed for {symbol}: {last}")


def fetch_quote(symbol: str, session: requests.Session | None = None) -> dict[str, Any]:
    session = session or _session()
    payload = _get_json(session, QUOTE_URL, {"functionName": "getSymbolData", "marketType": "N",
                                             "series": "EQ", "symbol": symbol}, symbol)
    try:
        row = payload["equityResponse"][0]
        meta, trade = row["metaData"], row["tradeInfo"]
        price = float(trade.get("lastPrice") or meta.get("closePrice") or 0)
        shares = int(trade["issuedSize"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Unexpected NSE quote payload for {symbol}: {exc}") from exc
    if str(meta.get("symbol", "")).upper() != symbol.upper():
        raise RuntimeError(f"NSE quote returned symbol {meta.get('symbol')!r}, expected {symbol}")
    if price <= 0 or shares <= 0:
        raise RuntimeError(f"NSE quote for {symbol} has no usable price/share count")
    return {"price": price, "shares": shares, "as_of": row.get("lastUpdateTime") or "",
            "previous_close": meta.get("previousClose")}


def fetch_result_filings(symbol: str, session: requests.Session | None = None) -> list[dict[str, Any]]:
    session = session or _session()
    rows = _get_json(session, ANNOUNCEMENTS_URL, {"index": "equities", "symbol": symbol}, symbol)
    if not isinstance(rows, list):
        raise RuntimeError(f"NSE announcements for {symbol} were not a list")
    return [r for r in rows if isinstance(r, dict) and is_results_filing(r)]


def is_results_filing(row: dict[str, Any]) -> bool:
    text = f"{row.get('desc', '')} {row.get('attchmntText', '')}".lower()
    return ("financial results for the period ended" in text
            or "integrated filing- financial" in text
            or "integrated filing - financial" in text)


def pe_ratio(price: float, shares: int, ttm_pat_crore: float) -> float:
    if ttm_pat_crore <= 0:
        raise ValueError("TTM PAT must be positive for a P/E alert")
    return price * shares / 1e7 / ttm_pat_crore


def _send(subject: str, body: str, key: str) -> None:
    sender, receiver = os.environ["EMAIL_SENDER"], os.environ["EMAIL_RECEIVER"]
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, receiver, subject
    message["Message-ID"] = f"<{hashlib.sha256(key.encode()).hexdigest()}@{sender.rsplit('@', 1)[-1]}>"
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, os.environ["EMAIL_PASSWORD"])
        refused = smtp.send_message(message)
    if refused:
        raise smtplib.SMTPRecipientsRefused(refused)


def _basis_lines(cfg: dict[str, Any], quote: dict[str, Any], pe: float) -> list[str]:
    mcap = quote["price"] * quote["shares"] / 1e7
    trigger_price = cfg["max_pe"] * cfg["ttm_pat_crore"] * 1e7 / quote["shares"]
    return [
        f"Last price: Rs {quote['price']:,.2f} (NSE, {quote['as_of']})",
        f"Shares outstanding: {quote['shares']:,} -> market cap Rs {mcap:,.1f} crore",
        f"TTM PAT: Rs {cfg['ttm_pat_crore']:,.2f} crore (earnings through {cfg['earnings_through']})",
        f"Basis: {cfg['ttm_basis']}",
        f"TTM P/E: {pe:.2f}x   Alert threshold: below {cfg['max_pe']}x (price below Rs {trigger_price:,.2f})",
        f"NSE quote: https://www.nseindia.com/get-quote/equity/{cfg['symbol']}",
        f"Results used: {cfg.get('results_source', 'n/a')}",
    ]


def check_alert(cfg: dict[str, Any], state: dict[str, Any], *, now: datetime,
                quote_fn: Callable[[str], dict[str, Any]] = fetch_quote,
                filings_fn: Callable[[str], list[dict[str, Any]]] = fetch_result_filings,
                send_fn: Callable[[str, str, str], None] = _send) -> list[str]:
    """Evaluate one configured alert; mutate ``state``; return sent subjects."""
    symbol, name = cfg["symbol"], cfg["name"]
    st = state.setdefault(symbol, {"armed": True, "consecutive_failures": 0,
                                   "failure_notified": False, "stale_notified": []})
    sent: list[str] = []
    stamp = now.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    try:
        quote = quote_fn(symbol)
    except Exception as exc:  # noqa: BLE001 - every failure must be counted and surfaced
        st["consecutive_failures"] = st.get("consecutive_failures", 0) + 1
        st["last_error"] = f"{stamp}: {exc}"
        if st["consecutive_failures"] >= FAILURE_NOTICE_AFTER and not st.get("failure_notified"):
            subject = f"[Valuation alert] {name}: price check failing"
            send_fn(subject, f"The P/E monitor could not read the NSE quote for {symbol} "
                    f"{st['consecutive_failures']} times in a row.\nLast error: {exc}\n"
                    "The alert cannot fire until this recovers.", f"fail:{symbol}:{stamp}")
            st["failure_notified"] = True
            sent.append(subject)
        raise
    if st.get("failure_notified"):
        send_fn(f"[Valuation alert] {name}: price check recovered",
                f"The NSE quote for {symbol} is readable again ({stamp}).", f"recover:{symbol}:{stamp}")
        sent.append("recovered")
    st.update(consecutive_failures=0, failure_notified=False, last_error=None)

    stale_rows: list[dict[str, Any]] = []
    try:
        stale_rows = [r for r in filings_fn(symbol)
                      if str(r.get("sort_date", "")) > cfg["last_results_filed"]]
    except Exception as exc:  # noqa: BLE001 - staleness check must not block the price alert
        st["last_filings_error"] = f"{stamp}: {exc}"
    new_stale = [r for r in stale_rows if str(r.get("seq_id") or r.get("attchmntFile"))
                 not in st.setdefault("stale_notified", [])]
    if new_stale:
        row = new_stale[0]
        subject = f"[Valuation alert] {name}: new results filed - update TTM earnings"
        send_fn(subject, "\n".join([
            f"{name} filed new financial results on NSE ({row.get('an_dt') or row.get('sort_date')}):",
            str(row.get("attchmntText", "")).strip(),
            str(row.get("attchmntFile", "")),
            "",
            f"The P/E alert still uses TTM PAT of Rs {cfg['ttm_pat_crore']} crore through {cfg['earnings_through']}.",
            "Update ttm_pat_crore, earnings_through, results_source and last_results_filed in",
            "bse-alerts/valuation_alerts.json. Until then, alerts are labelled as using stale earnings.",
        ]), f"stale:{symbol}:{row.get('seq_id')}")
        st["stale_notified"].extend(str(r.get("seq_id") or r.get("attchmntFile")) for r in new_stale)
        sent.append(subject)

    pe = pe_ratio(quote["price"], quote["shares"], cfg["ttm_pat_crore"])
    st.update(last_checked=stamp, last_price=quote["price"], last_pe=round(pe, 3), shares=quote["shares"])
    if not st.get("setup_confirmed"):
        subject = f"[Valuation alert] {name}: P/E alert active (now {pe:.2f}x)"
        send_fn(subject, "\n".join([f"P/E alert configured for {name}. You will get one email when the "
                                    f"TTM P/E falls below {cfg['max_pe']}x; it re-arms after P/E "
                                    f"recovers to {cfg['rearm_pe']}x.", "", *_basis_lines(cfg, quote, pe)]),
                f"setup:{symbol}:{cfg['max_pe']}:{cfg['ttm_pat_crore']}")
        st["setup_confirmed"] = True
        sent.append(subject)
    if pe < cfg["max_pe"] and st.get("armed", True):
        stale_note = (["", "WARNING: newer results have been filed; this P/E uses the older TTM earnings."]
                      if stale_rows else [])
        subject = f"[Valuation alert] {name} TTM P/E {pe:.2f}x is below {cfg['max_pe']}x"
        send_fn(subject, "\n".join([f"{name} is trading below {cfg['max_pe']}x trailing-twelve-month earnings.",
                                    "", *_basis_lines(cfg, quote, pe), *stale_note]),
                f"trigger:{symbol}:{stamp}")
        st.update(armed=False, last_triggered=stamp)
        sent.append(subject)
    elif pe >= cfg["rearm_pe"] and not st.get("armed", True):
        st["armed"] = True
        st["rearmed"] = stamp
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="valuation_alerts.json")
    parser.add_argument("--state", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() and state_path.read_text().strip() else {}
    failed = False
    now = datetime.now(timezone.utc)
    for cfg in config["alerts"]:
        try:
            for subject in check_alert(cfg, state, now=now):
                print("sent:", subject)
            st = state[cfg["symbol"]]
            print(f"{cfg['symbol']}: price {st['last_price']} P/E {st['last_pe']} armed={st['armed']}")
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"{cfg['symbol']}: check failed: {exc}", file=sys.stderr)
        finally:
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

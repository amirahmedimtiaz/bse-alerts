from datetime import datetime, timezone

import pytest

from src import valuation_alert as va

CFG = {"symbol": "VIVIANA", "name": "Viviana", "max_pe": 7.5, "rearm_pe": 7.725,
       "ttm_pat_crore": 61.88, "earnings_through": "2026-09-30", "ttm_basis": "test",
       "last_results_filed": "2026-11-02 20:01:35"}
SHARES = 10_124_800
NOW = datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)


def quote(price):
    return lambda _symbol: {"price": price, "shares": SHARES, "as_of": "x"}


def run(state, price, filings=(), sent=None, quote_fn=None):
    sent = [] if sent is None else sent
    va.check_alert(CFG, state, now=NOW, quote_fn=quote_fn or quote(price),
                   filings_fn=lambda _s: list(filings), send_fn=lambda s, b, k: sent.append((s, b)))
    return sent


def test_pe_matches_manual_calculation():
    assert va.pe_ratio(735, SHARES, 61.88) == pytest.approx(735 * 1.01248 / 61.88, rel=1e-6)


def test_setup_email_once_then_quiet_above_threshold():
    state = {}
    assert len(run(state, 735)) == 1 and "alert active" in run({}, 735)[0][0]
    assert run(state, 735) == []


def test_fires_once_below_threshold_and_rearms_with_hysteresis():
    state = {"VIVIANA": {"armed": True, "setup_confirmed": True}}
    low = 7.4 * 61.88e7 / SHARES
    sent = run(state, low)
    assert len(sent) == 1 and "below 7.5x" in sent[0][0]
    assert run(state, low) == []                       # no repeat while below
    assert run(state, 7.6 * 61.88e7 / SHARES) == []    # between thresholds: still disarmed
    assert state["VIVIANA"]["armed"] is False
    run(state, 7.8 * 61.88e7 / SHARES)                 # recovers above re-arm level
    assert state["VIVIANA"]["armed"] is True
    assert len(run(state, low)) == 1                   # fires again on the next crossing


def test_new_results_filing_notified_once_and_flagged_in_trigger():
    state = {"VIVIANA": {"armed": True, "setup_confirmed": True}}
    rows = [{"seq_id": "1", "sort_date": "2027-02-12 20:00:00", "attchmntText": "results"},
            {"seq_id": "0", "sort_date": "2026-11-02 20:01:35", "attchmntText": "old"}]
    sent = run(state, 7.0 * 61.88e7 / SHARES, rows)
    assert any("update TTM earnings" in s for s, _ in sent)
    trigger = [b for s, b in sent if "below" in s][0]
    assert "older TTM earnings" in trigger
    assert not any("update TTM" in s for s, _ in run(state, 800, rows))


def test_repeated_quote_failures_send_one_health_notice():
    state = {}
    sent = []

    def boom(_):
        raise RuntimeError("403")
    for _ in range(va.FAILURE_NOTICE_AFTER + 2):
        with pytest.raises(RuntimeError):
            run(state, 0, sent=sent, quote_fn=boom)
    assert [s for s, _ in sent] == ["[Valuation alert] Viviana: price check failing"]
    run(state, 735, sent=sent)
    assert "recovered" in sent[1][0]


def test_results_filing_classifier():
    assert va.is_results_filing({"desc": "Outcome of Board Meeting",
                                 "attchmntText": "has submitted to the Exchange, the financial results for the period ended September 30, 2026."})
    assert not va.is_results_filing({"desc": "Outcome of Board Meeting", "attchmntText": "Board meeting held"})

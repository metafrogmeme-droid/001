"""Four chat tools answered a failed or empty read with a verdict.

`scan_market` printed "No signals detected" when both venues had failed to
answer — the scanner folds a venue that did not answer into an empty half of
the universe, so an empty list was two facts wearing one sentence — and
overwrote the last good scan with [] for every other reader. `rejected_trades`
printed "No rejections yet. The risk gate is working." over an empty record: a
count read as a verdict, and the OPERATOR's record for every caller. `whynot`
keyed a perpetual's rejection as `HYPE:USDT` on one side and looked up `HYPE`
on the other, so no perp rejection was ever found by name. The journal card
filed a measured break-even as a LOSS, counted it in the L column, and labelled
the last-N rate "Session Summary" as though it were the record. And the chat
tools refused `HYPE/USDT:USDT` — the spelling the positions card itself prints.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from bot.core import engine as engine_mod
from bot.core import market_scanner as ms
from bot.core.live_executor import normalize_symbol
from bot.nlp.chat_tools import _normalise_symbol
from bot.skills.skill_registry import RejectedTradesSkill, ScanMarketSkill, TradeJournalSkill, WhyNotSkill
from tests.source_scan import code_only


def _run(coro):
    return asyncio.run(coro)


# ── scan_market ─────────────────────────────────────────────────────────────

def _scan_engine(signals, errors, last=None):
    async def scan():
        return signals
    scanner = SimpleNamespace(scan=scan)
    if errors is not None:
        scanner.last_fetch_errors = errors
    return SimpleNamespace(scanner=scanner, _last_scan_signals=list(last or []))


def test_both_venues_failing_is_a_failed_read_not_a_quiet_market():
    eng = _scan_engine([], {"spot": "TimeoutError", "futures": "NetworkError"}, last=["kept"])
    out = _run(ScanMarketSkill().execute(eng))
    assert "Could not read the market" in out and "spot: TimeoutError" in out and "futures: NetworkError" in out
    assert "No signals detected" not in out
    assert eng._last_scan_signals == ["kept"], "an absence erased the last good scan"


def test_one_venue_failing_is_a_partial_and_the_card_says_so():
    eng = _scan_engine([], {"spot": "TimeoutError", "futures": None})
    out = _run(ScanMarketSkill().execute(eng))
    assert "No signals detected" in out, "a read half of the universe with nothing in it is a reading"
    assert "spot: TimeoutError" in out and "part of the universe" in out


def test_both_venues_read_and_nothing_moving_stays_the_honest_sentence():
    eng = _scan_engine([], {"spot": None, "futures": None})
    out = _run(ScanMarketSkill().execute(eng))
    assert "No signals detected" in out and "part of the universe" not in out
    assert eng._last_scan_signals == []


def test_a_scanner_that_records_nothing_gets_the_old_behaviour():
    eng = _scan_engine([], None)
    assert "No signals detected" in _run(ScanMarketSkill().execute(eng))


def test_the_scanner_records_which_venue_answered_right_after_the_gather():
    src = code_only(inspect.getsource(ms))
    i = src.index("return_exceptions=True")
    j = src.index("self.last_fetch_errors = {")
    assert 0 < j - i < 900, "the venue outcomes are not recorded beside the gather"
    assert "type(spot_result).__name__" in src and "type(futures_result).__name__" in src, \
        "the card would print a driver's message (a host, a key) instead of the type"


# ── rejected_trades ─────────────────────────────────────────────────────────

def _risk(history):
    return SimpleNamespace(rejection_history=list(history))


def test_an_empty_rejection_record_is_a_count_not_a_verdict():
    eng = SimpleNamespace(risk=_risk([]))
    out = _run(RejectedTradesSkill().execute(eng))
    assert "working" not in out.lower()
    assert "not a verdict" in out


def test_the_callers_own_record_is_read_when_the_engine_has_one():
    mine = _risk([{"asset": "SOL/USDT", "direction": "LONG", "checks_failed": ["x"], "confidence": 0.5}])
    shared = _risk([])
    eng = SimpleNamespace(risk=shared, risk_for=lambda uid="", venue="": mine if uid == "7" else shared)
    out = _run(RejectedTradesSkill().execute(eng, user_id="7"))
    assert "SOL/USDT" in out and "engine-wide" not in out
    # and a caller the engine maps to the shared record is told so
    out2 = _run(RejectedTradesSkill().execute(eng, user_id="9"))
    assert "engine-wide record" in out2


# ── whynot ──────────────────────────────────────────────────────────────────

def test_a_perpetuals_rejection_is_found_by_every_spelling_of_its_name():
    key = normalize_symbol("HYPE/USDT:USDT")
    assert key == "HYPE"
    rej = {"symbol": "HYPE/USDT:USDT", "direction": "LONG", "confidence": 0.6,
           "entry_price": 30.0, "stop_loss": 28.0, "take_profit": 35.0,
           "checks_passed": [], "checks_failed": ["min_rr: 1.1 < 1.5"]}
    eng = SimpleNamespace(_last_rejections={key: rej})
    for spelling in ("HYPE", "hype", "HYPE/USDT", "hype/usdt", "HYPE/USDT:USDT"):
        out = _run(WhyNotSkill().execute(eng, symbol=spelling))
        assert "No rejection found" not in out, spelling
        assert "min_rr" in out, spelling


def test_the_engine_keys_rejections_with_the_same_normaliser():
    src = code_only(inspect.getsource(engine_mod))
    assert "symbol_key = normalize_symbol(idea.asset)" in src
    assert 'idea.asset.replace("/USDT", "")' not in src, "the private key spelling is back"


# ── trade_journal ───────────────────────────────────────────────────────────

class _Dir:
    value = "LONG"


def _trade(pnl, i):
    t0 = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=i)
    return SimpleNamespace(asset="ETH/USDT", direction=_Dir(), pnl=pnl, entry_price=100.0,
                           exit_price=100.0 + pnl, quantity=1.0, opened_at=t0,
                           closed_at=t0 + timedelta(hours=2))


def test_a_measured_break_even_is_flat_not_a_loss_and_the_rate_is_over_decided_closes():
    hist = [_trade(10.0, 0), _trade(-5.0, 1), _trade(0.0, 2)]
    eng = SimpleNamespace(portfolio=SimpleNamespace(_history=hist))
    out = _run(TradeJournalSkill().execute(eng))
    assert "1W / 1L / 1F" in out, out
    assert "50%" in out and "of 2 decided" in out
    assert "FLAT" in out
    assert "Session Summary" not in out and "Last 3 closes" in out


def test_the_rate_is_labelled_as_the_window_not_the_record():
    hist = [_trade(1.0, i) for i in range(20)]
    eng = SimpleNamespace(portfolio=SimpleNamespace(_history=hist))
    out = _run(TradeJournalSkill().execute(eng, count=10))
    assert "(10/20)" in out and "Last 10 closes" in out and "10W / 0L" in out


def test_a_window_with_no_decided_close_has_no_rate():
    hist = [_trade(0.0, 0), _trade(0.0, 1)]
    eng = SimpleNamespace(portfolio=SimpleNamespace(_history=hist))
    out = _run(TradeJournalSkill().execute(eng))
    assert "0W / 0L / 2F" in out and "no decided close" in out and "%" not in out.split("Record")[1].split("\n")[0]


# ── the symbol the positions card prints ────────────────────────────────────

def test_the_chat_tools_accept_the_spelling_the_positions_card_prints():
    assert _normalise_symbol("HYPE/USDT:USDT") == "HYPE/USDT"
    assert _normalise_symbol("btc") == "BTC/USDT"
    assert _normalise_symbol("$sol") == "SOL/USDT"
    assert _normalise_symbol(":USDT") is None
    assert _normalise_symbol("not a symbol!") is None

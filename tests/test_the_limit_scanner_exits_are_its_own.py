"""The Limit Entry Scanner's position runs its own exits, never the house's.

Its first card measured the scanner's entries with the house exits: 30 of 52
positions were closed by the intraday time stop's four-hour rule written for
another strategy, and the card would have published that under the scanner's
name. Its own exits, read off the playbook's description (its source was not
provided): 35% at +3.5%, half of the rest at +5% (the v0.1 share, a reading),
the rest at +20%; the stop steps to +1.5% at +2%, +3% at +4%, +4.5% at +6%;
after the scale-out the rest trails two ATR behind its best; a pullback entry
closes at four hours, as v0.1's time stop closed every position at its age.

Each bar reads the stop first against its adverse extreme (a gap fills at the
open), then the targets against its favourable one, then raises the stop for
the next bar, then reads the time stops at its close. The bar that filled the
limit traded before the fill too, so its favourable side counts at its close.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.core import limit_scanner as ls

P = ls.ScannerParams()
H = 3_600_000


def _state(side=ls.LONG, entry=100.0, stop=97.5, atr=1.0):
    return ls.ExitState(side=side, entry=entry, stop=stop, atr=atr, opened_ms=0)


def _bar(state, hour, *, high, low, open_=None, close=None):
    mid = (high + low) / 2
    return ls.step_exit(state, P, open_=open_ if open_ is not None else mid, high=high,
                        low=low, close=close if close is not None else mid,
                        close_ms=hour * H)


def _sum(fills):
    return [(round(f.share, 6), round(f.price, 6), f.reason) for f in fills]


def test_the_ladder_closes_its_shares_at_its_prices():
    s = _state()
    assert _sum(_bar(s, 1, high=103.6, low=100.5)) == [(0.35, 103.5, "SCALE_OUT")]
    assert _sum(_bar(s, 2, high=105.2, low=103.9)) == [(0.325, 105.0, "TP1")]
    assert s.remaining == pytest.approx(0.325)
    assert _sum(_bar(s, 3, high=120.5, low=104.5)) == [(0.325, 120.0, "TP2")]
    assert s.remaining == 0.0
    # One bar that reaches all three closes all three, in order.
    s2 = _state()
    assert [f.reason for f in _bar(s2, 1, high=121.0, low=100.1)] == [
        "SCALE_OUT", "TP1", "TP2"]


def test_the_stop_is_read_first_and_a_gap_fills_at_the_open():
    s = _state()
    assert _sum(_bar(s, 1, high=104.0, low=97.0)) == [(1.0, 97.5, "SL")], \
        "a bar that touched both the stop and a target is a stop-out"
    gap = _state()
    assert _sum(_bar(gap, 1, open_=96.0, high=96.5, low=95.0)) == [(1.0, 96.0, "SL")]


@pytest.mark.parametrize("best,lock", [(102.0, 101.5), (104.0, 103.0), (106.0, 104.5)])
def test_the_stop_steps_up_with_the_move_and_says_so(best, lock):
    s = _state(atr=None)                               # no trail: the step alone
    _bar(s, 1, high=best, low=best - 0.3, close=best - 0.1)
    assert s.stop == pytest.approx(lock)
    (fill,) = _bar(s, 2, high=best, low=lock - 0.1)
    assert (fill.reason, fill.price) == ("LOCK_SL", pytest.approx(lock))
    # Below the first step the stop does not move.
    flat = _state()
    _bar(flat, 1, high=101.9, low=100.0)
    assert flat.stop == 97.5


def test_the_rest_trails_two_atr_only_after_the_scale_out():
    s = _state(atr=0.5)
    _bar(s, 1, high=103.4, low=100.2)                  # +3.4%: steps to +1.5% only
    assert s.stop == pytest.approx(101.5) and not s.trailing, \
        "best - 2 ATR is 102.4, and no trail runs before the scale-out"
    _bar(s, 2, high=104.8, low=103.0)                  # scales out; best 104.8
    assert s.scaled and not s.tp1_hit
    assert s.stop == pytest.approx(103.8) and s.trailing, "the trail above the +3% lock"
    (fill,) = _bar(s, 3, high=104.6, low=103.7)
    assert (fill.reason, fill.share, fill.price) == (
        "TRAILING_SL", pytest.approx(0.65), pytest.approx(103.8))


def test_a_pullback_entry_closes_at_four_hours_and_not_before():
    s = _state()
    for hour in (1, 2, 3):
        assert _bar(s, hour, high=100.8, low=99.6, close=100.2) == []
    assert _sum(_bar(s, 4, high=100.8, low=99.6, close=100.3)) == [
        (1.0, 100.3, "PULLBACK_TIME_STOP")]


def test_the_short_side_mirrors_every_level():
    s = _state(side=ls.SHORT, entry=100.0, stop=102.5)
    assert _sum(_bar(s, 1, high=99.5, low=96.4)) == [(0.35, 96.5, "SCALE_OUT")]
    # +3.6%: the first step (+1.5%, 98.5), then the trail 2 ATR above the best.
    assert s.stop == pytest.approx(98.4) and s.trailing
    stopped = _state(side=ls.SHORT, entry=100.0, stop=102.5)
    assert _sum(_bar(stopped, 1, high=103.0, low=99.0)) == [(1.0, 102.5, "SL")]


@pytest.mark.parametrize("bad", [
    {"scale_out_fraction": 1.2}, {"tp1_fraction": 0}, {"tp2_pct": 4.0},
    {"step_locks": [[2.0, 2.0]]}, {"step_locks": [[2.0]]}, {"step_locks": "2:1.5"},
    {"pullback_time_stop_hours": -4},
])
def test_exit_numbers_that_do_not_read_refuse_the_preset(bad):
    assert ls.params_from_preset({"scanner": "limit_entry", **bad}) is None


# ── in the backtest ─────────────────────────────────────────────────────────

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
SYMS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
SIGNAL = 110
PLAN = ls.EntryPlan("ETH/USDT:USDT", ls.LONG, 99.5, 97.0, 104.5, 0.025, 1.0, 82.0)


def _bars(sym, shape):
    out = []
    for i in range(140):
        o, h, low, c = shape.get(i, (100.0, 100.3, 99.7, 100.0))
        out.append(BacktestBar(timestamp=T0 + timedelta(hours=i), open=o, high=h, low=low,
                               close=c, volume=1_000.0, symbol=sym))
    return out


def _run(eth_shape, monkeypatch):
    real = ls.scan_pass
    calls: list = []

    def scan(snaps, params, held=frozenset()):
        calls.append(1)
        if len(calls) == SIGNAL - 99:
            return ls.Pass(ls.Regime(ls.LONG, 1.0, 2, True, "t"), (), PLAN, "")
        return real(snaps, params, held)

    monkeypatch.setattr(ls, "scan_pass", scan)
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry"),
                             symbols=list(SYMS))
    try:
        result = asyncio.run(pb.run({s: _bars(s, eth_shape if s.startswith("ETH") else {})
                                     for s in SYMS}))
    finally:
        pb.cleanup()
    return [t for t in result.trades if t.symbol.startswith("ETH")]


def test_a_scanner_position_scales_out_by_its_own_ladder(monkeypatch):
    fill = SIGNAL + 1
    shape = {
        # The fill bar: touches 99.5 and prints 104 too, before or after the
        # fill nobody knows, so its high is not the position's.
        fill: (100.0, 104.0, 99.4, 100.2),
        fill + 1: (100.2, 103.2, 100.0, 103.0),       # +3.7% from 99.5: scales out
        fill + 2: (103.0, 103.1, 102.6, 102.8),
    }
    trades = _run(shape, monkeypatch)
    reasons = [t.exit_reason for t in trades]
    assert reasons[0] == "SCALE_OUT", reasons
    assert trades[0].exit_time == T0 + timedelta(hours=fill + 1), "scaled out on the fill bar"
    assert "TP1" not in reasons and "TIME_STOP" not in reasons
    assert reasons[-1] in ("PULLBACK_TIME_STOP", "LOCK_SL", "TRAILING_SL")
    # The shares add up to the position.
    assert sum(t.quantity for t in trades) == pytest.approx(
        trades[0].quantity / 0.35, rel=1e-6)


def test_no_house_exit_reads_a_scanner_position(monkeypatch):
    """Flat after the fill: the house intraday stop and ladder would act on
    their own clocks; the scanner's four-hour close is what closes it."""
    trades = _run({SIGNAL + 1: (100.0, 100.3, 99.4, 99.9)}, monkeypatch)
    (t,) = trades
    assert t.exit_reason == "PULLBACK_TIME_STOP"
    assert t.exit_time == T0 + timedelta(hours=SIGNAL + 1 + 3), \
        "closed at the close of its fourth hour"


def test_the_exit_numbers_reach_every_engine(monkeypatch):
    pb = PortfolioBacktester(BacktestConfig(scanner="limit_entry",
                                            scanner_config={"tp2_pct": 12.0}),
                             symbols=list(SYMS))
    try:
        assert {e._scanner_exits.tp2_pct for e in pb._engines.values()} == {12.0}
    finally:
        pb.cleanup()
    house = PortfolioBacktester(BacktestConfig(), symbols=list(SYMS))
    try:
        assert {e._scanner_exits for e in house._engines.values()} == {None}
    finally:
        house.cleanup()
    assert dataclasses.replace(P, tp2_pct=12.0).tp2_pct == 12.0

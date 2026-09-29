"""The backtest's partial-TP ladder locks the stop the live ladder locks.

The backtest scales out through `_check_ladder_intrabar`, a bar-aware copy of
the live ladder. The TP1 breakeven-after-fees chapter made the live lock read
the position's round trip, and `_execute_fill` was taught to hand the backtest
ladder its own (`fee_round_trip_pct=2 x commission_pct`). The copy never read
it: it built TP1's lock as `entry * 0.001` itself. `--honest` turns this ladder
on for the frozen benchmark and charges a 0.12% round trip, so every
"breakeven" stop-out there booked -0.02% of notional (-0.1% on a plain run's
0.2% round trip). The only pin over it asserted that the argument STRING was
in the source.

These drives go through the real `_execute_fill`, so the state is the one the
engine builds, and they read the stop the ladder moves to.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bot.backtest.engine as bt_engine
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _bar(o, h, lo, c, n=1):
    return BacktestBar(timestamp=T0 + timedelta(hours=n), open=o, high=h,
                       low=lo, close=c, volume=1000.0, symbol="BTC/USDT")


def _engine(commission_pct: float) -> BacktestEngine:
    eng = BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", initial_balance=10_000.0,
        commission_pct=commission_pct, slippage_pct=0.0))
    eng._partial_tp_enabled = True
    return eng


def _fill(eng, direction, entry, sl, tp):
    idea = TradeIdea(id=f"TI-{direction.value}", asset="BTC/USDT",
                     direction=direction, entry_price=entry, stop_loss=sl,
                     take_profit=tp, confidence=0.7, reasoning="x", source="t",
                     timestamp=T0)
    check = SimpleNamespace(position_size_usd=1000.0,
                            verdict=SimpleNamespace(value="APPROVED"))
    eng._execute_fill(idea, check, fill_price=entry, bar=_bar(entry, entry, entry, entry, 0))
    tid = idea.id
    return tid, eng.portfolio._positions[tid], eng._open_bt_positions[tid]


@pytest.mark.parametrize("commission_pct", [0.06, 0.1])
class TestTheLocks:
    def test_a_long_tp1_locks_breakeven_after_the_engines_round_trip(self, commission_pct):
        eng = _engine(commission_pct)
        try:
            tid, pos, meta = _fill(eng, Direction.LONG, 100.0, 90.0, 200.0)
            state = meta["ptp_state"]
            eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 116, 99, 114))
            assert state.tp1_hit and not state.tp2_hit
            rt = 2 * commission_pct
            assert state.current_sl == pytest.approx(100.0 * (1 + rt / 100))
            assert pos.stop_loss == state.current_sl
        finally:
            eng.cleanup()

    def test_a_short_mirrors_it(self, commission_pct):
        eng = _engine(commission_pct)
        try:
            tid, pos, meta = _fill(eng, Direction.SHORT, 100.0, 110.0, 50.0)
            state = meta["ptp_state"]
            eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 101, 84, 86))
            assert state.tp1_hit and not state.tp2_hit
            rt = 2 * commission_pct
            assert state.current_sl == pytest.approx(100.0 * (1 - rt / 100))
        finally:
            eng.cleanup()

    def test_tp2_locks_one_r(self, commission_pct):
        eng = _engine(commission_pct)
        try:
            tid, pos, meta = _fill(eng, Direction.LONG, 100.0, 90.0, 300.0)
            state = meta["ptp_state"]
            # A runner trail this wide cannot move the stop past the lock on
            # the bar that fires TP2, so the stop the bar leaves IS the lock.
            state.atr = 1000.0
            eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 125.5, 99, 125))
            assert state.tp1_hit and state.tp2_hit
            assert state.current_sl == pytest.approx(110.0)
        finally:
            eng.cleanup()

    def test_a_short_tp2_locks_one_r_below_the_entry(self, commission_pct):
        eng = _engine(commission_pct)
        try:
            tid, pos, meta = _fill(eng, Direction.SHORT, 100.0, 110.0, 20.0)
            state = meta["ptp_state"]
            state.atr = 1000.0
            eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 101, 74.5, 75))
            assert state.tp1_hit and state.tp2_hit
            assert state.current_sl == pytest.approx(90.0)
        finally:
            eng.cleanup()


class TestItAsksTheLiveReading:
    def test_the_tp1_lock_is_the_live_ladders(self, monkeypatch):
        """Plant a lock the live ladder could never produce: the backtest's
        stop is that lock, so the copy computes no lock of its own."""
        monkeypatch.setattr(bt_engine, "_tp1_lock", lambda state: 101.2345)
        eng = _engine(0.06)
        try:
            tid, pos, meta = _fill(eng, Direction.LONG, 100.0, 90.0, 200.0)
            eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 116, 99, 114))
            assert meta["ptp_state"].current_sl == 101.2345
        finally:
            eng.cleanup()

    def test_the_tp2_lock_is_the_live_ladders(self, monkeypatch):
        monkeypatch.setattr(bt_engine, "_tp2_lock", lambda state: 111.2345)
        eng = _engine(0.06)
        try:
            tid, pos, meta = _fill(eng, Direction.LONG, 100.0, 90.0, 300.0)
            state = meta["ptp_state"]
            state.atr = 1000.0   # keep the runner's trail under the lock
            eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 125.5, 99, 125))
            assert state.tp2_hit
            assert state.current_sl == 111.2345
        finally:
            eng.cleanup()

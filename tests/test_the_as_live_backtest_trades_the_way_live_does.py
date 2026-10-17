"""`--as-live` makes the backtest trade the way live trades.

The 17 Oct edge audit found `--honest` and live measuring different systems:
the honest run fills every idea at the next open and runs one of live's five
early exits, while live rests a post-only limit that fills only on a touch,
pays the maker rate on that leg, spares a time-stopped position only when it
clears its fees, and closes positions on four more rules nobody had measured.
Until a replay of live's own ideas lands near live's record, a card figure
says nothing about live.

These drives go through the engine's real seams: `_queue_entry` (what the
signal branch and a timing FIRE hand an approved idea to), `_fill_queued_at_open`,
`_drain_pending_limits`, `_execute_fill`, `_check_stops_intrabar` and
`_close_position`. Each asserts both arms: what as-live does, and that the
honest path is unchanged.
"""

from __future__ import annotations

import dataclasses
import os
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.runner import _apply_honest_fidelity
from bot.config import CONFIG
from bot.core.trade_costs import entry_rate_pct
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
TAKER = 0.06


def _bar(o, h, lo, c, n):
    return BacktestBar(timestamp=T0 + timedelta(hours=n), open=o, high=h,
                       low=lo, close=c, volume=1000.0, symbol="BTC/USDT")


def _engine(fill_mode: str, monkeypatch=None, time_stop: bool = False) -> BacktestEngine:
    if monkeypatch is not None:
        monkeypatch.setenv("BACKTEST_TIME_STOP", "1" if time_stop else "0")
    eng = BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", initial_balance=10_000.0, commission_pct=TAKER,
        slippage_pct=0.05, fill_mode=fill_mode))
    eng._partial_tp_enabled = False
    return eng


def _idea(direction=Direction.LONG, entry=99.0, sl=97.0, tp=120.0, order_type="limit",
          strategy_type="swing", signal_type="momentum_confluence", entry_vwap=None,
          tid="TI-live"):
    return TradeIdea(id=tid, asset="BTC/USDT", direction=direction, entry_price=entry,
                     stop_loss=sl, take_profit=tp, confidence=0.7, reasoning="x",
                     source="t", timestamp=T0, order_type=order_type,
                     strategy_type=strategy_type, signal_type=signal_type,
                     entry_vwap=entry_vwap)


def _check():
    return SimpleNamespace(position_size_usd=1000.0,
                           verdict=SimpleNamespace(value="APPROVED"))


class TestTheFillModel:
    def test_a_limit_idea_rests_from_the_next_bar_and_fills_only_on_a_touch(self):
        eng = _engine("live")
        signal_bar = _bar(100.0, 100.5, 98.5, 100.0, 0)   # its own range reaches 99
        eng._queue_entry(_idea(entry=99.0), _check(), signal_bar)
        assert eng._pending_entry is None
        assert len(eng._pending_limits) == 1
        assert eng.portfolio._positions == {}, "the signal bar's own touch is not a fill"
        for n, bar in enumerate([_bar(100.0, 100.8, 99.5, 100.2, 1),
                                 _bar(100.2, 100.6, 99.2, 100.0, 2)], start=1):
            eng._fill_queued_at_open(bar)
            eng._drain_pending_limits(bar)
            assert eng.portfolio._positions == {}, f"bar {n} did not reach 99"
        touch = _bar(100.0, 100.3, 98.9, 99.6, 3)
        eng._fill_queued_at_open(touch)
        eng._drain_pending_limits(touch)
        pos = eng.portfolio._positions["TI-live"]
        meta = eng._open_bt_positions["TI-live"]
        assert pos.entry_price == pytest.approx(99.0), "a maker fill has no slippage"
        assert meta["slippage_entry"] == 0.0
        assert meta["entry_commission_pct"] == pytest.approx(entry_rate_pct("limit"))
        assert meta["fee_round_trip_pct"] == pytest.approx(entry_rate_pct("limit") + TAKER)
        assert eng._maker_fills == 1 and eng._limits_filled == 1

    def test_the_maker_leg_is_settled_at_the_maker_rate(self):
        eng = _engine("live")
        eng._queue_entry(_idea(entry=99.0, sl=97.0), _check(), _bar(100, 100.5, 99.5, 100, 0))
        touch = _bar(100.0, 100.3, 98.9, 99.6, 1)
        balance_before = eng.portfolio.balance
        eng._drain_pending_limits(touch)
        qty = eng.portfolio._positions["TI-live"].quantity
        stop_bar = _bar(99.5, 99.6, 96.5, 97.5, 2)
        eng._check_stops_intrabar(stop_bar)
        t = eng._trades[-1]
        exit_px = t.exit_price
        want = 99.0 * qty * entry_rate_pct("limit") / 100 + exit_px * qty * TAKER / 100
        assert t.commission_usd == pytest.approx(want, abs=0.01)
        assert t.net_pnl_usd == pytest.approx(t.pnl_usd - t.commission_usd, abs=0.01)
        # The balance the book settled carries the refund too: the margin
        # went out at the open and came back at the close, so what is left
        # is the net.
        assert eng.portfolio.balance == pytest.approx(balance_before + t.net_pnl_usd, abs=0.02)

    def test_an_untouched_limit_expires_as_live_s_does(self):
        eng = _engine("live")
        eng._queue_entry(_idea(entry=99.0), _check(), _bar(100, 100.5, 99.5, 100, 0))
        hours = int(CONFIG.limit_orders.expire_seconds // 3600)
        for n in range(1, hours + 2):
            eng._drain_pending_limits(_bar(100.0, 100.4, 99.5, 100.1, n))
        assert eng._pending_limits == []
        assert eng._limits_expired == 1
        assert eng.portfolio._positions == {}

    def test_a_market_idea_fills_at_the_next_open_as_a_taker(self):
        eng = _engine("live")
        eng._queue_entry(_idea(entry=100.0, sl=98.0, order_type="market"), _check(),
                         _bar(100, 100.5, 99.5, 100, 0))
        assert eng._pending_limits == [] and eng._pending_entry is not None
        eng._fill_queued_at_open(_bar(100.4, 101.0, 100.0, 100.8, 1))
        pos = eng.portfolio._positions["TI-live"]
        meta = eng._open_bt_positions["TI-live"]
        assert pos.entry_price == pytest.approx(100.4 * (1 + 0.05 / 100))
        assert meta["entry_commission_pct"] is None
        assert meta["fee_round_trip_pct"] == pytest.approx(2 * TAKER)
        assert eng._maker_fills == 0

    def test_the_honest_path_still_fills_a_limit_idea_at_the_next_open(self):
        eng = _engine("next_open")
        eng._queue_entry(_idea(entry=99.0), _check(), _bar(100, 100.5, 99.5, 100, 0))
        assert eng._pending_limits == [] and eng._pending_entry is not None
        eng._fill_queued_at_open(_bar(100.4, 101.0, 100.0, 100.8, 1))
        meta = eng._open_bt_positions["TI-live"]
        assert eng.portfolio._positions["TI-live"].entry_price == pytest.approx(100.4 * 1.0005)
        assert meta["entry_commission_pct"] is None
        assert meta["fee_round_trip_pct"] == pytest.approx(2 * TAKER)

    def test_a_timing_fire_goes_through_the_same_model(self, monkeypatch):
        eng = _engine("live")
        seen = []
        monkeypatch.setattr(eng, "_queue_entry", lambda i, r, b: seen.append(i.id))
        from bot.core.entry_timing import FIRE
        monkeypatch.setattr("bot.core.entry_timing.evaluate_armed",
                            lambda *a, **k: (FIRE, "turn confirmed"))
        eng._armed_setups.append({"idea": _idea(), "risk_check": _check(),
                                  "armed_ts": T0.timestamp()})
        for n in range(12):
            eng._et_bar_win.append((100.0, 100.5, 99.5, 100.0))
        eng._evaluate_armed_setups(_bar(100, 100.5, 99.5, 100, 12))
        assert seen == ["TI-live"]
        assert eng.portfolio._positions == {}, "a FIRE is an order, not a fill"


def _fill_live(eng, idea, entry):
    eng._execute_fill(idea, _check(), fill_price=entry,
                      bar=_bar(entry, entry, entry, entry, 0), maker=True)
    return eng._open_bt_positions[idea.id]


def _flat_bars(eng, around: float, n_from: int, n_to: int):
    for n in range(n_from, n_to + 1):
        eng._check_stops_intrabar(_bar(around, around + 0.1, around - 0.1, around, n))


class TestTheSmartExits:
    def test_a_momentum_swing_flat_at_its_hold_limit_closes_at_the_bar_close(self):
        eng = _engine("live")
        _fill_live(eng, _idea(entry=100.0, sl=98.0), 100.0)
        _flat_bars(eng, 100.0, 1, 7)
        assert "TI-live" in eng.portfolio._positions
        eng._check_stops_intrabar(_bar(100.0, 100.1, 99.9, 100.05, 8))
        assert "TI-live" not in eng.portfolio._positions
        t = eng._trades[-1]
        assert t.exit_reason == "SMART_EXIT:signal_hold"
        assert t.exit_price == pytest.approx(100.05 * (1 - 0.05 / 100))

    def test_with_the_live_flag_off_the_same_position_stays_open(self, monkeypatch):
        off = dataclasses.replace(
            CONFIG, time_stop=dataclasses.replace(CONFIG.time_stop, live_auto_close_enabled=False))
        monkeypatch.setattr("bot.backtest.engine.CONFIG", off)
        eng = _engine("live")
        _fill_live(eng, _idea(entry=100.0, sl=98.0), 100.0)
        _flat_bars(eng, 100.0, 1, 12)
        assert "TI-live" in eng.portfolio._positions

    def test_the_honest_path_runs_no_smart_exit(self, monkeypatch):
        eng = _engine("next_open", monkeypatch, time_stop=True)
        eng._execute_fill(_idea(entry=100.0, sl=98.0), _check(), fill_price=100.0,
                          bar=_bar(100, 100, 100, 100, 0))
        _flat_bars(eng, 100.0, 1, 12)
        assert "TI-live" in eng.portfolio._positions

    def test_a_volume_signal_that_made_no_progress_decays(self):
        eng = _engine("live")
        _fill_live(eng, _idea(entry=100.0, sl=98.0, strategy_type="intraday",
                              signal_type="vol_breakout"), 100.0)
        _flat_bars(eng, 100.0, 1, 1)
        assert "TI-live" in eng.portfolio._positions
        _flat_bars(eng, 100.0, 2, 2)
        assert eng._trades[-1].exit_reason == "SMART_EXIT:volume_decay"

    def test_no_progress_closes_a_scalp_at_its_third_candle(self):
        eng = _engine("live")
        _fill_live(eng, _idea(entry=100.0, sl=98.0, strategy_type="scalp",
                              signal_type="regime_trend"), 100.0)
        _flat_bars(eng, 100.0, 1, 2)
        assert "TI-live" in eng.portfolio._positions
        _flat_bars(eng, 100.0, 3, 3)
        assert eng._trades[-1].exit_reason == "SMART_EXIT:progress"

    def test_a_vwap_reversion_closes_past_its_band(self):
        # Inside the band at the first close; past it at the second, still
        # inside the 2 h hold limit a VWAP reversion gets (live's order of
        # rules puts the hold limit first, so the band is read before it).
        eng = _engine("live")
        _fill_live(eng, _idea(entry=99.8, sl=98.0, strategy_type="scalp",
                              signal_type="vwap_reversion", entry_vwap=100.0), 99.8)
        eng._check_stops_intrabar(_bar(99.9, 100.2, 99.7, 100.1, 1))
        assert "TI-live" in eng.portfolio._positions, "inside the band"
        eng = _engine("live")
        _fill_live(eng, _idea(entry=99.8, sl=98.0, strategy_type="scalp",
                              signal_type="vwap_reversion", entry_vwap=100.0), 99.8)
        eng._check_stops_intrabar(_bar(100.1, 100.6, 100.0, 100.5, 1))
        assert eng._trades[-1].exit_reason == "SMART_EXIT:vwap"

    def test_a_position_whose_r_cannot_be_read_skips_the_r_rules(self):
        eng = _engine("live")
        meta = _fill_live(eng, _idea(entry=100.0, sl=98.0), 100.0)
        meta["initial_risk"] = 0.0
        _flat_bars(eng, 100.0, 1, 20)
        assert "TI-live" in eng.portfolio._positions

    def test_a_winner_at_its_hold_limit_rides(self):
        eng = _engine("live")
        _fill_live(eng, _idea(entry=100.0, sl=98.0), 100.0)   # 1R = 2
        _flat_bars(eng, 102.5, 1, 8)                            # R = 1.25 >= 1
        assert "TI-live" in eng.portfolio._positions


class TestTheTimeStop:
    def test_as_live_spares_profit_after_fees_only(self, monkeypatch):
        rt = entry_rate_pct("limit") + TAKER            # 0.08% at the shipped rates
        hours = int(CONFIG.strategy_types.get_time_close_hours("scalp"))
        # Up by less than the round trip: live closes it.
        eng = _engine("live", monkeypatch, time_stop=True)
        _fill_live(eng, _idea(entry=100.0, sl=99.0, strategy_type="scalp",
                              signal_type="regime_trend"), 100.0)
        thin = 100.0 * (1 + rt / 200)
        for n in range(1, hours + 1):
            eng._check_stops_intrabar(_bar(thin, thin + 0.02, thin - 0.02, thin, n))
        assert eng._trades and eng._trades[-1].exit_reason == "TIME_STOP"
        # Up by more than the round trip: spared.
        eng = _engine("live", monkeypatch, time_stop=True)
        _fill_live(eng, _idea(entry=100.0, sl=99.0, strategy_type="scalp",
                              signal_type="regime_trend"), 100.0)
        fat = 100.0 * (1 + rt / 50)
        for n in range(1, hours + 1):
            eng._check_stops_intrabar(_bar(fat, fat + 0.02, fat - 0.02, fat, n))
        assert "TI-live" in eng.portfolio._positions

    def test_the_honest_path_keeps_its_gross_reading(self, monkeypatch):
        hours = int(CONFIG.strategy_types.get_time_close_hours("scalp"))
        eng = _engine("next_open", monkeypatch, time_stop=True)
        eng._execute_fill(_idea(entry=100.0, sl=99.0, strategy_type="scalp",
                                signal_type="regime_trend"), _check(),
                          fill_price=100.0, bar=_bar(100, 100, 100, 100, 0))
        thin = 100.0 * (1 + 0.0007)   # above the slipped entry (100.05), below the fees
        for n in range(1, hours + 1):
            eng._check_stops_intrabar(_bar(thin, thin + 0.02, thin - 0.02, thin, n))
        assert "TI-live" in eng.portfolio._positions


def _fidelity(args):
    """`_apply_honest_fidelity` writes the BACKTEST_* env flags with setdefault;
    an engine built by a later test would read them. Hand the environment back."""
    before = dict(os.environ)
    try:
        _apply_honest_fidelity(args)
    finally:
        os.environ.clear()
        os.environ.update(before)


class TestTheRunnerFlag:
    def test_as_live_is_honest_with_the_live_fill_model(self):
        args = Namespace(as_live=True, honest=False, strict_data=False, fill_mode="close",
                         commission=None, leverage=None)
        _fidelity(args)
        assert args.honest is True and args.strict_data is True
        assert args.fill_mode == "live"
        assert args.commission == pytest.approx(CONFIG.risk.taker_fee_pct)
        assert args.leverage >= 1

    def test_honest_alone_keeps_next_open(self):
        args = Namespace(as_live=False, honest=True, strict_data=False, fill_mode="close",
                         commission=None, leverage=None)
        _fidelity(args)
        assert args.fill_mode == "next_open"

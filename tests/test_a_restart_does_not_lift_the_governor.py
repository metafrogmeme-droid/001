"""A restart does not lift the live-performance governor.

The governor scores a rolling window of realized live closes and REDUCES or
PAUSES a losing book. The window lived in memory only, under a comment saying
it "rebuilds after restart from live closes", which meant the next five new
closes: an empty window fails OPEN until `live_perf_min_samples` accrue. So a
governor that had paused a losing live book resumed full size on the next
boot, and this deployment redeploys often. The executor's closed-trade record
is on disk, and the engine now seeds the window from it at boot, with the
same filter the live feed applies.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import bot.config as bot_config
from bot.core import live_executor
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine

T0 = datetime(2026, 9, 20, tzinfo=UTC)


def _close(pnl, hours, reason="SL HIT"):
    return SimpleNamespace(pnl_usd=pnl, close_reason=reason,
                           closed_at=None if hours is None else T0 + timedelta(hours=hours))


def test_the_reading_keeps_what_the_live_feed_counts_in_close_order():
    record = [
        _close(-2.0, 3),
        _close(0.0, 1, reason="stale_pending"),   # never filled: not fired
        _close(None, 2),                          # unpriced: fed to no window
        _close(float("nan"), 4),                  # a NaN the loader kept
        _close(1.5, 0),
        _close(-0.4, 5, reason="leverage_overshoot"),  # an abort fires: counted
        _close(0.0, 6, reason="TP HIT"),          # a measured flat: counted
    ]
    assert live_executor.realized_close_pnls(record) == [1.5, -2.0, -0.4, 0.0]


def test_an_undated_close_is_the_oldest():
    record = [_close(3.0, 1), _close(-1.0, None)]
    assert live_executor.realized_close_pnls(record) == [-1.0, 3.0]


def _engine(tmp_path):
    return RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "r.json"))


def test_seeding_fills_an_empty_window_and_touches_no_streak(tmp_path):
    eng = _engine(tmp_path)
    streak = eng._consecutive_losses
    assert eng.seed_realized_window([-1.0, -2.0, 3.0]) == 3
    assert list(eng._realized_pnl_window) == [-1.0, -2.0, 3.0]
    assert eng._consecutive_losses == streak, (
        "the streak is persisted already; replaying closes would count them twice")


def test_a_window_fed_live_is_never_seeded_again(tmp_path):
    eng = _engine(tmp_path)
    eng.record_live_trade_result(-1.0)
    assert eng.seed_realized_window([-5.0, -5.0]) == 0
    assert list(eng._realized_pnl_window) == [-1.0]


def test_a_paused_governor_stays_paused_across_a_restart(tmp_path):
    """Twenty recorded losses: the governor pauses before the restart, and a
    fresh engine seeded from the same record pauses too."""
    losses = [-1.0] * 20
    before = _engine(tmp_path)
    for pnl in losses:
        before.record_live_trade_result(pnl)
    assert before.live_performance_size_multiplier == 0.0, "the premise: paused"
    after = RiskEngine(PortfolioTracker(),
                       state_file=os.path.join(str(tmp_path), "r2.json"))
    assert after.live_performance_size_multiplier == 1.0, (
        "the defect: an empty window fails open")
    after.seed_realized_window(losses)
    assert after.live_performance_size_multiplier == 0.0


@pytest.mark.parametrize("live, seeded", [(True, True), (False, False)])
def test_the_engine_seeds_at_boot_in_live_mode_only(monkeypatch, live, seeded):
    record = [_close(-1.0, h) for h in range(6)]
    monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: live)
    monkeypatch.setattr(live_executor.LiveExecutor, "closed_positions",
                        property(lambda self: record))
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine()
    window = list(eng.risk._realized_pnl_window)
    assert window == ([-1.0] * 6 if seeded else [])

"""The breakout backtest sizes, exits and charges costs as its pre-registration says."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import vol_breakout_backtest as vb  # noqa: E402

P = vb.Params(fee=0.0, slip=0.0)


def _flat(n=80, px=100.0):
    a = np.full(n, px)
    return a.copy(), a + 0.5, a - 0.5, a.copy()


def test_size_risks_exactly_one_percent_when_the_cap_does_not_bind():
    qty, risk = vb.position_size(10_000.0, 100.0, 5.0, P)
    assert qty == pytest.approx(20.0)
    assert risk == pytest.approx(0.01)


def test_the_notional_cap_binds_and_the_realised_risk_says_so():
    qty, risk = vb.position_size(10_000.0, 100.0, 0.5, P)
    assert qty * 100.0 == pytest.approx(10_000.0)       # 1x notional
    assert risk < 0.01
    assert risk == pytest.approx(qty * 0.5 / 10_000.0)


def test_unsizeable_inputs_give_no_position_not_a_zero_stop():
    assert vb.position_size(10_000.0, 100.0, 0.0, P) == (0.0, 0.0)
    assert vb.position_size(0.0, 100.0, 5.0, P) == (0.0, 0.0)


def test_a_long_that_reaches_the_target_makes_two_r_before_costs():
    o, h, lo, c = _flat()
    h[10] = 100.0 + 4.0 * 1.0 + 0.1                   # touches target (4 ATR, ATR=1)
    t = vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P)
    assert t["reason"] == "target"
    assert t["r"] == pytest.approx(2.0)


def test_a_stop_loses_one_r():
    o, h, lo, c = _flat()
    lo[7] = 100.0 - 2.0 - 0.1
    t = vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P)
    assert t["reason"] == "stop" and t["r"] == pytest.approx(-1.0)


def test_the_stop_wins_when_one_bar_touches_both():
    o, h, lo, c = _flat()
    h[6], lo[6] = 120.0, 80.0
    assert vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P)["reason"] == "stop"
    s = vb.run_trade(o, h, lo, c, 5, -1, 1.0, 10_000.0, P)
    assert s["reason"] == "stop"


def test_a_gap_through_the_stop_fills_at_the_open_and_loses_more_than_one_r():
    o, h, lo, c = _flat()
    o[8], h[8], lo[8], c[8] = 90.0, 90.5, 89.5, 90.0
    t = vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P)
    assert t["reason"] == "stop" and t["r"] == pytest.approx(-5.0)


def test_a_short_mirrors_a_long():
    o, h, lo, c = _flat()
    lo[9] = 100.0 - 4.0 - 0.1
    t = vb.run_trade(o, h, lo, c, 5, -1, 1.0, 10_000.0, P)
    assert t["reason"] == "target" and t["r"] == pytest.approx(2.0)


def test_the_time_stop_exits_at_the_next_open_after_48_bars():
    o, h, lo, c = _flat(120)
    t = vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P)
    assert t["reason"] == "time" and t["exit_i"] == 5 + 48 and t["bars"] == 49


def test_a_position_the_data_ended_on_is_not_a_result():
    o, h, lo, c = _flat(30)
    assert vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P) is None


def test_fees_and_slippage_are_charged_on_both_sides():
    o, h, lo, c = _flat(120)
    free = vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, P)
    cost = vb.run_trade(o, h, lo, c, 5, 1, 1.0, 10_000.0, vb.Params())
    assert cost["net"] < free["net"] - 0.0006 * 2 * 10_000.0 * 0.5   # fees alone exceed 6bp x2


def test_signals_use_only_bars_up_to_t():
    rng = np.random.default_rng(4)
    n = 400
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    o = np.concatenate(([c[0]], c[:-1]))
    h, lo = np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998
    v = rng.uniform(1e3, 2e3, n)
    full = vb.signals(o, h, lo, c, v, P)
    cut = 300
    part = vb.signals(o[:cut], h[:cut], lo[:cut], c[:cut], v[:cut], P)
    assert (full["side"][:cut] == part["side"]).all()


def test_a_breakout_bar_fires_a_long_and_a_quiet_bar_does_not():
    n = 120
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 0.05, n))
    o = np.concatenate(([c[0]], c[:-1]))
    h, lo = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05
    v = np.full(n, 1e3)
    c[100] = c[99] + 4.0
    h[100], lo[100] = c[100] + 0.05, o[100] - 0.05
    sig = vb.signals(o, h, lo, c, v, P)
    assert sig["side"][100] == 1
    assert (sig["side"][60:99] == 0).all()


def test_the_week_interval_is_nan_with_no_trades_and_brackets_the_mean():
    rng = np.random.default_rng(2)
    assert np.isnan(vb.week_interval([], 100, rng)[0])
    rows = [(f"2026-{w:02d}", r) for w in range(1, 41) for r in (0.5, 1.5)]
    mean, lo, hi = vb.week_interval(rows, 500, rng)
    assert lo <= mean <= hi and mean == pytest.approx(1.0)


def test_a_holdout_with_fewer_than_30_trades_is_not_passed():
    disc = {"trades": 200, "ci_lo": 0.2, "expectancy_r": 0.5, "null_p95": 0.1}
    short = {"trades": 12, "expectancy_r": 0.9}
    full = {"trades": 31, "expectancy_r": 0.1}
    assert vb.verdict(disc, short)["passed"] is False
    assert vb.verdict(disc, full)["passed"] is True
    assert vb.verdict({**disc, "ci_lo": -0.1}, full)["passed"] is False
    assert vb.verdict({**disc, "null_p95": 0.9}, full)["passed"] is False

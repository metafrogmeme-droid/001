"""scripts/daily_neutral_study.py on panels whose answer is known.

docs/research/prereg/daily_neutral_2026-11. Synthetic prices only: the
frozen panel is read by the study itself, once per window.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location(
        "daily_neutral_study", ROOT / "scripts" / "daily_neutral_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _panel(prices: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range("2018-01-01", periods=len(next(iter(prices.values()))), freq="D")
    cl = pd.DataFrame(prices, index=idx)
    return cl.copy(), cl     # open == close: the return of row s is close s -> s+1


def test_n1_earns_when_last_weeks_losers_bounce():
    m = _m()
    n, coins = 400, 12
    rng = np.random.default_rng(1)
    paths = {}
    for i in range(coins):
        # a 7-day sawtooth, phase-shifted per coin: the week's loser rises next.
        phase = i % 2
        steps = np.array([0.02 if ((d // 7) + phase) % 2 else -0.02 for d in range(n)])
        paths[f"C{i}"] = 100 * np.cumprod(1 + steps + rng.normal(0, 1e-4, n))
    paths["BTC"] = paths.pop("C0")
    op, cl = _panel(paths)
    bt = m.reversal(op, cl)
    live = bt[bt["exposure"] > 0]
    assert len(live) > 200
    assert live["gross"].sum() > 0, "the losers bounced and the spread did not earn"


def test_n1_holds_nothing_with_too_few_coins():
    m = _m()
    op, cl = _panel({f"C{i}": np.linspace(100, 120, 300) for i in range(8)} | {"BTC": np.linspace(1, 2, 300)})
    assert (m.reversal(op, cl)["exposure"] == 0).all(), "9 coins: under the floor of 10"
    op, cl = _panel({f"C{i}": np.linspace(100, 120, 300) for i in range(9)} | {"BTC": np.linspace(1, 2, 300)})
    assert (m.reversal(op, cl)["exposure"] > 0).any(), "10 coins: at the floor"


def test_n1_charges_the_net_weight_of_both_legs_at_a_rebalance():
    """12 coins, k = 2: each loser nets 0.5/2 - 0.5/12 long, each other coin
    0.5/12 short, so 0.8333 of equity changes hands from flat."""
    m = _m()
    op, cl = _panel({f"C{i}": 100 + np.arange(300) * (i + 1) * 0.01 for i in range(11)}
                    | {"BTC": 100 + np.arange(300) * 0.02})
    bt = m.reversal(op, cl)
    first = bt[bt["cost"] > 0].iloc[0]
    traded = 2 * (0.25 - 0.5 / 12) + 10 * (0.5 / 12)
    assert first["cost"] == pytest.approx(m.FEE * traded, rel=1e-9)
    assert first["exposure"] == pytest.approx(traded, rel=1e-6)


def _mean_reverting(n, phi, seed):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, 0.03)
    return x


def test_n2_trades_a_stretched_ratio_and_earns_on_its_return():
    """A ratio that reverts to its mean (AR(1), phi 0.7): a stretch comes back."""
    m = _m()
    n = 1500
    btc = 100 * np.ones(n)
    eth = 100 * np.exp(_mean_reverting(n, 0.7, 3))
    op, cl = _panel({"BTC": btc, "ETH": eth})
    bt, trips = m.eth_btc(op, cl)
    assert len(trips) >= 10
    assert bt["net"].sum() > 0


def test_n2_loses_on_a_ratio_that_trends():
    """The other arm: a random walk does not come back, and fading it pays costs for nothing."""
    m = _m()
    n = 1500
    eth = 100 * np.exp(np.cumsum(np.random.default_rng(4).normal(0, 0.03, n)))
    op, cl = _panel({"BTC": 100 * np.ones(n), "ETH": eth})
    bt, trips = m.eth_btc(op, cl)
    rev, _ = m.eth_btc(*_panel({"BTC": 100 * np.ones(n), "ETH": 100 * np.exp(_mean_reverting(n, 0.7, 4))}))
    assert trips and bt["net"].sum() < rev["net"].sum()
    assert all(tr["days"] <= m.Z_HOLD_MAX for tr in trips)


def test_n2_exits_after_twenty_days_when_the_ratio_does_not_come_back():
    m = _m()
    n = 200
    eth = np.r_[100 * np.ones(60), 100 * np.exp(-0.01 * np.arange(1, 141))]   # falls for good
    op, cl = _panel({"BTC": 100 * np.ones(n), "ETH": eth})
    _, trips = m.eth_btc(op, cl)
    assert trips and trips[0]["side"] == 1 and trips[0]["days"] == m.Z_HOLD_MAX


def test_the_decision_never_reads_the_day_it_trades():
    """Changing tomorrow's price must not change today's position."""
    m = _m()
    n = 300
    t = np.arange(n)
    base = {"BTC": 100 * np.ones(n), "ETH": 100 * np.exp(0.15 * np.sin(2 * np.pi * t / 60))}
    op, cl = _panel(base)
    a, _ = m.eth_btc(op, cl)
    bumped = {k: v.copy() for k, v in base.items()}
    bumped["ETH"][200:] *= 1.5
    op2, cl2 = _panel(bumped)
    b, _ = m.eth_btc(op2, cl2)
    assert (a["exposure"].iloc[:199] == b["exposure"].iloc[:199]).all()

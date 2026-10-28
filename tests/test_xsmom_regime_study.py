"""Tests for scripts/xsmom_regime_study.py on synthetic data only (no network, no real data)."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_p = Path(__file__).resolve().parents[1] / "scripts/xsmom_regime_study.py"
_spec = importlib.util.spec_from_file_location("xsmom_regime_study", _p)
m = importlib.util.module_from_spec(_spec)
sys.modules["xsmom_regime_study"] = m
_spec.loader.exec_module(m)


def panel(n=500, drifts=None, seed=1, start="2019-01-01"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D")
    drifts = drifts or {"BTC": 0.002, "ETH": 0.003, "XRP": 0.001, "ADA": -0.001, "LTC": 0.0015}
    cl = pd.DataFrame({c: 100 * np.exp(np.cumsum(d + 0.02 * rng.standard_normal(n))) for c, d in drifts.items()}, index=idx)
    op = cl.shift(1).fillna(cl.iloc[0])
    return op, cl


def test_no_lookahead_weights_depend_only_on_past():
    _, cl = panel()
    t = 400
    w1 = m.decide(cl, t, m.PRIMARY)
    cl2 = cl.copy()
    cl2.iloc[t + 1:] *= 3.0
    w2 = m.decide(cl2, t, m.PRIMARY)
    assert w1.equals(w2) and w1.sum() > 0  # both arms: a real position, unchanged by the future


def test_cash_when_btc_below_mean():
    _, cl = panel(drifts={"BTC": -0.004, "ETH": 0.003, "XRP": 0.003, "ADA": 0.003})
    assert m.decide(cl, 450, m.PRIMARY).sum() == 0.0
    _, up = panel()
    assert m.decide(up, 450, m.PRIMARY).sum() > 0


def test_weights_long_only_capped_and_top_k():
    _, cl = panel()
    w = m.decide(cl, 450, m.PRIMARY)
    assert (w >= 0).all() and w.sum() <= 1.0 + 1e-12
    assert (w > 0).sum() <= min(4, 5 // 3)  # n=5 coins -> k=1


def test_needs_three_eligible_coins():
    _, cl = panel(drifts={"BTC": 0.003, "ETH": 0.003})
    assert m.decide(cl, 450, m.PRIMARY).sum() == 0.0


def test_negative_momentum_dropped():
    _, cl = panel(drifts={"BTC": 0.003, "A": -0.01, "B": -0.01, "C": -0.01})
    # BTC is the only positive coin; others are excluded by the >0 rule
    w = m.decide(cl, 450, m.PRIMARY)
    assert w[["A", "B", "C"]].sum() == 0.0


def test_fill_at_next_open_and_cost_charged():
    op, cl = panel()
    bt = m.backtest(op, cl, m.PRIMARY)
    assert bt["cost"].sum() > 0
    free = m.backtest(op, cl, m.Params(fee=0.0))
    assert (free["net"] - free["gross"]).abs().max() < 1e-12
    assert free["net"].sum() > bt["net"].sum()
    # truncating the future does not change earlier net returns
    short = m.backtest(op.iloc[:400], cl.iloc[:400], m.PRIMARY)
    common = short.index
    assert np.allclose(short["net"], bt.loc[common, "net"])


def test_funding_charged_on_exposure_only():
    op, cl = panel()
    a = m.backtest(op, cl, m.PRIMARY)
    b = m.backtest(op, cl, m.Params(funding_per_day=0.0003))
    diff = a["net"] - b["net"]
    assert np.allclose(diff, 0.0003 * a["exposure"])
    assert (a["exposure"] == 0).any() and (a["exposure"] > 0).any()


def test_unreadable_held_return_raises_not_zero():
    op, cl = panel()
    held = m.decide(cl, 450, m.PRIMARY)
    coin = held[held > 0].index[0]
    op2 = op.copy()
    op2.iloc[451:, op2.columns.get_loc(coin)] = np.nan
    with pytest.raises(ValueError):
        m.backtest(op2, cl, m.PRIMARY)


def test_btc_missing_day_refused(tmp_path):
    d = pd.DataFrame({"date": ["2020-01-01", "2020-01-03"], "open": [1, 1], "close": [1, 1]})
    d.to_csv(tmp_path / "BTC.csv", index=False)
    with pytest.raises(ValueError):
        m.load_panel(tmp_path)


def test_load_panel_never_reads_past_last(tmp_path):
    dates = pd.date_range("2020-01-01", periods=10).strftime("%Y-%m-%d")
    pd.DataFrame({"date": dates, "open": 1.0, "close": 1.0}).to_csv(tmp_path / "BTC.csv", index=False)
    op, cl = m.load_panel(tmp_path, "2020-01-05")
    assert str(cl.index[-1].date()) == "2020-01-05" and len(cl) == 5


def test_stats_and_bootstrap_deterministic():
    r = pd.Series(np.random.default_rng(0).normal(0.001, 0.02, 600), index=pd.date_range("2020-01-01", periods=600))
    assert m.boot_lo(r) == m.boot_lo(r)
    lo, hi = m.boot_lo(r)
    assert lo < m.sharpe(r) < hi
    assert m.sharpe(pd.Series([0.0] * 100)) is None  # zero variance is unreadable, not 0
    assert m.max_dd(pd.Series([0.1, -0.5, 0.0])) == pytest.approx(0.5)


def test_by_year_skips_years_with_no_position():
    idx = pd.date_range("2020-12-01", "2021-12-31")
    r = pd.Series(0.001, index=idx)
    ex = pd.Series(0.0, index=idx)
    ex[ex.index.year == 2021] = 0.5
    assert list(m.by_year(r, ex)) == [2021]


def test_holdout_refuses_without_seal(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DIR", tmp_path)
    with pytest.raises(SystemExit):
        m.cmd_holdout()
    (tmp_path / "result_dev.json").write_text("{}")
    (tmp_path / "code_at_unseal.sha256").write_text("0" * 64 + "\n")
    with pytest.raises(SystemExit):
        m.cmd_holdout()  # hash mismatch
    (tmp_path / "code_at_unseal.sha256").write_text(m.code_hash() + "\n")
    (tmp_path / "holdout_read_at").write_text("x")
    with pytest.raises(SystemExit):
        m.cmd_holdout()  # second read

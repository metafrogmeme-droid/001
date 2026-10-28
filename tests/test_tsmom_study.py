"""The TSMOM study's mechanics: no look-ahead, costs, band, bars, and honest refusals."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tsmom_study as ts  # noqa: E402

D0 = date(2017, 1, 2)  # a Monday


def _panel(n=500, drift=0.002, vol=0.02, coins=3, seed=1, start_gap=0):
    rng = np.random.default_rng(seed)
    series = {}
    for k in range(coins):
        r = drift + vol * rng.standard_normal(n)
        c = 100 * np.exp(np.cumsum(r))
        o = np.concatenate([[100.0], c[:-1]])
        skip = start_gap * k
        ds = [D0 + timedelta(days=i) for i in range(skip, n)]
        series[f"C{k}"] = (ds, o[skip:], c[skip:])
    return series


def _sim(series, lbs=(20, 50), variant="LF", last=None):
    dates, names, op, cl = ts.build_panel(series, last)
    return dates, names, op, cl, ts.simulate(dates, op, cl, lbs, variant)


def test_uptrend_goes_long_and_long_flat_never_goes_short():
    _, _, _, _, sim = _sim(_panel())
    assert sim["long"].max() > 0 and sim["trades"].sum() > 0


def test_downtrend_long_flat_is_flat_and_long_short_is_short():
    s = _panel(drift=-0.004, vol=0.01)
    d, n, o, c = ts.build_panel(s)
    lf = ts.simulate(d, o, c, (20, 50), "LF")
    ls = ts.simulate(d, o, c, (20, 50), "LS")
    assert lf["long"].max() == 0 and np.allclose(lf["gross"], 0)
    assert ls["gross"].sum() > 0  # shorting a falling market earns; long/flat earns nothing
    assert ls["long"].max() == 0


def test_no_lookahead_truncating_the_future_leaves_earlier_returns_unchanged():
    s = _panel(n=600)
    _, _, _, _, full = _sim(s)
    cut = D0 + timedelta(days=450)
    _, _, _, _, part = _sim(s, last=cut)
    k = len(part["dates"])
    assert k > 50
    assert part["dates"] == full["dates"][:k]
    assert np.array_equal(part["gross"], full["gross"][:k])
    assert np.array_equal(part["turnover"], full["turnover"][:k])


def test_signal_uses_only_closes_up_to_t():
    s = _panel(n=400)
    d, n, o, c = ts.build_panel(s)
    t = 300
    w1, b1 = ts.target_weights(c, t, (20, 50), "LS")
    c2 = c.copy()
    c2[t + 1:] = 1.0  # destroy the future
    w2, b2 = ts.target_weights(c2, t, (20, 50), "LS")
    assert np.array_equal(w1, w2) and np.array_equal(b1, b2)


def test_fill_is_next_open_trades_only_after_a_sunday_close():
    s = _panel()
    d, n, o, c, sim = _sim(s)
    fills = [dt for dt, k in zip(sim["dates"], sim["trades"]) if k > 0]
    assert fills and all((dt - timedelta(days=1)).weekday() == 6 for dt in fills)


def test_costs_are_linear_in_turnover_and_funding_only_on_long_notional():
    _, _, _, _, sim = _sim(_panel(), variant="LS")
    r1 = ts.net_returns(sim, "LS", cost_side=0.001, funding=False)
    r2 = ts.net_returns(sim, "LS", cost_side=0.002, funding=False)
    assert np.allclose(r1 - r2, 0.001 * sim["turnover"])
    rf = ts.net_returns(sim, "LS", cost_side=0.001, funding=True)
    assert np.allclose(r1 - rf, ts.FUNDING_DAY * sim["long"])
    assert np.allclose(ts.net_returns(sim, "LF"), sim["gross"] - 0.001 * sim["turnover"])  # LF: no funding by default


def test_gross_never_exceeds_one_and_coin_cap_holds():
    d, n, o, c = ts.build_panel(_panel(coins=2, drift=0.003, vol=0.002))
    for t in range(250, 400, 7):
        w, _ = ts.target_weights(c, t, (20, 50), "LS")
        assert np.abs(w).sum() <= 1.0 + 1e-9 and np.abs(w).max() <= ts.COIN_CAP + 1e-9


def test_a_coin_with_too_little_history_is_not_eligible():
    s = _panel(n=500, coins=2, start_gap=0)
    ds, op_, cl_ = s["C1"]
    s["C1"] = (ds[300:], op_[300:], cl_[300:])  # 200 bars of history at the end
    d, n, o, c = ts.build_panel(s)
    j = n.index("C1")
    first = (s["C1"][0][0] - d[0]).days
    w_early, _ = ts.target_weights(c, first + 150, (20, 50), "LF")
    assert w_early[j] == 0.0
    w_late, _ = ts.target_weights(c, first + 190, (20, 50), "LF")
    assert w_late[j] > 0.0


def test_band_skips_small_changes_trades_big_ones_and_never_blocks_an_exit(monkeypatch):
    s = _panel(n=900, coins=1, drift=0.0, vol=0.0)  # constant prices: weights do not drift
    d, n, o, c = ts.build_panel(s)
    seq = iter([0.0] * 60 + [0.10, 0.11, 0.14, 0.0] + [0.0] * 200)

    def fake(cl, t, lbs, variant):
        return np.array([next(seq)]), np.array([0.10])

    monkeypatch.setattr(ts, "target_weights", fake)
    sim = ts.simulate(d, o, c, (20, 50), "LF")
    moved = sim["turnover"][sim["turnover"] > 0]
    assert np.allclose(moved, [0.10, 0.04, 0.14])  # 0.11 is inside the band (0.025) and is skipped; the exit executes


def test_gap_in_a_series_is_cannot_check_not_filled(tmp_path):
    p = tmp_path / "X.csv"
    p.write_text("date,open,high,low,close,volume\n2020-01-01,1,1,1,1,1\n2020-01-03,1,1,1,1,1\n")
    with pytest.raises(ts.CannotCheck):
        ts.load_series(p)


def test_closed_bar_filter_and_pagination():
    calls = []

    def get(url, params):
        calls.append(params["startTime"])
        base = params["startTime"]
        n = 1000 if len(calls) == 1 else 3
        return [[base + i * 86_400_000, "1", "1", "1", "1", "1", base + i * 86_400_000 + 86_399_999] for i in range(n)]

    rows = ts.fetch_coin("BTC", get)
    assert len(calls) == 2 and len(rows) == 1003 - (0)  # all in the past, so all closed
    far = 4_000_000_000_000

    def get_open(url, params):
        return [[0, "1", "1", "1", "1", "1", 1], [1, "1", "1", "1", "1", "1", far]]

    assert len(ts.fetch_coin("BTC", get_open)) == 1


def test_metrics_and_unreadable_is_none_not_zero():
    assert ts.sharpe(np.array([0.01, 0.01, 0.01])) is None  # zero variance -> unreadable
    assert ts.sharpe(np.array([0.01])) is None
    assert ts.boot_lower(np.zeros(10)) is None
    r = np.array([0.1, -0.5, 0.2])
    assert abs(ts.max_dd(r) - 0.5) < 1e-12
    assert ts.sharpe(np.array([0.01, -0.01] * 50)) == pytest.approx(0.0, abs=1e-9)


def test_bootstrap_is_deterministic_and_lower_bound_positive_for_a_strong_series():
    rng = np.random.default_rng(3)
    r = 0.01 + 0.005 * rng.standard_normal(400)
    a, b = ts.boot_lower(r), ts.boot_lower(r)
    assert a == b and a > 0
    assert ts.boot_lower(-r) < 0


def test_bars_need_every_input_and_each_arm():
    yrs = {2019: {"days": 365, "ret": 0.1}, 2020: {"days": 366, "ret": -0.1}, 2021: {"days": 365, "ret": 0.05}}
    ok = ts.bars(0.6, 0.6, 0.1, 0.3, 0.5, yrs)
    assert all(ok.values())
    assert not ts.bars(0.4, 0.6, 0.1, 0.3, 0.5, yrs)["dev_sharpe"]
    assert not ts.bars(0.6, 0.4, 0.1, 0.3, 0.5, yrs)["holdout_sharpe"]
    assert not ts.bars(0.6, 0.6, -0.1, 0.3, 0.5, yrs)["holdout_boot_lb_gt_0"]
    assert not ts.bars(0.6, 0.6, 0.1, 0.6, 0.5, yrs)["holdout_dd_le_btc"]
    assert not ts.bars(0.6, 0.6, None, 0.3, 0.5, yrs)["holdout_boot_lb_gt_0"]
    assert not ts.bars(None, 0.6, 0.1, 0.3, 0.5, yrs)["dev_sharpe"]
    assert not ts.bars(0.6, 0.6, 0.1, 0.3, None, yrs)["holdout_dd_le_btc"]
    two = {2019: {"days": 365, "ret": 0.1}, 2020: {"days": 366, "ret": -0.1}}
    assert not ts.bars(0.6, 0.6, 0.1, 0.3, 0.5, two)["years_majority_positive"]  # 1 of 2 is not a majority
    short = {**yrs, 2022: {"days": 10, "ret": -0.9}}
    assert ts.bars(0.6, 0.6, 0.1, 0.3, 0.5, short)["years_majority_positive"]  # 10-day year is not counted


def test_run_study_dev_and_holdout_agree_on_dev_and_holdout_reports_every_bar():
    s = _panel(n=2300, coins=3, vol=0.03)
    # shift the panel so the dev/holdout split lands inside it
    shift = (ts.DEV_START - D0).days - 300
    s = {k: ([d + timedelta(days=shift) for d in v[0]], v[1], v[2]) for k, v in s.items()}
    full_set = {k: s[k] for k in s}
    for c in ts.COINS:
        full_set.setdefault(c, s["C0"])
    dev = ts.run_study(full_set, "dev")
    hold = ts.run_study(full_set, "holdout")
    for key, t in dev["tests"].items():
        assert t["dev"]["sharpe"] == hold["tests"][key]["dev"]["sharpe"]
        assert "holdout" not in t
        h = hold["tests"][key]
        assert set(h["bars"]) == {"dev_sharpe", "holdout_sharpe", "holdout_boot_lb_gt_0", "holdout_dd_le_btc",
                                  "years_majority_positive"}
        assert h["passes"] == all(h["bars"].values())
    assert len(dev["tests"]) == 4

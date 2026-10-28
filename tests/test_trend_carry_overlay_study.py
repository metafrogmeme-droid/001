"""Mechanics of the trend + carry + de-gearing study: causality, funding sign, exclusion, gear, refusals."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import trend_carry_overlay_study as tc  # noqa: E402
import tsmom_study as ts  # noqa: E402

D0 = date(2017, 1, 2)  # a Monday


@pytest.fixture(autouse=True)
def _score_from_start(monkeypatch):
    monkeypatch.setattr(tc, "SCORE_START", D0)


def _series(n=520, drift=0.002, vol=0.02, coins=3, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(coins):
        r = drift + vol * rng.standard_normal(n)
        c = 100 * np.exp(np.cumsum(r))
        o = np.concatenate([[100.0], c[:-1]])
        out[f"C{k}"] = ([D0 + timedelta(days=i) for i in range(n)], o, c)
    return out


def _setup(series, funding, perp_follows_spot=True, last=None):
    dates, names, op, cl = ts.build_panel(series, last)
    F = np.full(op.shape, funding, dtype=float) if np.isscalar(funding) else funding.copy()
    PO = op.copy() if perp_follows_spot else op * 1.0
    return dates, names, op, cl, F, PO, tc.trend_targets(dates, cl)


def _sim(series, cfg, funding=0.0005, last=None, **kw):
    dates, names, op, cl, F, PO, tg = _setup(series, funding, last=last)
    return tc.simulate(dates, op, cl, F, PO, tg, cfg, **kw)


def test_a_settlement_at_midnight_closes_the_previous_utc_day():
    def ms(h, m=0, s=0, milli=0, day=2):
        return int(datetime(2023, 1, day, h, m, s, milli * 1000, tzinfo=timezone.utc).timestamp() * 1000)
    text = "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(
        f"{t},8,0.0001" for t in (ms(0, milli=0), ms(0, milli=8), ms(8), ms(16), ms(0, day=3, milli=8)))
    got = tc.parse_funding(text)
    assert got[date(2023, 1, 1)][1] == 2      # the exact and the +8 ms 00:00 settlements of Jan 2
    assert got[date(2023, 1, 2)][1] == 3      # 08:00, 16:00 and Jan 3's 00:00 (+8 ms)
    assert got[date(2023, 1, 2)][0] == pytest.approx(0.0003)


def test_no_lookahead_truncating_the_future_changes_no_earlier_return():
    s = _series()
    cfg = tc.CONFIGS["TC"]
    full = _sim(s, cfg)
    part = _sim(s, cfg, last=D0 + timedelta(days=400))
    k = len(part["dates"])
    assert k > 100 and part["dates"] == full["dates"][:k]
    assert np.array_equal(part["net"], full["net"][:k])


def test_carry_alone_earns_funding_minus_two_legs_of_cost():
    s = _series(drift=0.0)
    sim = _sim(s, tc.CONFIGS["K"], funding=0.0005)
    assert sim["cfund"].sum() > 0 and np.allclose(sim["cbasis"], 0)
    assert np.allclose(sim["trend"], 0) and sim["trend_gross"].max() == 0
    # entry costs two legs on every notional: first fill turnover 0.3 -> cost 0.001 * 2 * 0.3
    first = np.flatnonzero(sim["cv"] > 0)[0]
    assert sim["cv"][first] == pytest.approx(0.3)
    assert sim["cost"][first] == pytest.approx(0.001 * 2 * 0.3)
    assert sim["net"].sum() == pytest.approx(sim["cfund"].sum() - sim["cost"].sum())


def test_carry_pays_when_funding_is_negative_and_is_not_entered():
    s = _series(drift=0.0)
    sim = _sim(s, tc.CONFIGS["K"], funding=-0.0005)
    assert sim["carry_notional"].max() == 0 and sim["cfund"].sum() == 0


def test_a_missing_funding_reading_is_never_zero_for_entry_or_for_a_held_coin():
    s = _series(drift=0.0)
    dates, names, op, cl, F, PO, tg = _setup(s, 0.0005)
    F[:, 0] = np.nan  # coin 0 unreadable throughout: never entered
    sim = tc.simulate(dates, op, cl, F, PO, tg, tc.CONFIGS["K"])
    assert sim["carry_notional"].max() == pytest.approx(0.2)  # only the two readable coins
    F2 = np.full(op.shape, 0.0005)
    F2[300:310, :] = np.nan  # a held coin loses its reading
    with pytest.raises(tc.CannotCheck):
        tc.simulate(dates, op, cl, F2, PO, tg, tc.CONFIGS["K"])


def test_basis_move_is_charged_to_the_carry_book():
    s = _series(drift=0.0)
    dates, names, op, cl, F, PO, tg = _setup(s, 0.0005)
    PO = PO * np.exp(np.linspace(0, 0.2, len(dates)))[:, None]  # perp drifts up vs spot: short loses
    sim = tc.simulate(dates, op, cl, F, PO, tg, tc.CONFIGS["K"])
    assert sim["cbasis"].sum() < 0


def test_carry_skips_a_coin_the_trend_leg_is_long_and_trend_leg_is_spot():
    s = _series(drift=0.004, vol=0.01, coins=1)
    both = _sim(s, tc.CONFIGS["TK"], funding=0.0005)
    assert both["trend_gross"].max() > 0 and both["carry_notional"].max() > 0  # carry before the trend is eligible
    assert not np.any((both["trend_gross"] > 0) & (both["carry_notional"] > 0))  # never both on one coin
    assert np.allclose(both["tfund"], 0)  # spot execution pays no funding


def test_perp_comparator_pays_funding_on_longs_and_spot_does_not():
    s = _series(drift=0.004, vol=0.01)
    perp = _sim(s, tc.CONFIGS["P0"], funding=0.0005)
    spot = _sim(s, tc.CONFIGS["P1"], funding=0.0005)
    assert perp["tfund"].sum() > 0 and spot["tfund"].sum() == 0
    assert perp["net"].sum() < spot["net"].sum()
    assert perp["perp_fills"] == 0


def test_perp_comparator_fills_a_missing_funding_day_and_counts_it():
    s = _series(drift=0.004, vol=0.01)
    dates, names, op, cl, F, PO, tg = _setup(s, np.nan)
    sim = tc.simulate(dates, op, cl, F, PO, tg, tc.CONFIGS["P0"])
    assert sim["perp_fills"] > 0
    held = sim["trend_gross"] > 0
    assert sim["tfund"][held].sum() > 0  # filled with the 0.03%/day fallback, not zero


def test_gear_levels_and_degear_cuts_exposure_after_a_loss_and_costs_turnover():
    assert tc.gear_for(0.0) == 1.0 and tc.gear_for(0.079) == 1.0
    assert tc.gear_for(0.08) == 0.5 and tc.gear_for(0.149) == 0.5
    assert tc.gear_for(0.15) == 0.25
    n = 520
    rng = np.random.default_rng(3)
    r = np.where(np.arange(n) < 300, 0.004, -0.02) + 0.003 * rng.standard_normal(n)  # trend then a crash
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate([[100.0], c[:-1]])
    s = {"A": ([D0 + timedelta(days=i) for i in range(n)], o, c)}
    plain = _sim(s, tc.CONFIGS["P1"], funding=0.0)
    geared = _sim(s, tc.CONFIGS["TD"], funding=0.0)
    assert plain["gear"].min() == 1.0 and geared["gear"].min() < 1.0
    assert (geared["gear"] < 1.0).any() and geared["tv"].sum() > 0


def test_carry_notional_is_scaled_to_fit_capital():
    dates, names, op, cl, F, PO, tg = _setup(_series(drift=0.0, coins=3), 0.0005)
    d = next(i for i in range(60, len(dates) - 2) if dates[i].weekday() == 6)
    n = tc.carry_target(d, F, PO, None, np.zeros(3))
    assert n.sum() == pytest.approx(0.3)
    heavy = np.array([0.9, 0.0, 0.0])
    cap = tc.CARRY_CAPITAL_FACTOR * float(n.sum())
    scaled = n * (max(0.0, 1.0 - heavy.sum()) / cap)
    assert tc.CARRY_CAPITAL_FACTOR * scaled.sum() == pytest.approx(0.1)


def test_hysteresis_keeps_a_held_coin_between_the_stay_and_enter_levels():
    dates, names, op, cl, F, PO, tg = _setup(_series(drift=0.0, coins=1), 0.00015)
    d = next(i for i in range(60, len(dates) - 2) if dates[i].weekday() == 6)
    assert tc.carry_target(d, F, PO, None, np.zeros(1)).sum() == 0
    assert tc.carry_target(d, F, PO, None, np.array([0.1])).sum() == pytest.approx(0.1)


def test_a_gap_inside_a_funding_series_stops_the_run(tmp_path):
    (tmp_path / "funding_X.csv").write_text("date,sum,n\n2023-01-01,0.0003,3\n2023-01-03,0.0003,3\n")
    (tmp_path / "perp_X.csv").write_text("date,open\n2023-01-01,1\n2023-01-02,1\n")
    with pytest.raises(tc.CannotCheck):
        tc.load_aux(tmp_path, "X")
    with pytest.raises(tc.CannotCheck):
        tc.load_aux(tmp_path, "NOPE")


def test_bars_fail_when_an_input_is_unreadable():
    yrs = {2023: {"days": 365, "ret": 0.1}, 2024: {"days": 365, "ret": 0.1}}
    assert all(ts.bars(0.9, 0.9, 0.1, 0.1, 0.5, yrs).values())
    assert not ts.bars(0.9, None, 0.1, 0.1, 0.5, yrs)["holdout_sharpe"]
    assert not ts.bars(0.9, 0.9, None, 0.1, 0.5, yrs)["holdout_boot_lb_gt_0"]


def test_run_study_reports_every_config_and_bars_on_a_synthetic_universe(monkeypatch):
    monkeypatch.setattr(tc, "DEV_END", D0 + timedelta(days=449))
    monkeypatch.setattr(tc, "HOLD_START", D0 + timedelta(days=450))
    s = _series(n=700)
    aux = {}
    for nm, (ds, o, c) in s.items():
        aux[nm] = ({d: 0.0003 for d in ds}, {d: float(v) for d, v in zip(ds, o)})
    # study uses its own COINS; exercise run_study directly on the synthetic names
    res = tc.run_study(s, aux, "holdout")
    assert set(res["tests"]) == set(tc.CONFIGS)
    t = res["tests"]["TC"]
    assert {"dev", "holdout", "bars", "passes", "btc_holdout"} <= set(t)

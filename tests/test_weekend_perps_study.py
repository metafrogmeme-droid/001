"""scripts/weekend_perps_study.py on planted prices."""
from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location(
        "weekend_perps_study", ROOT / "scripts" / "weekend_perps_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _series(m, prices_by_weekend, funding=None):
    """{symbol: {candles, funding}} with a bar ending at A, B and C of each weekend.
    prices_by_weekend: {friday: {symbol: (pA, pB, pC)}}."""
    out: dict = {}
    for fri, per_sym in prices_by_weekend.items():
        times = (m.at_et(fri, 16), m.at_et(fri + timedelta(days=2), 20),
                 m.at_et(fri + timedelta(days=3), 9))
        for s, ps in per_sym.items():
            d = out.setdefault(s, {"candles": [], "funding": (funding or {}).get(s, [])})
            for t, p in zip(times, ps):
                if p is not None:
                    d["candles"].append([t - m.HOUR_MS, p, p, p, p, 1.0, p])
    return out


def test_momentum_and_reversal_are_mirror_images_net_of_costs():
    m = _m()
    fri = m.FIRST_FRIDAY
    # The symbol rises 2% over SPY to B, then a further 1% over SPY to C.
    s = _series(m, {fri: {"SPYUSDT": (100, 100, 100), "AAA": (100, 102, 103.02)}})
    res = m.evaluate(s, ["AAA"])
    h1, h2 = res["rows"]["H1"][0]["pnl"], res["rows"]["H2"][0]["pnl"]
    two_legs = m.legs_cost(2, m.COST_PER_SIDE)
    assert h1 == pytest.approx(0.01 - two_legs)
    assert h2 == pytest.approx(-0.01 - two_legs)


def test_a_missing_bar_is_left_out_and_counted_not_scored_as_flat():
    m = _m()
    fri = m.FIRST_FRIDAY
    s = _series(m, {fri: {"SPYUSDT": (100, 100, 100), "AAA": (100, None, 101),
                          "BBB": (100, 101, 102.01)}})
    res = m.evaluate(s, ["AAA", "BBB"])
    # Every other weekend has no SPY bar at all, so both symbols are left out
    # there; on the planted weekend only AAA's missing bar is.
    assert res["left_out"]["missing_bar"] == (len(m.weekends()) - 1) * 2 + 1
    assert res["rows"]["H1"][0]["n"] == 1


def test_funding_charges_the_side_that_pays_it():
    m = _m()
    fri = m.FIRST_FRIDAY
    B = m.at_et(fri + timedelta(days=2), 20)
    funding = {"AAA": [[B + m.HOUR_MS, 0.001]], "SPYUSDT": []}
    s = _series(m, {fri: {"SPYUSDT": (100, 100, 100), "AAA": (100, 102, 102)}}, funding)
    res = m.evaluate(s, ["AAA"], per_side=0.0)
    # Long AAA (signal up) pays its +0.1% funding; the reversal (short) receives it.
    assert res["rows"]["H1"][0]["pnl"] == pytest.approx(-0.001)
    assert res["rows"]["H2"][0]["pnl"] == pytest.approx(0.001)


def test_the_rule_needs_both_windows_and_the_lower_bound():
    m = _m()
    good = [{"window": "discovery", "pnl": 0.01}] * 5 + [{"window": "holdout", "pnl": 0.01}] * 5
    bad_disc = [{"window": "discovery", "pnl": -0.01}] * 5 + [{"window": "holdout", "pnl": 0.01}] * 5
    noisy = ([{"window": "discovery", "pnl": 0.01}] * 5
             + [{"window": "holdout", "pnl": v} for v in (0.05, -0.04, 0.03, -0.035, 0.002)])
    assert m.judge(good)["passed"] is True
    assert m.judge(bad_disc)["passed"] is False
    assert m.judge(noisy)["passed"] is False


def test_universe_rule_filters_launch_type_status_and_hedge():
    m = _m()
    early, late = "1767225600000", "1780000000000"
    inst = [
        {"symbol": "AAA", "symbolType": "stock", "status": "online", "launchTime": early},
        {"symbol": "BBB", "symbolType": "stock", "status": "online", "launchTime": late},
        {"symbol": "CCC", "symbolType": "crypto", "status": "online", "launchTime": early},
        {"symbol": "SPYUSDT", "symbolType": "stock", "status": "online", "launchTime": early},
        {"symbol": "DDD", "symbolType": "stock", "status": "offline", "launchTime": early},
        {"symbol": "EEE", "symbolType": "stock", "status": "online", "launchTime": early},
    ]
    tick = [{"symbol": "AAA", "turnover24h": "10"}, {"symbol": "EEE", "turnover24h": "20"}]
    assert m.universe(inst, tick) == ["EEE", "AAA"]

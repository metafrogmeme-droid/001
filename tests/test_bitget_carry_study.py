"""scripts/bitget_carry_study.py on planted prices and funding."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location(
        "bitget_carry_study", ROOT / "scripts" / "bitget_carry_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _coin(m, rate=0.0001, spot=None, perp=None, skip=()):
    """Flat prices and three equal settlements a day, unless overridden."""
    ds = [m.START - timedelta(days=1), *m.days()]
    def bars(prices):
        return [[m._ms(d), p, p, p, p, 1, 1] for d, p in zip(ds, prices) if d not in skip]
    flat = [100.0] * len(ds)
    funding = []
    for d in m.days():
        base = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        funding += [[base + h * 3_600_000, rate] for h in (0, 8, 16)]
    return {"spot": bars(spot or flat), "perp": bars(perp or flat), "funding": funding}


def test_carry_collects_half_the_funding_on_flat_prices():
    m = _m()
    pnl, fund, count = m.coin_days(_coin(m, rate=0.0001))
    day = m.days()[3]
    assert count[day] == 3
    assert pnl[day] == pytest.approx(0.5 * 0.0003)


def test_basis_moves_count_and_a_missing_close_is_absent_not_zero():
    m = _m()
    n = len(m.days()) + 1
    spot = [100.0] * n
    perp = [100.0] * n
    perp[5] = 101.0                     # the perp richens for a day, then reverts
    skip = {m.days()[10]}
    pnl, _, _ = m.coin_days(_coin(m, rate=0.0, spot=spot, perp=perp, skip=skip))
    day5 = m.days()[4]                  # index 5 in the bars is days()[4]
    assert pnl[day5] == pytest.approx(0.5 * (0.0 - 0.01))
    assert m.days()[10] not in pnl and m.days()[11] not in pnl


def test_c2_holds_nothing_until_the_first_rebalance_and_drops_negative_funding():
    m = _m()
    res = m.books({"AAA": _coin(m, rate=0.0001), "BBB": _coin(m, rate=-0.0001)})
    c2 = res["rows"]["C2"]
    assert all(r["n_held"] == 0 for r in c2[: m.REBAL_DAYS])
    assert c2[m.REBAL_DAYS]["n_held"] == 1          # only AAA qualifies
    # The day it opens pays the opening cost on full weight.
    assert c2[m.REBAL_DAYS]["pnl"] == pytest.approx(0.5 * 0.0003 - m.COIN_SIDE)


def test_the_rule_needs_five_percent_and_a_positive_lower_bound():
    m = _m()
    rich = [{"pnl": 0.0003}] * 84                     # about 11% a year, steady
    thin = [{"pnl": 0.00005}] * 84                    # about 1.8% a year
    noisy = [{"pnl": v} for v in (0.01, -0.0095) * 42]
    assert m.judge(rich)["passed"] is True
    assert m.judge(thin)["passed"] is False
    assert m.judge(noisy)["passed"] is False


def test_universe_needs_a_spot_pair_and_ranks_unknown_turnover_last():
    m = _m()
    perps = [{"symbol": s, "symbolType": t, "status": "online"}
             for s, t in (("AAA", "crypto"), ("BBB", "crypto"), ("CCC", "stock"), ("DDD", "crypto"))]
    spots = [{"symbol": "AAA", "status": "online"}, {"symbol": "BBB", "status": "online"},
             {"symbol": "CCC", "status": "online"}]
    tick = [{"symbol": "AAA", "turnover24h": None}, {"symbol": "BBB", "turnover24h": "0"}]
    assert m.universe(perps, spots, tick) == ["BBB", "AAA"]

"""The Limit Entry Scanner holds its playbook's portfolio caps: at most five
markets at once, and at most two of one class's alts.

Phase 4 of the port. The playbook's risk layer is a leverage, a margin
budget, a per-trade loss cap of 15% of that budget, a daily circuit (pause at
30%, stop at 40%), at most 5 positions and at most 2 correlated alts. Where
the house risk gate is tighter (the loss per trade, the daily stop) it stays
in charge, and the card says the playbook's looser figures are not applied.
The two caps are tighter than anything the house applies to these markets:
every perpetual pools into one correlation bucket of three
(`RiskEngine._correlation_group`), and the house counts open positions only.
So the scanner holds its own, counting open positions and resting limits
together: every scanner position is a limit that rested first, so a cap
counted at placement holds at every fill.

Driven through the real `PortfolioBacktester` on hand-built bars, with the
pass's choice fixed so the wiring is what is measured.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.portfolio_engine import PLACED, PortfolioBacktester
from bot.core import limit_scanner as ls
from bot.skills.skill_registry import RunStrategySkill

PRESET = RunStrategySkill.PRESETS["limit scanner"]
T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
SYMS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "LINK/USDT:USDT", "ADA/USDT:USDT")
N = 140
FIRST, SECOND = 110, 111       # the bars whose closes the two plans come from


def _params(**kw) -> ls.ScannerParams:
    p = ls.params_from_preset(dict(PRESET, **kw))
    assert p is not None
    return p


# ── the reading ──────────────────────────────────────────────────────────


def test_the_preset_carries_both_caps_and_the_runner_is_handed_them():
    p = _params()
    assert (p.max_concurrent, p.max_correlated_alts) == (5, 2)
    assert {"max_concurrent", "max_correlated_alts"} <= set(ls.SCANNER_KEYS)
    assert ls.scanner_config(PRESET)["max_correlated_alts"] == 2


@pytest.mark.parametrize("bad", [0, -1, 2.5, True, "5", None])
def test_a_cap_that_is_not_a_positive_count_is_not_run(bad):
    assert ls.params_from_preset(dict(PRESET, max_concurrent=bad)) is None


def test_the_concurrency_cap_counts_every_market_held():
    p = _params(max_concurrent=2)
    assert ls.portfolio_cap(p, "ETHUSDT", ["SOLUSDT"]) is None
    assert ls.portfolio_cap(p, "ETHUSDT", ["SOLUSDT", "XAGUSDT"]) == "2 markets already held"


def test_the_alt_cap_counts_one_class_s_alts_only():
    p = _params()
    two_alts = ["LINKUSDT", "ADAUSDT"]
    assert ls.portfolio_cap(p, "SUIUSDT", two_alts) == "2 of its class's alts already held"
    # A major is not an alt, so neither holding one nor adding one counts.
    assert ls.portfolio_cap(p, "SOLUSDT", two_alts) is None
    assert ls.portfolio_cap(p, "SUIUSDT", ["LINKUSDT", "ETHUSDT"]) is None
    # Two metals are the metals class's, not the crypto class's.
    assert ls.portfolio_cap(p, "SUIUSDT", ["XAGUSDT", "XPTUSDT"]) is None
    assert ls.portfolio_cap(p, "XPDUSDT", ["XAGUSDT", "XPTUSDT"]) == \
        "2 of its class's alts already held"


def test_a_preset_without_caps_is_capped_by_nothing_of_its_own():
    p = ls.ScannerParams()
    assert (p.max_concurrent, p.max_correlated_alts) == (None, None)
    assert ls.portfolio_cap(p, "SUIUSDT", ["LINKUSDT"] * 50) is None


def test_the_card_says_the_caps_and_what_the_house_gate_keeps():
    line = ls.how_line(PRESET)
    assert "at most 5 markets and 2 of one class's alts" in line
    assert "a pass places nothing while either is full" in line
    assert "per-trade loss cap and daily circuit are looser" in line
    bare = {k: v for k, v in PRESET.items() if k not in ("max_concurrent", "max_correlated_alts")}
    assert "at most 5 markets" not in ls.how_line(bare)
    assert "house risk gate" in ls.how_line(bare)


# ── the backtest ─────────────────────────────────────────────────────────


def _data():
    return {s: [BacktestBar(timestamp=T0 + timedelta(hours=i), open=100.0, high=100.3,
                            low=99.7, close=100.0, volume=1_000.0, symbol=s)
                for i in range(N)] for s in SYMS}


def _plan(sym):
    return ls.EntryPlan(sym, ls.LONG, 99.5, 97.0, 104.5, 0.025, 1.0, 82.0)


@pytest.fixture
def two_plans(monkeypatch):
    """The pass offers LINK at FIRST's close and ``second`` at SECOND's; the
    real pass otherwise runs on flat bars and places nothing."""
    real = ls.scan_pass
    state = {"n": 0, "second": "ADA/USDT:USDT"}

    def scan(snaps, params, held=frozenset()):
        state["n"] += 1
        regime = ls.Regime(ls.LONG, 1.0, 2, True, "test")
        if state["n"] == FIRST - 99:
            return ls.Pass(regime, (), _plan("LINK/USDT:USDT"), "")
        if state["n"] == SECOND - 99:
            return ls.Pass(regime, (), _plan(state["second"]), "")
        return real(snaps, params, held)

    monkeypatch.setattr(ls, "scan_pass", scan)
    return state


def _run(**caps):
    import asyncio
    config = {k: v for k, v in ls.scanner_config(PRESET).items() if k != "classes"}
    config.update(leader="BTC", min_quote_volume=1.0, **caps)
    for key in ("max_concurrent", "max_correlated_alts"):
        if caps.get(key) is None:
            config.pop(key, None)
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry",
                                            scanner_config=config), symbols=list(SYMS))
    try:
        asyncio.run(pb.run(_data()))
    finally:
        pb.cleanup()
    return pb.scanner_passes


def test_a_second_market_is_not_placed_past_the_concurrency_cap(two_plans):
    passes = _run(max_concurrent=1)
    assert passes.get(PLACED) == 1, passes
    assert passes.get("capped: 1 markets already held") == 1, passes
    # The other arm: room for two places both.
    two_plans["n"] = 0
    assert _run(max_concurrent=2).get(PLACED) == 2


def test_a_second_alt_is_not_placed_past_the_alt_cap(two_plans):
    passes = _run(max_correlated_alts=1)
    assert passes.get("capped: 1 of its class's alts already held") == 1, passes
    assert passes.get(PLACED) == 1
    # A major beside one alt is not an alt.
    two_plans.update(n=0, second="ETH/USDT:USDT")
    assert _run(max_correlated_alts=1).get(PLACED) == 2


def test_two_classes_placing_in_one_pass_count_each_other(monkeypatch):
    """Crypto and metals pass in turn on the same bar, so the second class's
    plan is checked against the limit the first one placed a moment ago."""
    import asyncio
    syms = ("BTC/USDT:USDT", "LINK/USDT:USDT", "XAU/USDT:USDT", "XAG/USDT:USDT")
    data = {s: [BacktestBar(timestamp=T0 + timedelta(hours=i), open=100.0, high=100.3,
                            low=99.7, close=100.0, volume=1_000.0, symbol=s)
                for i in range(N)] for s in syms}
    real = ls.scan_pass
    seen = {"BTC": 0}

    def scan(snaps, params, held=frozenset()):
        regime = ls.Regime(ls.LONG, 1.0, 2, True, "test")
        if params.leader == "BTC":
            seen["BTC"] += 1
        if seen["BTC"] == FIRST - 99:
            sym = "LINK/USDT:USDT" if params.leader == "BTC" else "XAG/USDT:USDT"
            return ls.Pass(regime, (), _plan(sym), "")
        return real(snaps, params, held)

    monkeypatch.setattr(ls, "scan_pass", scan)
    config = {k: v for k, v in ls.scanner_config(PRESET).items()
              if k not in ("max_correlated_alts",)}
    config.update(max_concurrent=1, classes=[
        {"name": "crypto", "leader": "BTCUSDT", "min_quote_volume": 1.0, "members": ["LINKUSDT"]},
        {"name": "metals", "leader": "XAUUSDT", "min_quote_volume": 1.0, "members": ["XAGUSDT"]}])
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry",
                                            scanner_config=config), symbols=list(syms))
    try:
        asyncio.run(pb.run(data))
    finally:
        pb.cleanup()
    assert pb.scanner_passes.get("crypto: " + PLACED) == 1, pb.scanner_passes
    assert pb.scanner_passes.get("metals: capped: 1 markets already held") == 1, pb.scanner_passes

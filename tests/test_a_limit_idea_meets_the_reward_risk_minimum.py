"""A limit idea meets its strategy's minimum reward:risk, like any other idea.

`RiskEngine.evaluate` used to skip the minimum for any limit, on the line
"RISK_REWARD: <rr> OK (limit order, user-confirmed)". Nobody confirmed those:
a manual ticket takes its own branch one step earlier, and the analyzer makes
every one of its ideas a limit (a pullback level, or a 0.1 ATR offset when it
finds none), so the minimum applied to NO analyzer idea at all. Driven on the
frozen majors benchmark: 2,712 of 2,712 ideas evaluated were limits, 221 sat
below their own strategy's minimum, and 27 of the 237 approved went through on
that line.

A limit fills at its own entry price, so the ratio the gate reads is exactly
the ratio the fill gets; there is no reason for the order type to change the
rule. Enforced on four frozen snapshots it refuses 8-11% of approved trades
with a return effect inside the fold noise (docs/FROZEN_BENCHMARK.md). A manual
ticket keeps its exemption: the person chose those levels.

The claim is ONE rule for both order types, so it is driven as that: the same
geometry reads the same line as a limit and as a market idea, across the
boundary, for every strategy type.
"""
from __future__ import annotations

import pytest

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import TradeIdea

STRATEGY_TYPES = ("scalp", "intraday", "swing", "position")


def _idea(rr: float = 0.2, **kw) -> TradeIdea:
    # Risk 10 from an entry of 100, so the target sets the ratio directly.
    base = dict(id="TI-LIM", asset="TEST/USDT", direction="LONG",
                entry_price=100.0, stop_loss=90.0, take_profit=100.0 + 10.0 * rr,
                confidence=0.8, reasoning="limit test", signals_used=["rsi"],
                strategy_type="swing", order_type="limit")
    base.update(kw)
    return TradeIdea(**base)


def _rr_lines(idea: TradeIdea) -> tuple[list[str], list[str]]:
    res = RiskEngine(PortfolioTracker()).evaluate(idea, atr=2.0)
    return ([c for c in res.checks_passed if c.startswith("RISK_REWARD")],
            [c for c in res.checks_failed if c.startswith("RISK_REWARD")])


@pytest.mark.parametrize("st", STRATEGY_TYPES)
def test_a_limit_below_its_minimum_is_refused(st):
    lo = CONFIG.strategy_types.get_min_rr(st) - 0.3
    passed, failed = _rr_lines(_idea(rr=lo, strategy_type=st))
    assert passed == [], passed
    assert len(failed) == 1 and f"minimum ({st})" in failed[0], failed


@pytest.mark.parametrize("st", STRATEGY_TYPES)
def test_a_limit_at_or_above_its_minimum_passes(st):
    ok = CONFIG.strategy_types.get_min_rr(st) + 0.5
    passed, failed = _rr_lines(_idea(rr=ok, strategy_type=st))
    assert failed == [], failed
    assert len(passed) == 1 and " OK (min " in passed[0], passed


@pytest.mark.parametrize("st", STRATEGY_TYPES)
@pytest.mark.parametrize("delta", (-0.3, -0.02, -0.01, 0.0, 0.01, 0.5))
def test_limit_and_market_read_one_rule(st, delta):
    # Straddles the gate's own 0.01 tolerance, so a limit branch with a
    # different comparison (or none) answers differently somewhere here.
    rr = round(CONFIG.strategy_types.get_min_rr(st) + delta, 2)
    as_limit = _rr_lines(_idea(rr=rr, strategy_type=st, order_type="limit"))
    as_market = _rr_lines(_idea(rr=rr, strategy_type=st, order_type="market"))
    assert as_limit == as_market


def test_no_line_claims_a_confirmation_or_an_unrun_check():
    passed, failed = _rr_lines(_idea())
    for line in passed + failed:
        assert "user-confirmed" not in line and "not checked" not in line, line


def test_a_manual_ticket_keeps_its_own_line_even_below_the_minimum():
    passed, failed = _rr_lines(_idea(rr=0.2, source="manual"))
    assert passed == ["RISK_REWARD: skipped (manual trade)"], passed
    assert failed == [], failed


def test_the_fixture_really_sits_below_every_minimum():
    # Otherwise the refusal tests above measure nothing.
    for st in STRATEGY_TYPES:
        assert _idea(rr=CONFIG.strategy_types.get_min_rr(st) - 0.3,
                     strategy_type=st).risk_reward_ratio < CONFIG.strategy_types.get_min_rr(st) - 0.01

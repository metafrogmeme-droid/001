"""A limit idea's reward:risk line says it was NOT checked, not "user-confirmed".

`RiskEngine.evaluate` handles a manual ticket one branch earlier, so every idea
reaching the limit branch is a NON-manual limit -- and the analyzer makes every
one of its ideas a limit (a pullback level, or a 0.1 ATR offset when it finds
none). Driven on the frozen majors benchmark: 2,712 of 2,712 ideas evaluated
were limits with source "unknown", 221 sat below their own strategy's minimum,
and 27 of the 237 approved went through on the line
"RISK_REWARD: <rr> OK (limit order, user-confirmed)" -- a confirmation no user
gave, printed as a passed check on the decision record.

Whether the minimum SHOULD apply to the analyzer's limits is a decision about
the live gate: enforced, it refuses 8-11% of approved trades on the frozen
snapshots, with a return effect inside the fold noise. That is left to the
operator. The label is not a decision: a check nobody ran does not print OK.
"""
from __future__ import annotations

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import TradeIdea


def _idea(**kw):
    base = dict(id="TI-LIM", asset="TEST/USDT", direction="LONG",
                entry_price=100.0, stop_loss=95.0, take_profit=101.0,
                confidence=0.8, reasoning="limit test", signals_used=["rsi"],
                strategy_type="swing", order_type="limit")
    base.update(kw)
    return TradeIdea(**base)


def _rr_lines(idea):
    res = RiskEngine(PortfolioTracker()).evaluate(idea, atr=2.0)
    return [c for c in res.checks_passed + res.checks_failed if c.startswith("RISK_REWARD")]


def test_an_analyzer_limit_is_not_called_user_confirmed():
    assert idea_rr_below_min() < CONFIG.strategy_types.get_min_rr("swing")
    lines = _rr_lines(_idea())
    assert len(lines) == 1, lines
    assert "user-confirmed" not in lines[0]
    assert "not checked" in lines[0] and " OK" not in lines[0], lines[0]


def test_the_limit_branch_is_still_exempt_so_this_is_a_label_not_a_gate():
    # The fixture's 0.2 R:R would be refused on the market branch; on the
    # limit branch it still is not. If this starts failing, the gate changed
    # -- a decision, not a wording fix -- and the docstring above is stale.
    lines = _rr_lines(_idea())
    assert not any("minimum (" in ln for ln in lines), lines
    market = _rr_lines(_idea(order_type="market"))
    assert any("minimum (" in ln for ln in market), market


def test_a_manual_ticket_keeps_its_own_line():
    lines = _rr_lines(_idea(source="manual"))
    assert lines == ["RISK_REWARD: skipped (manual trade)"], lines


def idea_rr_below_min() -> float:
    return _idea().risk_reward_ratio

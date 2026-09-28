"""The risk gate is the final authority on minimum stop distance."""
from __future__ import annotations

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _idea(stop: float) -> TradeIdea:
    return TradeIdea(
        id="TI-STOP-FLOOR", asset="TEST/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=stop, take_profit=102.0,
        confidence=0.9, reasoning="stop floor test", signals_used=["rsi"],
        strategy_type="swing", order_type="limit", source="manual",
    )


def _stop_lines(tmp_path, idea):
    risk = RiskEngine(
        PortfolioTracker(initial_balance=10_000.0),
        state_file=str(tmp_path / "risk_state.json"),
    )
    check = risk.evaluate(idea, atr=1.0)
    return ([x for x in check.checks_passed if x.startswith("STOP_DISTANCE")],
            [x for x in check.checks_failed if x.startswith("STOP_DISTANCE")])


def test_risk_engine_rejects_stop_below_floor(tmp_path):
    passed, failed = _stop_lines(tmp_path, _idea(99.87))
    assert passed == []
    assert len(failed) == 1
    assert "0.130% < 0.40% floor" in failed[0]


def test_risk_engine_accepts_normal_stop(tmp_path):
    passed, failed = _stop_lines(tmp_path, _idea(99.2))
    assert failed == []
    assert passed == ["STOP_DISTANCE: 0.80% OK"]

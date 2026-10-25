"""
Opt-in Kelly sizing in RiskEngine.evaluate() — config-gated, default OFF,
tighten-only. When enabled, the engine takes min(fixed-fractional, half-Kelly)
so Kelly can only SHRINK a position; the notional/margin caps stay authoritative.
Default OFF keeps sizing byte-identical to prior behaviour.
"""

import dataclasses
import os
import tempfile
from datetime import datetime

from bot.compat import UTC
from bot.config import CONFIG
import bot.risk.risk_engine as risk_engine_mod
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeExecution, TradeIdea, TradeStatus


def _engine(balance=10_000.0):
    state = os.path.join(tempfile.mkdtemp(prefix="rc-kelly-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


def _idea(conf=0.8):
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=98.0, take_profit=106.0, confidence=conf,
        reasoning="kelly", source="scan", timestamp=datetime.now(UTC),
    )


def _with_kelly(enabled=True, min_trades=20, on_risk=None):
    kwargs = {"kelly_sizing_enabled": enabled, "kelly_min_trades": min_trades}
    if on_risk is not None:
        kwargs["kelly_on_risk_enabled"] = on_risk
    new_risk = dataclasses.replace(CONFIG.risk, **kwargs)
    return dataclasses.replace(CONFIG, risk=new_risk)


def _seed_history(engine, n_wins, n_losses, win=50.0, loss=20.0):
    """Push closed trades onto the portfolio so Kelly has an edge estimate."""
    hist = engine._portfolio._history  # trade_history is a copy; append to backing store
    for i in range(n_wins):
        hist.append(TradeExecution(
            trade_id=f"W{i}", asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=98.0, take_profit=106.0, exit_price=105.0,
            quantity=1.0, status=TradeStatus.EXECUTED, pnl=win))
    for i in range(n_losses):
        hist.append(TradeExecution(
            trade_id=f"L{i}", asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=98.0, take_profit=106.0, exit_price=98.0,
            quantity=1.0, status=TradeStatus.EXECUTED, pnl=-loss))


class TestDefaultOn:
    def test_config_default_is_on(self):
        # Default ON since the operator enabled runbook stage-2 hardening
        # (tighten-only: Kelly can only ever REDUCE the base size).
        assert CONFIG.risk.kelly_sizing_enabled is True

    def test_disabled_path_does_not_apply_kelly(self, monkeypatch):
        eng = _engine()
        _seed_history(eng, 15, 5)
        baseline = eng.evaluate(_idea()).position_size_usd
        # Flip ON: with the same history, the tighten-only path must not grow size.
        monkeypatch.setattr(risk_engine_mod, "CONFIG", _with_kelly(True, 20))
        kelly_on = eng.evaluate(_idea()).position_size_usd
        assert kelly_on <= baseline + 1e-9


class TestKellyHelper:
    def test_short_history_uses_the_floor_not_full_size(self, monkeypatch):
        eng = _engine()
        _seed_history(eng, 3, 2)  # 5 < default 20
        from bot.risk.risk_engine import sizing_standard_now
        lev = sizing_standard_now()
        floor = CONFIG.risk.kelly_floor_risk_pct / 100.0
        stop = 0.02  # idea entry 100, stop 98
        # The suite turns the flag off; this reading is the shipped one.
        monkeypatch.setattr(risk_engine_mod, "CONFIG", _with_kelly(True, 20, on_risk=True))
        assert eng._kelly_size_usd(_idea(), 10_000.0) == round(
            10_000.0 * floor / (stop * lev), 2)
        monkeypatch.setattr(risk_engine_mod, "CONFIG", _with_kelly(True, 20, on_risk=False))
        assert eng._kelly_size_usd(_idea(), 10_000.0) == 0.0

    def test_positive_with_edge_and_history(self):
        eng = _engine()
        _seed_history(eng, 15, 5)  # 20 trades, 75% win, avg win 50 / loss 20
        assert eng._kelly_size_usd(_idea(), 10_000.0) > 0.0

    def test_noop_on_zero_equity(self):
        eng = _engine()
        _seed_history(eng, 15, 5)
        assert eng._kelly_size_usd(_idea(), 0.0) == 0.0


class TestTightenOnly:
    def test_kelly_only_shrinks_never_grows(self, monkeypatch):
        eng = _engine()
        _seed_history(eng, 15, 5)
        off = eng.evaluate(_idea()).position_size_usd
        monkeypatch.setattr(risk_engine_mod, "CONFIG", _with_kelly(True, 20))
        on = eng.evaluate(_idea()).position_size_usd
        assert on <= off + 1e-9

    def test_noedge_kelly_does_not_zero_the_position(self, monkeypatch):
        eng = _engine()
        # A losing record is not a full-size grant and not a zero-size grant.
        _seed_history(eng, 5, 15)
        monkeypatch.setattr(risk_engine_mod, "CONFIG", _with_kelly(True, 20, on_risk=True))
        sized = eng.evaluate(_idea()).position_size_usd
        monkeypatch.setattr(risk_engine_mod, "CONFIG", _with_kelly(False, 20, on_risk=True))
        full = eng.evaluate(_idea()).position_size_usd
        assert sized > 0.0
        assert sized < full

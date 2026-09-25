"""The high-conviction flat margin passes the risk gate's own limits.

`HIGH_CONVICTION_ENABLED` (off by default) gives an idea at or above a
confidence floor a flat margin. Its docstring said the rule "can never raise
one past a limit that already bound it", and the only ceiling it checked was
the executor's. Driven at $200 of equity, the risk gate sized an idea at $26
(its 13% notional cap, under a governor REDUCE) and the flat margin turned
that into $100: half the account, past the cap the gate had just applied and
past the governor's reduction.

The operator's decision (2026-09-25): the flat margin replaces only the
stop-distance BASE, and passes everything the gate does after it. The check
carries that as two numbers, `base_multiplier` and `base_ceiling_usd`.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

import bot.core.engine as engine_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


class _Session:
    def __init__(self, mult):
        self.size_multiplier = mult


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=0.9, reasoning="x",
                     source="scan")


def _check(tmp_path, equity, *, gov=1.0, name="r"):
    eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                     state_file=os.path.join(str(tmp_path), name + ".json"))
    with patch.object(RiskEngine, "live_performance_size_multiplier",
                      new_callable=PropertyMock, return_value=gov), \
         patch("bot.core.session_aware.get_current_session",
               lambda now=None: _Session(1.0)):
        return eng.evaluate(_idea(), atr=1.0, live_equity=equity, max_position_usd=None,
                            live_open_count=0, live_mode=True, live_book=())


class _Eng:
    _high_conviction_margin = RuneClawEngine._high_conviction_margin
    _high_conviction_ceiling = RuneClawEngine._high_conviction_ceiling

    def _per_user_margin_cap(self, user_id):
        return None


_FIELDS = ("high_conviction_enabled", "high_conviction_min_confidence",
           "high_conviction_margin_usd")


@pytest.fixture
def flat_margin(monkeypatch):
    before = {f: getattr(CONFIG.execution, f) for f in _FIELDS}
    object.__setattr__(CONFIG.execution, "high_conviction_enabled", True)
    object.__setattr__(CONFIG.execution, "high_conviction_min_confidence", 0.7)
    object.__setattr__(CONFIG.execution, "high_conviction_margin_usd", 100.0)
    rows: list = []
    monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: rows.append(kw))
    yield rows
    for f, v in before.items():
        object.__setattr__(CONFIG.execution, f, v)


def _flat(check, size=None):
    return _Eng()._high_conviction_margin(
        _idea(), check.position_size_usd if size is None else size, "", check=check)


def test_a_small_account_keeps_the_gates_cap(tmp_path, flat_margin):
    """The case that was driven: $200 of equity, a 13% cap of $26."""
    check = _check(tmp_path, 200.0, gov=0.5)
    assert check.position_size_usd == pytest.approx(26.0)
    assert _flat(check) == pytest.approx(26.0), "not $100: the cap had bound"


def test_a_large_account_still_gets_the_flat_margin(tmp_path, flat_margin):
    """Where the gate's cap is roomy the rule does what it was asked to do:
    the same margin whatever the stop."""
    check = _check(tmp_path, 10_000.0)
    assert check.position_size_usd > 100.0, "the premise: the gate sized larger"
    assert _flat(check) == pytest.approx(100.0)


def test_the_gates_reductions_apply_to_the_flat_margin(tmp_path, flat_margin):
    """A governor REDUCE halves the base; it halves the flat margin too."""
    check = _check(tmp_path, 10_000.0, gov=0.5)
    assert check.base_multiplier == pytest.approx(0.5)
    assert _flat(check) == pytest.approx(50.0)


def test_the_check_carries_the_lowest_ceiling_after_the_base(tmp_path):
    check = _check(tmp_path, 200.0)
    assert check.base_ceiling_usd == pytest.approx(26.0)
    assert check.base_multiplier == pytest.approx(1.0)


def test_a_check_that_sized_nothing_leaves_the_figure_alone(flat_margin):
    bare = SimpleNamespace()
    assert _Eng()._high_conviction_margin(_idea(), 37.5, "", check=bare) == 37.5
    assert _Eng()._high_conviction_margin(_idea(), 37.5, "") == 37.5
    assert [r.get("result") for r in flat_margin
            if r.get("action") == "high_conviction_size"] == ["UNBOUNDED", "UNBOUNDED"]


def test_a_ceiling_below_the_multiplied_target_binds(flat_margin):
    """Half-Kelly and the notional cap are one ceiling to the flat margin:
    whichever is lower."""
    check = SimpleNamespace(base_multiplier=0.8, base_ceiling_usd=30.0)
    assert _Eng()._high_conviction_margin(_idea(), 10.0, "", check=check) == pytest.approx(30.0)
    roomy = SimpleNamespace(base_multiplier=0.8, base_ceiling_usd=500.0)
    assert _Eng()._high_conviction_margin(_idea(), 10.0, "", check=roomy) == pytest.approx(80.0)


def test_both_call_sites_hand_over_the_check():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(engine_mod))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_high_conviction_margin"]
    assert len(calls) == 2
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert "check" in kw and isinstance(kw["check"], ast.Name), ast.unparse(c)


def test_the_gates_half_kelly_ceiling_travels_on_the_check(tmp_path):
    eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                     state_file=os.path.join(str(tmp_path), "k.json"))
    with patch.object(RiskEngine, "live_performance_size_multiplier",
                      new_callable=PropertyMock, return_value=1.0), \
         patch.object(RiskEngine, "_kelly_size_usd", lambda self, idea, eq: 40.0), \
         patch("bot.core.session_aware.get_current_session",
               lambda now=None: _Session(1.0)):
        check = eng.evaluate(_idea(), atr=1.0, live_equity=10_000.0, max_position_usd=None,
                             live_open_count=0, live_mode=True, live_book=())
    assert check.base_ceiling_usd == pytest.approx(40.0)

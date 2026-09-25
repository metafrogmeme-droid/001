"""A size reduction the notional cap takes back is not printed as a reduction.

The notional cap binds on ~every crypto trade, and on EVERY trade of a small
live account, so a multiplier applied to the pre-cap size alone is clamped
straight back to the same figure. Four reductions already tighten the cap
(regime, the equity throttle, the risk preference, the quality ladder), each
for that stated reason. Seven did not: session, the session provider fallback,
the equity-curve breaker, the live-performance governor, drawdown recovery,
macro, and correlation sizing. Driven at $128 of equity, the governor's REDUCE
x0.50 left the order at $16.64 either way, with "governor x0.50" on the size
trace one step above the cap that undid it.

Letting them reach the order was measured on the frozen benchmark and did not
come back harmless: it helped majors_1h and cost alts_1h and corr_dense_1h
(docs/FROZEN_BENCHMARK.md). So which of them tighten the cap is one named
policy, `PRE_CAP_TIGHTENS_CAP`, and whatever it leaves out is NAMED as taken
back whenever the cap binds, rather than left on the trace as a reduction.
"""
from __future__ import annotations

import ast
import os
import pathlib
from unittest.mock import PropertyMock, patch

import pytest

import bot.config as bot_config
from bot.risk import risk_engine as RE
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _idea():
    return TradeIdea(asset="ARB/USDT:USDT", direction=Direction.LONG, entry_price=1.0,
                     stop_loss=0.98, take_profit=1.06, confidence=0.8, reasoning="x")


class _Session:
    def __init__(self, mult):
        self.size_multiplier = mult


def _evaluate(tmp_path, equity, *, gov=1.0, session=1.0, eq_curve=1.0, name="r"):
    eng = RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), name + ".json"))
    with patch.object(RiskEngine, "live_performance_size_multiplier",
                      new_callable=PropertyMock, return_value=gov), \
         patch.object(RiskEngine, "equity_curve_size_multiplier",
                      new_callable=PropertyMock, return_value=eq_curve), \
         patch("bot.core.session_aware.get_current_session",
               lambda now=None: _Session(session)):
        return eng.evaluate(_idea(), atr=0.01, live_equity=equity, max_position_usd=100.0,
                            live_open_count=0, live_mode=True, live_book=())


def _taken_back_line(check):
    lines = [c for c in check.checks_passed if c.startswith("SIZE_REDUCTIONS:")]
    return lines[0] if lines else None


# $128 of equity: the 13% cap is $16.64, far under the $100 execution ceiling.
SMALL = 128.0


def test_the_cap_binds_on_a_small_account(tmp_path):
    """The premise: with nothing reducing, the cap decides the size."""
    check = _evaluate(tmp_path, SMALL)
    assert check.position_size_usd == pytest.approx(16.64)
    assert check.size_basis.startswith("notional cap")
    assert _taken_back_line(check) is None


@pytest.mark.parametrize("kw, label", [
    ({"gov": 0.5}, "live-performance governor x0.50"),
    ({"session": 0.75}, "session x0.75"),
    ({"eq_curve": 0.5}, "equity-curve breaker x0.50"),
])
def test_a_reduction_the_cap_takes_back_is_named_as_taken_back(tmp_path, kw, label):
    check = _evaluate(tmp_path, SMALL, **kw)
    assert check.position_size_usd == pytest.approx(16.64), "the policy is empty"
    line = _taken_back_line(check)
    assert line is not None and label in line, check.checks_passed
    assert "none of them reached it" in line
    assert f"took back {label}" in check.size_basis, check.size_basis


def test_every_reduction_the_cap_took_back_is_named(tmp_path):
    check = _evaluate(tmp_path, SMALL, gov=0.5, session=0.75)
    line = _taken_back_line(check)
    assert "session x0.75" in line and "live-performance governor x0.50" in line


@pytest.mark.parametrize("kw, kind", [
    ({"gov": 0.5}, "governor"),
    ({"session": 0.75}, "session"),
    ({"eq_curve": 0.5}, "equity_curve"),
])
def test_a_kind_the_policy_names_reaches_the_order(tmp_path, monkeypatch, kw, kind):
    monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({kind}))
    mult = next(iter(kw.values()))
    check = _evaluate(tmp_path, SMALL, **kw)
    assert check.position_size_usd == pytest.approx(16.64 * mult)
    assert _taken_back_line(check) is None


def test_the_policy_is_per_kind(tmp_path, monkeypatch):
    """One kind reaching the order does not carry another with it."""
    monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({"governor"}))
    check = _evaluate(tmp_path, SMALL, gov=0.5, session=0.75)
    assert check.position_size_usd == pytest.approx(16.64 * 0.5)
    line = _taken_back_line(check)
    assert "session x0.75" in line and "governor" not in line


def test_a_reduction_the_cap_does_not_undo_is_not_called_taken_back(tmp_path):
    """Where the cap does not bind the reduction reached the order already."""
    base = _evaluate(tmp_path, 1000.0, name="a")
    reduced = _evaluate(tmp_path, 1000.0, gov=0.5, name="b")
    assert base.position_size_usd == pytest.approx(100.0)
    assert reduced.position_size_usd == pytest.approx(50.0)
    assert _taken_back_line(reduced) is None
    assert "took back" not in reduced.size_basis


def test_macro_records_itself(tmp_path, monkeypatch):
    eng = RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "m.json"))

    class _Provider:
        def get_context(self, symbol=None):
            return type("Ctx", (), {"risk_state": "REDUCE", "size_multiplier": 0.5})()

    eng._macro_provider = _Provider()
    with patch.object(RiskEngine, "live_performance_size_multiplier",
                      new_callable=PropertyMock, return_value=1.0), \
         patch("bot.core.session_aware.get_current_session",
               lambda now=None: _Session(1.0)):
        taken = eng.evaluate(_idea(), atr=0.01, live_equity=SMALL, max_position_usd=100.0,
                             live_open_count=0, live_mode=True, live_book=())
        monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({"macro"}))
        reached = eng.evaluate(_idea(), atr=0.01, live_equity=SMALL, max_position_usd=100.0,
                               live_open_count=0, live_mode=True, live_book=())
    assert "macro x0.50" in _taken_back_line(taken)
    assert reached.position_size_usd == pytest.approx(16.64 * 0.5)


def test_drawdown_recovery_records_itself(tmp_path, monkeypatch):
    mult = bot_config.CONFIG.risk.drawdown_recovery_size_mult
    assert mult < 1.0, "the premise: recovery mode reduces"
    monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({"drawdown_recovery"}))
    eng = RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "d.json"))
    eng._in_drawdown_recovery = True
    with patch.object(RiskEngine, "live_performance_size_multiplier",
                      new_callable=PropertyMock, return_value=1.0), \
         patch("bot.core.session_aware.get_current_session",
               lambda now=None: _Session(1.0)):
        check = eng.evaluate(_idea(), atr=0.01, live_equity=SMALL, max_position_usd=100.0,
                             live_open_count=0, live_mode=True, live_book=())
    assert check.position_size_usd == pytest.approx(16.64 * mult)


def test_the_policy_starts_empty_and_names_only_real_kinds():
    """Empty is the measured decision (see the module docstring); a kind the
    policy names that no reduction records would make it a no-op."""
    assert RE.PRE_CAP_TIGHTENS_CAP == frozenset()
    assert RE.PRE_CAP_TIGHTENS_CAP <= set(RE.PRE_CAP_KINDS)


def test_every_recorded_kind_is_a_declared_kind():
    """The kinds are read off the append calls, so a reduction recorded under
    a new word fails here rather than reading as a kind the policy can never
    name."""
    tree = ast.parse((ROOT / "bot" / "risk" / "risk_engine.py").read_text())
    kinds = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_pre_cap_only"):
            arg = node.args[0]
            assert isinstance(arg, ast.Tuple) and isinstance(arg.elts[0], ast.Constant)
            kinds.add(arg.elts[0].value)
    assert kinds == set(RE.PRE_CAP_KINDS)


def test_correlation_sizing_records_itself(tmp_path, monkeypatch):
    """Driven on the live book a trade joins: a same-group, same-side
    position shrinks the new trade x0.80 before the cap."""
    from bot.risk.held_book import HeldRow
    idea = TradeIdea(asset="NEAR/USDT", direction=Direction.LONG, entry_price=5.0,
                     stop_loss=4.9, take_profit=5.3, confidence=0.8, reasoning="x")
    book = (HeldRow("AVAX/USDT", "LONG", 5.0, 25.0),)

    def run(name):
        eng = RiskEngine(PortfolioTracker(),
                         state_file=os.path.join(str(tmp_path), name + ".json"))
        with patch.object(RiskEngine, "live_performance_size_multiplier",
                          new_callable=PropertyMock, return_value=1.0), \
             patch("bot.core.session_aware.get_current_session",
                   lambda now=None: _Session(1.0)):
            return eng.evaluate(idea, atr=0.05, live_equity=SMALL, max_position_usd=100.0,
                                live_open_count=1, live_mode=True, live_book=book)
    taken = run("t")
    assert "correlation x0.80" in (_taken_back_line(taken) or ""), taken.checks_passed
    monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({"correlation"}))
    reached = run("r")
    assert reached.position_size_usd == pytest.approx(taken.position_size_usd * 0.8, abs=0.01)


# ── the audit card's "binds" line makes the same claim ─────────────────────


def _gov(**kw):
    base = dict(enabled=True, samples=20, win_rate=0.5, net=10.0, min_samples=10,
                pause_winrate=0.25, reduce_winrate=0.40, reduce_mult=0.5)
    base.update(kw)
    return base


def test_the_audit_says_the_cap_takes_a_reduce_change_back():
    from bot.core.self_audit import proposal_binding
    out = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.25, _gov(win_rate=0.30, net=5.0))
    assert "binds" in out and "0.25" in out
    assert "Not on the order while the notional cap binds" in out


def test_the_caveat_goes_once_the_governor_reaches_the_order(monkeypatch):
    from bot.core.self_audit import proposal_binding
    monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({"governor"}))
    out = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.25, _gov(win_rate=0.30, net=5.0))
    assert "binds" in out and "notional cap" not in out


def test_a_change_into_or_out_of_pause_carries_no_caveat():
    """x0 refuses the trade, so no cap is left to take it back."""
    from bot.core.self_audit import proposal_binding
    out = proposal_binding("LIVE_PERF_REDUCE_WINRATE", 0.55, _gov(win_rate=0.50, net=5.0))
    assert "binds" in out and "notional cap" in out  # OK -> REDUCE: both > 0
    paused = {"win_rate": 0.20, "net": -5.0}
    out = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.0, _gov(**{**paused, "pause_winrate": 0.1}))
    assert "binds" in out and "notional cap" not in out


def test_the_session_fallback_records_itself(tmp_path, monkeypatch):
    """A session provider that raises halves the pre-cap size (fail toward
    safety), and the cap takes that back like any other reduction."""
    def boom(now=None):
        raise RuntimeError("provider down")

    def run(name):
        eng = RiskEngine(PortfolioTracker(),
                         state_file=os.path.join(str(tmp_path), name + ".json"))
        with patch.object(RiskEngine, "live_performance_size_multiplier",
                          new_callable=PropertyMock, return_value=1.0), \
             patch("bot.core.session_aware.get_current_session", boom):
            return eng.evaluate(_idea(), atr=0.01, live_equity=SMALL, max_position_usd=100.0,
                                live_open_count=0, live_mode=True, live_book=())
    taken = run("t")
    assert "session provider fallback x0.5" in (_taken_back_line(taken) or "")
    monkeypatch.setattr(RE, "PRE_CAP_TIGHTENS_CAP", frozenset({"session_fallback"}))
    assert run("r").position_size_usd == pytest.approx(16.64 * 0.5, abs=0.01)

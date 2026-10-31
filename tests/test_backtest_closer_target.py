"""`target_r_after_gate`: the same approved entries, a closer target.

docs/research/prereg/closer_target_2026-10. Driven through the real
`BacktestEngine._apply_post_gate_target` on a real `TradeIdea`; where it sits
in `_process_bar` (after the risk gate, before the fill) is read off the AST,
because the pipeline around it needs a live analyzer to reach.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.backtest.runner import _preset_gate_kwargs, build_parser
from bot.utils.models import Direction, TradeIdea

_ENGINES: list[BacktestEngine] = []


@pytest.fixture(autouse=True)
def _cleanup_engines():
    yield
    while _ENGINES:
        _ENGINES.pop().cleanup()


def _engine(**cfg_kw):
    eng = BacktestEngine(BacktestConfig(symbol="BTC/USDT", **cfg_kw))
    _ENGINES.append(eng)
    return eng


def _idea(direction=Direction.LONG, entry=100.0, stop=98.0, target=108.0):
    return TradeIdea(id="t1", asset="BTC/USDT", direction=direction, entry_price=entry,
                     stop_loss=stop, take_profit=target, confidence=0.8, reasoning="x",
                     strategy_type="swing")


def test_unset_is_the_idea_unchanged():
    eng = _engine()
    idea = _idea()
    assert eng._apply_post_gate_target(idea) is idea
    assert eng._target_r_applied == 0 and eng._target_r_unread == 0


def test_a_long_target_moves_to_k_stop_distances_and_the_stop_stays():
    eng = _engine(target_r_after_gate=1.0)
    out = eng._apply_post_gate_target(_idea())
    assert out.take_profit == pytest.approx(102.0)
    assert out.stop_loss == 98.0 and out.entry_price == 100.0
    assert eng._target_r_applied == 1


def test_a_short_target_moves_below_entry():
    eng = _engine(target_r_after_gate=1.5)
    out = eng._apply_post_gate_target(
        _idea(Direction.SHORT, entry=100.0, stop=102.0, target=92.0))
    assert out.take_profit == pytest.approx(97.0)
    assert out.stop_loss == 102.0


@pytest.mark.parametrize("stop", [0.0, 100.0])
def test_an_unreadable_stop_distance_keeps_its_own_target_and_is_counted(stop):
    eng = _engine(target_r_after_gate=1.0)
    # model_copy does not validate: a stored idea can carry what a new one cannot.
    idea = _idea().model_copy(update={"stop_loss": stop})
    assert eng._apply_post_gate_target(idea) is idea
    assert eng._target_r_unread == 1 and eng._target_r_applied == 0


def test_a_target_that_would_cross_zero_is_not_given():
    eng = _engine(target_r_after_gate=3.0)
    idea = _idea(Direction.SHORT, entry=1.0, stop=1.5, target=0.5)
    assert eng._apply_post_gate_target(idea) is idea
    assert eng._target_r_unread == 1


def test_the_runner_flag_reaches_the_config():
    parser = build_parser()
    args = parser.parse_args(["--dataset", "x", "--target-r-after-gate", "1.5"])
    assert _preset_gate_kwargs(args)["target_r_after_gate"] == 1.5
    args = parser.parse_args(["--dataset", "x"])
    assert _preset_gate_kwargs(args)["target_r_after_gate"] is None


def _calls_in_order(func) -> list[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.append((node.lineno, node.func.attr))
    return [name for _, name in sorted(out)]


def test_it_runs_after_the_gate_and_before_the_fill():
    calls = _calls_in_order(BacktestEngine._process_bar)
    assert calls.count("_apply_post_gate_target") == 1
    at = calls.index("_apply_post_gate_target")
    assert calls.index("evaluate") < at, "the gate judges the analyzer's own levels"
    assert at < calls.index("register_pending_intent") < calls.index("_queue_entry")

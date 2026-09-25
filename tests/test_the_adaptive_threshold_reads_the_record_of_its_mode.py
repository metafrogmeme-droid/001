"""The adaptive auto-confirm bar reads the paper record, and says so in live mode.

The block in `_tick` moved the auto-confirm threshold on the recent win rate
of `self.portfolio._history`: the PAPER book. In LIVE mode nothing writes
that book, so a fresh live deploy never moved the bar, and what the book
HOLDS is whatever paper trading left there before the account went live.
Driven with ten paper closes at 80% on the record and a live bar of 0.85:
five ticks walked it to the 0.60 floor, one step each, on a record no live
trade was in. That is the RC-2026-021 shape one book over: the bar that
decides which real-money orders execute without a human, moved by a
feature reading somebody else's record.

Whether a LIVE record should move a live bar is the operator's decision
(the winning direction lowers the bar, the losing one raises it, and both
change what executes without a human), so in live mode the bar stays where
it was set and the engine says so once. Paper is unchanged. The block is a
seam now (`_adapt_auto_confirm_threshold`), because a block inline in a
434-line tick is a block nothing can drive.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime

import pytest

import bot.config as bot_config
from bot.compat import UTC
from bot.config import RUNTIME
from bot.core import engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeExecution, TradeStatus


def _closes(n_wins, n_losses):
    out = []
    for i in range(n_wins + n_losses):
        pnl = 5.0 if i < n_wins else -2.0
        out.append(TradeExecution(
            trade_id=f"C{i}", asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
            stop_loss=98.0, take_profit=106.0, exit_price=105.0 if pnl > 0 else 98.0,
            quantity=1.0, status=TradeStatus.EXECUTED, pnl=pnl,
            closed_at=datetime(2026, 9, 1, tzinfo=UTC)))
    return out


@pytest.fixture
def bar():
    prev = RUNTIME.auto_confirm_threshold
    RUNTIME.auto_confirm_threshold = 0.85
    try:
        yield RUNTIME
    finally:
        RUNTIME.auto_confirm_threshold = prev


@pytest.fixture
def adaptive(monkeypatch):
    """The flag on (its default), and the mode chosen per test."""
    prev = bot_config.CONFIG.adaptive.adaptive_threshold_enabled
    object.__setattr__(bot_config.CONFIG.adaptive, "adaptive_threshold_enabled", True)

    def _mode(live: bool):
        monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: live)
    try:
        yield _mode
    finally:
        object.__setattr__(bot_config.CONFIG.adaptive, "adaptive_threshold_enabled", prev)


def _engine(history):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.portfolio = PortfolioTracker(initial_balance=10_000.0)
    eng.portfolio._history.extend(history)
    return eng


def test_paper_still_walks_the_bar_on_the_paper_record(bar, adaptive):
    adaptive(False)
    eng = _engine(_closes(8, 2))
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.80)
    for _ in range(6):
        eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.60), "the floor"


def test_a_live_bar_is_left_where_it_was_set(bar, adaptive, monkeypatch):
    """The case that was driven: the same paper record, in live mode."""
    adaptive(True)
    eng = _engine(_closes(8, 2))
    notes: list = []
    monkeypatch.setattr(engine_mod.system_log, "info",
                        lambda msg, *a, **k: notes.append(msg % a if a else msg))
    for _ in range(5):
        eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)
    said = [n for n in notes if "does not move a live bar" in n]
    assert len(said) == 1, ("said once per process, not per tick", notes)
    assert "0.85" in said[0]


def test_a_losing_paper_record_does_not_raise_a_live_bar_either(bar, adaptive):
    """Both directions are the decision, not only the loosening one."""
    adaptive(True)
    eng = _engine(_closes(2, 8))
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)
    adaptive(False)
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.90), "paper: raised, as before"


def test_a_fresh_record_moves_nothing_in_either_mode(bar, adaptive):
    for live in (True, False):
        adaptive(live)
        _engine([])._adapt_auto_confirm_threshold()
        assert bar.auto_confirm_threshold == pytest.approx(0.85)


def test_the_flag_off_moves_nothing(bar, adaptive):
    adaptive(False)
    object.__setattr__(bot_config.CONFIG.adaptive, "adaptive_threshold_enabled", False)
    _engine(_closes(8, 2))._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)


def test_the_tick_calls_the_seam_and_no_longer_inlines_the_rule():
    tree = ast.parse(inspect.getsource(RuneClawEngine))
    tick = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_tick")
    names = [ast.unparse(n.func) for n in ast.walk(tick) if isinstance(n, ast.Call)]
    assert "self._adapt_auto_confirm_threshold" in names
    assert "next_auto_confirm_threshold" not in names
    seam = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_adapt_auto_confirm_threshold")
    assert "next_auto_confirm_threshold" in [ast.unparse(n.func) for n in ast.walk(seam)
                                             if isinstance(n, ast.Call)]

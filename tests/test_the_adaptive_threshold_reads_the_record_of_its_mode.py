"""The adaptive auto-confirm bar reads the record of its mode, and in live mode only rises.

The block in `_tick` moved the auto-confirm threshold on the recent win rate
of `self.portfolio._history`: the PAPER book, in both modes. In LIVE mode
nothing writes that book, so a fresh live deploy never moved the bar, and
what the book HOLDS is whatever paper trading left there before the account
went live. Driven with ten paper closes at 80% on the record and a live bar
of 0.85: five ticks walked it to the 0.60 floor, one step each, on a record
no live trade was in. That is the RC-2026-021 shape one book over: the bar
that decides which real-money orders execute without a human, moved by a
feature reading somebody else's record.

The operator's decision (2026-09-25): TIGHTEN ONLY. In live mode the bar
reads the operator engine's realized window of priced live closes and may
only rise: a losing streak raises it one step per tick toward the cap, a
winning one leaves it where it is, and a disabled bar (1.0) stays disabled.
Paper is unchanged. The block is a seam now (`_adapt_auto_confirm_threshold`),
because a block inline in a 434-line tick is a block nothing can drive, and
the rule is one function with a `tighten_only` mode rather than a copy.
"""
from __future__ import annotations

import ast
import inspect
import os
from datetime import datetime

import pytest

import bot.config as bot_config
from bot.compat import UTC
from bot.config import RUNTIME
from bot.core import engine as engine_mod
from bot.core.adaptive_threshold import next_auto_confirm_threshold
from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
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


def _pnls(n_wins, n_losses):
    return [5.0] * n_wins + [-2.0] * n_losses


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


@pytest.fixture
def audits(monkeypatch):
    rows: list = []
    monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: rows.append((msg, kw)))
    return rows


def _engine(tmp_path, paper=(), live=()):
    """A stand-in engine with a real paper book and a real risk engine, each
    holding the closes a test plants there."""
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.portfolio = PortfolioTracker(initial_balance=10_000.0)
    eng.portfolio._history.extend(paper)
    eng.risk = RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "r.json"))
    if live:
        eng.risk.seed_realized_window(list(live))
    return eng


# ── paper is unchanged ─────────────────────────────────────────────────────


def test_paper_still_walks_the_bar_on_the_paper_record(tmp_path, bar, adaptive):
    adaptive(False)
    eng = _engine(tmp_path, paper=_closes(8, 2))
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.80)
    for _ in range(6):
        eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.60), "the floor"


def test_paper_raises_the_bar_on_a_losing_paper_record(tmp_path, bar, adaptive, audits):
    adaptive(False)
    _engine(tmp_path, paper=_closes(2, 8))._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.90)
    assert "over last 10 paper closes" in audits[-1][0]


# ── live: the live record, upward only ──────────────────────────────────────


def test_a_losing_live_record_raises_a_live_bar_one_step_per_tick(tmp_path, bar, adaptive, audits):
    adaptive(True)
    eng = _engine(tmp_path, live=_pnls(2, 8))
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.90)
    assert audits[-1][0].endswith("(WR=20% over last 10 live closes)"), audits[-1][0]
    assert audits[-1][1] == {"action": "adaptive_threshold", "result": "ADJUSTED"}
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.90), "the cap"


def test_a_winning_live_record_never_lowers_a_live_bar(tmp_path, bar, adaptive, audits):
    """The decision: tighten only."""
    adaptive(True)
    eng = _engine(tmp_path, live=_pnls(8, 2))
    for _ in range(5):
        eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)
    assert audits == []


def test_the_paper_record_does_not_move_a_live_bar_in_either_direction(tmp_path, bar, adaptive):
    """The case that was driven: ten paper closes at 80%, a live bar. And the
    losing paper record must not raise it either, because the live rule
    reads the live record."""
    adaptive(True)
    eng = _engine(tmp_path, paper=_closes(8, 2))
    for _ in range(5):
        eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)
    _engine(tmp_path, paper=_closes(2, 8))._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)


def test_the_live_record_reads_only_its_newest_lookback(tmp_path, bar, adaptive):
    """Twenty losses under ten wins: the WHOLE record is losing (33%) and would
    raise the bar, the newest ten are wins, so nothing moves. The first draft
    put ten losses under twelve wins, and a whole record at 55% sits between
    the two bars, where a reader of the whole record moves nothing either: a
    fixture the mutation cannot fail is not a measurement of the lookback."""
    adaptive(True)
    eng = _engine(tmp_path, live=_pnls(0, 20) + _pnls(10, 0))
    eng._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)
    assert eng.risk.recent_live_closes(10) == [5.0] * 10
    assert eng.risk.recent_live_closes(0) == []


def test_fewer_than_five_live_closes_move_nothing(tmp_path, bar, adaptive):
    adaptive(True)
    _engine(tmp_path, live=_pnls(0, 4))._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)


def test_a_disabled_bar_stays_disabled_on_a_live_losing_streak(tmp_path, bar, adaptive):
    adaptive(True)
    bar.auto_confirm_threshold = 1.0
    _engine(tmp_path, live=_pnls(0, 10))._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(1.0)


def test_a_fresh_record_moves_nothing_in_either_mode(tmp_path, bar, adaptive):
    for live in (True, False):
        adaptive(live)
        _engine(tmp_path)._adapt_auto_confirm_threshold()
        assert bar.auto_confirm_threshold == pytest.approx(0.85)


def test_the_flag_off_moves_nothing(tmp_path, bar, adaptive):
    adaptive(True)
    object.__setattr__(bot_config.CONFIG.adaptive, "adaptive_threshold_enabled", False)
    _engine(tmp_path, live=_pnls(0, 10))._adapt_auto_confirm_threshold()
    assert bar.auto_confirm_threshold == pytest.approx(0.85)


# ── the rule is one function ───────────────────────────────────────────────


def test_tighten_only_is_a_mode_of_the_one_rule():
    kw = dict(high_wr=0.70, low_wr=0.40, floor=0.60, cap=0.90)
    assert next_auto_confirm_threshold(0.85, 0.8, **kw) == pytest.approx(0.80)
    assert next_auto_confirm_threshold(0.85, 0.8, tighten_only=True, **kw) is None
    assert next_auto_confirm_threshold(0.85, 0.2, tighten_only=True, **kw) == pytest.approx(0.90)
    assert next_auto_confirm_threshold(1.0, 0.2, tighten_only=True, **kw) is None


def test_the_tick_calls_the_seam_and_no_longer_inlines_the_rule():
    tree = ast.parse(inspect.getsource(RuneClawEngine))
    tick = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_tick")
    names = [ast.unparse(n.func) for n in ast.walk(tick) if isinstance(n, ast.Call)]
    assert "self._adapt_auto_confirm_threshold" in names
    assert "next_auto_confirm_threshold" not in names
    seam = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_adapt_auto_confirm_threshold")
    calls = [n for n in ast.walk(seam) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "next_auto_confirm_threshold"]
    assert len(calls) == 1
    assert {k.arg: ast.unparse(k.value) for k in calls[0].keywords}["tighten_only"] == "live"

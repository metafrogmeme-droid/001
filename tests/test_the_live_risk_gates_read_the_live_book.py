"""In live mode the correlation and exposure gates read the LIVE book.

A `RiskEngine` is built over a paper `PortfolioTracker`, and no live fill
writes one: `LiveExecutor` keeps its own positions. So on the operator engine
the tracker is empty in live mode, and every gate that read it evaluated the
empty book. Driven with three live positions open:

    CORRELATION: no concentrated exposure
    PORTFOLIO_EXPOSURE: 7.5% OK          (the new trade alone)
    CONCENTRATION_PCA: fewer than 2 open positions - not applicable

`MAX_CORRELATION_PER_GROUP`, `MAX_UNMAPPED_CORRELATED`, the two exposure caps
and correlation sizing bound every backtest the benchmark measured, and no
live entry. On a per-user engine the tracker is the person's PRACTICE book,
so a practice position counted toward a live cap there.

The engine now hands the executor's rows in (`live_book=`), the count caps,
the exposure caps and correlation sizing read them, and a live evaluation
with no rows is a book nobody read, which is refused by name rather than
passed as flat. `LIVE_BOOK_RISK_GATES_ENABLED` (default on) is the switch: off,
the same gates are measured on the live book and reported without refusing.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
from types import SimpleNamespace

import pytest

import bot.config as bot_config
from bot.core import live_executor
from bot.core.engine import RuneClawEngine
from bot.risk.held_book import HeldRow, direction_word, margin_read
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only


@pytest.fixture
def enforce():
    """Set LIVE_BOOK_RISK_GATES_ENABLED on the frozen config, restored after."""
    prev = bot_config.CONFIG.risk.live_book_risk_gates_enabled

    def _set(on):
        object.__setattr__(bot_config.CONFIG.risk, "live_book_risk_gates_enabled", on)

    try:
        _set(True)
        yield _set
    finally:
        _set(prev)


def _idea(asset="ARB/USDT:USDT", direction=Direction.LONG, price=1.0):
    return TradeIdea(asset=asset, direction=direction, entry_price=price,
                     stop_loss=price * 0.98, take_profit=price * 1.06,
                     confidence=0.8, reasoning="x")


def _engine(tmp_path, portfolio=None):
    return RiskEngine(portfolio or PortfolioTracker(),
                      state_file=os.path.join(str(tmp_path), "risk.json"))


def _live(tmp_path, book, idea=None, portfolio=None):
    return _engine(tmp_path, portfolio).evaluate(
        idea or _idea(), atr=0.01, live_equity=1000.0, max_position_usd=100.0,
        live_open_count=len(book or ()), live_mode=True, live_book=book)


def _line(check, gate):
    lines = [x for x in check.checks_passed + check.checks_failed if x.startswith(gate)]
    assert len(lines) == 1, (gate, lines)
    return lines[0]


def _row(symbol, margin=60.0, direction="LONG"):
    return HeldRow(symbol, direction, margin, None if margin is None else margin * 5)


THREE_ALTS = tuple(_row(s) for s in ("OP/USDT:USDT", "SUI/USDT:USDT", "TRX/USDT:USDT"))


# ── the count caps ─────────────────────────────────────────────────────────


def test_three_held_perps_refuse_a_fourth_on_the_pooled_cap(tmp_path, enforce):
    check = _live(tmp_path, THREE_ALTS)
    assert check.verdict.value == "REJECTED"
    assert _line(check, "CORRELATION") in check.checks_failed
    assert "already 3 positions" in _line(check, "CORRELATION")


def test_two_held_are_within_the_caps_and_say_whose_book(tmp_path, enforce):
    check = _live(tmp_path, THREE_ALTS[:2])
    assert check.verdict.value == "APPROVED"
    assert _line(check, "CORRELATION") == (
        "CORRELATION: within the group caps on the live book (2 held)")


def test_a_practice_book_does_not_count_toward_a_live_cap(tmp_path, enforce):
    """On a per-user engine the tracker is the person's PRACTICE book. Three
    practice positions used to refuse a live trade on an empty account."""
    practice = PortfolioTracker()
    for asset in ("OP/USDT:USDT", "SUI/USDT:USDT", "TRX/USDT:USDT"):
        practice.open_position(_idea(asset), 50.0)
    assert len(practice.open_positions) == 3, "the premise: a full practice book"
    check = _live(tmp_path, (), portfolio=practice)
    assert check.verdict.value == "APPROVED", check.checks_failed
    assert "(0 held)" in _line(check, "CORRELATION")


def test_the_paper_book_is_still_what_paper_reads(tmp_path, enforce):
    """Paper and backtest evaluations are unchanged: the tracker decides."""
    paper = PortfolioTracker()
    for asset in ("OP/USDT:USDT", "SUI/USDT:USDT", "TRX/USDT:USDT"):
        paper.open_position(_idea(asset), 50.0)
    refused = _engine(tmp_path, paper).evaluate(_idea(), atr=0.01)
    assert "already 3 positions" in _line(refused, "CORRELATION")
    clean = _engine(tmp_path).evaluate(_idea(), atr=0.01)
    assert _line(clean, "CORRELATION") == "CORRELATION: no concentrated exposure"


# ── a book nobody read ─────────────────────────────────────────────────────


@pytest.mark.parametrize("gate", ["CORRELATION", "PORTFOLIO_EXPOSURE", "SYMBOL_EXPOSURE"])
def test_a_live_book_nobody_handed_in_is_refused_by_name(tmp_path, enforce, gate):
    check = _live(tmp_path, None)
    assert check.verdict.value == "REJECTED"
    line = _line(check, gate)
    assert line in check.checks_failed and "the live book was not read" in line


def test_an_unread_book_sizes_nothing_down(tmp_path, enforce):
    """Not on the practice book either: an AVAX practice long would size a
    NEAR long down x0.80 if the unread live book fell back to the tracker."""
    practice = PortfolioTracker()
    practice.open_position(_idea("AVAX/USDT", price=20.0), 50.0)
    enforce(False)
    check = _live(tmp_path, None, idea=_idea("NEAR/USDT", price=5.0),
                  portfolio=practice)
    assert not any("correlation x" in step for step in check.size_path)
    assert not any(line.startswith("CORRELATION_SIZING") for line in check.checks_passed)


# ── the exposure caps ──────────────────────────────────────────────────────


def test_portfolio_exposure_is_the_committed_margin_of_the_live_book(tmp_path, enforce):
    check = _live(tmp_path, THREE_ALTS[:2])
    # 2 x $60 held + the new $75 on $1,000 of equity
    assert _line(check, "PORTFOLIO_EXPOSURE") == (
        "PORTFOLIO_EXPOSURE: 19.5% OK (committed margin on the live book, 2 held)")


def test_over_the_portfolio_cap_refuses(tmp_path, enforce):
    heavy = tuple(_row(s, 400.0) for s in ("BTC/USDT:USDT", "ETH/USDT:USDT"))
    check = _live(tmp_path, heavy)
    line = _line(check, "PORTFOLIO_EXPOSURE")
    assert line in check.checks_failed and "87.5% > 80.0%" in line


def test_an_unread_margin_makes_the_sum_a_floor_and_refuses_by_name(tmp_path, enforce):
    book = THREE_ALTS[:1] + (_row("PENDLE/USDT:USDT", None),)
    check = _live(tmp_path, book)
    line = _line(check, "PORTFOLIO_EXPOSURE")
    assert line in check.checks_failed
    assert "margin unread on 1 of 2" in line and "cannot be checked" in line


def test_a_floor_over_the_cap_says_it_is_a_floor(tmp_path, enforce):
    book = (_row("BTC/USDT:USDT", 800.0), _row("PENDLE/USDT:USDT", None))
    line = _line(_live(tmp_path, book), "PORTFOLIO_EXPOSURE")
    assert "> 80.0%" in line and "a floor: margin read on 1 of 2 held" in line


def test_symbol_exposure_matches_the_venues_spelling_to_the_scanners(tmp_path, enforce):
    """A pyramid add: the held row is spelled as the venue spells it, the idea
    as the scanner does, and they are one symbol."""
    book = (HeldRow("ARB/USDT:USDT", "LONG", 180.0, 900.0),)
    check = _live(tmp_path, book, idea=_idea("ARB/USDT"))
    line = _line(check, "SYMBOL_EXPOSURE")
    assert line in check.checks_failed and "25.5% > 20.0%" in line
    other = _live(tmp_path, (HeldRow("OP/USDT:USDT", "LONG", 180.0, 900.0),))
    assert _line(other, "SYMBOL_EXPOSURE").endswith(
        "7.5% OK (committed margin on the live book, 0 held)")


def test_same_symbol_rows_normalise_both_sides():
    rows = (HeldRow("ARB/USDT:USDT", "LONG", 1.0, 5.0), HeldRow("OP/USDT", "LONG", 1.0, 5.0))
    assert RiskEngine._same_symbol_rows(rows, "ARB/USDT") == [rows[0]]
    assert RiskEngine._same_symbol_rows(None, "ARB/USDT") is None


# ── correlation sizing ─────────────────────────────────────────────────────


def test_correlation_sizing_reads_the_live_book(tmp_path, enforce):
    idea = _idea("NEAR/USDT", price=5.0)
    alone = _live(tmp_path, (), idea=idea)
    stacked = _live(tmp_path, (_row("AVAX/USDT"),), idea=idea)
    assert stacked.position_size_usd == pytest.approx(alone.position_size_usd * 0.8)
    assert any(step.startswith("correlation x0.80") for step in stacked.size_path)


def test_an_opposite_side_does_not_stack(tmp_path, enforce):
    idea = _idea("NEAR/USDT", price=5.0)
    alone = _live(tmp_path, (), idea=idea)
    hedged = _live(tmp_path, (_row("AVAX/USDT", direction="SHORT"),), idea=idea)
    assert hedged.position_size_usd == alone.position_size_usd


# ── not enforced ───────────────────────────────────────────────────────────


def test_off_measures_and_reports_without_refusing(tmp_path, enforce):
    enforce(False)
    check = _live(tmp_path, THREE_ALTS)
    assert check.verdict.value == "APPROVED"
    line = _line(check, "CORRELATION")
    assert line in check.checks_passed
    assert "already 3 positions" in line and "not enforced" in line


def test_off_does_not_size_down_and_says_what_it_would_have(tmp_path, enforce):
    idea = _idea("NEAR/USDT", price=5.0)
    enforce(False)
    alone = _live(tmp_path, (), idea=idea)
    stacked = _live(tmp_path, (_row("AVAX/USDT"),), idea=idea)
    assert stacked.position_size_usd == alone.position_size_usd
    assert _line(stacked, "CORRELATION_SIZING").startswith(
        "CORRELATION_SIZING: would size x0.80 (live book; not enforced")


def test_off_still_reports_a_book_nobody_read(tmp_path, enforce):
    enforce(False)
    check = _live(tmp_path, None)
    line = _line(check, "CORRELATION")
    assert line in check.checks_passed and "not read" in line and "not enforced" in line


# ── concentration is not claimed ───────────────────────────────────────────


def test_concentration_is_not_claimed_over_a_book_it_did_not_read(tmp_path, enforce):
    check = _live(tmp_path, THREE_ALTS[:2])
    assert _line(check, "CONCENTRATION_PCA") == (
        "CONCENTRATION_PCA: not evaluated in live mode "
        "(the benchmark never measured it on a held book)")


# ── the rows ───────────────────────────────────────────────────────────────


def test_held_rows_read_the_margin_and_never_invent_one():
    adopted = SimpleNamespace(symbol="PENDLE/USDT:USDT", direction="LONG",
                              cost_usd=0.0, entry_price=0.0, quantity=12.0)
    bot = SimpleNamespace(symbol="BTC/USDT:USDT", direction="SHORT",
                          cost_usd=100.0, entry_price=60000.0, quantity=0.01)
    rows = live_executor.held_rows([adopted, bot])
    assert rows[0] == HeldRow("PENDLE/USDT:USDT", "LONG", None, None)
    assert rows[1] == HeldRow("BTC/USDT:USDT", "SHORT", 100.0, 600.0)


def test_a_row_speaks_the_risk_engines_side_vocabulary():
    """The same-direction figures compare a row's side to
    `direction_word(idea.direction)`, so a row is built in that vocabulary
    whatever spelling the position carries."""
    def _pos(direction):
        return SimpleNamespace(symbol="X/USDT", direction=direction, cost_usd=1.0,
                               entry_price=1.0, quantity=1.0)
    rows = live_executor.held_rows([_pos("long"), _pos(Direction.SHORT), _pos("BUY")])
    assert [r.direction for r in rows] == ["LONG", "SHORT", ""]


@pytest.mark.parametrize("value, word", [
    ("LONG", "LONG"), ("short", "SHORT"), (Direction.LONG, "LONG"),
    ("", ""), (None, ""), ("BUY", "")])
def test_a_side_nobody_can_read_is_no_side(value, word):
    assert direction_word(value) == word


def test_margin_read_counts_what_it_could_not_read():
    read = margin_read((_row("A", 10.0), _row("B", None)))
    assert (read.total, read.scored, read.counted, read.complete) == (10.0, 1, 2, False)
    assert margin_read(()).complete


# ── the engine hands the book in ───────────────────────────────────────────


def test_both_engine_evaluations_hand_the_live_book_in():
    """The claim is that the call carries the argument. A drive of the
    analysis path needs a scanner, an analyzer and an exchange; the recheck
    context that feeds the confirm is driven below."""
    tree = ast.parse(code_only(inspect.getsource(RuneClawEngine)))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "evaluate"
             and any(k.arg == "live_mode" for k in n.keywords)]
    assert len(calls) == 2, len(calls)
    for call in calls:
        assert any(k.arg == "live_book" for k in call.keywords), ast.unparse(call)


def _recheck(monkeypatch, positions):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = SimpleNamespace(open_positions=positions)
    eng.live_balance_cached = lambda: {"total": 1000.0}
    eng._executor_for = lambda uid: eng.live_executor

    async def _count(engine):
        return len(positions)

    monkeypatch.setattr("bot.core.engine.get_exchange_position_count", _count)
    monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: True)
    return asyncio.run(eng._live_recheck_context(""))


def test_the_recheck_reads_the_book_it_counts(monkeypatch):
    pos = SimpleNamespace(symbol="OP/USDT:USDT", direction="LONG", status="open",
                          cost_usd=60.0, entry_price=1.0, quantity=300.0)
    rc = _recheck(monkeypatch, [pos])
    assert rc.open_count == 1
    assert rc.book == (HeldRow("OP/USDT:USDT", "LONG", 60.0, 300.0),)


def test_the_rolling_correlation_never_reads_a_practice_book_live(tmp_path, enforce):
    """The price-series check reads the paper tracker's positions. Live, on a
    per-user engine, that is the practice book, so it must not run there."""
    practice = PortfolioTracker()
    practice.open_position(_idea("OP/USDT"), 50.0)
    eng = _engine(tmp_path, practice)
    for i in range(30):
        px = 1.0 + 0.01 * i + (0.003 if i % 2 else 0.0)
        eng.update_price_history("OP/USDT", px, ts=1000.0 + i)
        eng.update_price_history("ARB/USDT", px * 2, ts=1000.0 + i)
    paper = eng._check_correlation(_idea("ARB/USDT"))
    assert paper and paper.startswith("CORRELATION_V2"), (
        "the control: on the paper book the correlated pair is refused")
    assert eng._check_correlation(_idea("ARB/USDT"), ()) is None


def test_the_gates_are_enforced_by_default():
    import re
    src = inspect.getsource(bot_config)
    assert re.search(r'_env_bool\("LIVE_BOOK_RISK_GATES_ENABLED",\s*True\)', src)

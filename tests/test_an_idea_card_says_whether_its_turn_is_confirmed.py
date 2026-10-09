"""An idea card says whether the turn is confirmed on its closed bars.

The bot computes, for every idea it analyses, whether the sub-degree turn is
confirmed (a confirmed ZigZag pullback pivot and a with-trade trigger bar). It
gated only autonomous entries on it, in TREND_DOWN, and every live order is a
tap. The reading is on the card now, as information, never as a gate or a
default.

Three values, not two: confirmed, not confirmed yet, and not read (too little
history, or the check failed). The two-valued check the gates ask read "not
read" as "not confirmed", which is right for a gate that only asks whether to
fire, and wrong for a sentence to a person. An idea the engine never analysed
has no reading and no line.

Driven: the real reading on the entry-timing suite's own series, the real
engine method that records it, the real analyze card, the real signal caption
and `/latest_signal` through the real handler.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.engine import RuneClawEngine
from bot.core.entry_timing import (
    TURN_CONFIRMED,
    TURN_NOT_CONFIRMED,
    TURN_UNREAD,
    subdegree_turn_confirmed,
    turn_reading,
)
from bot.formatters.timing_line import entry_timing_line
from bot.skills import skill_registry as sr
from bot.skills.alerts_monitor import AlertsMonitor, signal_card_caption
from bot.utils.models import Direction, TradeIdea
from tests.test_entry_timing import _mirror, _pullback_then_turn

STOCK = "TSLA/USDT:USDT"


def _idea(direction=Direction.LONG, asset=STOCK):
    long = direction == Direction.LONG
    return TradeIdea(asset=asset, direction=direction, entry_price=100.0,
                     stop_loss=97.0 if long else 103.0,
                     take_profit=106.0 if long else 94.0, confidence=0.95,
                     reasoning="x", source="unknown")


def _ohlcv(series):
    o, h, lo, c = series
    return [[i * 3_600_000, o[i], h[i], lo[i], c[i], 1.0] for i in range(len(c))]


# ── the reading: three values ───────────────────────────────────────────────

def test_a_confirmed_turn_reads_confirmed():
    o, h, lo, c = _pullback_then_turn(trigger=True)
    assert turn_reading("LONG", h, lo, c, opens=o) == (
        TURN_CONFIRMED, "pullback low confirmed + bullish trigger bar")
    mo, mh, ml, mc = _mirror(_pullback_then_turn(trigger=True))
    assert turn_reading("SHORT", mh, ml, mc, opens=mo)[0] == TURN_CONFIRMED


def test_a_turn_without_its_trigger_bar_reads_not_confirmed():
    o, h, lo, c = _pullback_then_turn(trigger=False)
    assert turn_reading("LONG", h, lo, c, opens=o) == (
        TURN_NOT_CONFIRMED, "structure turned, awaiting bullish trigger bar")
    # A turn the other way is read, and is not this direction's.
    o, h, lo, c = _pullback_then_turn(trigger=True)
    assert turn_reading("SHORT", h, lo, c, opens=o)[0] == TURN_NOT_CONFIRMED


def test_too_little_history_or_a_failed_check_is_not_read():
    assert turn_reading("LONG", [1, 2], [1, 2], [1, 2]) == (
        TURN_UNREAD, "insufficient sub-degree history")
    junk = ["x"] * 30
    assert turn_reading("LONG", junk, junk, junk) == (
        TURN_UNREAD, "confirmation check error")


def test_the_gates_two_valued_check_is_the_same_reading():
    o, h, lo, c = _pullback_then_turn(trigger=True)
    no, nh, nl, nc = _pullback_then_turn(trigger=False)
    for args in (("LONG", h, lo, c, o), ("LONG", nh, nl, nc, no),
                 ("LONG", [1, 2], [1, 2], [1, 2], None), ("SHORT", h, lo, c, o)):
        state, reason = turn_reading(*args[:4], opens=args[4])
        assert subdegree_turn_confirmed(*args[:4], opens=args[4]) == (
            state == TURN_CONFIRMED, reason)


# ── the engine records it for every analysed idea ───────────────────────────

def test_the_engine_records_the_reading_with_its_timeframe():
    eng = RuneClawEngine()
    idea = _idea()
    eng._record_turn(idea, _ohlcv(_pullback_then_turn(trigger=False)), "1h")
    assert eng._pending_turn[idea.id] == (
        TURN_NOT_CONFIRMED, "structure turned, awaiting bullish trigger bar", "1h")
    short = _idea(direction=Direction.SHORT)
    eng._record_turn(short, _ohlcv(_pullback_then_turn())[:5], "4h")
    assert eng._pending_turn[short.id] == (
        TURN_UNREAD, "insufficient sub-degree history", "4h")
    broken = _idea()
    eng._record_turn(broken, [["t", "o", "h", "l", "c", "v"]] * 30, "1h")
    assert eng._pending_turn[broken.id] == (TURN_UNREAD, "confirmation check error", "1h")


def test_every_analysed_idea_is_recorded_before_it_is_refined():
    # `_analyze_signal` is the pipeline every card's idea comes through, built
    # around live fetches; the shape a drive does not reach is that it records
    # the turn on the analysed candles and timeframe, once, before refining.
    src = textwrap.dedent(inspect.getsource(RuneClawEngine._analyze_signal))
    fn = ast.parse(src).body[0]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)]
    record = [n for n in calls if n.func.attr == "_record_turn"]
    assert len(record) == 1
    assert [a.id for a in record[0].args] == ["idea", "ohlcv", "timeframe"]
    refine = [n for n in calls if n.func.attr == "_refine_entry_mtf"]
    assert refine and record[0].lineno < min(n.lineno for n in refine)


# ── the line ────────────────────────────────────────────────────────────────

def _engine_with(reading=None, idea=None):
    turns = {idea.id: reading} if idea is not None and reading is not None else {}
    return NS(_pending_turn=turns,
              live_view=lambda uid: {"scope": "none", "executor": None},
              _is_operator_user=lambda uid: False)


def test_each_state_has_its_own_sentence():
    idea = _idea()
    assert entry_timing_line(_engine_with(
        (TURN_CONFIRMED, "pullback low confirmed + bullish trigger bar", "1h"), idea), idea) == (
        "⏱ Entry timing (1h, closed bars): turn confirmed "
        "(pullback low confirmed + bullish trigger bar)")
    assert entry_timing_line(_engine_with(
        (TURN_NOT_CONFIRMED, "structure turned, awaiting bullish trigger bar", "1h"), idea), idea) == (
        "⏱ Entry timing (1h, closed bars): turn not confirmed yet "
        "(structure turned, awaiting bullish trigger bar)")
    assert entry_timing_line(_engine_with(
        (TURN_UNREAD, "insufficient sub-degree history", "4h"), idea), idea) == (
        "⏱ Entry timing (4h, closed bars): not read (insufficient sub-degree history)")


@pytest.mark.parametrize("engine", [
    NS(_pending_turn={}),                      # never analysed (a hand-typed ticket)
    NS(),                                      # an engine with no readings at all
    NS(_pending_turn=None),
])
def test_an_idea_with_no_reading_gets_no_line(engine):
    assert entry_timing_line(engine, _idea()) is None


@pytest.mark.parametrize("reading", [("confirmed", "x"), ("maybe", "x", "1h"), "confirmed"])
def test_a_reading_it_cannot_read_is_no_line(reading):
    idea = _idea()
    assert entry_timing_line(NS(_pending_turn={idea.id: reading}), idea) is None


# ── the cards ───────────────────────────────────────────────────────────────

NOT_YET = (TURN_NOT_CONFIRMED, "structure turned, awaiting bullish trigger bar", "1h")
LINE = ("⏱ Entry timing (1h, closed bars): turn not confirmed yet "
        "(structure turned, awaiting bullish trigger bar)")


def test_the_pushed_caption_carries_the_timing_for_every_recipient():
    idea = _idea()
    host = NS(engine=_engine_with(NOT_YET, idea))
    for chat in ("1001", "2002"):
        assert AlertsMonitor._signal_caption_for(host, chat, idea) == signal_card_caption(idea, LINE)
    bare = NS(engine=_engine_with())
    assert AlertsMonitor._signal_caption_for(bare, "1001", idea) == signal_card_caption(idea)


def _analyze_card(engine_extra, idea):
    class _Ex:
        async def fetch_ticker(self, sym):
            return {"last": idea.entry_price, "percentage": 1.2, "quoteVolume": 5e6}

    class _Scanner:
        async def _get_exchange(self):
            return _Ex()

        async def _get_futures_exchange(self):
            return _Ex()

    async def _an(sig, **kw):
        return idea

    eng = NS(scanner=_Scanner(), _analyze_signal=_an, _pending_ideas={},
             _pending_atr={}, analyzer=NS(_last_rejection_diag=None),
             journal=None, _engine_pending_ids=lambda: set(), **engine_extra)
    return asyncio.run(sr.AnalyzeAssetSkill().execute(eng, symbol="TSLA/USDT", user_id="2002"))


def test_the_analyze_card_says_whether_the_turn_is_confirmed():
    idea = _idea()
    card = _analyze_card({"_pending_turn": {idea.id: NOT_YET}}, idea)
    assert LINE in card
    assert card.index("Entry timing") < card.index("\U0001f4ce")
    assert "Entry timing" not in _analyze_card({"_pending_turn": {}}, idea)


def test_latest_signal_says_it_too(tmp_path):
    from bot.core.live_executor import LiveExecutor
    from bot.skills.telegram_handler import TelegramHandler

    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin("6307156912")
    handler._signal_card_fn = None                    # the text path
    engine.live_executor = LiveExecutor(state_dir=str(tmp_path))
    idea = _idea()
    engine._pending_ideas.clear()
    engine._pending_ideas[idea.id] = idea
    engine._pending_turn[idea.id] = NOT_YET

    update = MagicMock()
    update.effective_user.id = 6307156912
    update.effective_user.first_name = "T"
    update.effective_chat.id = 6307156912
    update.message.reply_text = AsyncMock()
    update.message.text = "/latest_signal"
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    asyncio.run(handler._cmd_latest_signal(update, ctx))
    out = "\n".join(str(c.args[0]) for c in update.message.reply_text.call_args_list if c.args)
    assert "Entry:" in out and LINE in out

"""An idea card carries the viewer's own live record in the idea's asset class.

Live `/parity`, 9 October: Stock 52 trades, 23% won, PF 0.19; Crypto 159
trades, 45% won, PF 0.73. Every live order is a tap, so the evidence belongs on
the card with the Confirm button. `class_record_line` is the one reading:
`/parity`'s own class row (`parity.class_rows`), on the book `live_view` says
the caller may see; the operator's record only to the operator; nothing under
`MIN_TRADES`; a partly read record said so.

Driven: a real `LiveExecutor` holding planted closes, the real `/parity`
command over the file it writes, the real analyze card, the real signal
caption and `/latest_signal`'s text path through the real handler.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.formatters.class_record import MIN_TRADES, class_record, class_record_line
from bot.skills import skill_registry as sr
from bot.skills.alerts_monitor import AlertsMonitor, signal_card_caption
from bot.utils.models import Direction, TradeIdea

OP, OTHER = "1001", "2002"
STOCK, CRYPTO = "TSLA/USDT:USDT", "BTC/USDT:USDT"
STOCK_ICON = "\U0001f4c8"


def _close(symbol, pnl, reason="TP HIT", n=[0]):
    n[0] += 1
    return LivePosition(
        trade_id=f"T{n[0]}", symbol=symbol, direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=20.0, stop_loss=97.0, take_profit=106.0, leverage=5,
        status="closed", opened_at=datetime.now(UTC) - timedelta(hours=3),
        close_reason=reason, pnl_usd=pnl)


def _book(tmp_path, closes, *, read_failed=False):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = list(closes)
    ex._closed_trades_read_failed = read_failed
    return ex


def _stock_book(tmp_path, **kw):
    """12 stock strategy exits (3 won), plus rows /parity excludes, plus crypto."""
    closes = ([_close(STOCK, 2.0) for _ in range(3)]
              + [_close(STOCK, -1.0, "SL HIT") for _ in range(9)]
              + [_close(STOCK, -0.5, "leverage_overshoot")]      # an execution abort
              + [_close(STOCK, None, "TP HIT")]                   # unpriced
              + [_close(CRYPTO, 5.0) for _ in range(4)])
    return _book(tmp_path, closes, **kw)


def _engine(executor, scope="operator", operators=(OP,)):
    return NS(live_view=lambda uid: {"scope": scope, "executor": executor},
              _is_operator_user=lambda uid: str(uid) in operators)


# ── the reading ─────────────────────────────────────────────────────────────

def test_it_counts_parity_s_population_in_the_idea_s_class(tmp_path):
    rec = class_record(_stock_book(tmp_path), "Stock")
    assert (rec["trades"], rec["wins"]) == (12, 3)
    assert rec["pf"] == pytest.approx(6.0 / 9.0)
    assert rec["partial"] is False


def test_it_is_the_row_parity_prints(tmp_path):
    # The real /parity command over the file the executor writes: its Stock
    # row and the card's line are one reading, so they say the same thing.
    from bot.skills.engine_ops_commands import EngineOpsCommands

    ex = _stock_book(tmp_path)
    assert ex._save_closed_trades()
    sent: list = []

    class Host:
        engine = NS(live_executor=ex)

        def _is_admin(self, update):
            return True

        def _lang(self, update):
            return "en"

        async def _send(self, update, text, **kw):
            sent.append(text)

    asyncio.run(EngineOpsCommands._cmd_parity(Host(), NS(), NS()))
    stock = [ln for ln in sent[0].splitlines() if re.match(r"\s+Stock\s", ln)]
    assert len(stock) == 1, sent[0]
    assert " 12 tr " in stock[0] and "win 25%" in stock[0] and "PF 0.67" in stock[0]
    rec = class_record(ex, "Stock")
    assert (rec["trades"], rec["wins"], round(rec["pf"], 2)) == (12, 3, 0.67)


def test_a_class_with_no_exits_is_no_record(tmp_path):
    assert class_record(_stock_book(tmp_path), "ETF") is None


# ── the line: whose book, how many, how read ────────────────────────────────

def test_the_operator_sees_the_operator_s_record(tmp_path):
    line = class_record_line(_engine(_stock_book(tmp_path)), OP, STOCK)
    assert line == f"{STOCK_ICON} Stock on your live book: 3 of 12 won · PF 0.67"


def test_nobody_else_is_shown_the_operator_s_record(tmp_path):
    # Per-user live off hands every caller the operator's executor.
    assert class_record_line(_engine(_stock_book(tmp_path)), OTHER, STOCK) is None


def test_a_linked_user_sees_their_own_book(tmp_path):
    line = class_record_line(_engine(_stock_book(tmp_path), scope="own"), OTHER, STOCK)
    assert line and "3 of 12 won" in line


def test_no_book_is_no_line(tmp_path):
    assert class_record_line(_engine(None, scope="none"), OTHER, STOCK) is None


@pytest.mark.parametrize("nobody", [None, "", "  "])
def test_no_caller_is_nobody_s_book(tmp_path, nobody):
    # An engine that would call anyone the operator: the missing id is refused
    # before it is asked.
    eng = NS(live_view=lambda uid: {"scope": "operator", "executor": _stock_book(tmp_path)},
             _is_operator_user=lambda uid: True)
    assert class_record_line(eng, nobody, STOCK) is None
    assert class_record_line(eng, OP, STOCK)


def test_too_few_trades_say_nothing(tmp_path):
    few = [_close(CRYPTO, 1.0) for _ in range(MIN_TRADES - 1)]
    assert class_record_line(_engine(_book(tmp_path, few)), OP, CRYPTO) is None
    enough = [_close(CRYPTO, 1.0) for _ in range(MIN_TRADES - 1)] + [_close(CRYPTO, -1.0, "SL HIT")]
    assert "of 10 won" in class_record_line(_engine(_book(tmp_path, enough)), OP, CRYPTO)


def test_no_losing_trade_is_no_ratio(tmp_path):
    wins = [_close(CRYPTO, 1.0) for _ in range(MIN_TRADES)]
    line = class_record_line(_engine(_book(tmp_path, wins)), OP, CRYPTO)
    assert line.endswith("10 of 10 won · PF —")


def test_a_record_read_in_part_says_so(tmp_path):
    line = class_record_line(_engine(_stock_book(tmp_path, read_failed=True)), OP, STOCK)
    assert line.endswith("(record read in part)")


def test_a_failing_read_is_no_line_not_a_failed_card():
    def boom(uid):
        raise RuntimeError("down")
    assert class_record_line(NS(live_view=boom, _is_operator_user=lambda u: True),
                             OP, STOCK) is None


# ── the cards ───────────────────────────────────────────────────────────────

def _idea(asset=STOCK):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=106.0, confidence=0.7,
                     reasoning="x", source="unknown")


def test_the_signal_caption_carries_the_line_it_is_handed():
    idea = _idea()
    assert signal_card_caption(idea) == signal_card_caption(idea, None)
    cap = signal_card_caption(idea, "📈 Stock on your live book: 3 of 12 won · PF 0.67 <x>")
    assert cap.endswith("\n📈 Stock on your live book: 3 of 12 won · PF 0.67 &lt;x&gt;")


def test_the_pushed_image_carries_each_recipient_s_own_record(tmp_path):
    # The proactive push sends one image to every watching chat; the caption
    # is built per recipient, so a watcher never receives the operator's record.
    host = NS(engine=_engine(_stock_book(tmp_path)))
    idea = _idea()
    mine = AlertsMonitor._signal_caption_for(host, OP, idea)
    assert mine == signal_card_caption(
        idea, f"{STOCK_ICON} Stock on your live book: 3 of 12 won · PF 0.67")
    assert AlertsMonitor._signal_caption_for(host, OTHER, idea) == signal_card_caption(idea)


def test_the_pushed_image_asks_for_the_recipient_s_caption():
    # The sender is a closure inside `start_monitor`, built around a live bot;
    # this is the one shape a drive cannot reach: it asks the seam above, with
    # the chat it is sending to.
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(AlertsMonitor.start_monitor))
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_signal_card_fn")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_signal_caption_for"]
    assert len(calls) == 1
    assert [a.id for a in calls[0].args if isinstance(a, ast.Name)] == ["chat_id", "idea"]
    photo = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "send_photo"]
    assert len(photo) == 1
    cap = next(k.value for k in photo[0].keywords if k.arg == "caption")
    assert isinstance(cap, ast.Name) and cap.id == "cap"


def _analyze_card(engine_extra, user_id):
    idea = _idea()

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
    kw = {"symbol": "TSLA/USDT"}
    if user_id is not None:
        kw["user_id"] = user_id
    return asyncio.run(sr.AnalyzeAssetSkill().execute(eng, **kw))


def test_the_analyze_card_shows_the_operator_their_class_record(tmp_path):
    eng = _engine(_stock_book(tmp_path))
    card = _analyze_card({"live_view": eng.live_view,
                          "_is_operator_user": eng._is_operator_user}, OP)
    assert "Stock on your live book: 3 of 12 won · PF 0.67" in card
    assert card.index("on your live book") < card.index("\U0001f4ce")


def test_the_analyze_card_shows_another_caller_nothing(tmp_path):
    eng = _engine(_stock_book(tmp_path))
    card = _analyze_card({"live_view": eng.live_view,
                          "_is_operator_user": eng._is_operator_user}, OTHER)
    assert "on your live book" not in card


def test_the_analyze_card_without_a_caller_is_unchanged(tmp_path):
    # The bare stand-in engines elsewhere carry no live_view: no line, no error.
    assert "on your live book" not in _analyze_card({}, None)
    # An engine that would call anyone the operator: no caller is still no line.
    book = _stock_book(tmp_path)
    anyone = {"live_view": lambda uid: {"scope": "operator", "executor": book},
              "_is_operator_user": lambda uid: True}
    assert "on your live book" not in _analyze_card(anyone, None)
    assert "on your live book" in _analyze_card(anyone, OTHER)


# ── /latest_signal's text card, through the real handler ───────────────────

ADMIN, MEMBER = 6307156912, 5550001111


@pytest.fixture
def operator_configured():
    """The operator's chat configured, as on a deployed bot: a card that read
    the record under the configured id instead of the caller's would show the
    operator's record to everyone."""
    import dataclasses

    from bot.config import CONFIG
    was = CONFIG.telegram
    object.__setattr__(CONFIG, "telegram", dataclasses.replace(was, chat_id=str(ADMIN)))
    try:
        yield
    finally:
        object.__setattr__(CONFIG, "telegram", was)


def _latest_signal(tmp_path, caller):
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler

    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(ADMIN))
    handler.users.register(MEMBER, "member")
    handler._signal_card_fn = None                    # the text path
    engine.live_executor = _stock_book(tmp_path)
    idea = _idea()
    idea.confidence = 0.95
    engine._pending_ideas.clear()
    engine._pending_ideas[idea.id] = idea

    update = MagicMock()
    update.effective_user.id = caller
    update.effective_user.first_name = "T"
    update.effective_chat.id = caller
    update.message.reply_text = AsyncMock()
    update.message.text = "/latest_signal"
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    asyncio.run(handler._cmd_latest_signal(update, ctx))
    return "\n".join(str(c.args[0]) for c in update.message.reply_text.call_args_list
                     if c.args)


def test_latest_signal_shows_the_operator_their_class_record(tmp_path, operator_configured):
    out = _latest_signal(tmp_path, ADMIN)
    assert "TSLA" in out and "Entry:" in out
    assert f"{STOCK_ICON} Stock on your live book: 3 of 12 won · PF 0.67" in out


def test_latest_signal_shows_a_member_the_card_and_not_the_operator_s_record(
        tmp_path, operator_configured):
    out = _latest_signal(tmp_path, MEMBER)
    assert "TSLA" in out and "Entry:" in out          # the card itself rendered
    assert "on your live book" not in out

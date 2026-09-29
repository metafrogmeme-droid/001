"""An agent open is announced when the position OPENS, off the row it opened.

The confirm path announced every order that placed. It emitted the public
feed's ``trade_open`` event ("Opened LONG BTC/USDT", which the website pushes
to every subscriber's phone), the Confirm button posted TRADE OPENED to the
public channels, and the website sync sent the row as a held position. A
resting limit order did all three the moment it was placed, off the IDEA's
levels, while the executor had re-priced the order and moved its stop and
target. And the fill that later opened the position was announced nowhere
public: the monitor only sent the watching chats a card.

`RuneClawEngine._announce_agent_open` is the one place now. It runs when the
operator's row is ``open``: at confirm for an order that filled there, and on
the monitor pass after a resting limit fills. It reads the ROW's levels
(`agent_feed.opened_levels`), announces a row once, and only the agent's own
rows: a person's book is private and an adopted row is nobody's open.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace as NS

import pytest

import bot.core.engine as engine_mod
from bot.core.agent_feed import FEED, open_event, opened_levels
from bot.core.confirm_result import left_resting, placed_nothing
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import ENTRY_ESTIMATED, RESTING_ANSWER, LivePosition
from bot.utils.models import Direction
from tests.test_a_scan_cards_levels_are_placed_as_shown import _engine_idea, _pending
from tests.test_a_seal_failure_does_not_unplace_a_trade import _confirm

# The idea the engine confirms, and the row the executor keeps after it has
# re-priced the limit and moved the stop and target with it.
IDEA_LEVELS = dict(entry_price=99.4, stop_loss=95.0, take_profit=106.0)
ROW_LEVELS = dict(entry_price=98.9, stop_loss=94.5, take_profit=105.5)


def _row(tid, *, status="open", origin="executed", direction="LONG",
         symbol="SOL/USDT", **over):
    levels = dict(ROW_LEVELS)
    levels.update(over)
    pos = LivePosition(trade_id=tid, symbol=symbol, direction=direction,
                       quantity=1.0, cost_usd=20.0, status=status, **levels)
    pos.origin = origin
    return pos


@pytest.fixture
def feed(monkeypatch):
    emitted: list = []
    monkeypatch.setattr(FEED, "emit", lambda etype, title, **kw: emitted.append(
        dict(kw, type=etype, title=title)))
    return emitted


@pytest.fixture
def synced(monkeypatch):
    sent: list = []
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda equity, positions, closed: sent.append(positions))
    return sent


def _posts(engine):
    posted: list = []

    async def _cb(levels):
        posted.append(levels)

    engine.set_public_open_callback(_cb)
    return posted


# ── the reading ──────────────────────────────────────────────────────────

class TestTheReading:

    def test_an_open_row_of_the_agents_own_is_its_levels(self):
        assert opened_levels(_row("T")) == {
            "symbol": "SOL/USDT", "direction": "LONG", "entry": 98.9,
            "stop": 94.5, "target": 105.5, "entry_estimated": False}

    @pytest.mark.parametrize("status", ["pending_fill", "closing", "closed", "error"])
    def test_a_row_that_is_not_open_is_not_an_open(self, status):
        assert opened_levels(_row("T", status=status)) is None

    @pytest.mark.parametrize("origin", ["adopted", "reclaimed", None, ""])
    def test_a_row_the_agent_did_not_place_is_not_its_open(self, origin):
        assert opened_levels(_row("T", origin=origin)) is None

    @pytest.mark.parametrize("direction,word", [
        ("LONG", "LONG"), ("short", "SHORT"), (Direction.LONG, "LONG")])
    def test_the_direction_is_read_in_every_spelling(self, direction, word):
        assert opened_levels(_row("T", direction=direction))["direction"] == word

    @pytest.mark.parametrize("direction", ["", "BUY", "FLAT", None])
    def test_a_direction_that_cannot_be_read_announces_nothing(self, direction):
        assert opened_levels(_row("T", direction=direction)) is None

    def test_no_symbol_is_no_open(self):
        assert opened_levels(_row("T", symbol="")) is None

    def test_a_level_the_row_does_not_state_is_none_not_zero(self):
        got = opened_levels(_row("T", stop_loss=0.0, take_profit=float("nan")))
        assert got["stop"] is None and got["target"] is None

    def test_an_estimated_entry_says_so(self):
        pos = _row("T")
        pos.entry_source = ENTRY_ESTIMATED
        assert opened_levels(pos)["entry_estimated"] is True
        pos.entry_source = "order"
        assert opened_levels(pos)["entry_estimated"] is False


class TestTheEvent:

    def test_the_event_states_the_rows_levels(self):
        ev = open_event(opened_levels(_row("T")))
        assert ev["title"] == "Opened LONG SOL/USDT"
        assert ev["body"] == "Entry $98.9000 · SL $94.5000 · TP $105.50"
        assert ev["severity"] == "success" and ev["symbol"] == "SOL/USDT"
        assert ev["data"] == {"direction": "LONG", "entry": 98.9, "sl": 94.5,
                              "tp": 105.5, "entry_estimated": False}

    def test_it_carries_no_size_and_no_confidence(self):
        """The size is account money on a public payload; the confidence is
        the thesis event's, stated once for the signal."""
        data = open_event(opened_levels(_row("T")))["data"]
        assert "confidence" not in data
        assert not {"quantity", "cost_usd", "size_usd", "margin"} & set(data)

    def test_an_unstated_level_prints_the_dash(self):
        ev = open_event(opened_levels(_row("T", take_profit=0.0)))
        assert ev["body"].endswith("TP —") and ev["data"]["tp"] is None

    def test_an_estimated_entry_is_marked_on_the_body(self):
        pos = _row("T")
        pos.entry_source = ENTRY_ESTIMATED
        body = open_event(opened_levels(pos))["body"]
        assert body.startswith("Entry ~$98.9000 (estimated: the venue stated no fill price)")
        assert open_event(opened_levels(pos))["data"]["entry_estimated"] is True


# ── the confirm path ─────────────────────────────────────────────────────

def _confirm_with(tmp_path, row_status, *, answer=None, feed_row=True):
    idea = _engine_idea(**IDEA_LEVELS)
    engine, handed = _pending(tmp_path, idea, 100.0)
    posted = _posts(engine)

    async def _exec(i, *a, **kw):
        handed.append(i)
        if feed_row:
            engine.live_executor._positions[i.id] = _row(i.id, status=row_status)
        return answer or ("LIMIT ORDER BUY SOL/USDT\n- Status: " + RESTING_ANSWER
                          if row_status == "pending_fill" else "✅ LIVE LONG SOL/USDT filled")

    engine.live_executor.execute = _exec
    return engine, idea, _confirm(engine, idea), posted


class TestTheConfirmAnnouncesTheRow:

    def test_a_resting_limit_is_not_announced(self, tmp_path, feed, synced):
        engine, idea, answer, posted = _confirm_with(tmp_path, "pending_fill")
        assert left_resting(answer) and not placed_nothing(answer)
        assert [e for e in feed if e["type"] == "trade_open"] == []
        assert posted == [] and synced == []

    def test_a_fill_at_confirm_is_announced_off_the_row(self, tmp_path, feed, synced):
        engine, idea, answer, posted = _confirm_with(tmp_path, "open")
        (ev,) = [e for e in feed if e["type"] == "trade_open"]
        assert ev["title"] == "Opened LONG SOL/USDT"
        assert ev["body"] == "Entry $98.9000 · SL $94.5000 · TP $105.50", (
            "the idea's own levels were 99.4 / 95 / 106; the order was re-priced")
        (lv,) = posted
        assert (lv["entry"], lv["stop"], lv["target"]) == (98.9, 94.5, 105.5)
        assert len(synced) == 1 and [p["asset"] for p in synced[0]] == ["SOL/USDT"]

    def test_a_refusal_announces_nothing(self, tmp_path, feed, synced):
        engine, idea, answer, posted = _confirm_with(
            tmp_path, "open", answer="❌ LIVE EXECUTION FAILED: venue refused", feed_row=False)
        assert placed_nothing(answer)
        assert feed == [] or all(e["type"] != "trade_open" for e in feed)
        assert posted == [] and synced == []

    def test_the_fill_is_announced_once_when_the_monitor_sees_it_too(self, tmp_path, feed, synced):
        """The confirm announces, then the monitor's pass reaches the same
        open row: one announcement."""
        engine, idea, answer, posted = _confirm_with(tmp_path, "open")
        assert asyncio.run(engine._announce_new_agent_opens(engine.live_executor)) == 0
        assert len(posted) == 1 and len([e for e in feed if e["type"] == "trade_open"]) == 1

    def test_the_seed_is_taken_before_the_order(self):
        """A seed taken after `execute` would hold the row the order just
        opened, and that row would never be announced. A scan, stated as
        one: the claim is an ORDER inside a 1,000-line coroutine."""
        import inspect
        import textwrap

        from tests.source_scan import code_only
        src = code_only(textwrap.dedent(inspect.getsource(RuneClawEngine._confirm_trade_inner)))
        seed = src.index("self._agent_open_ids(executor)")
        order = src.index("result = await executor.execute(")
        announce = src.index("await self._announce_agent_open(executor, trade_id)")
        assert seed < order < announce


# ── the monitor pass ─────────────────────────────────────────────────────

class _Book:
    """An operator executor whose pass may fill a resting row or book a new one."""

    def __init__(self, rows=(), *, on_check=None):
        self.user_id = None
        self._positions = {r.trade_id: r for r in rows}
        self._closed_trades: list = []
        self.closed_trades_read_failed = False
        self._on_check = on_check
        self.passes = 0

    @property
    def open_positions(self):
        return [p for p in self._positions.values() if p.status in ("open", "pending_fill")]

    @property
    def closed_positions(self):
        return []

    async def check_positions(self, entry_halt=None):
        self.passes += 1
        if self._on_check is not None:
            return self._on_check(self) or []
        return []

    async def reconcile_positions(self):
        return []

    async def verify_and_fix_sltp(self):
        return None

    async def sync_positions_from_exchange(self):
        return None


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)

    async def _no_sync(_eng):
        return []

    monkeypatch.setattr(engine_mod, "sync_portfolio_with_exchange", _no_sync)


def _monitor(book):
    eng = RuneClawEngine()
    eng.live_executor = book
    eng._user_executors = {}
    eng._last_sltp_verify_ts = time.monotonic()
    heard: list = []
    for name in ("close", "fill", "sync"):
        async def _cb(msg, _n=name):
            heard.append((_n, msg))
        setattr(eng, f"_{name}_notify_callback", _cb)
    eng.heard = heard
    eng.posted = _posts(eng)
    return eng


def _fill(tid):
    def _on(book):
        book._positions[tid].status = "open"
        return ["LIMIT FILLED: LONG SOL/USDT [SWING]\nFill: $98.9000 | Qty: 1.000000"]
    return _on


class TestTheMonitorAnnouncesTheFill:

    def test_a_resting_limit_that_fills_is_announced_on_that_pass(self, live, feed, synced):
        book = _Book([_row("T1", status="pending_fill")], on_check=_fill("T1"))
        eng = _monitor(book)
        asyncio.run(eng._check_open_positions())
        (ev,) = [e for e in feed if e["type"] == "trade_open"]
        assert ev["body"] == "Entry $98.9000 · SL $94.5000 · TP $105.50"
        assert [lv["symbol"] for lv in eng.posted] == ["SOL/USDT"]
        assert ("fill", "LIMIT FILLED: LONG SOL/USDT [SWING]\nFill: $98.9000 | Qty: 1.000000") in eng.heard
        assert len(synced) == 1

    def test_a_second_pass_announces_nothing_more(self, live, feed, synced):
        book = _Book([_row("T1", status="pending_fill")], on_check=_fill("T1"))
        eng = _monitor(book)
        asyncio.run(eng._check_open_positions())
        book._on_check = None
        asyncio.run(eng._check_open_positions())
        assert len(eng.posted) == 1 and len(synced) == 1

    def test_a_row_that_still_rests_is_not_announced(self, live, feed, synced):
        eng = _monitor(_Book([_row("T1", status="pending_fill")]))
        asyncio.run(eng._check_open_positions())
        assert eng.posted == [] and synced == [] and feed == []

    def test_a_restart_does_not_announce_the_book_it_found(self, live, feed, synced):
        eng = _monitor(_Book([_row("T0"), _row("T2", symbol="ETH/USDT")]))
        asyncio.run(eng._check_open_positions())
        assert eng.posted == [] and synced == []

    def test_a_row_booked_on_the_pass_is_announced(self, live, feed, synced):
        """A submission recovered by client id is booked as a NEW open row,
        not a pending one that flips: the state is read, not the message."""
        def _recover(book):
            book._positions["T9"] = _row("T9", symbol="ETH/USDT")
        eng = _monitor(_Book([], on_check=_recover))
        asyncio.run(eng._check_open_positions())
        assert [lv["symbol"] for lv in eng.posted] == ["ETH/USDT"]

    def test_an_adopted_row_is_synced_and_not_announced(self, live, feed, synced):
        def _adopt(book):
            book._positions["A1"] = _row("A1", origin="adopted")
        eng = _monitor(_Book([], on_check=_adopt))
        asyncio.run(eng._check_open_positions())
        assert eng.posted == [] and [e for e in feed if e["type"] == "trade_open"] == []
        assert len(synced) == 1

    def test_a_venue_switch_does_not_announce_the_new_venues_book(self, live, feed, synced):
        eng = _monitor(_Book([_row("T0")]))
        asyncio.run(eng._check_open_positions())
        eng.live_executor = _Book([_row("B1", symbol="BTC/USDT")])
        asyncio.run(eng._check_open_positions())
        assert eng.posted == [] and synced == []

    def test_a_row_no_longer_on_the_book_leaves_the_record(self, live, feed, synced):
        book = _Book([_row("T0")])
        eng = _monitor(book)
        asyncio.run(eng._check_open_positions())
        del book._positions["T0"]
        asyncio.run(eng._check_open_positions())
        assert eng._agent_open_seen[1] == set()

    def test_a_persons_book_is_never_announced(self, live, feed, synced):
        """Seeded while its row still rested, so the row opening afterwards is
        new to that seed: only the identity check stands between it and the
        public channel."""
        eng = _monitor(_Book([]))
        mine = _Book([_row("U1", status="pending_fill")])
        mine.user_id = "111"
        eng._agent_open_ids(mine)
        mine._positions["U1"].status = "open"
        assert asyncio.run(eng._announce_agent_open(mine, "U1")) is False
        assert asyncio.run(eng._announce_new_agent_opens(mine)) == 0
        assert eng.posted == [] and synced == [] and feed == []

    def test_a_persons_book_does_not_cost_the_operator_its_seed(self, live, feed, synced):
        """The record is one book's. An attempt on a person's book that seeded
        it would replace the operator's, and the operator's next fill would be
        taken for a row the book already held."""
        book = _Book([_row("T1", status="pending_fill")])
        eng = _monitor(book)
        eng._agent_open_ids(book)
        mine = _Book([_row("U1")])
        mine.user_id = "111"
        asyncio.run(eng._announce_agent_open(mine, "U1"))
        book._positions["T1"].status = "open"
        assert asyncio.run(eng._announce_new_agent_opens(book)) == 1
        assert [lv["symbol"] for lv in eng.posted] == ["SOL/USDT"]

    def test_one_row_is_announced_once_whoever_reaches_it(self, live, feed, synced):
        book = _Book([])
        eng = _monitor(book)
        eng._agent_open_ids(book)
        book._positions["T1"] = _row("T1")
        assert asyncio.run(eng._announce_agent_open(book, "T1")) is True
        assert asyncio.run(eng._announce_agent_open(book, "T1")) is False
        assert asyncio.run(eng._announce_new_agent_opens(book)) == 0
        assert len(eng.posted) == 1

    def test_a_failing_post_does_not_cost_the_feed_or_the_sync(self, live, feed, synced):
        book = _Book([])
        eng = _monitor(book)
        eng._agent_open_ids(book)
        book._positions["T1"] = _row("T1")

        async def _broken(levels):
            raise RuntimeError("channel down")

        eng.set_public_open_callback(_broken)
        assert asyncio.run(eng._announce_agent_open(book, "T1")) is True
        assert [e["type"] for e in feed] == ["trade_open"] and len(synced) == 1


# ── the website sync ─────────────────────────────────────────────────────

class TestTheWebsiteSync:

    def test_only_positions_are_sent_not_resting_orders(self, synced):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng.live_executor = _Book([_row("T0"), _row("R1", status="pending_fill", symbol="ETH/USDT")])
        eng.resolve_display_equity_sync = lambda: (100.0, "live")
        eng._sync_live_state_to_website()
        ((only,),) = synced
        assert only["asset"] == "SOL/USDT"


# ── the channel post ─────────────────────────────────────────────────────

class _Bot:
    def __init__(self):
        self.sent: list = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


def _forwarder():
    from bot.marketing.channel_forwarder import ChannelForwarder
    fwd = ChannelForwarder.__new__(ChannelForwarder)
    fwd._bot, fwd._group_ids = _Bot(), {-1001}
    fwd._lock, fwd._enabled = threading.Lock(), True
    return fwd


class TestTheChannelPost:

    def test_the_post_prints_the_rows_levels(self):
        fwd = _forwarder()
        asyncio.run(fwd.post_trade_opened(opened_levels(_row("T"))))
        (post,) = fwd._bot.sent
        assert "TRADE OPENED" in post and "LIVE" in post
        for want in ("Entry: <code>$98.9000</code>", "Stop Loss: <code>$94.5000</code>",
                     "Take Profit: <code>$105.50</code>"):
            assert want in post, (want, post)

    def test_an_unstated_level_and_an_estimated_entry_are_said(self):
        pos = _row("T", stop_loss=0.0)
        pos.entry_source = ENTRY_ESTIMATED
        fwd = _forwarder()
        asyncio.run(fwd.post_trade_opened(opened_levels(pos)))
        (post,) = fwd._bot.sent
        assert "Stop Loss: <code>—</code>" in post
        assert "~$98.9000 (estimated: the venue stated no fill price)" in post

    def test_a_symbol_is_escaped(self):
        fwd = _forwarder()
        asyncio.run(fwd.post_trade_opened(dict(opened_levels(_row("T")), symbol="<b>X")))
        (post,) = fwd._bot.sent
        assert "&lt;b&gt;X" in post and "<b>X" not in post

    def test_the_monitor_hands_the_levels_to_the_forwarder(self):
        """Driven through the real `start_monitor`: the callback it installs
        is called with a reading and must reach the forwarder with it."""
        from unittest.mock import AsyncMock

        from tests.test_an_alert_is_in_the_transcript import ALICE, _started, _store, _Users
        fwd = NS(set_bot=lambda b: None, post_signal=AsyncMock(),
                 post_trade_opened=AsyncMock(), post_trade_closed=AsyncMock())
        w = _started(_store(), _Users([ALICE]), ALICE, forwarder=fwd)
        try:
            levels = opened_levels(_row("T"))
            asyncio.run(w.engine.cbs["open"](levels))
        finally:
            w.restore()
        fwd.post_trade_opened.assert_awaited_once_with(levels)


# ── the door ─────────────────────────────────────────────────────────────

class TestTheDoor:

    def test_a_resting_answer_is_not_called_executed(self):
        from tests.test_a_refusal_is_never_announced_as_a_trade import _tap
        replies, posts, _ = _tap(
            "🟢 <b>LIMIT ORDER BUY SOL/USDT</b>\n- Status: " + RESTING_ANSWER)
        assert replies[-1].startswith("⏳ <b>Order placed — resting, not filled yet</b>")
        assert "Trade executed" not in replies[-1]
        assert posts == []

    def test_the_resting_words_are_what_execute_answers_with(self):
        import inspect

        from bot.core.live_executor import LiveExecutor
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor.execute))
        assert 'f"- Status: {RESTING_ANSWER}\\n"' in src
        assert "PENDING FILL" not in src

    def test_every_language_says_it(self):
        from bot.utils.i18n import _STRINGS, SUPPORTED_LANGS
        entry = _STRINGS["trade_order_resting"]
        assert set(SUPPORTED_LANGS) <= set(entry) and all(entry[c] for c in SUPPORTED_LANGS)

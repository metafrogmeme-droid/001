"""The /livepositions picture card prints what the record holds, or says it cannot.

Driven before the fix, the card's producer handed the renderer placeholders:

* ``rr`` read ``getattr(p, "rr", 0)``, an attribute ``LivePosition`` does not
  have, so every card printed ``R:R 0.0x``: no reward per unit of risk, on
  every position.
* ``fees`` was the literal ``0.0`` and ``net_pnl`` was the gross, so the NET
  cell read the gross beside ``fees $0.00``.
* An adopted position whose margin the venue never stated printed
  ``SIZE $0.00``, and its stop, which the record holds as ``0.0``, was tagged
  ``bot-managed``: a stop the card vouches for and nothing will act on.
* A mark that could not be read printed the stop as ``(0.0%)`` away.
* A pending order whose mark could not be read raised on ``None > 0`` after the
  positions picture had been sent, so the caller sent the text readout too.

/positions carried two of the same: its stop tag, and a ``size_usd`` that fell
back to the notional when the margin was unread, which ``open_book_return`` and
the text card then multiplied by the ROE. And its pending-order card raised on
an adopted resting order, whose leverage is recorded as 0.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from bot.core.live_executor import LivePosition
from bot.formatters import signal_card as sc
from bot.skills import trading_commands as tc
from bot.skills.trading_commands import (
    level_status,
    live_pending_order_row,
    live_position_card_data,
    pending_order_card,
    position_fee_estimate,
)
from tests.png_text import text_of

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _pos(tid="A", **kw):
    base = dict(trade_id=tid, symbol="SOL/USDT", direction="LONG",
                entry_price=100.0, quantity=1.0, cost_usd=20.0,
                stop_loss=95.0, take_profit=110.0, leverage=5,
                order_type="limit", sl_order_id="s1")
    base.update(kw)
    p = LivePosition(**base)
    p.opened_at = NOW - timedelta(hours=2)
    return p


def _adopted():
    """What adoption writes for a position the venue priced but did not size."""
    return _pos("B", stop_loss=0.0, take_profit=0.0, cost_usd=0.0,
                sl_order_id=None)


class TestTheCardData:
    def test_the_ratio_is_the_live_one(self):
        d = live_position_card_data(_pos(), 102.0, NOW)
        # reward 110-102 = 8, risk 102-95 = 7
        assert d["rr"] == pytest.approx(8 / 7)

    def test_the_fees_are_the_round_trip_and_the_net_is_after_them(self):
        p = _pos()
        d = live_position_card_data(p, 102.0, NOW)
        est = position_fee_estimate({
            "entry": 100.0, "current": 102.0, "quantity": 1.0,
            "hold_hours": 2.0, "order_type": "limit", "notional_usd": 100.0})
        cost = est["total_fees"] + est["funding_paid"]
        assert cost > 0
        assert d["fees"] == pytest.approx(cost)
        assert d["pnl_usd"] == pytest.approx(2.0)
        assert d["net_pnl"] == pytest.approx(2.0 - cost)

    def test_the_entry_leg_is_charged_at_the_positions_own_rate(self):
        limit = live_position_card_data(_pos(order_type="limit"), 102.0, NOW)
        market = live_position_card_data(_pos(order_type="market"), 102.0, NOW)
        assert market["fees"] > limit["fees"]

    def test_a_margin_nobody_stated_is_unread_not_zero(self):
        d = live_position_card_data(_adopted(), 102.0, NOW)
        assert d["size_usd"] is None
        assert d["pnl_usd"] is None and d["net_pnl"] is None

    def test_a_stop_the_record_does_not_hold_is_named_as_none(self):
        d = live_position_card_data(_adopted(), 102.0, NOW)
        assert d["sl"] is None and d["tp"] is None
        assert d["sl_status"] == "none on record"
        assert d["tp_status"] == "none on record"
        assert d["rr"] is None

    def test_an_unread_mark_gives_no_distance_and_no_ratio(self):
        d = live_position_card_data(_pos(), None, NOW)
        assert d["now"] is None
        assert d["sl"] == 95.0
        assert d["sl_pct"] is None and d["tp_pct"] is None
        assert d["rr"] is None
        assert d["pnl_usd"] is None and d["net_pnl"] is None

    def test_a_read_mark_gives_both_distances(self):
        d = live_position_card_data(_pos(), 100.0, NOW)
        assert d["sl_pct"] == pytest.approx(5.0)
        assert d["tp_pct"] == pytest.approx(10.0)

    def test_an_unread_entry_leaves_the_fees_unread(self):
        d = live_position_card_data(_pos(entry_price=0.0), 102.0, NOW)
        assert d["fees"] is None and d["net_pnl"] is None

    def test_the_hold_is_counted_from_the_fill(self):
        p = _pos()
        p.opened_at = NOW - timedelta(hours=5)
        p.filled_at = NOW - timedelta(minutes=30)
        assert live_position_card_data(p, 102.0, NOW)["hold_time"] == "30m"
        p.filled_at = None
        assert live_position_card_data(p, 102.0, NOW)["hold_time"] == "5h 0m"

    def test_a_short_is_signed_as_a_short(self):
        p = _pos(direction="SHORT", stop_loss=105.0, take_profit=90.0)
        d = live_position_card_data(p, 98.0, NOW)
        assert d["pnl_usd"] == pytest.approx(2.0)


class TestLevelStatus:
    def test_an_order_on_the_venue(self):
        assert level_status(95.0, "s1") == "on exchange"

    def test_a_level_the_bot_holds(self):
        assert level_status(95.0, None) == "bot-managed"

    @pytest.mark.parametrize("level", [0.0, None, -1.0, float("nan"), "x"])
    def test_no_level_on_record(self, level):
        assert level_status(level, None) == "none on record"


class TestThePendingRow:
    def test_an_unread_mark_is_none_not_zero(self):
        row = live_pending_order_row(_pos(status="pending_fill"), None)
        assert row["current_price"] is None and row["dist_pct"] is None

    def test_a_read_mark_gives_the_distance(self):
        row = live_pending_order_row(_pos(status="pending_fill"), 98.0)
        assert row["dist_pct"] == pytest.approx(2.0)
        assert row["side"] == "BUY"

    def test_an_unread_quantity_is_none(self):
        row = live_pending_order_row(_pos(status="pending_fill", quantity=0.0), 98.0)
        assert row["amount"] is None


class TestTheRenderers:
    """Read back through the PNG text seam: what the card DREW."""

    def test_the_card_prints_the_ratio_and_the_fees(self):
        out = text_of(sc.render_position_card,
                      live_position_card_data(_pos(), 102.0, NOW))
        assert "1.1x" in out
        assert "fees $0.08" in out
        assert "$+1.92" in out
        assert "0.0x" not in out

    def test_an_adopted_position_prints_unread_and_no_stop(self):
        out = text_of(sc.render_position_card,
                      live_position_card_data(_adopted(), 102.0, NOW))
        assert "STOP LOSS  none on record" in out
        assert "bot-managed" not in out
        assert "unread" in out
        assert "$0.00" not in out

    def test_an_unread_mark_prints_the_stop_without_a_distance(self):
        out = text_of(sc.render_position_card,
                      live_position_card_data(_pos(), None, NOW))
        assert "95.0000" in out
        assert "(0.0%)" not in out

    def test_the_orders_card_draws_an_unread_mark_and_quantity(self):
        row = live_pending_order_row(_pos(status="pending_fill", quantity=0.0), None)
        out = text_of(sc.render_orders_card, [row], timestamp="12:00 UTC")
        assert "Qty: unread" in out
        assert "CURRENT" not in out and "TO FILL" not in out

    def test_the_orders_card_reads_junk_as_unread(self):
        """The renderer reads each figure, so a value that is not a number is
        unread rather than compared with 0, which raised."""
        out = text_of(sc.render_orders_card, [{
            "sym": "SOL/USDT", "side": "BUY", "price": 100.0,
            "current_price": "n/a", "amount": "n/a", "dist_pct": "n/a",
            "type": "limit", "oid": "o1"}], timestamp="12:00 UTC")
        assert "Qty: unread" in out and "CURRENT" not in out

    def test_a_read_mark_with_no_distance_draws_no_distance(self):
        """A pending order whose own price was not read has a mark and no
        distance to it: the card draws the mark and no TO FILL cell."""
        row = live_pending_order_row(
            _pos(status="pending_fill", entry_price=0.0), 102.0)
        assert row["dist_pct"] is None
        out = text_of(sc.render_orders_card, [row], timestamp="12:00 UTC")
        assert "CURRENT" in out and "TO FILL" not in out

    def test_the_orders_card_draws_a_read_row(self):
        row = live_pending_order_row(_pos(status="pending_fill"), 98.0)
        out = text_of(sc.render_orders_card, [row], timestamp="12:00 UTC")
        assert "Qty: 1.0000" in out and "+2.00%" in out


class _Ticker:
    def __init__(self, marks):
        self.marks = marks

    async def fetch_ticker(self, sym):
        m = self.marks.get(sym)
        if m is None:
            raise RuntimeError("ticker down")
        return {"last": m}


class _Exec:
    def __init__(self, marks):
        self._ex = _Ticker(marks)

    async def _get_exchange(self):
        return self._ex

    def _last_sltp_reason(self, sym):
        return "venue refused the trigger price"


def _host():
    sent = {"photos": [], "texts": []}

    async def _photo(update, png, cap, reply_markup=None):
        sent["photos"].append(cap)
        return True

    async def _send(update, text, **kw):
        sent["texts"].append(text)

    return NS(_lang=lambda u: "en", _send_photo=_photo, _send=_send), sent


class TestTheHandler:
    def _run(self, filled, pending, marks, monkeypatch, cards=None):
        seen = [] if cards is None else cards
        monkeypatch.setattr(sc, "render_position_card",
                            lambda d: (seen.append(d), b"PNG")[1])
        host, sent = _host()
        r = asyncio.run(tc.TradingCommands._render_livepositions_cards(
            host, None, filled, pending, _Exec(marks)))
        return r, sent, seen

    def test_every_card_is_the_one_reading(self, monkeypatch):
        planted = {"planted": True}
        monkeypatch.setattr(tc, "live_position_card_data",
                            lambda p, cur, now: planted)
        r, sent, seen = self._run([_pos()], [], {"SOL/USDT": 102.0}, monkeypatch)
        assert r is True and seen == [planted]

    def test_the_mark_the_card_reads_is_the_one_fetched(self, monkeypatch):
        got = []
        monkeypatch.setattr(tc, "live_position_card_data",
                            lambda p, cur, now: (got.append(cur), {})[1])
        self._run([_pos(), _pos("C", symbol="ETH/USDT")], [],
                  {"SOL/USDT": 102.0}, monkeypatch)
        assert got == [102.0, None]

    def test_the_time_exit_line_is_handed_the_marks_it_read(self, monkeypatch):
        """The caption's time-exit line measures R from the mark, so it has to
        be handed the marks this pass read, with an unread one as None."""
        got = []

        def _cap(cap, filled, now, lang, marks):
            got.append(dict(marks))
            return cap
        monkeypatch.setattr(tc, "_caption_with_time_exits", _cap)
        self._run([_pos(), _pos("C", symbol="ETH/USDT")], [],
                  {"SOL/USDT": 102.0}, monkeypatch)
        assert got == [{"A": 102.0, "C": None}]

    def test_a_pending_order_with_an_unread_mark_does_not_resend(self, monkeypatch):
        r, sent, _ = self._run([_pos()], [_pos("D", symbol="ETH/USDT",
                                                status="pending_fill")],
                               {"SOL/USDT": 102.0}, monkeypatch)
        assert r is True
        assert len(sent["photos"]) == 2
        assert sent["texts"] == []

    def test_a_pending_card_that_fails_says_so_once(self, monkeypatch):
        def _raise(*a, **k):
            raise RuntimeError("draw failed")
        monkeypatch.setattr(sc, "render_orders_card", _raise)
        r, sent, _ = self._run([_pos()], [_pos("D", status="pending_fill")],
                               {"SOL/USDT": 102.0}, monkeypatch)
        assert r is True
        assert len(sent["photos"]) == 1
        assert len(sent["texts"]) == 1
        assert "could not be drawn" in sent["texts"][0]
        assert "/orders" in sent["texts"][0]

    def test_with_nothing_sent_a_failed_pending_card_falls_back(self, monkeypatch):
        def _raise(*a, **k):
            raise RuntimeError("draw failed")
        monkeypatch.setattr(sc, "render_orders_card", _raise)
        r, sent, _ = self._run([], [_pos("D", status="pending_fill")],
                               {"SOL/USDT": 102.0}, monkeypatch)
        assert r is False and sent["texts"] == []

    def test_the_refusal_line_does_not_call_a_missing_stop_bot_managed(
            self, monkeypatch):
        r, sent, _ = self._run([_adopted()], [], {"SOL/USDT": 102.0},
                               monkeypatch)
        cap = sent["photos"][0]
        assert "SL none on record" in cap
        assert "SL bot-managed" not in cap

    def test_a_stop_the_bot_holds_keeps_its_word(self, monkeypatch):
        r, sent, _ = self._run([_pos(sl_order_id=None)], [],
                               {"SOL/USDT": 102.0}, monkeypatch)
        assert "SL bot-managed" in sent["photos"][0]


class TestThePositionsCommand:
    """/positions' live branch, driven through the real handler."""

    def _drive(self, positions, marks, monkeypatch):
        from bot.config import CONFIG
        from bot.skills import telegram_handler as th

        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
        cards, texts = [], []
        monkeypatch.setattr(sc, "render_position_card",
                            lambda d: (cards.append(d), b"PNG")[1])
        h = object.__new__(th.TelegramHandler)
        ex = NS(open_positions=positions,
                _get_exchange=_Exec(marks)._get_exchange)
        h.engine = NS(user_portfolios={}, position_watch=lambda: None,
                      pending_ideas=[])
        h._get_tg_id = lambda update: 7
        h._lang = lambda update: "en"
        h._caller_executor = lambda update: ex

        async def _allow(update, command="", ctx=None):
            return True
        h._guard = _allow

        async def _send(update, text, **kw):
            texts.append(text)
        h._send = _send

        async def _send_photo(update, png, caption, reply_markup=None):
            texts.append(caption)
            return True
        h._send_photo = _send_photo
        asyncio.run(h._cmd_open_positions(None, None))
        return cards, "\n".join(texts)

    def test_a_margin_nobody_stated_is_not_printed_as_the_notional(
            self, monkeypatch):
        p = _pos(cost_usd=0.0, leverage=5)
        cards, out = self._drive([p], {"SOL/USDT": 102.0}, monkeypatch)
        assert cards and cards[0]["size_usd"] is None
        # notional 102 x ROE 10% would have been printed as $10.20
        assert "$+10.20" not in out

    def test_a_stop_the_record_does_not_hold_is_not_bot_managed(
            self, monkeypatch):
        cards, _ = self._drive([_adopted()], {"SOL/USDT": 102.0}, monkeypatch)
        assert cards[0]["sl_status"] == "none on record"

    def test_an_adopted_resting_order_is_listed(self, monkeypatch):
        rest = _pos("R", status="pending_fill", cost_usd=0.0, leverage=0,
                    sl_order_id=None)
        cards, out = self._drive([rest], {"SOL/USDT": 102.0}, monkeypatch)
        assert "PENDING ORDERS (1)" in out
        assert "margin unread" in out and "leverage unread" in out


class TestTheOrdersCommand:
    """/orders hands its card the reading's own figures, with an unread one as
    None: the card prints "Qty: unread" for that, where a 0 printed a measured
    "Qty: 0.0000"."""

    def test_an_unread_amount_and_mark_reach_the_card_as_none(self, monkeypatch):
        from bot.skills import telegram_handler as th
        rows = []
        monkeypatch.setattr(sc, "render_orders_card",
                            lambda r, timestamp="": (rows.extend(r), b"PNG")[1])
        reading = NS(unavailable=None, state="read", notes=[], read_at=NOW,
                     error_kind=None, prices={"SOL/USDT": None},
                     orders=[{"sym": "SOL/USDT", "side": "BUY", "price": 100.0,
                              "amount": None, "oid": "o1", "type": "limit"}])

        async def _read(engine, uid):
            return reading
        monkeypatch.setattr(tc, "open_orders_for", _read)
        h = object.__new__(th.TelegramHandler)
        h.engine = NS()

        async def _dispatch(*a, **k):
            return "orders text"
        h.registry = NS(dispatch=_dispatch)

        async def _allow(update, command="", ctx=None):
            return True
        h._guard = _allow

        async def _send(update, text, **kw):
            pass
        h._send = _send

        async def _photo(**kw):
            return None
        update = NS(effective_user=NS(id=7), effective_chat=NS(id=7),
                    get_bot=lambda: NS(send_photo=_photo))
        asyncio.run(h._cmd_orders(update, None))
        assert rows and rows[0]["amount"] is None
        assert rows[0]["current_price"] is None and rows[0]["dist_pct"] is None


class TestThePendingOrderCard:
    def test_an_unread_margin_and_leverage_are_said(self):
        card = pending_order_card({
            "pair": "SOLUSDT", "direction": "LONG", "entry": 100.0,
            "current": 102.0, "sl": 95.0, "tp": 110.0, "size_usd": None,
            "notional_usd": 102.0, "leverage": None, "hold_hours": 1.0,
            "quantity": 1.0, "order_type": "limit",
            "sl_order": "manual", "tp_order": "manual"})
        assert "margin unread" in card and "leverage unread" in card
        assert "[bot-managed]" in card


class TestThePositionsRowReadsEntryAndMark:
    """/positions' own row builder, driven through the command."""

    _drive = TestThePositionsCommand._drive

    def test_an_unread_entry_does_not_end_the_command(self, monkeypatch):
        p = _pos("E", entry_price=0.0, cost_usd=0.0, leverage=0,
                 stop_loss=0.0, take_profit=0.0, sl_order_id=None)
        cards, out = self._drive([p], {"SOL/USDT": 102.0}, monkeypatch)
        assert "OPEN POSITIONS (1)" in out
        assert "entry unread" in out
        assert cards[0]["pnl_pct"] is None

    def test_an_unread_mark_measures_nothing_from_the_entry(self, monkeypatch):
        cards, out = self._drive([_pos()], {}, monkeypatch)
        d = cards[0]
        assert d["sl_pct"] is None and d["tp_pct"] is None
        assert d["rr"] is None
        assert "price unavailable" in out

    def test_a_mark_that_is_not_a_price_is_not_printed_as_one(self, monkeypatch):
        """The fetch stores any `last > 0`, which an infinity passes. The row
        read it as a mark for the unread flag and as no mark for everything
        else, so the card printed the ENTRY as the current price."""
        cards, out = self._drive([_pos()], {"SOL/USDT": float("inf")},
                                 monkeypatch)
        assert cards[0]["now"] is None
        assert "price unavailable" in out

    def test_a_read_mark_measures_from_the_mark(self, monkeypatch):
        cards, _ = self._drive([_pos()], {"SOL/USDT": 100.0}, monkeypatch)
        assert cards[0]["sl_pct"] == pytest.approx(5.0)
        assert cards[0]["rr"] == pytest.approx(2.0)

    def test_the_row_names_a_margin_nobody_stated(self, monkeypatch):
        cards, out = self._drive([_pos(cost_usd=0.0)], {"SOL/USDT": 102.0},
                                 monkeypatch)
        assert "margin unread" in out
        assert "price unavailable" not in out
        assert cards[0]["pnl_unread"] == "margin unread"
        assert "no recorded margin" in out


class TestTheCause:
    @pytest.mark.parametrize("args,want", [
        ((100.0, None, 5.0, 20.0), "price unavailable"),
        ((None, None, 5.0, 20.0), "price unavailable"),
        ((None, 102.0, 5.0, 20.0), "entry unread"),
        ((100.0, 102.0, None, None), "leverage unread"),
        ((100.0, 102.0, 5.0, None), "margin unread"),
        ((100.0, 102.0, 5.0, 20.0), None),
    ])
    def test_the_first_missing_reading_is_named(self, args, want):
        assert tc.pnl_unread_cause(*args) == want


class TestTheCoverageNote:
    def test_the_causes_are_counted_apart(self):
        from bot.utils.portfolio_return import coverage_note, open_book_return
        book = open_book_return([
            {"pnl_usd": 2.0, "size_usd": 20.0, "pnl_pct": 10.0},
            {"pnl_usd": None, "size_usd": 20.0, "price_unavailable": True},
            {"pnl_usd": None, "size_usd": None, "pnl_pct": 10.0},
            {"pnl_usd": None, "size_usd": 0.0, "pnl_pct": 10.0},
        ])
        assert book["no_mark"] == 1 and book["no_margin"] == 2
        note = coverage_note(book, html=False)
        assert "1 with no readable mark" in note
        assert "2 with no recorded margin" in note

    def test_a_book_that_counted_no_cause_names_none(self):
        from bot.utils.portfolio_return import coverage_note
        note = coverage_note({"measured": 1, "unmeasured": 1, "total": 2},
                             html=False)
        assert "could not be priced and are counted" in note
        assert "readable mark" not in note and "margin" not in note


class TestTheTextCard:
    def test_a_read_percent_without_dollars_prints_a_dash(self):
        from bot.formatters.rich_cards import render_open_positions
        out = render_open_positions([{
            "pair": "SOLUSDT", "direction": "LONG", "entry": 100.0,
            "current": 102.0, "price_unavailable": False, "pnl_pct": 10.0,
            "pnl_usd": None, "size_usd": None, "leverage": 5.0,
            "sl": 95.0, "tp": 110.0, "hold_hours": 2.0}])
        assert "+10.0" in out and "(—)" in out
        assert "price unavailable" not in out
        assert "102" in out


    def test_the_paper_rows_unread_mark_is_not_printed_as_zero(self):
        """The paper row writes `current: 0` for a mark it could not read, and
        sets the flag beside it; the mark cell reads the flag."""
        from bot.formatters.rich_cards import render_open_positions
        out = render_open_positions([{
            "pair": "SOLUSDT", "direction": "LONG", "entry": 100.0,
            "current": 0, "price_unavailable": True, "pnl_pct": None,
            "pnl_usd": None, "size_usd": 20.0, "leverage": 5.0,
            "sl": 95.0, "tp": 110.0, "hold_hours": 2.0}])
        assert "$0.000000" not in out
        assert "price unavailable" in out


class TestTheOrphanRow:
    def _row(self, **pos):
        from bot.formatters.orphan_position import orphan_position_row
        base = {"symbol": "SOL/USDT:USDT", "side": "long", "contracts": 1.0,
                "entryPrice": 100.0, "initialMargin": 20.0, "leverage": 5,
                "unrealizedPnl": 2.0}
        base.update(pos)
        return orphan_position_row(base, mark=102.0, sl_price=95.0,
                                   tp_price=110.0, now=NOW)

    def test_a_pnl_the_venue_did_not_state(self):
        assert self._row(unrealizedPnl=None)["pnl_unread"] == "P&L not stated"

    def test_a_margin_the_venue_did_not_state(self):
        r = self._row(initialMargin=None)
        assert r["pnl_unread"] == "margin unread"

    def test_a_priced_orphan_names_nothing(self):
        assert self._row()["pnl_unread"] is None

"""A live record books what filled, and is trued down to what the venue holds.

Live, 11 October: the M/USDT short TI-e9db9a8b's record said more than Bitget
held, so every close and its stop were refused for size (fixed where the
order is sent, `_sized_to_venue`). This is why the record said more.

Two booking sites took the requested size whenever the order stated no fill:
the drift fallback's market leg (`float(order.get("filled", 0) or qty)`) and
the limit fill. Bitget's create-order answer states no fill, so a market leg
that filled part was booked as if it filled in full. Both now ask
`_confirmed_fill`: the order's figure, the order read again, then the side of
the venue's book this position is on, never more than was asked.

Nothing ever lowered a record afterwards. The position sync saw the drift and
only reported it, because a ladder stage or a close in flight differs from one
snapshot. It still reports. When the venue holds LESS on this side, on two
passes running, against the same record, with no close in flight, the record
is trued down (`_true_down_to_venue`). MORE is never written.

The drift fallback also read a cancelled limit's unstated fill as nothing
filled and marketed the full size on top of it. That is refused now, as a
raised read already was.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue

TID = "TI-e9db9a8b"


class Book:
    """A Bitget-like futures book on M/USDT: ``short`` held on the short side,
    ``long`` on the long side; ``None`` is a row with no size; and every
    order read answered from ``orders``."""

    def __init__(self, short=1200.0, long=0.0, *, raises=False, orders=None):
        self.short, self.long, self.raises = short, long, raises
        self.orders = dict(orders or {})
        self.book_reads = 0
        self.order_reads: list[str] = []

    async def fetch_positions(self, symbols=None, params=None):
        self.book_reads += 1
        if self.raises:
            raise RuntimeError("book unreadable")
        rows = []
        for side, held in (("short", self.short), ("long", self.long)):
            if held is None:
                rows.append({"symbol": "M/USDT:USDT", "side": side})
            elif held:
                rows.append({"symbol": "M/USDT:USDT", "side": side, "contracts": held})
        return rows

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        self.order_reads.append(oid)
        answer = self.orders.get(oid, RuntimeError("no such order"))
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)


def _executor(tmp_path, book) -> LiveExecutor:
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget")
    e._get_exchange = AsyncMock(return_value=book)
    e._is_uta = False
    return e


def _fill(e, book, order, requested=1234.6, *, also_held=0.0, direction="SHORT"):
    return asyncio.run(e._confirmed_fill(book, order, "M/USDT", direction, requested,
                                         also_held=also_held))


# ── what an order filled ─────────────────────────────────────────────────────


def test_a_fill_the_order_states_is_booked_without_another_read(tmp_path):
    book = Book()
    assert _fill(_executor(tmp_path, book), book, {"id": "O1", "filled": 900.0}) == (900.0, "order")
    assert (book.order_reads, book.book_reads) == ([], 0)


@pytest.mark.parametrize("stated", [None, 0.0, ""])
def test_an_unstated_fill_is_read_again_from_the_order(tmp_path, stated):
    """A create-order answer's 0.0 is a placeholder as often as a reading."""
    book = Book(orders={"O1": {"id": "O1", "filled": 1100.0}})
    got = _fill(_executor(tmp_path, book), book, {"id": "O1", "filled": stated})
    assert got == (1100.0, "fetched_order")
    assert book.book_reads == 0


@pytest.mark.parametrize("held,booked", [
    (1200.0, 1200.0),       # the venue holds less than asked: what it holds
    (2000.0, 1234.6),       # more (another record's share): never more than asked
])
def test_an_order_that_never_states_its_fill_books_this_sides_holding(tmp_path, held, booked):
    book = Book(short=held, long=5000.0, orders={"O1": {"id": "O1", "filled": None}})
    got = _fill(_executor(tmp_path, book), book, {"id": "O1"})
    assert got == (booked, "venue_book")


def test_what_already_rested_is_not_counted_twice(tmp_path):
    book = Book(short=1500.0)
    got = _fill(_executor(tmp_path, book), book, {"id": "O1"}, requested=1234.6, also_held=400.0)
    assert got == (1100.0, "venue_book")


@pytest.mark.parametrize("book_kw", [
    {"raises": True},       # unread
    {"short": None},        # a row with no size: unread
    {"short": 400.0},       # nothing beyond what already rested: not caught up
    {"short": 0.0},         # flat: not caught up, or nothing; not an empty fill
])
def test_a_book_that_bears_out_no_fill_books_an_estimate_not_nothing(tmp_path, book_kw):
    book = Book(**book_kw)
    got = _fill(_executor(tmp_path, book), book, {"id": None}, requested=1234.6, also_held=400.0)
    assert got == (1234.6, "ESTIMATED")


# ── the drift fallback's market leg ─────────────────────────────────────────


def _drift(market_answer, *, held, limit_read=None):
    """Cancel the resting limit OID1 (0.3 of 1.0 filled), market the rest."""
    ex = LiveExecutor.__new__(LiveExecutor)
    ex.user_id = None
    ex._standard_leverage = lambda symbol: 5
    ex._venue = get_venue("bitget")
    ex._positions = {}
    ex._save_positions = lambda: None
    ex._record_warning = lambda key: None
    ex._place_sl_tp = AsyncMock(return_value=("SL1", "TP1"))
    ex._reattempt_post_fill_sl = AsyncMock(return_value=("SL1", "TP1", None))
    ex._guard_fill_leverage = AsyncMock(return_value=None)
    pos = LivePosition(trade_id="T", symbol="ETH/USDT", direction="LONG",
                       entry_price=140.0, quantity=1.0, cost_usd=28.0,
                       stop_loss=137.0, take_profit=150.0, leverage=5,
                       status="pending_fill", limit_order_id="OID1", order_type="limit")
    ex._positions["T"] = pos
    reads = {"OID1": limit_read or {"status": "canceled", "filled": 0.3, "average": 140.0},
             "MKT1": {"id": "MKT1", "filled": None}}
    exchange = MagicMock()
    exchange.cancel_order = AsyncMock()
    exchange.fetch_order = AsyncMock(side_effect=lambda oid, *a, **k: dict(reads[oid]))
    exchange.fetch_positions = AsyncMock(return_value=[
        {"symbol": "ETH/USDT:USDT", "side": "long", "contracts": held}])
    exchange.create_order = AsyncMock(return_value=dict(market_answer, id="MKT1"))
    audits: list = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        msg = asyncio.run(ex._execute_drift_market_fallback(exchange, "T", pos, 141.0))
    return pos, msg, audits, exchange


def test_the_market_leg_books_what_filled_not_what_it_asked():
    # Asked 0.7 on top of 0.3 resting; the book holds 0.3 + 0.2.
    pos, _msg, audits, _ = _drift({"average": 141.0, "filled": None}, held=0.5)
    assert pos.quantity == pytest.approx(0.5), "booked the requested 0.7 on top"
    assert pos.status == "open"
    assert [a["result"] for a in audits if a.get("action") == "fill_verify"] == ["VENUE_BOOK"]


def test_a_market_leg_that_states_its_fill_is_booked_as_stated():
    pos, _msg, audits, exchange = _drift({"average": 141.0, "filled": 0.7}, held=0.5)
    assert pos.quantity == pytest.approx(1.0)
    assert not [a for a in audits if a.get("action") == "fill_verify"]
    exchange.fetch_positions.assert_not_called()


@pytest.mark.parametrize("limit_read", [
    {"status": "canceled", "average": 140.0},                  # no "filled" key
    {"status": "canceled", "filled": None, "average": 140.0},  # stated null
])
def test_an_unstated_resting_fill_refuses_the_market_leg(limit_read):
    pos, msg, audits, exchange = _drift({"average": 141.0, "filled": 0.7}, held=1.0,
                                        limit_read=limit_read)
    exchange.create_order.assert_not_called()
    assert msg is None and pos.status == "pending_fill"
    assert [a["data"]["reason"] for a in audits if a.get("result") == "REFUSED"] == [
        "pre_fill_unread"]


def test_a_resting_fill_stated_as_zero_markets_the_whole_size():
    pos, _msg, _audits, exchange = _drift(
        {"average": 141.0, "filled": 1.0}, held=1.0,
        limit_read={"status": "canceled", "filled": 0.0, "average": None})
    assert exchange.create_order.call_args.args[3] == pytest.approx(1.0)
    assert pos.quantity == pytest.approx(1.0)


# ── the limit fill ───────────────────────────────────────────────────────────


def _limit_fill(tmp_path, filled, held):
    pos = LivePosition(trade_id="TI-L", symbol="ETH/USDT", direction="LONG",
                       entry_price=4000.0, quantity=0.05, cost_usd=40.0,
                       stop_loss=3920.0, take_profit=4240.0, leverage=5,
                       order_type="limit", limit_order_id="o9", status="pending_fill")
    venue = MagicMock()
    venue.fetch_order = AsyncMock(return_value={
        "id": "o9", "status": "closed", "filled": filled, "average": 3999.0,
        "price": 4000.0, "amount": 0.05, "info": {}})
    venue.fetch_positions = AsyncMock(return_value=[
        {"symbol": "ETH/USDT:USDT", "side": "long", "contracts": held}])
    venue.fetch_ticker = AsyncMock(return_value={"last": 4000.0})
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    ex._exchange = venue
    ex._positions[pos.trade_id] = pos
    audits: list = []

    async def _sl_tp(*a, **k):
        return ("sl1", "tp1")

    async def _ladder(self_, exchange, pos, direction, qty, sl_id, tp_id, trade_id=None):
        return sl_id, tp_id, None

    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(type(le.CONFIG), "is_live", return_value=True), \
         patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
         patch.object(LiveExecutor, "_reattempt_post_fill_sl", _ladder), \
         patch.object(LiveExecutor, "_guard_fill_leverage", AsyncMock(return_value=None)):
        asyncio.run(ex._check_pending_limit(venue, pos.trade_id, pos))
    return pos, audits


def test_a_limit_fill_with_no_stated_amount_books_the_held_side(tmp_path):
    pos, audits = _limit_fill(tmp_path, filled=None, held=0.03)
    assert pos.status == "open"
    assert pos.quantity == pytest.approx(0.03), "booked the requested 0.05"
    assert [a["result"] for a in audits if a.get("action") == "fill_verify"] == ["VENUE_BOOK"]


def test_a_limit_fill_that_states_its_amount_is_booked_as_stated(tmp_path):
    pos, audits = _limit_fill(tmp_path, filled=0.05, held=0.03)
    assert (pos.status, pos.quantity) == ("open", pytest.approx(0.05))
    assert not [a for a in audits if a.get("action") == "fill_verify"]


# ── the sync trues a record down ────────────────────────────────────────────


def _sync_rig(tmp_path, *, row_total="1200", held=1200.0, quantity=1234.6):
    """The real sync against a v3 row stating ``row_total`` and a futures book
    holding ``held`` on the short side."""
    book = Book(short=held)
    e = _executor(tmp_path, book)
    pos = LivePosition(trade_id=TID, symbol="M/USDT", direction="SHORT",
                       entry_price=0.9927, quantity=quantity, cost_usd=100.0,
                       stop_loss=0.9983, take_profit=0.9700, leverage=5, status="open")
    e._positions = {TID: pos}
    saves: list[int] = []
    e._save_positions = lambda: saves.append(1)
    rows = {"rows": [{"symbol": "MUSDT", "total": row_total, "leverage": "5"}]}
    audits: list = []

    def one_pass():
        with patch.object(LiveExecutor, "_fetch_v3_positions_raw",
                          staticmethod(lambda *a: list(rows["rows"]))), \
             patch.object(le, "audit",
                          lambda log, msg, **kw: audits.append({"message": msg, **kw})):
            asyncio.run(e.sync_positions_from_exchange())

    return e, pos, book, rows, audits, saves, one_pass


def _trued(audits):
    return [a for a in audits if a.get("result") == "TRUED_DOWN"]


def test_a_record_the_venue_holds_less_of_is_trued_down_on_the_second_pass(tmp_path):
    e, pos, _book, _rows, audits, saves, one_pass = _sync_rig(tmp_path)
    one_pass()
    assert pos.quantity == 1234.6, "one snapshot is never written"
    assert [a["result"] for a in audits if a.get("action") == "qty_sync"] == ["DRIFT"]
    assert not saves
    one_pass()
    assert pos.quantity == 1200.0
    assert pos.cost_usd == pytest.approx(100.0 * 1200.0 / 1234.6)
    assert len(_trued(audits)) == 1 and saves
    one_pass()
    assert len(_trued(audits)) == 1, "a trued record is not drift"


def test_a_venue_holding_more_is_never_written(tmp_path):
    e, pos, _book, _rows, audits, _saves, one_pass = _sync_rig(
        tmp_path, row_total="2000", held=2000.0)
    for _ in range(3):
        one_pass()
    assert pos.quantity == 1234.6 and not _trued(audits)


def test_a_close_in_flight_trues_nothing(tmp_path):
    e, pos, _book, _rows, audits, _saves, one_pass = _sync_rig(tmp_path)
    lock = e._close_locks.setdefault(TID, asyncio.Lock())

    async def _held():
        await lock.acquire()

    asyncio.run(_held())
    one_pass()
    one_pass()
    assert pos.quantity == 1234.6 and not _trued(audits)
    lock.release()
    one_pass()
    one_pass()
    assert pos.quantity == 1200.0


def test_a_record_that_moved_between_passes_starts_again(tmp_path):
    """A ladder stage lowered the record between the two passes: the second
    reading is against a different record, so it is a first sighting."""
    e, pos, book, _rows, audits, _saves, one_pass = _sync_rig(tmp_path)
    one_pass()
    pos.quantity = 1220.0
    one_pass()
    assert pos.quantity == 1220.0 and not _trued(audits)
    one_pass()
    assert pos.quantity == 1200.0


def test_a_venue_figure_that_moved_between_passes_starts_again(tmp_path):
    e, pos, book, rows, audits, _saves, one_pass = _sync_rig(tmp_path)
    one_pass()
    book.short = 1100.0
    rows["rows"] = [{"symbol": "MUSDT", "total": "1100", "leverage": "5"}]
    one_pass()
    assert pos.quantity == 1234.6 and not _trued(audits)
    one_pass()
    assert pos.quantity == 1100.0


@pytest.mark.parametrize("between", ["unmatched", "in_tolerance"])
def test_a_pass_that_does_not_read_short_resets_the_count(tmp_path, between):
    e, pos, _book, rows, audits, _saves, one_pass = _sync_rig(tmp_path)
    short = list(rows["rows"])
    one_pass()
    rows["rows"] = ([{"symbol": "OTHERUSDT", "total": "1", "leverage": "5"}]
                    if between == "unmatched"
                    else [{"symbol": "MUSDT", "total": "1234", "leverage": "5"}])
    one_pass()
    rows["rows"] = short
    one_pass()
    assert pos.quantity == 1234.6 and not _trued(audits), "two short passes, not running"
    one_pass()
    assert pos.quantity == 1200.0


@pytest.mark.parametrize("book_kw", [{"raises": True}, {"short": None}, {"short": 0.0}])
def test_a_side_book_that_does_not_bear_the_row_out_trues_nothing(tmp_path, book_kw):
    """The v3 row is keyed by symbol alone; the true-down sizes from this
    side of the book, as the close does. Unread or flat there is not short."""
    e, pos, book, _rows, audits, _saves, one_pass = _sync_rig(tmp_path)
    for k, v in book_kw.items():
        setattr(book, k, v)
    for _ in range(3):
        one_pass()
    assert pos.quantity == 1234.6 and not _trued(audits)


def test_the_other_sides_holding_is_not_this_records(tmp_path):
    """A hedge's long leg on the same market does not count toward the short."""
    e, pos, book, _rows, audits, _saves, one_pass = _sync_rig(tmp_path)
    book.long = 5000.0
    one_pass()
    one_pass()
    assert pos.quantity == 1200.0


def test_a_row_reading_less_while_this_side_holds_more_writes_nothing(tmp_path):
    """The row is keyed by symbol alone; the side book is what is written
    from, and it says more, which is never written up."""
    e, pos, book, _rows, audits, _saves, one_pass = _sync_rig(tmp_path, held=2000.0)
    for _ in range(3):
        one_pass()
    assert pos.quantity == 1234.6 and not _trued(audits)

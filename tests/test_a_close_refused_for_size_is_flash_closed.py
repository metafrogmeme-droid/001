"""A close the venue refuses for its size is flash-closed, and the size is read
from the futures book.

Live, 11 October, after the venue-sized close shipped: the same M/USDT short
was still refused, now in the new words: "venue code 25231: the close asked
for more than the position the venue holds". Two things were left:

* The size read asked `fetch_positions` without the product params, which the
  25227 branch has always sent. A Bitget account answered an empty list, and an
  empty list is a flat reading, which sends the record's size uncapped.
* What the venue held was under its 5 USDT minimum order value, so no stop could
  be placed (25610), and a size the bot sends can be refused for that minimum
  too. The flash close names no size and no minimum: it closes what the venue
  holds. 25227's branch already falls back to it; 25231 now does too.

`order_state.flash_close_applied` is the one reading of "the flash close was
applied", for both branches.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import flash_close_applied

REFUSAL_25231 = ('bitget {"code":"25231","msg":"Close quantity cannot exceed the held '
                 'position quantity","requestTime":1791733754258,"data":null}')
APPLIED = {"code": "00000", "data": {"list": [{"orderId": "F1"}]}}


# ── the reading ──────────────────────────────────────────────────────────────

def test_a_flash_close_is_applied_only_when_every_item_was():
    assert flash_close_applied(APPLIED) is True
    assert flash_close_applied({"code": "00000", "data": {"list": [
        {"orderId": "F1"}, {"code": "22002", "msg": "no position"}]}}) is False
    assert flash_close_applied({"code": "00000", "data": {"list": []}}) is False, (
        "an accepted request that applied nothing is not a flat book")
    assert flash_close_applied({"code": "40001"}) is False
    assert flash_close_applied(None) is False


# ── driven: the real close ───────────────────────────────────────────────────

class Venue:
    """A whole-unit M/USDT book; every close order refused with 25231."""

    def __init__(self, rows):
        self.rows = rows
        self.book_reads: list[dict] = []
        self.orders: list[float] = []
        self.fetch_ticker = AsyncMock(return_value={"last": 0.99, "bid": 0.99, "ask": 0.99})
        self.fetch_open_orders = AsyncMock(return_value=[])
        self.cancel_order = AsyncMock(return_value={"status": "canceled"})

    async def fetch_positions(self, symbols=None, params=None):
        self.book_reads.append({"symbols": symbols, "params": params})
        return [dict(r) for r in self.rows]

    def amount_to_precision(self, symbol, amount):
        return str(math.floor(amount))

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.orders.append(amount)
        raise Exception(REFUSAL_25231)

    async def fetch_order(self, oid, symbol=None, params=None):
        return {"id": oid, "status": "closed", "filled": None}


def _pos():
    return LivePosition(
        trade_id="TI-e9db9a8b", symbol="M/USDT", direction="SHORT",
        entry_price=0.9927, quantity=150.0, cost_usd=15.0,
        stop_loss=0.9983, take_profit=0.9700, status="open")


def _executor(tmp_path, venue, flash):
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget")
    pos = _pos()
    e._positions = {pos.trade_id: pos}
    e._get_exchange = AsyncMock(return_value=venue)
    e._is_uta = False
    e._fetch_bitget_close_data = AsyncMock(return_value=None)
    e._flash_close_position = AsyncMock(return_value=flash)
    e._handle_already_closed_position = AsyncMock(
        return_value="✅ CLOSED SHORT M/USDT (flash close)")
    return e, pos


@pytest.mark.asyncio
async def test_a_25231_refusal_is_flash_closed_and_booked(tmp_path, monkeypatch):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    venue = Venue([])
    e, _pos_ = _executor(tmp_path, venue, APPLIED)
    msg = await e.close_position("TI-e9db9a8b", reason="manual")
    assert msg == "✅ CLOSED SHORT M/USDT (flash close)", msg
    e._flash_close_position.assert_awaited_once()
    assert e._handle_already_closed_position.await_args.kwargs == {
        "bot_reason": "manual", "bot_closed": True}, "the bot closed it, for its reason"


@pytest.mark.asyncio
@pytest.mark.parametrize("flash", [None, {"code": "00000", "data": {"list": []}}])
async def test_a_flash_close_that_did_not_apply_is_still_a_failed_close(tmp_path, monkeypatch, flash):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    venue = Venue([])
    e, pos = _executor(tmp_path, venue, flash)
    msg = await e.close_position("TI-e9db9a8b", reason="manual")
    assert msg.startswith("CLOSE FAILED for TI-e9db9a8b: ") and "25231" in msg, msg
    e._handle_already_closed_position.assert_not_awaited()
    assert pos.status == "open" and "TI-e9db9a8b" in e._positions, "the record must survive"


@pytest.mark.asyncio
async def test_the_size_is_read_from_the_futures_book(tmp_path, monkeypatch):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    venue = Venue([{"symbol": "M/USDT:USDT", "side": "short", "contracts": 3.0}])
    e, _ = _executor(tmp_path, venue, APPLIED)
    await e.close_position("TI-e9db9a8b", reason="manual")
    sizing_read = venue.book_reads[0]
    assert sizing_read["params"] == e._venue.futures_params(), (
        "asked without the product params, a Bitget account answered an empty list")
    assert venue.orders[0] == 3.0, "the close was sized to what the futures book holds"

"""A close and a stop send what the venue holds, on the market's size grid.

Live, 11 October: an M/USDT short whose record said more than Bitget held.
Every close was refused, on the tap and on `/liveclose`, with Bitget 25231
("Close quantity cannot exceed the held position quantity"). Its stop was
refused too (25610), so the position sat open with no stop until the
operator closed it by hand. MUSDT trades in whole units.

The close and both classic stop paths sent the RECORD's size. Reconcile reads
the venue's size and, by design, only reports the drift: the ladder, a pyramid
and a close in flight all diverge from a snapshot. So the size is read where the
order is sent (`LiveExecutor._sized_to_venue`). It is the record's size, never
more than the venue holds on that side, truncated to the market's grid. An
unread book sends the record's size, as before.

The close-failed answer printed the venue's raw JSON. It names the refusal's
class and the venue's code now (`order_state.refusal_text`).
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import Direction, LiveExecutor, LivePosition
from bot.core.order_state import amount_to_send, held_on_side, refusal_text

LIVE_REFUSAL = ('bitget {"code":"25231","msg":"Close quantity cannot exceed the held '
                'position quantity","requestTime":1791733702461,"data":null}')


# ── the readings ─────────────────────────────────────────────────────────────

def test_the_held_size_is_this_sides_rows_summed():
    rows = [{"symbol": "M/USDT:USDT", "side": "short", "contracts": 1200.0},
            {"symbol": "M/USDT:USDT", "side": "long", "contracts": 50.0},
            {"symbol": "BTC/USDT:USDT", "side": "short", "contracts": 3.0}]
    assert held_on_side(rows, "M/USDT:USDT", "short") == 1200.0
    assert held_on_side(rows, "M/USDT:USDT", "long") == 50.0
    assert held_on_side([], "M/USDT:USDT", "short") == 0.0, "an empty book is a reading"


def test_a_row_it_cannot_size_makes_the_held_size_unread():
    rows = [{"symbol": "M/USDT:USDT", "side": "short", "contracts": 1200.0},
            {"symbol": "M/USDT:USDT", "side": "short"}]
    assert held_on_side(rows, "M/USDT:USDT", "short") is None, (
        "a partial sum is smaller than the venue holds")
    assert held_on_side(None, "M/USDT:USDT", "short") is None
    assert held_on_side("not a list", "M/USDT:USDT", "short") is None


def test_only_a_positive_reading_caps_the_record():
    assert amount_to_send(1234.6, 1200.0) == 1200.0
    assert amount_to_send(1000.0, 1200.0) == 1000.0, "never more than the record"
    assert amount_to_send(1234.6, None) == 1234.6, "unread sends the record"
    assert amount_to_send(1234.6, 0.0) == 1234.6, (
        "a flat book is the close verification's to read, not a zero order")


def test_a_refusal_names_its_class_and_the_venues_code_not_its_json():
    text = refusal_text(Exception(LIVE_REFUSAL))
    assert text == ("Exception, venue code 25231: the close asked for more than "
                    "the position the venue holds")
    assert "requestTime" not in text and "{" not in text
    assert refusal_text(RuntimeError('bitget {"code":"40001"}')) == (
        "RuntimeError, venue code 40001")
    assert refusal_text(RuntimeError("venue 5xx")) == "RuntimeError"


# ── driven: the real executor against a whole-unit market ────────────────────

class Venue:
    """A Bitget-like venue for M/USDT: whole units, a book that holds
    ``held`` on the short side, and every order recorded."""

    def __init__(self, held=1200.0, *, book_raises=False, close_raises=None):
        self.held = held
        self.book_raises = book_raises
        self.close_raises = close_raises
        self.orders: list[dict] = []
        self.fetch_ticker = AsyncMock(return_value={"last": 0.99, "bid": 0.99, "ask": 0.99})
        self.fetch_open_orders = AsyncMock(return_value=[])
        self.cancel_order = AsyncMock(return_value={"status": "canceled"})

    async def fetch_positions(self, symbols=None, params=None):
        if self.book_raises:
            raise RuntimeError("book unreadable")
        if self.held is None:
            return [{"symbol": "M/USDT:USDT", "side": "short"}]
        return [{"symbol": "M/USDT:USDT", "side": "short", "contracts": self.held}]

    def amount_to_precision(self, symbol, amount):
        return str(math.floor(amount))      # MUSDT: volumePlace 0, truncated

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.orders.append({"symbol": symbol, "side": side, "amount": amount,
                            "params": params or {}})
        if not (params or {}).get("triggerPrice"):
            if self.close_raises:
                raise self.close_raises
            self.held, self.book_raises = 0.0, False    # the close filled: flat
        return {"id": f"O{len(self.orders)}"}

    async def fetch_order(self, oid, symbol=None, params=None):
        return {"id": oid, "status": "closed", "filled": None}


def _pos(quantity=1234.6):
    return LivePosition(
        trade_id="TI-e9db9a8b", symbol="M/USDT", direction="SHORT",
        entry_price=0.9927, quantity=quantity, cost_usd=100.0,
        stop_loss=0.9983, take_profit=0.9700, status="open")


def _executor(tmp_path, venue, pos):
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget")
    e._positions = {pos.trade_id: pos}
    e._get_exchange = AsyncMock(return_value=venue)
    e._is_uta = False                                   # the classic path, no v3 REST
    e._fetch_bitget_close_data = AsyncMock(return_value=None)   # no history read
    return e


def _closes(venue):
    return [o for o in venue.orders if not o["params"].get("triggerPrice")]


@pytest.mark.asyncio
@pytest.mark.parametrize("held,sent", [
    (1200.0, 1200.0),     # the record says more than the venue holds: what is held
    (2000.0, 1234.0),     # the venue holds more: the record, on the grid
])
async def test_the_close_sends_the_held_size_never_more(tmp_path, monkeypatch, held, sent):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    venue = Venue(held=held)
    e = _executor(tmp_path, venue, _pos())
    await e.close_position("TI-e9db9a8b", reason="manual")
    assert _closes(venue), "no close order was sent"
    assert _closes(venue)[0]["amount"] == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("venue_kw", [{"book_raises": True}, {"held": None}])
async def test_an_unread_book_sends_the_record_on_the_grid(tmp_path, monkeypatch, venue_kw):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    venue = Venue(**venue_kw)
    e = _executor(tmp_path, venue, _pos())
    await e.close_position("TI-e9db9a8b", reason="manual")
    assert _closes(venue)[0]["amount"] == 1234.0


@pytest.mark.asyncio
async def test_a_refused_close_answers_with_the_code_not_the_json(tmp_path, monkeypatch):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    venue = Venue(close_raises=Exception(LIVE_REFUSAL))
    e = _executor(tmp_path, venue, _pos())
    # 25231 falls back to the flash close; here it applies nothing, so the
    # close is still a failure and the answer is what this test reads.
    e._flash_close_position = AsyncMock(return_value=None)
    msg = await e.close_position("TI-e9db9a8b", reason="manual")
    assert msg.startswith("CLOSE FAILED for TI-e9db9a8b: "), msg
    assert "venue code 25231" in msg and "requestTime" not in msg, msg


@pytest.mark.asyncio
@pytest.mark.parametrize("held,sent", [(1200.0, 1200.0), (2000.0, 1234.0)])
async def test_a_classic_stop_sends_the_held_size_on_the_grid(tmp_path, held, sent):
    venue = Venue(held=held)
    e = _executor(tmp_path, venue, _pos())
    oid = await e._place_classic_trigger(
        venue, "M/USDT", "sl", Direction.SHORT, 1234.6, 0.9983)
    assert oid, "the stop was not placed"
    assert venue.orders[-1]["amount"] == sent


@pytest.mark.asyncio
async def test_a_moved_classic_stop_sends_the_held_size(tmp_path):
    venue = Venue(held=1200.0)
    pos = _pos()
    pos.sl_order_id = "SL-OLD"
    e = _executor(tmp_path, venue, pos)
    e._is_uta = False
    assert await e._update_exchange_sl(venue, pos, 0.9960) is True
    stops = [o for o in venue.orders if o["params"].get("triggerPrice")]
    assert stops, "no replacement stop was sent"
    assert stops[-1]["amount"] == 1200.0

"""close_position's final handler answers for how far the close got.

`_close_position_inner` wraps everything from "get the exchange" to "book the
result" in one try, and its final `except` used to answer every raise the
same way: status restored to open, `"CLOSE FAILED for …"`. That is the right
answer for a close the venue REJECTED. It is the wrong answer for a raise
after the venue ACCEPTED the close order — verification or bookkeeping
failing over a book that is most likely flat — and for the 25227 path where
the venue said there was no position and only the fill lookup failed. Once
the post-fill guards started reading "CLOSE FAILED" as "open, place the stop"
(the honest reading for a rejected close), those two answers would have put a
stop and a take-profit on a flat book.

So the handler distinguishes them by what it knows: `close_order_id` is
bound the moment the venue accepts the order, `_venue_flat` the moment the
venue reports no position. Either makes the answer CLOSE NOT CONFIRMED —
kept OPEN for the next reconcile, which books it from the venue's own history
— and the guards read that as "not mine to place anything on".

It also clears the ids the cancel pass actually removed. The cancel pass runs
before the close order, so after a rejected close the record used to name
stop and take-profit orders that were no longer on the venue: a protection
claimed that was not there, and the periodic stop check (which re-places on
EMPTY ids) had nothing to act on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import flatten_outcome


def _pos(sl="sl-1", tp="tp-1"):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=95.0, take_profit=110.0,
        sl_order_id=sl, tp_order_id=tp, status="open",
    )


def _venue(create_order, cancel_order=None):
    ex = AsyncMock()
    ex.create_order = create_order
    ex.cancel_order = cancel_order or AsyncMock(return_value={"status": "canceled"})
    ex.fetch_ticker = AsyncMock(return_value={"last": 100.0})
    ex.fetch_positions = AsyncMock(return_value=[])
    return ex


def _executor(tmp_path, venue):
    e = LiveExecutor(state_dir=str(tmp_path))
    p = _pos()
    e._positions = {"T1": p}
    e._get_exchange = AsyncMock(return_value=venue)
    return e, p


# ── a rejected close is still CLOSE FAILED ───────────────────────────────────

@pytest.mark.asyncio
async def test_a_rejected_close_answers_close_failed_and_clears_the_cancelled_ids(tmp_path):
    e, p = _executor(tmp_path, _venue(AsyncMock(side_effect=RuntimeError("venue 5xx"))))
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed"
    assert p.status == "open" and "T1" in e._positions
    assert p.sl_order_id is None and p.tp_order_id is None, (
        "the cancel pass removed them before the venue rejected the close; the "
        "record must not claim a protection that is not there")


@pytest.mark.asyncio
async def test_a_cancel_that_itself_failed_keeps_its_id(tmp_path):
    """A stop the venue would not cancel is still live. Clearing its id would
    make the bot forget an order that can still fire."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(side_effect=RuntimeError("cancel refused")))
    e, p = _executor(tmp_path, venue)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id == "sl-1" and p.tp_order_id == "tp-1"


@pytest.mark.asyncio
async def test_a_raise_before_the_venue_was_reached_keeps_the_ids(tmp_path):
    """Nothing was cancelled, so nothing is cleared: the stops are in place."""
    e, p = _executor(tmp_path, _venue(AsyncMock()))
    e._get_exchange = AsyncMock(side_effect=RuntimeError("no exchange"))
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed"
    assert p.sl_order_id == "sl-1" and p.tp_order_id == "tp-1"
    assert p.status == "open"


# ── an accepted close that could not be confirmed is NOT a failed close ──────

@pytest.mark.asyncio
async def test_a_close_the_venue_accepted_is_not_confirmed_rather_than_failed(tmp_path):
    e, p = _executor(tmp_path, _venue(AsyncMock(return_value={"id": "CLOSE-1"})))
    e._verify_position_closed = AsyncMock(side_effect=RuntimeError("book unreadable"))
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE NOT CONFIRMED" in msg and "accepted (CLOSE-1)" in msg, msg
    assert "CLOSE FAILED" not in msg
    assert flatten_outcome(msg) == "kept_open", (
        "the guards must place nothing on a book the venue most likely flattened")
    assert p.status == "open" and "T1" in e._positions, "kept for the next reconcile"
    assert "book unreadable" not in msg, "driver text belongs in the log, not on the card"


@pytest.mark.asyncio
async def test_a_venue_that_reports_no_position_is_not_confirmed_rather_than_failed(tmp_path):
    """25227 ("No position available to close"), the book read flat, and the
    fill lookup then failed. The venue has said the position is gone; the only
    thing missing is the booking."""
    venue = _venue(AsyncMock(side_effect=Exception(
        'bitget {"code":"25227","msg":"No position available to close"}')))
    e, p = _executor(tmp_path, venue)
    e._handle_already_closed_position = AsyncMock(return_value=None)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE NOT CONFIRMED" in msg and "reports no position" in msg, msg
    assert flatten_outcome(msg) == "kept_open"
    assert p.status == "open" and "T1" in e._positions


@pytest.mark.asyncio
async def test_a_venue_that_reports_no_position_but_a_book_that_could_not_be_read_stays_failed(tmp_path):
    """The same 25227 with the position book unreadable: nobody confirmed the
    position is gone, so this is still a failed close — open, place the stop
    — exactly as before."""
    venue = _venue(AsyncMock(side_effect=Exception(
        'bitget {"code":"25227","msg":"No position available to close"}')))
    venue.fetch_positions = AsyncMock(side_effect=RuntimeError("book unreadable"))
    e, p = _executor(tmp_path, venue)
    e._flash_close_position = AsyncMock(return_value=None)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed"

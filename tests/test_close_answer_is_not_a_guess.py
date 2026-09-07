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


# ── the cancel pass records what it REMOVED ──────────────────────────────────

@pytest.mark.asyncio
async def test_an_order_the_venue_says_no_longer_exists_is_cleared(tmp_path):
    """25204 ("Order does not exist") is the venue saying the stop fired or was
    already gone. It used to be filed with the cancels that FAILED, so the id
    of an order the venue had just declared gone was kept on the record."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(side_effect=Exception(
                       'bitget {"code":"25204","msg":"Order does not exist"}')))
    e, p = _executor(tmp_path, venue)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id is None and p.tp_order_id is None


@pytest.mark.asyncio
async def test_a_combined_stop_is_cancelled_once_and_both_legs_are_cleared(tmp_path):
    """A v3 combined stop carries ONE id for both legs. Cancelling it twice
    made the second attempt fail, which counted the id as still live and kept
    BOTH ids on every rejected close on that account type."""
    calls: list = []

    async def _cancel(oid, symbol):
        calls.append(oid)
        if calls.count(oid) > 1:
            raise RuntimeError("already cancelled")
        return {"status": "canceled"}

    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")), cancel_order=AsyncMock(side_effect=_cancel))
    e, p = _executor(tmp_path, venue)
    p.sl_order_id = p.tp_order_id = "combined-1"
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert calls == ["combined-1"], "one id, one cancel"
    assert p.sl_order_id is None and p.tp_order_id is None


@pytest.mark.asyncio
async def test_a_cancel_nobody_could_verify_keeps_its_id(tmp_path):
    """The venue answered the cancel with a non-terminal status and the
    follow-up read failed. Nobody saw the order go, so it may still be live:
    the id stays. A forgotten live stop is extra protection; a forgotten live
    stop the record does not name is not."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(return_value={"status": "open"}))
    venue.fetch_order = AsyncMock(side_effect=RuntimeError("read failed"))
    e, p = _executor(tmp_path, venue)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id == "sl-1" and p.tp_order_id == "tp-1"


# ── how far the close got, read precisely ────────────────────────────────────

def _flash(items):
    return {"code": "00000", "data": {"list": items}}


@pytest.mark.asyncio
async def test_a_flash_close_whose_item_failed_is_not_a_flat_book(tmp_path):
    """The envelope says the request was accepted; the per-item list says
    whether the close was APPLIED. An accepted request with a failed item is a
    position still open — a failed close, not an unconfirmed one."""
    venue = _venue(AsyncMock(side_effect=Exception(
        'bitget {"code":"25227","msg":"No position available to close"}')))
    venue.fetch_positions = AsyncMock(return_value=[{"contracts": 1.0}])     # still there
    e, p = _executor(tmp_path, venue)
    e._flash_close_position = AsyncMock(return_value=_flash([{"orderId": "x", "code": "40001", "msg": "rejected"}]))
    e._handle_already_closed_position = AsyncMock(return_value=None)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed"
    e._handle_already_closed_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_flash_close_that_was_applied_but_not_booked_is_not_confirmed(tmp_path):
    venue = _venue(AsyncMock(side_effect=Exception(
        'bitget {"code":"25227","msg":"No position available to close"}')))
    venue.fetch_positions = AsyncMock(return_value=[{"contracts": 1.0}])
    e, p = _executor(tmp_path, venue)
    e._flash_close_position = AsyncMock(return_value=_flash([{"orderId": "x", "code": "00000"}]))
    e._handle_already_closed_position = AsyncMock(return_value=None)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE NOT CONFIRMED" in msg and "applied the flash close" in msg, msg
    assert flatten_outcome(msg) == "kept_open"


@pytest.mark.asyncio
async def test_a_network_error_while_sending_the_close_is_not_a_rejection(tmp_path):
    """A timeout on the send is not the venue saying no: the order may have
    reached it and filled. Read as "CLOSE FAILED", the guards would put a stop
    on a book that is most likely flat."""
    import ccxt

    venue = _venue(AsyncMock(side_effect=ccxt.RequestTimeout("timed out")))
    e, p = _executor(tmp_path, venue)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE NOT CONFIRMED" in msg and "may have reached the venue" in msg, msg
    assert flatten_outcome(msg) == "kept_open"
    assert p.sl_order_id is None and p.tp_order_id is None, "the cancel pass ran; those are gone"


@pytest.mark.asyncio
async def test_a_network_error_before_the_venue_was_reached_is_still_a_failed_close(tmp_path):
    import ccxt

    e, p = _executor(tmp_path, _venue(AsyncMock()))
    e._get_exchange = AsyncMock(side_effect=ccxt.NetworkError("no route"))
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed"
    assert p.sl_order_id == "sl-1", "nothing was sent and nothing was cancelled"


@pytest.mark.asyncio
async def test_a_close_booked_before_the_report_raised_says_closed(tmp_path):
    """The success path books the close and prunes the record; only the card
    after it raised. "kept OPEN" would describe a record that no longer
    exists, and "CLOSE FAILED" would send the guards to a flat book."""
    venue = _venue(AsyncMock(return_value={"id": "CLOSE-1", "average": 101.0, "filled": 1.0}))
    e, p = _executor(tmp_path, venue)
    e._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 101.0, "fill_qty": 1.0, "failure_stage": "", "fees": 0.0})
    real_append = e._append_closed_trade

    def _append_then_die(pos):
        real_append(pos)
        raise RuntimeError("card renderer down")
    e._append_closed_trade = _append_then_die
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSED" in msg and "booked" in msg and "could not be rendered" in msg, msg
    assert flatten_outcome(msg) == "closed"
    assert p.status == "closed" and "T1" not in e._positions
    assert "card renderer down" not in msg


@pytest.mark.asyncio
async def test_a_sizeless_position_row_is_not_a_flat_book(tmp_path):
    """25227 plus a position row that states no size: unreadable, not flat.
    The venue did not say the position is gone, so this is a failed close."""
    venue = _venue(AsyncMock(side_effect=Exception(
        'bitget {"code":"25227","msg":"No position available to close"}')))
    venue.fetch_positions = AsyncMock(return_value=[{"symbol": "BTC/USDT:USDT", "contracts": None}])
    e, p = _executor(tmp_path, venue)
    e._flash_close_position = AsyncMock(return_value=None)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed"


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

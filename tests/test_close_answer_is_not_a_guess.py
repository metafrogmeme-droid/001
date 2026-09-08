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

And "actually removed" is read from the table the order lives in. The v3
combined stop is a STRATEGY order; ccxt's cancel_order with no trigger flag
goes to the regular-order endpoint under UTA, where the id is unknown, and its
25204 "Order does not exist" was read as "the stop fired" — so every rejected
close blanked both ids off a record whose stop was live on the venue. The
combined id is cancelled in the strategy table now and read back from it.

Four verdicts, and exactly ONE of them keeps the id: `live`, where the venue
answered and the order is still resting. `removed`, `gone` and `unverified`
all clear it. That is not a shrug at the third — it is the point of having it.
Nothing in the tree reads `sl_order_id` three-valued: non-empty is a green
tick to /positions, to the web gateway, to the unprotected escalation and to
the periodic re-place, so an id nobody could verify would answer "protected"
over a position that may be naked, with the self-heal standing down. The file
says so 2,000 lines up, beside `_mark_stop_absent`: "A cancelled stop that
could not be replaced is an ABSENT stop, and the field has to say so."
Clearing is also idempotent — `_place_sl_tp` cancels what it finds before it
places — so a stop that turns out to be live is replaced, not doubled.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition, send_outcome_unknown
from bot.core.order_state import close_card_is_wrong, flatten_outcome
from bot.core.venues import get_venue


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
async def test_a_does_not_exist_from_the_regular_table_does_not_leave_a_green_tick(tmp_path):
    """On the ccxt route, 25204 ("Order does not exist") cannot be told from
    the answer the regular-order endpoint gives a trigger order it does not
    hold: a stop that fired, or a live stop looked up in the wrong table.

    Unverified is not protected. Nothing in the tree reads `sl_order_id`
    three-valued — non-empty is a green tick to /positions, to the web
    gateway, to the unprotected escalation and to the periodic re-place —
    so keeping an id nobody could verify answers "protected" over a position
    that may be naked, with the self-heal standing down. The id goes, and
    the re-place is idempotent: `_place_sl_tp` cancels what it finds before
    it places."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(side_effect=Exception(
                       'bitget {"code":"25204","msg":"Order does not exist"}')))
    e, p = _executor(tmp_path, venue)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id is None and p.tp_order_id is None


def _v3(e, *, cancel=None, resting=None):
    """Plant the strategy-order channel: `cancel` answers the cancel, `resting`
    answers the unfilled-strategy-orders read. Records the ids cancelled."""
    calls: list = []

    def _cancel(oid):
        calls.append(oid)
        return cancel if cancel is not None else {"code": "00000", "data": None}

    e._venue = get_venue("bitget")
    e._v3_strategy_cancel_sync = _cancel
    e._v3_strategy_order_resting_sync = lambda oid: resting
    return calls


@pytest.mark.asyncio
async def test_a_combined_stop_is_cancelled_once_in_the_strategy_table(tmp_path):
    """A v3 combined stop carries ONE id for both legs, and that id is a
    STRATEGY order: it is cancelled through cancel-strategy-order, once, and
    read back from the unfilled-strategy list before the record forgets it.
    ccxt's cancel_order — the regular table — is never asked."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")))
    e, p = _executor(tmp_path, venue)
    p.sl_order_id = p.tp_order_id = "combined-1"
    calls = _v3(e, resting=False)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert calls == ["combined-1"], "one id, one cancel, in the strategy table"
    venue.cancel_order.assert_not_awaited()
    assert p.sl_order_id is None and p.tp_order_id is None


@pytest.mark.asyncio
async def test_a_strategy_order_the_table_says_is_gone_is_cleared(tmp_path):
    """25204 FROM THE STRATEGY TABLE means what it says: the stop fired or was
    already cancelled. That is the one table where the answer is a reading."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")))
    e, p = _executor(tmp_path, venue)
    p.sl_order_id = p.tp_order_id = "combined-1"
    _v3(e, cancel={"code": "25204", "msg": "Order does not exist"})
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id is None and p.tp_order_id is None


@pytest.mark.asyncio
async def test_a_strategy_order_still_listed_after_the_cancel_keeps_its_id(tmp_path):
    """The cancel was accepted — "only indicates receipt", per the venue's own
    docs — and the unfilled list still carries the order. It is live."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")))
    e, p = _executor(tmp_path, venue)
    p.sl_order_id = p.tp_order_id = "combined-1"
    _v3(e, resting=True)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id == "combined-1" and p.tp_order_id == "combined-1"


@pytest.mark.asyncio
async def test_a_strategy_cancel_nobody_could_verify_does_not_leave_a_green_tick(tmp_path):
    """Accepted, and then the strategy list could not be read; or the cancel
    request never completed. Nobody saw the order go — so the record must not
    say one is there. Same rule as the ccxt route above."""
    for cancel, resting in (({"code": "00000"}, None),
                            ({"code": "TRANSPORT", "msg": "connection reset"}, False)):
        venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")))
        e, p = _executor(tmp_path, venue)
        p.sl_order_id = p.tp_order_id = "combined-1"
        _v3(e, cancel=cancel, resting=resting)
        msg = await e.close_position("T1", reason="leverage_overshoot")
        assert "CLOSE FAILED" in msg
        assert p.sl_order_id is None and p.tp_order_id is None, (cancel, resting)


@pytest.mark.asyncio
async def test_a_transport_failure_whose_text_says_does_not_exist_is_not_a_reading(tmp_path):
    """The TRANSPORT envelope's msg is `str(exc)` of an arbitrary transport
    failure. Testing the venue's "does not exist" wording first would read
    such a message as the venue's own answer."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")))
    e, p = _executor(tmp_path, venue)
    p.sl_order_id = p.tp_order_id = "combined-1"
    _v3(e, cancel={"code": "TRANSPORT", "msg": "<urlopen error host does not exist>"},
        resting=None)
    verdict, detail = await e._cancel_stop_leg(venue, p, "combined-1", combined=True)
    assert verdict == "unverified", detail


@pytest.mark.asyncio
async def test_a_status_less_cancel_answer_is_read_back_before_the_record_forgets_the_id(tmp_path):
    """ccxt-bitget's cancel_order returns an order with no status. A cancel
    that returned is not a confirmation (the pending-fill path says the same),
    so the order is read back: gone clears the id, an unreadable read keeps it."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(return_value={"id": "sl-1", "status": None}))
    venue.fetch_order = AsyncMock(return_value={"id": "sl-1", "status": "canceled"})
    e, p = _executor(tmp_path, venue)
    await e.close_position("T1", reason="leverage_overshoot")
    assert p.sl_order_id is None and p.tp_order_id is None
    assert venue.fetch_order.await_count == 2

    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(return_value={"id": "sl-1"}))
    venue.fetch_order = AsyncMock(side_effect=RuntimeError("read failed"))
    e, p = _executor(tmp_path, venue)
    await e.close_position("T1", reason="leverage_overshoot")
    assert p.sl_order_id is None and p.tp_order_id is None, (
        "an unreadable follow-up is not a stop the record may claim")


@pytest.mark.asyncio
async def test_the_post_close_sweep_re_cancels_a_combined_id_in_the_strategy_table(tmp_path):
    """A strategy cancel that could not be verified lands in the post-close
    sweep; the sweep's re-cancel must go to the same table."""
    venue = _venue(AsyncMock(return_value={"id": "CLOSE-1", "average": 101.0, "filled": 1.0}))
    e, p = _executor(tmp_path, venue)
    p.sl_order_id = p.tp_order_id = "combined-1"
    calls = _v3(e, resting=None)                      # accepted, list unreadable
    e._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 101.0, "fill_qty": 1.0, "failure_stage": "", "fees": 0.0})
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert flatten_outcome(msg) == "closed"
    assert calls == ["combined-1", "combined-1"], "the cancel, then the sweep's re-cancel"
    venue.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cancel_nobody_could_verify_does_not_leave_a_green_tick(tmp_path):
    """The venue answered the cancel with a non-terminal status and the
    follow-up read failed. Nobody saw the order go, so the record must not
    keep naming it: unverified is not protected."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(return_value={"status": "open"}))
    venue.fetch_order = AsyncMock(side_effect=RuntimeError("read failed"))
    e, p = _executor(tmp_path, venue)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg
    assert p.sl_order_id is None and p.tp_order_id is None


@pytest.mark.asyncio
async def test_only_a_stop_the_venue_says_is_still_there_keeps_its_id(tmp_path):
    """The one verdict that keeps the id: the venue answered and the order is
    still resting. The record naming it is then true."""
    venue = _venue(AsyncMock(side_effect=RuntimeError("venue 5xx")),
                   cancel_order=AsyncMock(return_value={"status": "open"}))
    venue.fetch_order = AsyncMock(return_value={"id": "sl-1", "status": "open"})
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


def _refusals():
    import aiohttp
    import ccxt

    never_connected = ccxt.ExchangeNotAvailable("bitget POST /order")
    never_connected.__cause__ = aiohttp.ClientConnectorError(
        SimpleNamespace(host="api.bitget.com", port=443, ssl=None),
        OSError(111, "Connection refused"))
    return [
        ccxt.RateLimitExceeded('bitget {"code":"1001","msg":"too frequent"}'),
        ccxt.DDoSProtection("bitget 429 Too Many Requests"),
        ccxt.OnMaintenance("bitget maintenance"),
        ccxt.InvalidNonce("bitget bad timestamp"),
        never_connected,
    ]


def _unknown_sends():
    import aiohttp
    import ccxt

    dropped = ccxt.ExchangeNotAvailable("bitget POST /order")
    dropped.__cause__ = aiohttp.ServerDisconnectedError()
    reset = ccxt.ExchangeNotAvailable("bitget POST /order")
    reset.__cause__ = aiohttp.ClientOSError(104, "Connection reset by peer")
    # A 5xx: the request REACHED a server that then failed. Whether the order
    # reached the book is exactly what nobody knows, and ccxt raises it with
    # no aiohttp cause. A first draft called this a refusal, which is a
    # verdict from no reading.
    http_5xx = ccxt.ExchangeNotAvailable("bitget 503 Service Unavailable")
    # The venue was answering and the body was cut mid-transfer.
    cut_body = ccxt.ExchangeError("bitget POST /order")
    cut_body.__cause__ = aiohttp.ClientPayloadError("response payload is not completed")
    return [ccxt.RequestTimeout("timed out"), dropped, reset, http_5xx, cut_body]


def test_a_refusal_is_not_an_unknown_send():
    """ccxt files rate limits, maintenance and a bad nonce under NetworkError
    beside the timeout. Those are the venue ANSWERING no. Unknown is anything
    that happened once the request was on the wire: a timeout, a connection
    dropped after the send, a body cut mid-transfer, and a 5xx — which says
    the request reached a server that then failed."""
    for exc in _refusals():
        assert send_outcome_unknown(exc) is False, type(exc).__name__
    for exc in _unknown_sends():
        assert send_outcome_unknown(exc) is True, type(exc).__name__


@pytest.mark.asyncio
async def test_a_rate_limit_on_the_send_is_a_rejected_close(tmp_path):
    """The cancel pass has removed the stops; a 429 on the close order means
    the order never reached the book. "CLOSE NOT CONFIRMED" here told every
    guard to stand down on a live, stop-less position — during the rate-limit
    storms in which closes fail most. It is a failed close: the guards place
    the stop. A 5xx is NOT in this list — see the test below."""
    for exc in _refusals():
        venue = _venue(AsyncMock(side_effect=exc))
        e, p = _executor(tmp_path, venue)
        msg = await e.close_position("T1", reason="leverage_overshoot")
        assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed", (type(exc).__name__, msg)
        assert "may have reached the venue" not in msg
        assert p.sl_order_id is None and p.tp_order_id is None, "the cancel pass ran"


@pytest.mark.asyncio
async def test_a_connection_dropped_after_the_send_is_not_a_rejection(tmp_path):
    """The server disconnected, the socket reset, the body was cut, or a
    server answered 5xx — all once the request was on the wire: the venue may
    have filled it. Same reading as the timeout."""
    for exc in _unknown_sends():
        venue = _venue(AsyncMock(side_effect=exc))
        e, p = _executor(tmp_path, venue)
        msg = await e.close_position("T1", reason="leverage_overshoot")
        assert "CLOSE NOT CONFIRMED" in msg and "may have reached the venue" in msg, (
            type(exc).__name__, msg)
        assert flatten_outcome(msg) == "kept_open"


@pytest.mark.asyncio
async def test_a_flash_close_that_applied_nothing_is_not_a_flat_book(tmp_path):
    """`all([])` is True: a 00000 envelope with an EMPTY item list applied no
    close, on a position the book had just shown as present."""
    venue = _venue(AsyncMock(side_effect=Exception(
        'bitget {"code":"25227","msg":"No position available to close"}')))
    venue.fetch_positions = AsyncMock(return_value=[{"contracts": 1.0}])
    e, p = _executor(tmp_path, venue)
    e._flash_close_position = AsyncMock(return_value=_flash([]))
    e._handle_already_closed_position = AsyncMock(return_value=None)
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSE FAILED" in msg and flatten_outcome(msg) == "failed", msg
    e._handle_already_closed_position.assert_not_awaited()


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
    e._last_close_data = {"trade_id": "T0", "symbol": "BTC/USDT", "pnl_usd": 9.0}   # an EARLIER close
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSED" in msg and "booked" in msg and "could not be rendered" in msg, msg
    assert flatten_outcome(msg) == "closed"
    assert "The ledger row is written" in msg, "the row IS there — say so as a reading"
    assert p.status == "closed" and "T1" not in e._positions
    assert "card renderer down" not in msg
    assert close_card_is_wrong(msg), "no card was built for this close; the renderers must not use the slot"
    assert e._last_close_data is None, "the slot held an earlier close of the same symbol"


@pytest.mark.asyncio
async def test_a_close_booked_whose_ledger_row_is_missing_says_so(tmp_path):
    """The record says closed, the ledger does not have the row: "The ledger
    row is written" was asserted, not read. It is read now."""
    venue = _venue(AsyncMock(return_value={"id": "CLOSE-1", "average": 101.0, "filled": 1.0}))
    e, p = _executor(tmp_path, venue)
    e._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 101.0, "fill_qty": 1.0, "failure_stage": "", "fees": 0.0})
    e._append_closed_trade = lambda pos: (_ for _ in ()).throw(RuntimeError("ledger disk full"))
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSED" in msg and "booked" in msg and flatten_outcome(msg) == "closed", msg
    assert "could NOT be confirmed" in msg and "The ledger row is written" not in msg, msg
    assert "ledger disk full" not in msg


@pytest.mark.asyncio
async def test_a_ledger_write_that_failed_is_not_a_written_row(tmp_path):
    """The comment names two ways the row can be missing — the dedup skip and
    a swallowed write failure — and reading the IN-MEMORY list caught only the
    first: a disk-full write leaves the row in memory and the sentence still
    claimed it."""
    venue = _venue(AsyncMock(return_value={"id": "CLOSE-1", "average": 101.0, "filled": 1.0}))
    e, p = _executor(tmp_path, venue)
    e._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 101.0, "fill_qty": 1.0, "failure_stage": "", "fees": 0.0})
    e._save_closed_trades = lambda: False          # the atomic write did not land
    e._fire_position_closed = lambda pos: (_ for _ in ()).throw(RuntimeError("card renderer down"))
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert "CLOSED" in msg and "booked" in msg and flatten_outcome(msg) == "closed", msg
    assert "could NOT be confirmed" in msg and "The ledger row is written" not in msg, msg


@pytest.mark.asyncio
async def test_a_completed_close_stamps_the_slot_with_its_trade_id(tmp_path):
    """The close slot is last-write-wins and was matched on the symbol alone;
    a reader that knows which close it asked about can now refuse an earlier
    close of the same symbol."""
    venue = _venue(AsyncMock(return_value={"id": "CLOSE-1", "average": 101.0, "filled": 1.0}))
    e, p = _executor(tmp_path, venue)
    e._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 101.0, "fill_qty": 1.0, "failure_stage": "", "fees": 0.0})
    msg = await e.close_position("T1", reason="leverage_overshoot")
    assert flatten_outcome(msg) == "closed" and not close_card_is_wrong(msg)
    assert e._last_close_data and e._last_close_data["trade_id"] == "T1"


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


# ── the strategy channel itself, not a stub of it ────────────────────────────

class _FakeV3:
    """Records what would go on the wire and answers what the test says."""

    def __init__(self, post=None, get=None):
        self.calls: list = []
        self._post, self._get = post, get
        self.has_credentials = True

    def request(self, method, path, body_dict=None, timeout=10):
        self.calls.append((method, path, body_dict))
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def get(self, path, timeout=10):
        self.calls.append(("GET", path, None))
        if isinstance(self._get, Exception):
            raise self._get
        return self._get


def _client(monkeypatch, fake):
    import bot.core.bitget_v3_client as mod
    monkeypatch.setattr(mod.BitgetV3Client, "for_account",
                        classmethod(lambda cls, creds: fake))
    return fake


def test_the_strategy_cancel_addresses_the_strategy_table(tmp_path, monkeypatch):
    """Every test above stubs these two helpers, so nothing checked the
    endpoint, the body or the query — and the whole point of the change is
    WHICH table the cancel reaches. One word apart from cancel-order, which
    is the bug it fixes."""
    e = LiveExecutor(state_dir=str(tmp_path))
    fake = _client(monkeypatch, _FakeV3(post={"code": "00000", "data": None}))
    resp = e._v3_strategy_cancel_sync("combined-1")
    assert resp == {"code": "00000", "data": None}
    assert fake.calls == [("POST", "/api/v3/trade/cancel-strategy-order",
                           {"orderId": "combined-1"})]


def test_the_strategy_read_back_asks_for_unfilled_tpsl_orders(tmp_path, monkeypatch):
    e = LiveExecutor(state_dir=str(tmp_path))
    fake = _client(monkeypatch, _FakeV3(get={"code": "00000", "data": []}))
    assert e._v3_strategy_order_resting_sync("combined-1") is False
    (method, path, _), = fake.calls
    assert method == "GET"
    assert path.startswith("/api/v3/trade/unfilled-strategy-orders")
    assert "category=USDT-FUTURES" in path and "type=tpsl" in path


@pytest.mark.parametrize("payload, expected", [
    ({"code": "00000", "data": [{"orderId": "combined-1", "status": "pending"}]}, True),
    ({"code": "00000", "data": [{"orderId": "combined-1", "status": "submitting"}]}, True),
    ({"code": "00000", "data": [{"orderId": "combined-1", "status": "cancelled"}]}, False),
    ({"code": "00000", "data": [{"orderId": "combined-1", "status": "success"}]}, False),
    ({"code": "00000", "data": [{"orderId": "combined-1", "status": "failed"}]}, False),
    ({"code": "00000", "data": [{"orderId": "other", "status": "pending"}]}, False),
    ({"code": "00000", "data": {"list": [{"orderId": "combined-1", "status": "pending"}]}}, True),
    ({"code": "00000", "data": []}, False),
    # unreadable — and each of these MUST stay None, because False would clear
    # a live stop's id off the record and True would keep a dead one
    ({"code": "40001", "msg": "bad request"}, None),
    ({"code": "00000"}, None),
    ({"code": "00000", "data": "not a list"}, None),
    ("not a dict", None),
])
def test_the_strategy_list_is_read_three_valued(tmp_path, monkeypatch, payload, expected):
    e = LiveExecutor(state_dir=str(tmp_path))
    _client(monkeypatch, _FakeV3(get=payload))
    assert e._v3_strategy_order_resting_sync("combined-1") is expected, payload


def test_a_request_that_never_completed_is_not_a_venue_answer(tmp_path, monkeypatch):
    """The envelope-shaped error body of an HTTPError is the venue's answer;
    a transport failure with no body is not, and must not be read as one."""
    e = LiveExecutor(state_dir=str(tmp_path))
    _client(monkeypatch, _FakeV3(post=OSError("connection reset")))
    assert e._v3_strategy_cancel_sync("combined-1")["code"] == "TRANSPORT"
    _client(monkeypatch, _FakeV3(get=OSError("connection reset")))
    assert e._v3_strategy_order_resting_sync("combined-1") is None


# ── the promise about the monitor is per position ────────────────────────────

@pytest.mark.asyncio
async def test_the_not_confirmed_answer_promises_a_re_place_only_where_the_monitor_makes_one(tmp_path):
    """The periodic re-place runs only for a position carrying BOTH a stop
    level and a target (`_place_sl_tp` refuses a non-positive leg). The
    answer used to promise it unconditionally; an adopted position with no
    target is price-monitored only and needs a stop placed by hand."""
    import ccxt

    for tp, expect, forbid in ((110.0, "re-places one on its next pass", "will NOT re-place"),
                               (0.0, "will NOT re-place", "re-places one on its next pass")):
        venue = _venue(AsyncMock(side_effect=ccxt.RequestTimeout("timed out")))
        e, p = _executor(tmp_path, venue)
        p.take_profit = tp
        msg = await e.close_position("T1", reason="manual_telegram")
        assert "CLOSE NOT CONFIRMED" in msg and expect in msg and forbid not in msg, (tp, msg)

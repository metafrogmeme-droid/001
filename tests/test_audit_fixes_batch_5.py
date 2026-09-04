"""Batch 5: a close nobody could verify is not a close.

THE FAILURE. `_close_position_inner` cancels the SL and TP, sends a reduceOnly
close, then asks `_verify_position_closed` what happened. That function ran
`_verify_order_fill` and, when the fill came back unconfirmed (rate-limited,
rejected, or three raised attempts), RETURNED IMMEDIATELY — so step 2, the
`fetch_positions` read that is the only thing which measures `remaining_qty`,
never ran. The caller's residual guard fires on

    (not close_confirmed) and remaining_qty > 0

and `remaining_qty` was 0 because nothing had measured it, not because nothing
was there. So the guard was skipped, the trade booked at $0.00, and
`_save_positions` pruned the record — leaving a live, leveraged position
untracked and with no stop, seconds after this same function cancelled the one
it had. A later real close was then swallowed by `_is_duplicate_close_booking`.

Compounding it, the `except` around that same `fetch_positions` set
`confirmed = True` — "trusting order fill" — so the close card printed
"Verified: CONFIRMED" from a thrown read, and on the path above there was no
confirmed fill to trust either.

THE ASYMMETRY THE FIX RESTS ON. Keeping a position that DID close is
recoverable: `reconcile_positions()` runs every tick, finds no exchange
position, and books it from the venue's own history. Booking a close that did
NOT happen is not recoverable. The pending-cancel path in the same file already
resolves the identical question the same way, and says so in its own comment.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor, LivePosition


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path):
    with patch.object(live_executor_mod, "_POSITIONS_FILE",
                      str(tmp_path / "live_positions.json")), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE",
                         str(tmp_path / "closed_trades.json")):
        yield


def _mock_exchange() -> AsyncMock:
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": 100_000.0})
    ex.create_order = AsyncMock(return_value={
        "id": "CLOSE-1", "average": 97_000.0, "filled": 0.0002,
        "cost": 19.4, "status": "filled"})
    ex.cancel_order = AsyncMock(return_value=None)
    ex.fetch_open_orders = AsyncMock(return_value=[])
    ex.fetch_my_trades = AsyncMock(return_value=[])
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.close = AsyncMock()
    return ex


def _executor():
    ex = LiveExecutor()
    ex._exchange = _mock_exchange()
    return ex, ex._exchange


def _seed(executor, tid="T-UNV-1", qty=0.0002):
    executor._positions[tid] = LivePosition(
        trade_id=tid, symbol="BTC/USDT", direction="LONG",
        entry_price=100_000.0, quantity=qty, cost_usd=20.0,
        stop_loss=98_000.0, take_profit=105_000.0,
        status="open", sl_order_id="SL-OLD", tp_order_id="TP-OLD")
    return tid


# ── _verify_position_closed: the book is the authority ──────────────────

def _verifier(executor, *, fill_confirmed, positions=None, positions_raise=False):
    executor._verify_order_fill = AsyncMock(return_value={
        "confirmed": fill_confirmed,
        "fill_price": 97_000.0 if fill_confirmed else 0.0,
        "fill_qty": 0.0002 if fill_confirmed else 0.0,
        "fees": 0.0,
        "status": "closed" if fill_confirmed else "open",
        "failure_stage": "" if fill_confirmed else "post_check_unconfirmed",
        "raw": {},
    })
    if positions_raise:
        executor._exchange.fetch_positions = AsyncMock(side_effect=RuntimeError("429"))
    else:
        executor._exchange.fetch_positions = AsyncMock(return_value=positions or [])
    return executor._exchange


@pytest.mark.asyncio
async def test_an_unconfirmed_fill_no_longer_skips_the_book_read():
    # THE BUG. The fill could not be confirmed, but the venue's book shows the
    # position gone — so the close DID happen and we can say so.
    ex, _ = _executor()
    exchange = _verifier(ex, fill_confirmed=False, positions=[])
    with patch("asyncio.sleep", new=AsyncMock()):
        r = await ex._verify_position_closed(exchange, "BTC/USDT", "LONG", "CLOSE-1")
    exchange.fetch_positions.assert_awaited()
    assert r["confirmed"] is True
    assert r["failure_stage"] == ""


@pytest.mark.asyncio
async def test_an_unconfirmed_fill_with_the_position_still_there_reports_the_residual():
    ex, _ = _executor()
    exchange = _verifier(ex, fill_confirmed=False, positions=[
        {"symbol": "BTC/USDT", "side": "long", "contracts": 0.0002}])
    with patch("asyncio.sleep", new=AsyncMock()):
        r = await ex._verify_position_closed(exchange, "BTC/USDT", "LONG", "CLOSE-1")
    assert r["confirmed"] is False
    assert r["remaining_qty"] == pytest.approx(0.0002)
    assert r["failure_stage"] == "position_still_open"


@pytest.mark.asyncio
async def test_a_thrown_book_read_is_book_unread_not_confirmed():
    # "Trusting order fill" printed "Verified: CONFIRMED" from an exception.
    ex, _ = _executor()
    exchange = _verifier(ex, fill_confirmed=True, positions_raise=True)
    with patch("asyncio.sleep", new=AsyncMock()):
        r = await ex._verify_position_closed(exchange, "BTC/USDT", "LONG", "CLOSE-1")
    assert r["confirmed"] is False
    assert r["failure_stage"] == "book_unread"


@pytest.mark.asyncio
async def test_neither_read_succeeding_is_also_book_unread():
    ex, _ = _executor()
    exchange = _verifier(ex, fill_confirmed=False, positions_raise=True)
    with patch("asyncio.sleep", new=AsyncMock()):
        r = await ex._verify_position_closed(exchange, "BTC/USDT", "LONG", "CLOSE-1")
    assert r["confirmed"] is False
    assert r["failure_stage"] == "book_unread"


@pytest.mark.asyncio
async def test_the_control_a_clean_close_still_confirms():
    ex, _ = _executor()
    exchange = _verifier(ex, fill_confirmed=True, positions=[])
    with patch("asyncio.sleep", new=AsyncMock()):
        r = await ex._verify_position_closed(exchange, "BTC/USDT", "LONG", "CLOSE-1")
    assert r["confirmed"] is True
    assert r["failure_stage"] == ""
    assert r["fill_qty"] == pytest.approx(0.0002)


# ── the caller: an unverifiable close is not booked ─────────────────────

def _unverifiable(stage="book_unread"):
    return {"confirmed": False, "fill_price": 0.0, "fill_qty": 0.0,
            "fees": 0.0, "remaining_qty": 0.0, "failure_stage": stage}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["book_unread", "post_check_unconfirmed", "order_cancelled"])
async def test_an_unverifiable_close_keeps_the_position_and_books_nothing(stage):
    ex, _ = _executor()
    tid = _seed(ex, qty=0.0002)
    ex._place_sl_tp = AsyncMock(return_value=("SL-NEW", "TP-NEW"))
    ex._verify_position_closed = AsyncMock(return_value=_unverifiable(stage))

    msg = await ex.close_position(tid, reason="manual")

    assert tid in ex._positions, "an unverified close must not drop the record"
    pos = ex._positions[tid]
    assert pos.status == "open"
    # The size is UNKNOWN, so it must be left alone — never written to 0.0.
    assert pos.quantity == pytest.approx(0.0002)
    assert all(t.trade_id != tid for t in ex._closed_trades), \
        "nothing may be booked from a close nobody confirmed"
    assert "NOT CONFIRMED" in (msg or "")


@pytest.mark.asyncio
async def test_an_unverifiable_close_re_places_the_stop_it_cancelled():
    ex, _ = _executor()
    tid = _seed(ex)
    ex._place_sl_tp = AsyncMock(return_value=("SL-NEW", "TP-NEW"))
    ex._verify_position_closed = AsyncMock(return_value=_unverifiable())

    await ex.close_position(tid, reason="manual")

    ex._place_sl_tp.assert_awaited()
    pos = ex._positions[tid]
    assert pos.sl_order_id == "SL-NEW"
    assert getattr(pos, "unprotected", False) is False


@pytest.mark.asyncio
async def test_when_the_stop_cannot_be_re_placed_the_position_is_flagged_unprotected():
    ex, _ = _executor()
    tid = _seed(ex)
    ex._place_sl_tp = AsyncMock(return_value=(None, None))
    ex._verify_position_closed = AsyncMock(return_value=_unverifiable())

    with patch.object(live_executor_mod, "audit") as rec:
        await ex.close_position(tid, reason="manual")

    pos = ex._positions[tid]
    assert getattr(pos, "unprotected", False) is True
    assert [c for c in rec.call_args_list if c.kwargs.get("result") == "UNPROTECTED"]


@pytest.mark.asyncio
async def test_the_unverified_close_is_on_the_audit_trail():
    ex, _ = _executor()
    tid = _seed(ex)
    ex._place_sl_tp = AsyncMock(return_value=("SL-NEW", "TP-NEW"))
    ex._verify_position_closed = AsyncMock(return_value=_unverifiable())

    with patch.object(live_executor_mod, "audit") as rec:
        await ex.close_position(tid, reason="manual")

    unv = [c for c in rec.call_args_list
           if c.kwargs.get("action") == "close_unverified"
           and c.kwargs.get("result") == "UNVERIFIED"]
    assert unv, "an unverified close must be auditable after the fact"
    assert unv[0].kwargs["data"]["stage"] == "book_unread"


@pytest.mark.asyncio
async def test_the_control_a_measured_residual_still_takes_the_residual_path():
    # The pre-existing behaviour must be untouched: a MEASURED remainder still
    # resizes the position and reports as a partial close.
    ex, _ = _executor()
    tid = _seed(ex, qty=0.0002)
    ex._place_sl_tp = AsyncMock(return_value=("SL-RES", "TP-RES"))
    ex._verify_position_closed = AsyncMock(return_value={
        "confirmed": False, "fill_price": 97_000.0, "fill_qty": 0.0001,
        "fees": 0.0, "remaining_qty": 0.0001,
        "failure_stage": "position_still_open"})

    msg = await ex.close_position(tid, reason="manual")

    pos = ex._positions[tid]
    assert pos.status == "open"
    assert pos.quantity == pytest.approx(0.0001), "resized to the MEASURED remainder"
    assert "RESIDUAL REMAINS" in (msg or "")
    assert all(t.trade_id != tid for t in ex._closed_trades)


@pytest.mark.asyncio
async def test_the_control_a_confirmed_close_still_books():
    ex, _ = _executor()
    tid = _seed(ex)
    ex._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 97_000.0, "fill_qty": 0.0002,
        "fees": 0.01, "remaining_qty": 0.0, "failure_stage": ""})

    msg = await ex.close_position(tid, reason="manual")

    assert any(t.trade_id == tid for t in ex._closed_trades), \
        "a verified close must still be booked"
    assert "CLOSED" in (msg or "")

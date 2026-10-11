"""A stop the venue refuses for the position's size is never retried forever.

Live, 11 October, after the venue-sized close shipped: the M/USDT short's stop
was refused with 25610 on every scan ("Order amount does not meet precision
requirements"), and the alert asked the person to place a stop on Bitget by
hand. The unified account's stop names no size (``tpslMode: "full"``): the
venue sized it to the whole position, which was under its 5 USDT minimum order
value, and refused that. No retry could place it, and a stop placed by hand is
the same order.

RC-AUD-001's end state is protected, closed, or URGENT, and the post-fill
ladder already flattens when a stop will not place. The per-scan retry never
did. Now a 25610 stop refusal flattens a bot-opened position through
`close_position` (whose flash-close fallback closes a size the venue refuses),
at most once per alert interval. An adopted or reclaimed position is never
auto-closed (RC-AUD-022), and its alert says to close it, not to place the
refused stop.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import venue_code

UTC = timezone.utc
SIZE_REFUSAL = "25610: Order amount does not meet precision requirements"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _executor(refusal: str, close_answer: str):
    """Every stop placement is refused with ``refusal``, recorded as the placer records it."""
    exe = LiveExecutor()
    exe._is_uta = True

    async def _refused(*_a, **_k):
        exe._note_sltp_error("M/USDT", refusal)
        return None, None

    exe._place_sl_tp = AsyncMock(side_effect=_refused)
    exe._run_partial_tp = AsyncMock(return_value=None)
    exe._save_positions = MagicMock(return_value=None)
    exe._record_warning = MagicMock(return_value=None)
    exe.close_position = AsyncMock(return_value=close_answer)
    exe.adopt_exchange_positions = AsyncMock(return_value=[])
    exe._last_exchange_sync = time.time()
    return exe


def _pos(origin: str = "executed"):
    # A short inside its band: price 0.99, stop 0.9983, target 0.97.
    p = LivePosition(
        trade_id="TI-e9db9a8b", symbol="M/USDT", direction="SHORT",
        entry_price=0.9927, quantity=3.0, cost_usd=0.3,
        stop_loss=0.9983, take_profit=0.9700, status="open")
    p.opened_at = datetime.now(UTC) - timedelta(minutes=30)
    p.origin = origin
    return p


def _check(exe, pos, price=0.99):
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": price})
    exe._get_exchange = AsyncMock(return_value=ex)
    exe._positions = {pos.trade_id: pos}
    return _run(exe.check_positions())


def _alert(msgs):
    return next((m for m in msgs if "UNPROTECTED POSITION" in m), None)


def test_the_placer_s_record_of_a_refusal_reads_as_its_code():
    assert venue_code(SIZE_REFUSAL) == "25610"
    assert venue_code('bitget {"code":"40019","requestTime":1791725610000}') == "40019", (
        "the field, not five digits of the time")
    assert venue_code("Order amount does not meet precision requirements") is None


def test_a_bot_opened_position_whose_stop_is_refused_for_size_is_flattened():
    exe = _executor(SIZE_REFUSAL, "✅ CLOSED SHORT M/USDT (flash close)")
    pos = _pos()
    msgs = _check(exe, pos)
    exe.close_position.assert_awaited_once_with("TI-e9db9a8b", reason="sl_placement_failed")
    assert "✅ CLOSED SHORT M/USDT (flash close)" in msgs
    assert _alert(msgs) is None, "a closed position is not re-alerted as unprotected"


def test_a_failed_flatten_is_alerted_with_the_right_door_and_retried_on_the_interval():
    exe = _executor(SIZE_REFUSAL, "CLOSE FAILED for TI-e9db9a8b: ExchangeError")
    pos = _pos()
    first = _check(exe, pos)
    assert exe.close_position.await_count == 1
    alert = _alert(first)
    assert alert and "close it on Bitget manually" in alert, alert
    assert "place a stop on Bitget manually" not in alert, "that stop is the refused one"

    _check(exe, pos)
    assert exe.close_position.await_count == 1, "not a close on every scan"

    pos._size_flatten_at -= CONFIG.execution.unprotected_alert_interval_s + 1
    _check(exe, pos)
    assert exe.close_position.await_count == 2, "tried again once the interval passed"


def test_an_adopted_position_is_never_auto_closed_and_is_told_to_close():
    exe = _executor(SIZE_REFUSAL, "✅ CLOSED")
    pos = _pos(origin="adopted")
    msgs = _check(exe, pos)
    exe.close_position.assert_not_awaited()
    alert = _alert(msgs)
    assert alert and "close it on Bitget manually" in alert, alert


def test_another_refusal_is_retried_and_asks_for_a_stop():
    exe = _executor("40019: Parameter verification exception", "✅ CLOSED")
    pos = _pos()
    msgs = _check(exe, pos)
    exe.close_position.assert_not_awaited()
    alert = _alert(msgs)
    assert alert and "place a stop on Bitget manually" in alert, alert

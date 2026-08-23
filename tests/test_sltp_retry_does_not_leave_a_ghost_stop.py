"""A failed SL/TP re-place must not leave the position believing it has a stop.

`_place_sl_tp` CANCELS every existing plan order before placing new ones
(live_executor.py, "GETCLAW: Check and cancel existing plan orders") — correct
on its own, it prevents duplicate stops causing double-closes.

The retry that calls it fires when EITHER leg is missing:

    if (not pos.sl_order_id or not pos.tp_order_id) and ...

So a position with a live SL and a missing TP takes this path, and the first
thing that happens is its working stop being cancelled. If the replacement is
then refused — precision, min-distance, 45115, an already-breached trigger —
`_place_sl_tp` returns (None, None) and:

    if sl_id or tp_id:          # False, so nothing below runs

`pos.sl_order_id` is left naming the order that was just cancelled. Every
signal downstream then reads as protected:

  * the grace-window skip trusts it outright —
    `if pos.sl_order_id: continue  # protected by exchange SL`
  * the stale-ticker skip logs "exchange stop still active" and skips the
    local check, and the comment beneath it describes the XPD incident where
    exactly that kept the backstop from running for 40+ minutes
  * the `unprotected` alarm is actively CLEARED, because clearing is gated on
    `pos.sl_order_id` being truthy

and the only trace of the failure is a `logger.debug`.

NOT a permanently naked position: the static SL/TP check runs unconditionally
once past grace, so the local backstop still closes on breach. The cost is the
grace window, every stale-ticker cycle, and an operator-facing marker that
says protected while the venue holds nothing.

The fix is to make the ids tell the truth: a cancelled stop that could not be
replaced is an absent stop.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


def _pos(trade_id="TI-ghost", symbol="BTC/USDT:USDT", sl_id="SL1", tp_id=""):
    p = LivePosition(
        trade_id=trade_id, symbol=symbol, direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=95.0, take_profit=120.0, leverage=5, status="open",
        opened_at=datetime.now(UTC) - timedelta(hours=2),
    )
    p.sl_order_id = sl_id
    p.tp_order_id = tp_id
    return p


def _executor(place_result, price=100.0):
    ex = LiveExecutor()
    mock = AsyncMock()
    mock.fetch_ticker = AsyncMock(return_value={"last": price})
    ex._exchange = mock
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = __import__("time").time()
    ex.close_position = AsyncMock(return_value="CLOSED")
    ex._save_positions = lambda *a, **k: None
    # The real one cancels every plan order first, then places. Both outcomes
    # are driven from here.
    ex._place_sl_tp = AsyncMock(return_value=place_result)
    return ex


class TestAFailedRePlaceTellsTheTruth:
    @pytest.mark.asyncio
    async def test_a_cancelled_stop_that_could_not_be_replaced_is_not_still_named(self):
        # The live SL is cancelled to replace a missing TP, and the placement
        # is refused. `sl_order_id` must not keep naming the dead order.
        ex = _executor(place_result=(None, None))
        pos = _pos(sl_id="SL1", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert ex._place_sl_tp.await_count == 1, "the retry did not run"
        assert not pos.sl_order_id, (
            "sl_order_id still names the order _place_sl_tp cancelled — the "
            "grace skip, the stale-ticker skip and the unprotected marker all "
            "read this as an exchange stop being in place")

    @pytest.mark.asyncio
    async def test_it_is_marked_unprotected(self):
        ex = _executor(place_result=(None, None))
        pos = _pos(sl_id="SL1", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert getattr(pos, "unprotected", False) is True, (
            "the position carries no unprotected marker after its stop was "
            "cancelled and not replaced")

    @pytest.mark.asyncio
    async def test_losing_a_working_stop_is_reported(self, monkeypatch):
        # It used to be one `logger.debug` line. The CAUSE — a stop that
        # existed and was cancelled — is distinct from the standing state and
        # is recorded once, where an operator sees it.
        # `audit()` emits through `channel.log(level, ...)`, not `.warning()`
        # — patching the convenience method catches nothing.
        import bot.core.live_executor as le
        seen = []
        monkeypatch.setattr(le.trade_log, "log",
                            lambda lvl, msg, *a, **k: seen.append((lvl, str(msg))),
                            raising=False)
        ex = _executor(place_result=(None, None))
        ex._positions["TI-ghost"] = _pos(sl_id="SL1", tp_id="")

        await ex.check_positions()

        import logging
        hits = [(lvl, m) for lvl, m in seen if "NO exchange stop" in m]
        assert hits, f"losing a working stop left no operator-visible trace. saw: {seen}"
        assert hits[0][0] >= logging.WARNING, (
            "reported below WARNING — it used to be logger.debug, which is why "
            "nobody saw it")

    @pytest.mark.asyncio
    async def test_it_unlocks_the_escalation_that_could_never_fire(self, monkeypatch):
        """The alarm written for exactly this condition, and unreachable in it.

        `if (unprotected_escalation_enabled and not pos.sl_order_id ...)` logs
        CRITICAL "UNPROTECTED POSITION ... Place a stop on Bitget manually."
        While the cancelled order was still named, `not pos.sl_order_id` was
        False and the escalation could not fire in the one case it describes.
        """
        import logging as _logging

        import bot.core.live_executor as le
        crits = []
        monkeypatch.setattr(le.logger, "critical",
                            lambda *a, **k: crits.append(str(a[0]) if a else ""),
                            raising=False)
        monkeypatch.setattr(le.trade_log, "log", lambda *a, **k: None,
                            raising=False)
        assert _logging  # keep the import meaningful to linters

        ex = _executor(place_result=(None, None))
        ex._positions["TI-ghost"] = _pos(sl_id="SL1", tp_id="")

        await ex.check_positions()

        assert any("UNPROTECTED POSITION" in c for c in crits), (
            "the CRITICAL escalation still cannot fire — it is gated on "
            "`not pos.sl_order_id`, which the ghost id kept truthy")


class TestItDoesNotOverreact:
    @pytest.mark.asyncio
    async def test_a_SUCCESSFUL_replacement_keeps_the_new_ids(self):
        ex = _executor(place_result=("SL2", "TP2"))
        pos = _pos(sl_id="", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert pos.sl_order_id == "SL2"
        assert pos.tp_order_id == "TP2"
        assert not getattr(pos, "unprotected", False)

    @pytest.mark.asyncio
    async def test_a_PARTIAL_success_keeps_the_stop_it_did_get(self):
        # SL placed, TP refused. The stop is real; the position is protected
        # and must NOT be flagged unprotected just because the TP is missing.
        ex = _executor(place_result=("SL2", None))
        pos = _pos(sl_id="", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert pos.sl_order_id == "SL2"
        assert not getattr(pos, "unprotected", False), (
            "a missing TAKE-PROFIT is not an unprotected position — the stop "
            "is what protects it")

    @pytest.mark.asyncio
    async def test_a_position_that_never_had_a_stop_is_unchanged_by_this(self):
        # No stop before, none after: already the unprotected path's business,
        # and this fix must not invent a state transition for it.
        ex = _executor(place_result=(None, None))
        pos = _pos(sl_id="", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert not pos.sl_order_id
        assert getattr(pos, "unprotected", False) is True

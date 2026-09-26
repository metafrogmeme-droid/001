"""A close that is CANCELLED part-way must not leave its position "closing".

`close_position` sets the row "closing", saves, and then awaits the venue:
the stop and target cancels, the market close, the reads after it. Every
revert in `_close_position_inner` sits under ``except Exception``, and
``asyncio.CancelledError`` is a BaseException, so a close cancelled mid-way
left the row "closing" for the rest of the process. Driven through the real
positions phase with the close order parked at the per-phase cap:

    both stop legs cancelled on the venue, the close order in flight,
    the cap fires -> status "closing", out of open_positions, skipped by the
    monitor and by reconcile, refused by close_position ("already closing"),
    with no stop resting on the venue.

The cancel paths are ordinary: the per-phase cap and the whole-tick cap both
cancel the monitor, and a maintenance cap cancels a web flatten.

The row now becomes what the loader already makes of a row it finds
"closing" at startup: kept OPEN and held for reconcile when the close reached
the venue, simply open again when it did not, and back to "pending_fill" when
a limit cancel was cut off. The stops the cancel pass removed are cleared
from the record and a cleared stop marks the position unprotected.

Every case is driven through the real `close_position` with an AsyncMock
venue; the phase case through the real `RuneClawEngine._phase`.
"""

from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.compat import UTC
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition

UID = "7"
CREDS = {"api_key": "a", "api_secret": "b", "passphrase": "c"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


def _ex():
    return LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")


def _pos(status="open", **kw):
    p = LivePosition(trade_id="T1", symbol="BTC/USDT", direction="LONG",
                     entry_price=100.0, quantity=1.0, cost_usd=20.0,
                     stop_loss=95.0, take_profit=110.0, leverage=5,
                     status=status, sl_order_id="SL1", tp_order_id="TP1",
                     opened_at=datetime.now(UTC) - timedelta(hours=1))
    for k, v in kw.items():
        setattr(p, k, v)
    return p


class _Venue:
    """An AsyncMock venue whose chosen call parks until released."""

    def __init__(self, park: str):
        self.park = park
        self.parked = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []
        x = AsyncMock()
        x.fetch_ticker = AsyncMock(return_value={"last": 80.0, "bid": 80.0, "ask": 80.0})
        x.fetch_open_orders = AsyncMock(return_value=[])
        x.fetch_positions = AsyncMock(return_value=[])
        for name, ret in (("cancel_order", {"status": "canceled"}),
                          ("create_order", {"id": "C1"})):
            setattr(x, name, self._wrap(name, ret))
        self.x = x

    def _wrap(self, name, ret):
        async def f(*a, **k):
            self.calls.append(name)
            if name == self.park:
                self.parked.set()
                await self.release.wait()
            return ret
        return f


def _wire(ex, venue):
    ex._get_exchange = AsyncMock(return_value=venue.x)
    return ex


async def _cancel_when_parked(ex, venue, reason="SL HIT"):
    task = asyncio.ensure_future(ex.close_position("T1", reason=reason))
    await asyncio.wait_for(venue.parked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return task


def _on_disk(ex) -> dict:
    return json.loads(Path(ex._positions_file).read_text())["T1"]


class TestTheRowComesBack:
    @pytest.mark.asyncio
    async def test_cancelled_at_the_close_order_is_held_for_reconcile(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v)

        p = ex._positions["T1"]
        assert p.status == "open", "a cancelled close left the row 'closing'"
        assert p in ex.open_positions
        assert ex.awaiting_reconcile("T1"), (
            "the close order may have reached the venue: nothing may send "
            "another close until reconcile has asked it")
        assert (p.sl_order_id, p.tp_order_id) == (None, None), (
            "both legs were cancelled on the venue; an id it no longer holds "
            "reads as protection to every reader")
        assert getattr(p, "unprotected", False) is True
        assert getattr(p, "close_interrupted", False) is True
        row = _on_disk(ex)
        assert row["status"] == "open"
        assert row.get("awaiting_reconcile") is True
        assert row.get("close_interrupted") is True

    @pytest.mark.asyncio
    async def test_the_next_monitor_pass_sends_no_second_close(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)
        await _cancel_when_parked(ex, v)
        sent = v.calls.count("create_order")

        ex._last_exchange_sync = 1e18
        v.park = ""
        await ex.check_positions()

        assert v.calls.count("create_order") == sent, (
            "the local stop check sent a second close for a row whose first "
            "close may already have filled")

    @pytest.mark.asyncio
    async def test_the_leg_whose_cancel_was_in_flight_is_cleared(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="cancel_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v)

        p = ex._positions["T1"]
        assert p.status == "open"
        assert p.sl_order_id is None, (
            "nobody can say whether the stop's cancel landed")
        assert p.tp_order_id == "TP1", "the target's cancel was never sent"
        assert getattr(p, "unprotected", False) is True
        assert ex.awaiting_reconcile("T1")
        assert v.calls.count("create_order") == 0

    @pytest.mark.asyncio
    async def test_cancelled_before_the_venue_is_simply_open_again(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        parked, release = asyncio.Event(), asyncio.Event()

        async def slow_exchange():
            parked.set()
            await release.wait()

        ex._get_exchange = slow_exchange
        task = asyncio.ensure_future(ex.close_position("T1", reason="SL HIT"))
        await asyncio.wait_for(parked.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        p = ex._positions["T1"]
        assert p.status == "open"
        assert (p.sl_order_id, p.tp_order_id) == ("SL1", "TP1")
        assert not ex.awaiting_reconcile("T1"), (
            "nothing reached the venue, so there is nothing to ask it about")
        assert not getattr(p, "unprotected", False)
        assert not getattr(p, "close_interrupted", False)
        row = _on_disk(ex)
        assert row["status"] == "open" and not row.get("awaiting_reconcile")

    @pytest.mark.asyncio
    async def test_a_limit_cancel_cut_off_goes_back_to_pending_fill(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos(status="pending_fill", limit_order_id="L1",
                                   sl_order_id=None, tp_order_id=None)
        v = _Venue(park="cancel_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v, reason="cancel")

        p = ex._positions["T1"]
        assert p.status == "pending_fill", (
            "the pending check re-reads a resting order every tick; 'closing' "
            "is read by nothing")
        assert _on_disk(ex)["status"] == "pending_fill"

    @pytest.mark.asyncio
    async def test_a_caller_cancelled_while_waiting_for_the_lock_touches_nothing(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)
        first = asyncio.ensure_future(ex.close_position("T1", reason="SL HIT"))
        await asyncio.wait_for(v.parked.wait(), timeout=5)
        second = asyncio.ensure_future(ex.close_position("T1", reason="manual"))
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second

        p = ex._positions["T1"]
        assert p.status == "closing", "the close in progress is not the waiter's"
        assert getattr(p, "_close_flight", None) is not None, (
            "the waiter cleared the in-flight close's record")
        assert not first.done()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert p.status == "open"

    @pytest.mark.asyncio
    async def test_the_marker_survives_a_restart(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)
        await _cancel_when_parked(ex, v)

        again = _ex()
        p = again._positions["T1"]
        assert p.status == "open"
        assert again.awaiting_reconcile("T1")
        assert getattr(p, "close_interrupted", False) is True
        assert getattr(p, "unprotected", False) is True


    @pytest.mark.asyncio
    async def test_a_close_order_alone_is_enough_to_hold_it(self, state):
        # No stop and no target rest on the venue, so the cancel pass removes
        # nothing: the close order is the only thing that reached the venue,
        # and it alone may have filled.
        ex = _ex()
        ex._positions["T1"] = _pos(sl_order_id=None, tp_order_id=None)
        v = _Venue(park="create_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v)

        p = ex._positions["T1"]
        assert "cancel_order" not in v.calls
        assert p.status == "open"
        assert ex.awaiting_reconcile("T1"), (
            "the close order was sent and may have filled; nothing may send "
            "another until reconcile has asked the venue")
        assert getattr(p, "close_interrupted", False) is True

    @pytest.mark.asyncio
    async def test_a_leg_the_venue_refused_to_cancel_keeps_its_id(self, state):
        # The leg verdicts are planted: this is about what the flight record
        # does with a verdict, and "live" is the one that keeps an id.
        ex = _ex()
        ex._positions["T1"] = _pos()
        verdicts = {"SL1": ("removed", "cancelled"),
                    "TP1": ("live", "the venue refused the cancel")}

        async def leg(exchange, pos, oid, *, combined):
            return verdicts[oid]

        ex._cancel_stop_leg = leg
        v = _Venue(park="create_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v)

        p = ex._positions["T1"]
        assert p.sl_order_id is None
        assert p.tp_order_id == "TP1", (
            "the venue said the target is still resting and its cancel had "
            "finished; the record naming it is true")
        assert ex.awaiting_reconcile("T1")

    @pytest.mark.asyncio
    async def test_a_limit_that_filled_while_being_cancelled_stays_open(self, state):
        # The cancel finds the order gone and the venue holding the fill, so
        # the close path makes the row "open" and places its stop. A cancel
        # while that placement is in flight is not a cancelled close: the row
        # is a filled position, and putting it back to "pending_fill" would
        # have the pending check read an order that no longer rests.
        ex = _ex()
        ex._positions["T1"] = _pos(status="pending_fill", limit_order_id="L1",
                                   sl_order_id=None, tp_order_id=None)
        x = AsyncMock()
        x.cancel_order = AsyncMock(side_effect=RuntimeError("25204 Order does not exist"))
        x.fetch_positions = AsyncMock(return_value=[
            {"contracts": 1.0, "side": "long", "symbol": "BTC/USDT:USDT"}])
        ex._get_exchange = AsyncMock(return_value=x)
        parked, release = asyncio.Event(), asyncio.Event()

        async def place(*a, **k):
            parked.set()
            await release.wait()
            return ("SL9", "TP9")

        ex._place_sl_tp = place
        task = asyncio.ensure_future(ex.close_position("T1", reason="cancel"))
        await asyncio.wait_for(parked.wait(), timeout=5)
        assert ex._positions["T1"].status == "open"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        p = ex._positions["T1"]
        assert p.status == "open", "a filled position was put back to pending_fill"
        assert not ex.awaiting_reconcile("T1")
        assert not getattr(p, "close_interrupted", False)


class _Log:
    """Every call on the module logger, as (level, text)."""

    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def __getattr__(self, level):
        def record(msg, *args, **kw):
            try:
                text = str(msg) % args if args else str(msg)
            except (TypeError, ValueError):
                text = str(msg)
            self.lines.append((level, text))
        return record


class TestTheInterruptionIsSaid:
    @pytest.mark.asyncio
    async def test_an_audit_row_a_warning_event_and_a_critical_line(self, state, monkeypatch):
        rows: list = []
        monkeypatch.setattr(le, "audit", lambda *a, **k: rows.append(k))
        log = _Log()
        monkeypatch.setattr(le, "logger", log)
        ex = _ex()
        warned: list = []
        ex._record_warning = warned.append
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v)

        mine = [k for k in rows if k.get("action") == "close_interrupted"]
        assert len(mine) == 1 and mine[0]["result"] == "INTERRUPTED", mine
        assert mine[0]["data"]["awaiting_reconcile"] is True
        assert warned == ["close_interrupted"], (
            "the warning-rate breaker was not told a close was cut off")
        said = [(lvl, t) for lvl, t in log.lines if "CLOSE INTERRUPTED" in t]
        assert len(said) == 1 and said[0][0] == "critical", said
        assert "UNPROTECTED" in said[0][1]

    @pytest.mark.asyncio
    async def test_a_fault_while_putting_it_back_does_not_eat_the_cancellation(self, state):
        ex = _ex()

        def refuse(key):
            raise RuntimeError("the breaker could not be told")

        ex._record_warning = refuse
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)

        await _cancel_when_parked(ex, v)   # still a CancelledError

        assert ex._positions["T1"].status == "open"


class TestTheRealPhaseCap:
    @pytest.mark.asyncio
    async def test_the_positions_phase_cap_cancels_the_close_and_the_row_comes_back(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        ex._last_exchange_sync = 1e18
        v = _Venue(park="create_order")
        _wire(ex, v)
        stub = types.SimpleNamespace(_record_phase_duration=lambda *a, **k: None,
                                     _phase_durations={}, _last_phase_timeout=None)
        old = CONFIG.monitoring.tick_phase_timeout_sec
        object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.5)
        try:
            out = await RuneClawEngine._phase(stub, ex.check_positions(),
                                              "positions", fatal=False)
        finally:
            object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", old)

        assert out is None, "the cap fired"
        p = ex._positions["T1"]
        assert p.status == "open" and p in ex.open_positions
        assert ex.awaiting_reconcile("T1")


class TestReconcileSaysSo:
    @staticmethod
    def _flat(ex):
        x = AsyncMock()
        x.fetch_positions = AsyncMock(return_value=[])
        ex._exchange = x
        ex._get_exchange = AsyncMock(return_value=x)
        ex._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 80.0, "pnl": -20.0, "fees": 0.1,
            "reason": "CLOSED (unknown)", "source": "bitget_position_history",
            "pnl_is_net": True})

    @pytest.mark.asyncio
    async def test_an_interrupted_close_found_flat_is_announced(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)
        await _cancel_when_parked(ex, v)

        self._flat(ex)
        messages = await ex.reconcile_positions()

        assert len(messages) == 1 and "RECONCILED" in messages[0], (
            "the cancelled close was announced to nobody; this is its first report")
        assert "T1" not in ex._positions or ex._positions["T1"].status == "closed"

    @pytest.mark.asyncio
    async def test_a_row_recovered_at_startup_stays_quiet(self, state):
        # The existing rule, unchanged: a "closing" row found at startup was
        # probably announced before the process died.
        ex = _ex()
        ex._positions["T1"] = _pos()
        ex._recovered_from_closing.add("T1")
        self._flat(ex)
        assert await ex.reconcile_positions() == []

    @pytest.mark.asyncio
    async def test_the_venue_holding_it_clears_the_marker(self, state):
        ex = _ex()
        ex._positions["T1"] = _pos()
        v = _Venue(park="create_order")
        _wire(ex, v)
        await _cancel_when_parked(ex, v)

        x = AsyncMock()
        x.fetch_positions = AsyncMock(return_value=[
            {"contracts": 1.0, "side": "long", "symbol": "BTC/USDT:USDT",
             "info": {"stopLoss": "0", "takeProfit": "0"}}])
        ex._exchange = x
        ex._get_exchange = AsyncMock(return_value=x)
        await ex.reconcile_positions()

        p = ex._positions["T1"]
        assert not ex.awaiting_reconcile("T1")
        assert not getattr(p, "close_interrupted", False), (
            "the venue answered: the row is an ordinary open position again")
        assert "close_interrupted" not in _on_disk(ex)

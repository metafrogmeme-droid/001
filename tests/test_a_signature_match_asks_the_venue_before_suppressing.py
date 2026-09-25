"""A duplicate-close SIGNATURE is not a venue read, so a match asks the venue.

`_is_duplicate_close_booking` matches a record to an already-booked close by
symbol + direction + entry within 0.05% + a close booked within two hours --
the 2026-07-07 incident, where an adoption sweep minted a second record of one
exchange position. `check_positions` acted on a match alone: it marked the
record closed and pruned it with no venue read at all. A limit re-entry at a
fixed level fills at exactly the old entry, so it matches. Driven on the
unfixed tree, a SOL long re-entered at 150 thirty minutes after a SOL long at
150 was booked closed, with the venue HOLDING it:

    after check_positions: status=closed reason=duplicate_suppressed tracked=False
    after reconcile:       (unchanged; nothing monitors it any more)

-- a live position with no time exits, no ladder and no breach monitoring,
while its docstring said "a real re-entry fills at a different price".

Two halves now. A record OPENED AFTER the booked close is not its duplicate (a
duplicate is minted while the one position is still open). And the sweep
DEFERS a match to reconcile -- the deferral a row recovered from "closing"
already takes -- which asks the venue: flat, and it suppresses there as
before, with no booking; holding it, and the row is real and monitored from
the next tick; unreadable, and it stays held until somebody can read it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _now(*a, **k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _now)


def _booked(now, closed_ago=timedelta(minutes=30)):
    return LivePosition(trade_id="TI-old", symbol="SOL/USDT:USDT", direction="LONG",
                        entry_price=150.0, quantity=10.0, cost_usd=150.0,
                        stop_loss=140.0, take_profit=170.0, leverage=10,
                        opened_at=now - timedelta(hours=1), status="closed",
                        closed_at=now - closed_ago, close_price=145.0, pnl_usd=-50.0)


def _record(now, opened_ago, trade_id="TI-new"):
    return LivePosition(trade_id=trade_id, symbol="SOL/USDT:USDT", direction="LONG",
                        entry_price=150.0, quantity=10.0, cost_usd=150.0,
                        stop_loss=140.0, take_profit=170.0, leverage=10,
                        sl_order_id="sl-2", tp_order_id="tp-2",
                        opened_at=now - opened_ago, status="open", order_type="limit")


HELD_ROW = {"symbol": "SOL/USDT:USDT", "side": "long", "contracts": 10.0,
            "info": {"stopLoss": "140", "takeProfit": "170"}}


def _executor(pos, rows=(), ticker=151.0, closed=None):
    now = datetime.now(UTC)
    ex = LiveExecutor()
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    ex._save_positions = MagicMock()
    ex._save_closed_trades = MagicMock()
    ex._fire_position_closed = MagicMock()
    ex._closed_trades = [closed or _booked(now)]
    ex._positions = {pos.trade_id: pos}
    x = AsyncMock()
    x.fetch_ticker = AsyncMock(return_value={"last": ticker})
    x.fetch_positions = AsyncMock(return_value=list(rows))
    x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(return_value={"data": {"list": []}})
    x.fetch_my_trades = AsyncMock(return_value=[])
    x.fetch_closed_orders = AsyncMock(return_value=[])
    ex._get_exchange = AsyncMock(return_value=x)
    return ex, x


# ── the signature ────────────────────────────────────────────────────────────

class TestTheSignature:
    def test_a_record_opened_after_the_booked_close_is_a_new_position(self):
        now = datetime.now(UTC)
        ex, _x = _executor(_record(now, timedelta(minutes=10)))
        assert ex._is_duplicate_close_booking(ex._positions["TI-new"]) is False

    def test_a_record_opened_before_it_is_still_a_duplicate(self):
        now = datetime.now(UTC)
        ex, _x = _executor(_record(now, timedelta(minutes=50)))
        assert ex._is_duplicate_close_booking(ex._positions["TI-new"]) is True

    def test_opened_at_the_same_instant_is_not_after_it(self):
        now = datetime.now(UTC)
        booked = _booked(now)
        pos = _record(now, timedelta(0))
        pos.opened_at = booked.closed_at
        ex, _x = _executor(pos, closed=booked)
        assert ex._is_duplicate_close_booking(pos) is True

    def test_a_naive_open_time_is_read_as_utc(self):
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=10))
        pos.opened_at = pos.opened_at.replace(tzinfo=None)
        ex, _x = _executor(pos)
        assert ex._is_duplicate_close_booking(pos) is False


# ── the sweep defers; reconcile asks the venue ───────────────────────────────

class TestTheSweepDefers:
    def test_a_match_is_deferred_not_suppressed_and_no_close_is_sent(self):
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=50))
        ex, x = _executor(pos, rows=[HELD_ROW], ticker=130.0)   # through the stop
        ex.close_position = AsyncMock(return_value="closed")
        asyncio.run(ex.check_positions())
        assert pos.status == "open" and pos.close_reason is None
        assert "TI-new" in ex._positions
        assert ex.awaiting_reconcile("TI-new")
        ex.close_position.assert_not_awaited()
        x.fetch_positions.assert_not_awaited()

    def test_it_is_audited_once_not_once_per_tick(self, monkeypatch):
        said = []
        monkeypatch.setattr(le, "audit", lambda log, msg, **kw: said.append(kw))
        now = datetime.now(UTC)
        ex, _x = _executor(_record(now, timedelta(minutes=50)))
        for _ in range(3):
            asyncio.run(ex.check_positions())
        assert [kw["result"] for kw in said if kw.get("action") == "duplicate_close"] == ["DEFERRED"]

    def test_the_venue_flat_suppresses_it_without_a_booking(self):
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=50))
        ex, _x = _executor(pos, rows=[])
        asyncio.run(ex.check_positions())
        asyncio.run(ex.reconcile_positions())
        assert pos.status == "closed" and pos.close_reason == "duplicate_suppressed"
        assert len(ex._closed_trades) == 1, "the duplicate was booked"
        ex._fire_position_closed.assert_not_called()
        assert not ex.awaiting_reconcile("TI-new")

    def test_the_venue_holding_it_makes_it_real_and_monitored(self, monkeypatch):
        said = []
        real = le.audit
        monkeypatch.setattr(le, "audit",
                            lambda log, msg, **kw: (said.append(kw), real(log, msg, **kw)))
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=50))
        ex, x = _executor(pos, rows=[HELD_ROW])
        asyncio.run(ex.check_positions())
        asyncio.run(ex.reconcile_positions())
        assert pos.status == "open" and "TI-new" in ex._positions
        assert not ex.awaiting_reconcile("TI-new")
        assert any(kw.get("result") == "VENUE_HOLDS" for kw in said)
        # The next tick monitors it: a price through the stop closes it.
        x.fetch_ticker.return_value = {"last": 130.0}
        ex.close_position = AsyncMock(return_value="closed")
        asyncio.run(ex.check_positions())
        ex.close_position.assert_awaited_once()
        assert not ex.awaiting_reconcile("TI-new")

    def test_an_unreadable_book_keeps_it_held(self):
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=50))
        ex, _x = _executor(pos, rows=[{"symbol": "SOL/USDT:USDT", "side": "long",
                                       "contracts": None}])
        asyncio.run(ex.check_positions())
        asyncio.run(ex.reconcile_positions())
        assert pos.status == "open"
        assert ex.awaiting_reconcile("TI-new"), (
            "a possible duplicate was handed back to local monitoring on a "
            "reading nobody made")
        assert getattr(pos, "_duplicate_signature", None) == "deferred"

    def test_a_restart_recovered_row_is_still_released_by_an_unreadable_book(self):
        """The hold is the signature's own. A row recovered from "closing"
        keeps the release it always had; nothing about that path moved."""
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=10))     # no signature match
        ex, _x = _executor(pos, rows=[{"symbol": "SOL/USDT:USDT", "side": "long",
                                       "contracts": None}])
        ex._recovered_from_closing.add("TI-new")
        asyncio.run(ex.reconcile_positions())
        assert not ex.awaiting_reconcile("TI-new")


class TestARealReentryIsBooked:
    def test_its_genuine_close_is_booked_not_suppressed(self):
        """Opened after the old booking: the venue going flat is ITS close."""
        now = datetime.now(UTC)
        pos = _record(now, timedelta(minutes=10))
        ex, x = _executor(pos, rows=[])
        x.fetch_my_trades = AsyncMock(return_value=[{
            "order": "sl-2", "price": 140.0, "side": "sell",
            "timestamp": int(now.timestamp() * 1000),
            "info": {"profit": "-100"}}])
        asyncio.run(ex.check_positions())
        assert not ex.awaiting_reconcile("TI-new")
        asyncio.run(ex.reconcile_positions())
        assert pos.status == "closed" and pos.close_reason != "duplicate_suppressed"
        assert len(ex._closed_trades) == 2
        ex._fire_position_closed.assert_called_once_with(pos)

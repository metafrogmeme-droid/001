"""A re-place must leave the record naming what rests on the venue.

WHEN THIS FILE WAS WRITTEN `_place_sl_tp` CANCELLED every existing plan order
BEFORE placing new ones. The retry that calls it fires when EITHER leg is
missing, so a position with a live SL and a missing TP had its working stop
cancelled first, and a refused replacement left `pos.sl_order_id` naming the
cancelled order: every signal downstream read "protected". The fix then was to
clear the id and mark the position unprotected.

The fix that sent the plan listing to the plan table reversed the placer's order: it places first and
cancels this side's resting plan orders only once a NEW stop rests. That made
this file's premise false and its fix the defect. An answer of (None, None)
now means nothing was cancelled, so clearing the id cleared a stop that was
still on the venue, marked a protected position unprotected, audited "the
existing one was cancelled", and let a 25588-family refusal of the new stop
close the position at market beside the working one. The tests below
pin the claim under the current contract: the record names what rests.
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
    # The real one places first and sweeps this side's plan orders only once
    # a new stop rests. Both outcomes are driven from here.
    ex._place_sl_tp = AsyncMock(return_value=place_result)
    # A UTA account: the retry places the pair through the placer above (a
    # classic account places only the missing leg, driven in
    # tests/test_a_retry_places_only_the_missing_leg.py). The hold mode is
    # read, so the monitor's probe leaves the account type alone.
    ex._is_uta = True
    ex._hedge_mode = False
    return ex


class TestTheRecordNamesWhatRests:
    """The same claim under the placer's current contract.

    `_place_sl_tp` cancels this side's resting plan orders only ONCE A NEW
    STOP RESTS. So an answer of (None, None) means nothing was cancelled, and
    a held stop is still on the venue: clearing its id there was the defect,
    and a 25588-family refusal of the new stop then closed the position at
    market beside a working stop. A placed stop means the held one was swept,
    so the record takes the new id.
    """

    @pytest.mark.asyncio
    async def test_a_stop_the_placer_did_not_replace_is_still_named(self):
        ex = _executor(place_result=(None, None))
        pos = _pos(sl_id="SL1", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert ex._place_sl_tp.await_count == 1, "the retry did not run"
        assert pos.sl_order_id == "SL1", (
            "the placer placed no stop, so it cancelled none: the held stop "
            "still rests and the record must keep naming it")
        assert not getattr(pos, "unprotected", False), (
            "a position whose stop still rests was marked unprotected")

    @pytest.mark.asyncio
    async def test_a_placed_stop_replaces_the_swept_ones_id(self):
        ex = _executor(place_result=("SL2", None))
        pos = _pos(sl_id="SL1", tp_id="")
        ex._positions[pos.trade_id] = pos

        await ex.check_positions()

        assert pos.sl_order_id == "SL2", (
            "the record kept the id of the stop the placer's sweep cancelled "
            "beside an untracked live one")
        assert not pos.tp_order_id

    @pytest.mark.asyncio
    async def test_a_held_stop_is_never_reported_lost(self, monkeypatch):
        import bot.core.live_executor as le
        seen = []
        monkeypatch.setattr(le.trade_log, "log",
                            lambda lvl, msg, *a, **k: seen.append((lvl, str(msg))),
                            raising=False)
        crits = []
        monkeypatch.setattr(le.logger, "critical",
                            lambda *a, **k: crits.append(str(a[0]) if a else ""),
                            raising=False)
        ex = _executor(place_result=(None, None))
        ex._positions["TI-ghost"] = _pos(sl_id="SL1", tp_id="")

        await ex.check_positions()

        assert not [m for _, m in seen if "NO exchange stop" in m], (
            "a stop that still rests was reported as cancelled and not replaced")
        assert not [c for c in crits if "UNPROTECTED POSITION" in c]

    @pytest.mark.asyncio
    async def test_the_escalation_fires_for_a_record_with_no_stop(self, monkeypatch):
        """`if (unprotected_escalation_enabled and not pos.sl_order_id ...)`
        logs CRITICAL "UNPROTECTED POSITION". It must still fire for the case
        it is written for: a position that names no stop after the retry."""
        import bot.core.live_executor as le
        crits = []
        monkeypatch.setattr(le.logger, "critical",
                            lambda *a, **k: crits.append(str(a[0]) if a else ""),
                            raising=False)
        monkeypatch.setattr(le.trade_log, "log", lambda *a, **k: None,
                            raising=False)
        ex = _executor(place_result=(None, None))
        pos = _pos(sl_id="", tp_id="")
        ex._positions["TI-ghost"] = pos

        await ex.check_positions()

        assert getattr(pos, "unprotected", False) is True
        assert any("UNPROTECTED POSITION" in c for c in crits)


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

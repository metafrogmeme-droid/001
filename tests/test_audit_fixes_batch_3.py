"""Audit batch 3: unread fills and invented account facts in live_executor.

Three findings from the false-claim sweep, all the same rule one level below
a display: something the venue never told us was used as if it had.

1. `_run_partial_tp` subtracted `_partial_close`'s SUBMITTED quantity from
   `pos.quantity` and then re-sized the exchange stop to the remainder.
   reduceOnly prevents an over-close -- which is what the old docstring argued
   -- but says nothing about an UNDER-fill. Ask to close 0.5 of 1.0 on a thin
   book, get 0.2: the book says 0.5 remains, `_update_exchange_sl` places a
   0.5-sized stop and cancels the correctly-sized one, and 0.3 contracts run
   with no stop on either side, invisible to every surface.

2. Both secondary UTA probes wrote `self._is_uta = False` in the except branch
   for ANY non-40085 failure -- a timeout, a 429, a 5xx -- and cached it for
   the process. On a real UTA account that sends every later stop down the
   classic triggerPrice channel, where it executes IMMEDIATELY as a reduceOnly
   market order and flat-closes the position, while `execute()` reports "SL
   order placed ... Risk: APPROVED". `_update_exchange_sl` did it directly
   beneath its own comment saying never to take that path unresolved.

3. `adopt_exchange_limit_orders` writes `CONFIG.exchange.default_leverage` into
   an ADOPTED order -- one this bot did not place and has no venue leverage for
   -- and `_check_pending_limit` read it back as the APPROVED leverage. An
   operator's own 20x order was flattened for "4.0x the approved leverage"
   against a target that never existed.
"""

from __future__ import annotations

import dataclasses as _dc
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.partial_tp import create_partial_tp_state
from tests.source_scan import code_only

UTC = timezone.utc


def _pos(qty=1.0, entry=100.0, origin="executed", status="open"):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=entry, quantity=qty, cost_usd=entry * qty / 5,
        stop_loss=90.0, take_profit=130.0, leverage=5,
        status=status, atr_at_entry=0.0, origin=origin,
        opened_at=datetime.now(UTC) - timedelta(minutes=30))


def _executor(pos):
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._positions = {pos.trade_id: pos}
    ex._venue = SimpleNamespace(
        order_symbol=lambda s: s,
        futures_params=lambda: {},
        close_params=lambda uta: {"reduceOnly": True},
        market_order_needs_price=False,
        supports_native_triggers=True,
        swap_symbol=lambda s: s,
        trigger_params=lambda k, p: {},
    )
    ex._is_uta = False
    ex._save_positions = lambda: None
    ex._record_warning = lambda k: None
    ex._round_price_to_market = lambda e, s, p: p
    return ex


def _exchange(order, verify=None):
    x = MagicMock()
    x.amount_to_precision = lambda s, q: q
    x.create_order = AsyncMock(return_value=order)
    x.fetch_order = AsyncMock(return_value=verify or {})
    x.cancel_order = AsyncMock()
    return x


# ── 1a. _partial_close reports a MEASURED fill, or says it could not ────

@pytest.mark.asyncio
async def test_a_response_that_carries_the_fill_is_taken_at_face_value():
    ex = _executor(_pos())
    x = _exchange({"id": "O1", "filled": 0.2})
    qty, source = await ex._partial_close(x, ex._positions["T1"], 0.5, "tp1")
    assert (qty, source) == (0.2, "filled")
    x.fetch_order.assert_not_awaited()          # no need to ask twice


@pytest.mark.asyncio
async def test_a_response_without_a_fill_is_verified_against_the_venue():
    ex = _executor(_pos())
    x = _exchange({"id": "O1"},
                  verify={"status": "closed", "filled": 0.2, "average": 105.0})
    qty, source = await ex._partial_close(x, ex._positions["T1"], 0.5, "tp1")
    assert (qty, source) == (0.2, "filled")
    x.fetch_order.assert_awaited()


@pytest.mark.asyncio
async def test_an_unverifiable_fill_is_unknown_not_the_submitted_quantity():
    ex = _executor(_pos())
    x = _exchange({"id": "O1"}, verify={"status": "open", "filled": 0})
    qty, source = await ex._partial_close(x, ex._positions["T1"], 0.5, "tp1")
    assert source == "unknown"
    assert qty == 0.0, "an unread fill must not report the submitted quantity"


@pytest.mark.asyncio
async def test_a_cancelled_order_closed_nothing():
    ex = _executor(_pos())
    x = _exchange({"id": "O1"}, verify={"status": "canceled", "filled": 0})
    assert await ex._partial_close(x, ex._positions["T1"], 0.5, "tp1") == (0.0, "none")


@pytest.mark.asyncio
async def test_a_response_with_no_order_id_is_unknown():
    ex = _executor(_pos())
    x = _exchange({})
    assert await ex._partial_close(x, ex._positions["T1"], 0.5, "tp1") == (0.0, "unknown")


@pytest.mark.asyncio
async def test_a_quantity_that_rounds_away_submits_nothing():
    ex = _executor(_pos())
    x = _exchange({"id": "O1", "filled": 1.0})
    assert await ex._partial_close(x, ex._positions["T1"], 0.0, "tp1") == (0.0, "none")
    x.create_order.assert_not_awaited()


# ── 1b. the ladder acts on the measured fill, and only on it ────────────

def _armed(pos, price_r=2.0):
    """A ladder state whose next check at `price` triggers TP1."""
    st = create_partial_tp_state(
        trade_id=pos.trade_id, direction=pos.direction,
        entry_price=pos.entry_price, stop_loss=pos.stop_loss,
        take_profit=pos.take_profit, quantity=pos.quantity,
        atr=pos.entry_price * 0.02)
    pos.partial_tp_state = _dc.asdict(st)
    # entry 100, stop 90 -> 1R = 10, so 2R is 120 and TP1 (1.5R) has fired.
    return pos.entry_price + st.initial_risk * price_r


@pytest.mark.asyncio
async def test_the_ladder_subtracts_what_filled_not_what_was_submitted():
    # THE BUG. TP1 asks to close 0.5 of 1.0; only 0.2 fills.
    pos = _pos(qty=1.0)
    price = _armed(pos)
    ex = _executor(pos)
    ex._update_exchange_sl = AsyncMock(return_value=True)
    x = _exchange({"id": "O1", "filled": 0.2})
    await ex._run_partial_tp(x, pos, price)
    assert pos.quantity == pytest.approx(0.8), (
        "the book must reflect the 0.2 that filled, not the 0.5 submitted — "
        "otherwise the stop is re-sized for a position larger than it thinks")


@pytest.mark.asyncio
async def test_an_unread_fill_changes_neither_the_quantity_nor_the_stop():
    # The dangerous half: the old code would have decremented by 0.5 and then
    # replaced the full-size stop with a 0.5-sized one.
    pos = _pos(qty=1.0)
    price = _armed(pos)
    before_sl, before_qty = pos.stop_loss, pos.quantity
    ex = _executor(pos)
    ex._update_exchange_sl = AsyncMock(return_value=True)
    x = _exchange({"id": "O1"}, verify={"status": "open", "filled": 0})
    with patch.object(live_executor_mod, "audit") as rec:
        await ex._run_partial_tp(x, pos, price)
    assert pos.quantity == before_qty
    assert pos.stop_loss == before_sl
    ex._update_exchange_sl.assert_not_awaited(), \
        "the existing full-size stop must be left alone while the fill is unread"
    unread = [c for c in rec.call_args_list if c.kwargs.get("result") == "FILL_UNREAD"]
    assert unread, "an unread partial fill must reach the audit trail"


@pytest.mark.asyncio
async def test_a_full_measured_fill_still_moves_the_stop_to_breakeven():
    # The control: the ladder's normal behaviour is unchanged.
    pos = _pos(qty=1.0)
    price = _armed(pos)
    ex = _executor(pos)
    ex._update_exchange_sl = AsyncMock(return_value=True)
    x = _exchange({"id": "O1", "filled": 0.5})
    await ex._run_partial_tp(x, pos, price)
    assert pos.quantity == pytest.approx(0.5)
    ex._update_exchange_sl.assert_awaited_once()
    assert pos.stop_loss > 100.0, "TP1 ratchets the stop to breakeven+buffer"


# ── 2. an inconclusive probe leaves the account type unresolved ─────────

def _uta_probe_sources():
    return {
        "_place_sl_tp": code_only(inspect.getsource(LiveExecutor._place_sl_tp)),
        "_update_exchange_sl": code_only(inspect.getsource(LiveExecutor._update_exchange_sl)),
    }


def test_neither_probe_records_a_verdict_from_a_transient_failure():
    for name, src in _uta_probe_sources().items():
        i = src.index('if "40085" in str(exc)')
        # 1200, not 700: code_only blanks the comment explaining this branch
        # in place, so the following statement sits past the blanked span.
        tail = src[i:i + 1200]
        assert "self._is_uta = True" in tail, name
        assert "self._is_uta = False" not in tail, (
            f"{name}: a non-40085 failure still records a definite account type, "
            "so one timeout decides the SL channel for the whole process")


def test_an_unresolved_account_takes_the_v3_channel_not_the_classic_one():
    # The classic triggerPrice path on a UTA account executes immediately as a
    # market order. Unresolved must not land there.
    for name, src in _uta_probe_sources().items():
        i = src.index('if "40085" in str(exc)')
        tail = src[i:i + 1200]
        assert tail.count("use_v3 = True") >= 2, (
            f"{name}: the inconclusive branch must select the v3 channel, whose "
            "failure mode is a loud unprotected alert rather than a silent close")


def test_a_successful_probe_still_settles_the_account_as_classic():
    src = _uta_probe_sources()["_place_sl_tp"]
    probe = src.index("privateMixGetV2MixAccountAccount")
    assert "self._is_uta = False" in src[probe:probe + 400], (
        "a probe that ANSWERS is a real reading and must still be cached")


# ── 3. a default leverage is not an approved one ───────────────────────

def test_an_adopted_order_has_no_approved_leverage_to_overshoot():
    code = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
    i = code.index('_intended_lev = int(getattr(pos, "leverage", 0) or 0)')
    block = code[i:i + 1700]
    assert 'getattr(pos, "origin", "") == "adopted"' in block
    assert "_intended_lev = 0" in block
    guard = code.index("_guard_fill_leverage", i)
    assert code.index("_intended_lev = 0", i) < guard, (
        "the reset must happen before the guard reads it")


def test_the_verdict_for_a_zero_target_is_unknown_not_close():
    # Why 0 is the right signal: the pure helper already treats a non-positive
    # requested leverage as unmeasured, and "unknown" keeps the position.
    from bot.core.live_executor import leverage_overshoot_verdict
    assert leverage_overshoot_verdict(0, 20, 1.5)["decision"] == "unknown"
    # The control: a REAL 5x target filled at 20x still flattens.
    assert leverage_overshoot_verdict(5, 20, 1.5)["decision"] == "close"


def test_a_reclaimed_order_keeps_its_leverage_because_the_bot_set_it():
    code = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
    i = code.index('_intended_lev = int(getattr(pos, "leverage", 0) or 0)')
    block = code[i:i + 1700]
    assert '"reclaimed"' not in block, (
        "a reclaimed order IS this bot's, so set_leverage really did apply its "
        "default — only 'adopted' is unmeasured")

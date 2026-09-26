"""An unstated fill profit is not a break-even, and a stated fee is used.

**The bot's own close booked a winning trade as a loss of the fees.**
`_close_position_inner` falls back to `fetch_my_trades` when position history
priced nothing, sums the close order's fills, and took `exchange_pnl =
total_profit` whenever the profit OR the fee was non-zero. Bitget writes
`profit: "0"` on a close fill whose realized figure it did not fill in, so a
fill carrying a fee and that `"0"` booked the close at gross `0.0` and net
exactly minus the fees, whatever the price did. Driven: a long from 100 to
105 on 0.001 BTC, +$5.00 gross, was booked at gross 0.0 and net -0.123. The
lookup stages already read that field as "not stated" (`*_local_pnl`), and the
2026-09-26 DOT card shows Bitget leaving it unset in live trading.

**All three local branches re-estimated fees the venue had stated.** When the
P&L is computed from two prices (the bot's close, a close found already done,
reconcile), the commission was the configured rate on both legs, however the
venue's row had priced them. A position-history row states the round trip,
a fill or close order states its own leg; `_local_close_commission` uses what
was stated and estimates only the rest.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor
from tests.test_an_unread_entry_is_an_unpriced_close import (  # noqa: F401
    _executor,
    _hist_row,
    _no_sleep,
    _pos100,
)

ENTRY_NOTIONAL = 100.0 * 10.0      # _pos100: entry 100, quantity 10
EXIT_PX = 95.0                     # a stop-out, gross -50


def _entry_fee(p):
    return ENTRY_NOTIONAL * le.entry_rate_pct(p.order_type) / 100.0


def _exit_fee():
    return EXIT_PX * 10.0 * le.exit_rate_pct() / 100.0


def _own_close(fills=(), verify_fee=0.0, trades_raise=False):
    ex, x = _executor()
    if trades_raise:
        x.fetch_my_trades = AsyncMock(side_effect=RuntimeError("ER_TIMEOUT"))
    else:
        x.fetch_my_trades = AsyncMock(return_value=list(fills))
    p = _pos100()
    ex._positions = {p.trade_id: p}
    ex._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": EXIT_PX, "fill_qty": 10.0, "fees": verify_fee,
        "remaining_qty": 0.0, "failure_stage": ""})
    ex._fetch_bitget_close_data = AsyncMock(return_value=None)
    asyncio.run(ex.close_position(p.trade_id, reason="SL"))
    return p


def _fill(profit, fee):
    return {"order": "CL-1", "price": EXIT_PX, "side": "sell",
            "info": {"profit": profit, "feeDetail": {"totalFee": fee}}}


class TestTheBotsOwnClose:
    def test_an_unstated_profit_is_priced_from_the_fill(self):
        p = _own_close([_fill("0", "-0.2")])
        assert p.gross_pnl == pytest.approx(-50.0), (
            "a stop-out booked at exactly minus the fees off an unpopulated field")
        assert p.commission == pytest.approx(0.2 + _entry_fee(p))
        assert p.pnl_usd == pytest.approx(-50.0 - 0.2 - _entry_fee(p))

    def test_a_stated_profit_is_still_the_venues(self):
        p = _own_close([_fill("-49", "-0.2")])
        assert p.gross_pnl == pytest.approx(-49.0)
        assert p.commission == pytest.approx(0.2 + _entry_fee(p))

    def test_the_verified_close_orders_fee_is_used(self):
        p = _own_close(fills=[], verify_fee=0.15)
        assert p.gross_pnl == pytest.approx(-50.0)
        assert p.commission == pytest.approx(0.15 + _entry_fee(p))

    def test_no_stated_fee_is_estimated_on_both_legs(self):
        p = _own_close(fills=[])
        assert p.commission == pytest.approx(_entry_fee(p) + _exit_fee())

    def test_the_pessimistic_estimate_is_not_read_as_a_stated_fee(self):
        """A failed fills read sets the fee variable to a 20bp round-trip
        GUESS; charged as the close leg beside the entry estimate, it would
        count the entry twice."""
        p = _own_close(trades_raise=True)
        assert p.commission == pytest.approx(_entry_fee(p) + _exit_fee())


class TestACloseFoundAlreadyDone:
    def test_a_history_row_states_the_round_trip(self):
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.25")])
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.gross_pnl == pytest.approx(-50.0)
        assert p.commission == pytest.approx(0.55), "the venue's round trip, not a guess"

    def test_a_fill_states_its_own_leg(self):
        ex, _x = _executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": EXIT_PX, "pnl": None, "fees": 0.2, "fees_cover": "close",
            "reason": "SL HIT (exchange)", "reason_inferred": False,
            "source": "exchange_fill_sltp_local_pnl", "pnl_is_net": False})
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.commission == pytest.approx(0.2 + _entry_fee(p))


class TestReconcile:
    def test_a_history_row_states_the_round_trip(self):
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.25")])
        p = _pos100()
        ex._positions = {p.trade_id: p}
        asyncio.run(ex.reconcile_positions())
        assert p.gross_pnl == pytest.approx(-50.0)
        assert p.commission == pytest.approx(0.55)


class TestTheStagesSayWhatTheirFeeCovers:
    def test_history(self):
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.25")])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["fees_cover"] == "round_trip"

    def test_the_stop_orders_own_fill(self):
        fill = {"order": "SL-1", "price": EXIT_PX, "side": "sell",
                "info": {"profit": "0", "feeDetail": {"totalFee": "-0.2"}}}
        ex, _x = _executor(fills=[fill])
        pos = _pos100()
        pos.sl_order_id = "SL-1"
        out = asyncio.run(ex._fetch_bitget_close_data(pos))
        assert out["source"] == "exchange_fill_sltp_local_pnl"
        assert out["fees_cover"] == "close"

    def test_a_close_side_fill(self):
        fill = {"order": "app", "price": EXIT_PX, "side": "sell",
                "timestamp": None, "info": {"profit": "0", "tradeSide": "close"}}
        ex, _x = _executor(fills=[fill])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["fees_cover"] == "close"


class TestTheArithmetic:
    @pytest.mark.parametrize("stated,cover,expect", [
        (0.55, "round_trip", 0.55),
        (0.2, "close", 0.2 + 1.0),
        (0.0, "close", 1.0 + 2.0),
        (0.0, "round_trip", 1.0 + 2.0),
    ])
    def test_stated_legs_are_used_and_the_rest_estimated(self, stated, cover, expect):
        # entry 1000 at 0.1% = 1.0, exit 2000 at 0.1% = 2.0
        got = LiveExecutor._local_close_commission(1000.0, 2000.0, 0.1, 0.1, stated, cover)
        assert got == pytest.approx(expect)

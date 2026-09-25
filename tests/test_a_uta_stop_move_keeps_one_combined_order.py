"""A trailing stop move on a UTA account keeps one combined order.

A v3 strategy order is ONE order carrying both legs: `_place_sl_tp_v3`
returns its id as the stop and the take-profit alike. `_update_exchange_sl`
kept only the stop half (`sl_id, _ = ...`), so after a move the record held
the NEW id as the stop and the OLD id as the take-profit. The close path reads
a pair as combined only when both ids agree, so it then sent both to the
regular table, and the move itself had cancelled the old order through
ccxt's regular `cancel_order`, which `_cancel_stop_leg` says cannot reach a
strategy order. The old order stayed resting beside the new one.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition


def _pos(sl_id="OLD", tp_id="OLD"):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=98.0, take_profit=110.0, status="open",
        sl_order_id=sl_id, tp_order_id=tp_id,
    )


def _uta(strategy_cancel=None, resting=False, new=("NEW", "NEW")):
    ex = LiveExecutor()
    ex._is_uta = True
    ex._save_positions = MagicMock()
    ex._round_price_to_market = MagicMock(return_value="99.0")
    ex._place_sl_tp_v3 = AsyncMock(return_value=new)
    ex._v3_strategy_cancel_sync = MagicMock(
        return_value=strategy_cancel or {"code": "00000", "msg": "success"})
    ex._v3_strategy_order_resting_sync = MagicMock(return_value=resting)
    return ex


@pytest.mark.asyncio
async def test_the_new_combined_id_names_both_legs():
    ex, pos, exchange = _uta(), _pos(), AsyncMock()
    assert await ex._update_exchange_sl(exchange, pos, new_sl=99.0) is True
    assert (pos.sl_order_id, pos.tp_order_id) == ("NEW", "NEW")


@pytest.mark.asyncio
async def test_the_old_combined_order_is_cancelled_in_the_strategy_table():
    ex, pos, exchange = _uta(), _pos(), AsyncMock()
    await ex._update_exchange_sl(exchange, pos, new_sl=99.0)
    ex._v3_strategy_cancel_sync.assert_called_once_with("OLD")
    exchange.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_a_refused_cancel_is_said_out_loud(caplog):
    ex = _uta(strategy_cancel={"code": "40001", "msg": "refused"})
    pos, exchange = _pos(), AsyncMock()
    with caplog.at_level(logging.WARNING, logger="bot.core.live_executor"):
        await ex._update_exchange_sl(exchange, pos, new_sl=99.0)
    assert any("still resting beside the new one" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_an_order_replaced_in_place_is_not_cancelled():
    ex, pos, exchange = _uta(new=("OLD", "OLD")), _pos(), AsyncMock()
    await ex._update_exchange_sl(exchange, pos, new_sl=99.0)
    ex._v3_strategy_cancel_sync.assert_not_called()
    exchange.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_a_separate_old_stop_goes_through_the_regular_table():
    """An old stop that was never combined is a ccxt trigger order."""
    ex, exchange = _uta(), AsyncMock()
    pos = _pos(sl_id="OLD-SL", tp_id="OLD-TP")
    await ex._update_exchange_sl(exchange, pos, new_sl=99.0)
    ex._v3_strategy_cancel_sync.assert_not_called()
    exchange.cancel_order.assert_awaited_once()
    assert exchange.cancel_order.await_args.args[0] == "OLD-SL"

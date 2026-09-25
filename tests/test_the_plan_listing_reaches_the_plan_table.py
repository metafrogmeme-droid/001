"""The resting stops are listed, and cancelled, in the table that holds them.

Three readers listed Bitget's resting SL/TP orders with
`{"productType": "USDT-FUTURES", "isPlan": "plan_order"}`, and the adoption
reader's own comment said why: "Query the plan channel with the same params
the replace path uses." ccxt 4.5.56 sends that listing to the REGULAR
pending-orders endpoint: it routes to the plan endpoint only on `trigger` (or
a `planType`), and `isPlan` routes nowhere. Driven with the transport stubbed,
below. So adoption never saw an adopted position's real stops, the protective
check never found a resting stop's id, and the cleanup before a re-place
never cancelled an old stop; the one thing it could cancel was a resting
limit order. Its cancel was a plain `cancel_order`, which goes to the regular
table too, so even a correct listing would not have cleared anything.

Fixing both would have exposed a third defect: the cleanup cancelled BEFORE
it placed, so a failed placement would leave the position with no stop.
`_update_exchange_sl` was restructured to place first for exactly that
(C2-03), and the replace path now keeps that order.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import ccxt.async_support as ca
import pytest

from bot.core.live_executor import LiveExecutor
from bot.core.venues import get_venue
from bot.utils.models import Direction

BG = get_venue("bitget")


def _bitget():
    ex = ca.bitget({"apiKey": "k", "secret": "s", "password": "p"})
    ex.set_markets([{
        "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
        "settle": "USDT", "baseId": "BTC", "quoteId": "USDT", "settleId": "USDT",
        "type": "swap", "spot": False, "margin": False, "swap": True, "future": False,
        "option": False, "contract": True, "linear": True, "inverse": False,
        "active": True, "contractSize": 1,
        "precision": {"amount": 0.001, "price": 0.1}, "limits": {}, "info": {}}])
    return ex


def _record(ex, name, answer):
    calls = []

    async def endpoint(req, *a, **k):
        calls.append(dict(req))
        return answer
    setattr(ex, name, endpoint)
    return calls


EMPTY = {"code": "00000", "data": {"entrustedList": []}}


def test_the_old_params_listed_the_regular_table():
    """The defect, pinned: ccxt ignores `isPlan` for routing."""
    async def go():
        ex = _bitget()
        regular = _record(ex, "privateMixGetV2MixOrderOrdersPending", EMPTY)
        plan = _record(ex, "privateMixGetV2MixOrderOrdersPlanPending", EMPTY)
        await ex.fetch_open_orders("BTC/USDT:USDT",
                                   params={"productType": "USDT-FUTURES", "isPlan": "plan_order"})
        await ex.close()
        return regular, plan
    regular, plan = asyncio.run(go())
    assert len(regular) == 1 and plan == []


def test_every_listing_reaches_the_plan_table_under_its_plan_type():
    async def go():
        ex = _bitget()
        regular = _record(ex, "privateMixGetV2MixOrderOrdersPending", EMPTY)
        plan = _record(ex, "privateMixGetV2MixOrderOrdersPlanPending", EMPTY)
        for params in BG.plan_order_queries():
            await ex.fetch_open_orders("BTC/USDT:USDT", params=dict(params))
        await ex.close()
        return regular, plan
    regular, plan = asyncio.run(go())
    assert regular == []
    assert [c["planType"] for c in plan] == ["normal_plan", "profit_loss"]


@pytest.mark.parametrize("plan_type", ["normal_plan", "profit_loss"])
def test_a_cancel_reaches_the_plan_table_under_the_type_that_listed_it(plan_type):
    async def go():
        ex = _bitget()
        regular = _record(ex, "privateMixPostV2MixOrderCancelOrder", {"code": "00000", "data": {}})
        plan = _record(ex, "privateMixPostV2MixOrderCancelPlanOrder",
                       {"code": "00000", "data": {"successList": [{"orderId": "P1"}]}})
        row = {"id": "P1", "_plan_query": {"planType": plan_type}}
        await ex.cancel_order("P1", "BTC/USDT:USDT", params=BG.plan_order_cancel_params(row))
        await ex.close()
        return regular, plan
    regular, plan = asyncio.run(go())
    assert regular == []
    assert len(plan) == 1 and plan[0]["planType"] == plan_type


# ── the executor ─────────────────────────────────────────────────────────────


def _executor(tmp_path, rows, *, place_ok=True):
    ex = AsyncMock()
    ex.fetch_open_orders = AsyncMock(side_effect=lambda sym, params=None: [dict(r) for r in rows])
    ex.cancel_order = AsyncMock(return_value={"status": "canceled"})
    events: list = []

    async def _create(**kw):
        events.append("place")
        if not place_ok:
            raise RuntimeError("45115 rejected")
        return {"id": "NEW"}

    async def _cancel(oid, sym, params=None):
        events.append(("cancel", oid, (params or {}).get("planType")))
        return {"status": "canceled"}

    ex.create_order = AsyncMock(side_effect=_create)
    ex.cancel_order = AsyncMock(side_effect=_cancel)
    ex.price_to_precision = lambda symbol, price: str(price)
    ex.market = lambda symbol: {}
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = BG
    e._get_exchange = AsyncMock(return_value=ex)
    e._is_uta = False
    e._hedge_mode = False
    return e, ex, events


OLD = [{"id": "OLD-SL", "info": {"posSide": "long"}}]


@pytest.mark.asyncio
async def test_the_old_stop_is_cancelled_after_the_new_one_is_placed(tmp_path):
    e, ex, events = _executor(tmp_path, OLD)
    sl, tp = await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
    assert (sl, tp) == ("NEW", "NEW")
    first_cancel = next(i for i, ev in enumerate(events) if isinstance(ev, tuple))
    assert events[:first_cancel] == ["place", "place"], events
    assert events[first_cancel] == ("cancel", "OLD-SL", "normal_plan")


@pytest.mark.asyncio
async def test_a_failed_placement_cancels_nothing(tmp_path):
    e, ex, events = _executor(tmp_path, OLD, place_ok=False)
    sl, _tp = await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
    assert sl is None
    assert not any(isinstance(ev, tuple) for ev in events), (
        "the old stop was the position's only protection")


@pytest.mark.asyncio
async def test_the_new_stops_are_never_cancelled(tmp_path):
    e, ex, events = _executor(tmp_path, OLD + [{"id": "NEW", "info": {"posSide": "long"}}])
    await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
    assert [ev[1] for ev in events if isinstance(ev, tuple)] == ["OLD-SL"]


@pytest.mark.asyncio
async def test_the_listing_unions_the_queries_and_keeps_which_one_listed_a_row(tmp_path):
    ex = AsyncMock()
    answers = {"normal_plan": [{"id": "A"}, {"id": "B"}], "profit_loss": [{"id": "B"}, {"id": "C"}]}
    ex.fetch_open_orders = AsyncMock(
        side_effect=lambda sym, params=None: [dict(r) for r in answers[params["planType"]]])
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = BG
    rows = await e._fetch_plan_orders(ex, "BTC/USDT:USDT")
    assert [(r["id"], r["_plan_query"]["planType"]) for r in rows] == [
        ("A", "normal_plan"), ("B", "normal_plan"), ("C", "profit_loss")]


@pytest.mark.asyncio
async def test_a_query_that_fails_is_a_listing_that_failed(tmp_path):
    ex = AsyncMock()
    calls = {"n": 0}

    async def _fetch(sym, params=None):
        calls["n"] += 1
        if params["planType"] == "profit_loss":
            raise RuntimeError("timeout")
        return [{"id": "A"}]
    ex.fetch_open_orders = AsyncMock(side_effect=_fetch)
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = BG
    with pytest.raises(RuntimeError):
        await e._fetch_plan_orders(ex, "BTC/USDT:USDT")


@pytest.mark.asyncio
async def test_a_refused_cancel_is_said_out_loud(tmp_path, caplog):
    import logging
    e, ex, events = _executor(tmp_path, OLD)
    ex.cancel_order = AsyncMock(side_effect=RuntimeError("40768 order does not exist"))
    with caplog.at_level(logging.WARNING, logger="bot.core.live_executor"):
        await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
    assert any("still resting beside the new" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_classic_trailing_move_cancels_the_old_stop_in_the_plan_table(tmp_path):
    from bot.core.live_executor import LivePosition
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = BG
    e._is_uta = False
    e._save_positions = lambda: None
    e._round_price_to_market = lambda exchange, sym, px: str(px)
    ex = AsyncMock()
    ex.create_order = AsyncMock(return_value={"id": "SL-NEW"})
    ex.cancel_order = AsyncMock(return_value={"status": "canceled"})
    pos = LivePosition(trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
                       entry_price=100.0, quantity=1.0, cost_usd=100.0,
                       stop_loss=98.0, take_profit=110.0, status="open",
                       sl_order_id="SL-OLD")
    assert await e._update_exchange_sl(ex, pos, new_sl=99.0) is True
    ex.cancel_order.assert_awaited_once()
    call = ex.cancel_order.await_args
    assert call.args[0] == "SL-OLD"
    assert call.kwargs["params"] == BG.plan_order_cancel_params({})
    assert call.kwargs["params"]["trigger"] is True

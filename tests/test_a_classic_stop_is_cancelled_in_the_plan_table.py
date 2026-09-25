"""The close path cancels a classic stop in the table that holds it.

`_cancel_stop_leg`'s non-combined branch sent a plain `cancel_order`, which
on Bitget goes to the REGULAR table, and that table answers "does not
exist" for every plan order. Driven against ccxt 4.5.56 with the transport
stubbed: one regular cancel, no plan cancel, verdict `unverified`. The
verdict clears the id (a stop nobody could verify is not protection), so
the record forgot a stop that was still RESTING through the market close
that followed, on every close of every classic-account position. The
re-place path had already been routed to the plan table a slice earlier;
this is the other reader, and the post-close sweep is the third.

The cancel now goes to the plan table under the type that listed the order,
and what became of it is read off the listing afterwards, because ccxt
parses a plan cancel from its `successList` and a refused one raises
without saying why. The regular table is asked only when the plan tables
were read and none lists the id, and then its "does not exist" is the
second table's answer: `gone`, not `unverified`.
"""
from __future__ import annotations

import asyncio
import inspect
import textwrap
from unittest.mock import AsyncMock

import ccxt
import ccxt.async_support as ca

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue
from tests.source_scan import code_only

BG = get_venue("bitget")
HL = get_venue("hyperliquid")


def _executor(tmp_path, venue=BG, *, hedge=False):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = venue
    e._is_uta = False
    e._hedge_mode = hedge
    return e


def _pos():
    return LivePosition(trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
                        entry_price=60_000.0, quantity=0.01, cost_usd=120.0,
                        stop_loss=58_800.0, take_profit=63_600.0, status="open",
                        sl_order_id="SL1", tp_order_id="TP1")


class _PlanVenue:
    """A venue with a plan table: lists what it holds per plan type, and
    records every call. `cancel` decides what a cancel does: "remove",
    "keep" (accept and leave it resting), or an exception to raise."""

    def __init__(self, rows, *, cancel="remove", listing=None, regular=None):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.cancel = cancel
        self.listing = listing          # None: read; "fail": raise; a list of outcomes per call
        self.regular = regular          # what the regular cancel answers (default: not found)
        self.calls: list = []
        self.ex = AsyncMock()
        self.ex.fetch_open_orders = AsyncMock(side_effect=self._list)
        self.ex.cancel_order = AsyncMock(side_effect=self._cancel)
        self.ex.fetch_order = AsyncMock(side_effect=RuntimeError("no detail for a plan order"))

    async def _list(self, sym, params=None):
        pt = (params or {}).get("planType")
        self.calls.append(("list", pt))
        outcome = self.listing
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if outcome else None
        if outcome == "fail":
            raise RuntimeError("timeout")
        return [dict(r) for r in self.rows.values() if r["_pt"] == pt]

    async def _cancel(self, oid, sym, params=None):
        params = params or {}
        self.calls.append(("cancel", oid, params.get("planType"), bool(params.get("trigger"))))
        if not params.get("trigger"):
            if self.regular == "removed":
                return {"status": "canceled"}
            raise ccxt.ExchangeError('bitget {"code":"40768","msg":"Order does not exist"}')
        if isinstance(self.cancel, BaseException):
            raise self.cancel
        if self.cancel == "remove":
            self.rows.pop(oid, None)
        return {"info": {"successList": [{"orderId": oid}]}}


def _row(oid="SL1", pt="normal_plan"):
    return {"id": oid, "_pt": pt, "info": {"posSide": "long"}, "side": "sell"}


def _leg(tmp_path, v, **kw):
    e = _executor(tmp_path, **kw)
    return asyncio.run(e._cancel_stop_leg(v.ex, _pos(), "SL1", combined=False))


def test_a_listed_stop_is_cancelled_under_its_plan_type_and_read_back(tmp_path):
    v = _PlanVenue([_row()])
    assert _leg(tmp_path, v) == ("removed", "cancel accepted; the plan table no longer lists it")
    cancels = [c for c in v.calls if c[0] == "cancel"]
    assert cancels == [("cancel", "SL1", "normal_plan", True)]
    assert [c for c in v.calls if c[0] == "list"] == [("list", "normal_plan"), ("list", "profit_loss")] * 2


def test_a_position_tp_sl_is_cancelled_under_profit_loss(tmp_path):
    v = _PlanVenue([_row(pt="profit_loss")])
    assert _leg(tmp_path, v)[0] == "removed"
    assert [c for c in v.calls if c[0] == "cancel"] == [("cancel", "SL1", "profit_loss", True)]


def test_a_cancel_the_listing_says_did_not_take_keeps_the_id(tmp_path):
    v = _PlanVenue([_row()], cancel="keep")
    assert _leg(tmp_path, v) == ("live", "cancel accepted, but the plan table still lists it")


def test_a_refused_cancel_over_an_order_no_longer_listed_is_gone(tmp_path):
    v = _PlanVenue([_row()], cancel=ccxt.ExchangeError("bitget {\"code\":\"40768\"}"))
    v.listing = [None, None, "fail", "fail"]  # listed before; unreadable after
    verdict, detail = _leg(tmp_path, v)
    assert verdict == "live" and "could not be read" in detail
    v = _PlanVenue([_row()], cancel=ccxt.ExchangeError("x"))
    v.rows.clear()  # what the venue will answer after the cancel: not listed
    v.rows["SL1"] = _row()
    calls = {"n": 0}
    real_list = v._list

    async def _list_then_forget(sym, params=None):
        calls["n"] += 1
        if calls["n"] > 2:
            v.rows.pop("SL1", None)
        return await real_list(sym, params)
    v.ex.fetch_open_orders = AsyncMock(side_effect=_list_then_forget)
    verdict, detail = asyncio.run(_executor(tmp_path)._cancel_stop_leg(v.ex, _pos(), "SL1", combined=False))
    assert verdict == "gone" and "refused cancel (ExchangeError)" in detail


def test_a_refused_cancel_over_an_order_still_listed_is_live(tmp_path):
    v = _PlanVenue([_row()], cancel=ccxt.ExchangeError("x"))
    verdict, detail = _leg(tmp_path, v)
    assert verdict == "live" and detail.endswith("(ExchangeError)")


def test_a_cancel_nobody_could_read_back_is_unverified(tmp_path):
    v = _PlanVenue([_row()], listing=[None, None, "fail", "fail"])
    assert _leg(tmp_path, v) == ("unverified", "cancel accepted; the plan table could not be read")


def test_a_transport_failure_nobody_could_read_back_is_unverified_and_a_refusal_is_live(tmp_path):
    v = _PlanVenue([_row()], cancel=ccxt.NetworkError("reset"), listing=[None, None, "fail", "fail"])
    verdict, detail = _leg(tmp_path, v)
    assert verdict == "unverified" and detail == "the cancel request did not complete (NetworkError)"
    v = _PlanVenue([_row()], cancel=ccxt.ExchangeError("x"), listing=[None, None, "fail", "fail"])
    assert _leg(tmp_path, v)[0] == "live"


def test_an_unreadable_listing_before_the_cancel_sends_the_bots_own_plan_type(tmp_path):
    # The listing stops at the first query that raises, so one outcome is
    # consumed before the cancel and the two after it are read.
    v = _PlanVenue([_row()], listing=["fail", None, None])
    verdict, _ = _leg(tmp_path, v)
    assert verdict == "removed"
    assert [c for c in v.calls if c[0] == "cancel"] == [("cancel", "SL1", "normal_plan", True)]


def test_an_id_in_no_plan_table_is_asked_of_the_regular_table_and_both_answers_are_gone(tmp_path):
    """A record written before adoption listed the plan table may name a
    REGULAR order as its stop."""
    v = _PlanVenue([])
    assert _leg(tmp_path, v) == ("gone", "neither the plan tables nor the regular-order table "
                                         "knows it: it fired, or had already been cancelled")
    assert [c for c in v.calls if c[0] == "cancel"] == [("cancel", "SL1", None, False)]
    v = _PlanVenue([], regular="removed")
    assert _leg(tmp_path, v) == ("removed", "cancel answered canceled")


def test_a_venue_with_no_plan_table_keeps_the_regular_reading(tmp_path):
    """Hyperliquid's trigger orders are regular orders, and its regular
    table's "does not exist" still cannot be told from the wrong-table
    answer, so it stays unverified and no listing is read."""
    v = _PlanVenue([])
    verdict, detail = _leg(tmp_path, v, venue=HL)
    assert verdict == "unverified" and "regular-order table does not know it" in detail
    assert [c for c in v.calls if c[0] == "list"] == []


# ── against the pinned ccxt, transport stubbed ───────────────────────────────


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


def test_the_cancel_reaches_bitgets_plan_endpoint_and_never_the_regular_one(tmp_path):
    async def go():
        ex = _bitget()
        state = {"resting": True}
        calls = {"regular": 0, "plan": 0, "listings": 0}

        async def regular(req, *a, **k):
            calls["regular"] += 1
            raise ccxt.ExchangeError('bitget {"code":"40768","msg":"Order does not exist"}')

        async def plan(req, *a, **k):
            calls["plan"] += 1
            assert req["planType"] == "normal_plan", req
            state["resting"] = False
            return {"code": "00000", "data": {"successList": [{"orderId": "SL1"}], "failureList": []}}

        async def listing(req, *a, **k):
            calls["listings"] += 1
            rows = [] if not state["resting"] or req["planType"] != "normal_plan" else [
                {"orderId": "SL1", "symbol": "BTCUSDT", "planType": "normal_plan",
                 "planStatus": "live", "triggerPrice": "58800", "side": "sell", "posSide": "long"}]
            return {"code": "00000", "data": {"entrustedList": rows}}
        ex.privateMixPostV2MixOrderCancelOrder = regular
        ex.privateMixPostV2MixOrderCancelPlanOrder = plan
        ex.privateMixGetV2MixOrderOrdersPlanPending = listing
        e = _executor(tmp_path)
        verdict = await e._cancel_stop_leg(ex, _pos(), "SL1", combined=False)
        await ex.close()
        return verdict, calls
    verdict, calls = asyncio.run(go())
    assert verdict[0] == "removed", verdict
    assert calls == {"regular": 0, "plan": 1, "listings": 4}


# ── the post-close sweep takes the same route (a scan, and why) ───────────────


def test_the_post_close_sweep_cancels_in_the_plan_table_too():
    """`_close_position_inner` is a 400-line method behind a venue, a
    verification read and a fill parser, so its sweep is pinned as a shape:
    a stale classic id goes through `_cancel_stop_leg` (the routing above),
    the plan rows this side owns are listed through the cleanup rule, and
    each is cancelled with the params that reach its table."""
    src = code_only(textwrap.dedent(inspect.getsource(LiveExecutor._close_position_inner)))
    i = src.index("if cancel_failed:")
    j = src.index("self._fire_position_closed(pos)", i)
    sweep = src[i:j]
    assert "await self._cancel_stop_leg(exchange, pos, stale_oid, combined=False)" in sweep
    assert "await exchange.cancel_order(stale_oid" not in sweep
    assert "plan_rows_to_cancel(" in sweep and "hedge_mode=self._hedge_mode" in sweep
    assert "params=self._venue.plan_order_cancel_params(oo)" in sweep

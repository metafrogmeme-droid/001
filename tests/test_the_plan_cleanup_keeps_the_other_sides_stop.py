"""The re-place sweep cancels THIS side's stops, and in hedge mode keeps the other side's.

`_place_sl_tp` clears the plan orders it finds on a symbol before placing new
ones — the right idea, with no side filter: every plan order on the symbol,
both sides. The bot's own book never holds both sides of one symbol, so the
hazard needs a position the bot did not open — an operator's manual short
beside a bot long, an adopted orphan — on a HEDGE-mode account, where
re-placing the long's protection stripped the short's stop and placed one.

`plan_cleanup.plan_rows_to_cancel` is the rule, the asymmetry pointed the
other way from `order_state.rows_for_side`: a row is CANCELLED only when it
definitely protects this side. In hedge mode (and an undetected mode) a row
whose side could not be read is KEPT and audited — a reduce-only survivor on
the same side cannot double-close, a stripped other-side stop leaves real
money naked. One-way keeps the sweep it always had: one side is all there is.

THE FIXTURE PLANTS BOTH SIDES with different ids and different readings of
the side (posSide on one, the normalised close side on the other, nothing on
a third), so a sweep that cancels by any rule other than the side is a
different set of ids.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import Direction, LiveExecutor
from bot.core.plan_cleanup import PROTECTS, plan_row_protects, plan_rows_to_cancel
from bot.core.venues import get_venue
from tests.source_scan import code_only

LONG_STOP = {"id": "L-SL", "side": "sell", "triggerPrice": 95.0,
             "info": {"planType": "loss_plan", "posSide": "long"}}
SHORT_STOP = {"id": "S-SL", "side": "buy", "triggerPrice": 130.0,
              "info": {"planType": "loss_plan", "posSide": "short"}}
SHORT_TP = {"id": "S-TP", "side": "buy", "triggerPrice": 80.0,
            "info": {"planType": "profit_plan", "holdSide": "short"}}
LONG_BY_SIDE_ONLY = {"id": "L-TP", "side": "sell", "triggerPrice": 110.0,
                     "info": {"planType": "profit_plan"}}
NO_SIDE = {"id": "??", "triggerPrice": 100.0, "info": {"planType": "loss_plan"}}
ALL = [LONG_STOP, SHORT_STOP, SHORT_TP, LONG_BY_SIDE_ONLY, NO_SIDE]


def _ids(rows):
    return [r["id"] for r in rows]


# ── the reading ──────────────────────────────────────────────────────────────

class TestTheSideReading:
    def test_the_documented_fields_and_the_close_side(self):
        assert PROTECTS == ("long", "short")
        assert plan_row_protects(LONG_STOP) == "long"
        assert plan_row_protects(SHORT_STOP) == "short"
        assert plan_row_protects(SHORT_TP) == "short", "holdSide reads too"
        assert plan_row_protects(LONG_BY_SIDE_ONLY) == "long", "a close-side SELL protects a long"
        assert plan_row_protects({"side": "BUY"}) == "short"
        assert plan_row_protects({"info": {"posSide": "LONG"}, "side": "buy"}) == "long", \
            "the raw position side wins over the normalised order side"
        assert plan_row_protects({"info": {"holdSide": "long"}}) == "long", "holdSide alone is a reading"
        assert plan_row_protects({"info": {"posSide": "short"}}) == "short", "posSide alone is a reading"

    @pytest.mark.parametrize("row", [NO_SIDE, {}, {"side": "close"}, {"side": None},
                                     {"info": {"posSide": "net"}}, {"info": "junk", "side": 3},
                                     "not a dict", None])
    def test_anything_else_is_not_a_reading(self, row):
        assert plan_row_protects(row) is None


# ── the rule ─────────────────────────────────────────────────────────────────

class TestTheRule:
    def test_hedge_mode_cancels_only_this_sides_rows_and_keeps_the_unreadable_one(self):
        cancel, kept = plan_rows_to_cancel(ALL, hedge_mode=True, protects="long")
        assert _ids(cancel) == ["L-SL", "L-TP"]
        assert _ids(kept) == ["??"], "unreadable is kept AND reported"
        cancel, kept = plan_rows_to_cancel(ALL, hedge_mode=True, protects="short")
        assert _ids(cancel) == ["S-SL", "S-TP"] and _ids(kept) == ["??"]

    def test_one_way_keeps_the_full_sweep(self):
        cancel, kept = plan_rows_to_cancel(ALL, hedge_mode=False, protects="long")
        assert _ids(cancel) == _ids(ALL) and kept == []

    def test_an_undetected_mode_takes_the_hedge_rule(self):
        # Guessing one-way on a hedge account strips the other side's stop;
        # guessing hedge on a one-way account leaves a reduce-only duplicate
        # that cannot fire. The cheaper mistake is the rule.
        cancel, kept = plan_rows_to_cancel(ALL, hedge_mode=None, protects="long")
        assert _ids(cancel) == ["L-SL", "L-TP"] and _ids(kept) == ["??"]

    def test_the_other_sides_rows_are_left_silently(self):
        cancel, kept = plan_rows_to_cancel([SHORT_STOP, SHORT_TP], hedge_mode=True, protects="long")
        assert cancel == [] and kept == [], "somebody's stop is not ours to touch or to report"

    def test_a_non_list_and_a_bad_side_word(self):
        assert plan_rows_to_cancel(None, hedge_mode=True, protects="long") == ([], [])
        assert plan_rows_to_cancel("rows", hedge_mode=False, protects="long") == ([], [])
        with pytest.raises(ValueError):
            plan_rows_to_cancel(ALL, hedge_mode=True, protects="sell")


# ── the executor, driven ─────────────────────────────────────────────────────

def _exchange(rows):
    ex = AsyncMock()
    ex.fetch_open_orders = AsyncMock(return_value=list(rows))
    ex.cancel_order = AsyncMock(return_value={"status": "canceled"})
    ex.create_order = AsyncMock(return_value={"id": "NEW"})
    ex.price_to_precision = lambda symbol, price: str(price)
    ex.market = lambda symbol: {}
    return ex


def _executor(tmp_path, ex, *, hedge_mode):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = get_venue("bitget")
    e._get_exchange = AsyncMock(return_value=ex)
    e._is_uta = False            # the classic ccxt trigger path, no probe, no v3 sleep
    e._hedge_mode = hedge_mode
    return e


def _cancelled(ex):
    return [c.args[0] for c in ex.cancel_order.await_args_list]


class TestTheExecutor:
    @pytest.mark.asyncio
    async def test_re_placing_the_longs_protection_leaves_the_shorts_stop_alone_in_hedge_mode(self, tmp_path):
        ex = _exchange(ALL)
        e = _executor(tmp_path, ex, hedge_mode=True)
        sl, tp = await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
        assert (sl, tp) == ("NEW", "NEW"), "the placement itself still happens"
        assert _cancelled(ex) == ["L-SL", "L-TP"], (
            "THE DEFECT: the short's stop and target were cancelled with the long's")
        # RED HERRING: the plan channel was queried with the venue's own params,
        # as before — the filter is on what came back, not on the question.
        ex.fetch_open_orders.assert_awaited_once()
        assert ex.fetch_open_orders.await_args.kwargs["params"] == e._venue.plan_order_query_params()

    @pytest.mark.asyncio
    async def test_the_short_side_is_the_mirror(self, tmp_path):
        ex = _exchange(ALL)
        e = _executor(tmp_path, ex, hedge_mode=True)
        await e._place_sl_tp(ex, "BTC/USDT", Direction.SHORT, 1.0, 130.0, 80.0)
        assert _cancelled(ex) == ["S-SL", "S-TP"]

    @pytest.mark.asyncio
    async def test_one_way_still_sweeps_everything(self, tmp_path):
        # RED HERRING: the same five rows, the same long — the MODE decides.
        ex = _exchange(ALL)
        e = _executor(tmp_path, ex, hedge_mode=False)
        await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
        assert _cancelled(ex) == _ids(ALL)

    @pytest.mark.asyncio
    async def test_an_undetected_mode_keeps_the_other_side_too(self, tmp_path):
        ex = _exchange(ALL)
        e = _executor(tmp_path, ex, hedge_mode=None)
        await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
        assert _cancelled(ex) == ["L-SL", "L-TP"]

    @pytest.mark.asyncio
    async def test_a_kept_row_is_audited_and_a_cancel_that_fails_is_not_counted(self, tmp_path, monkeypatch):
        import bot.core.live_executor as lx
        seen: list = []
        monkeypatch.setattr(lx, "audit", lambda log, msg, **kw: seen.append((msg, kw)))
        ex = _exchange(ALL)
        ex.cancel_order = AsyncMock(side_effect=[RuntimeError("gone"), {"status": "canceled"}])
        e = _executor(tmp_path, ex, hedge_mode=True)
        await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
        kept = [kw for _m, kw in seen if kw.get("action") == "plan_order_kept"]
        assert len(kept) == 1 and kept[0]["result"] == "UNREAD_SIDE"
        assert kept[0]["data"]["kept"] == 1 and kept[0]["data"]["ids"] == ["??"]
        assert kept[0]["data"]["hedge_mode"] is True
        cleared = [kw for _m, kw in seen if kw.get("action") == "plan_order_cleanup"]
        assert len(cleared) == 1 and cleared[0]["data"]["cancelled"] == 1, (
            "one of the two cancels raised; the count is what was cleared, not what was tried")
        assert cleared[0]["data"]["side"] == "long" and cleared[0]["data"]["hedge_mode"] is True

    @pytest.mark.asyncio
    async def test_nothing_of_ours_means_no_cancel_and_no_cleanup_record(self, tmp_path, monkeypatch):
        import bot.core.live_executor as lx
        seen: list = []
        monkeypatch.setattr(lx, "audit", lambda log, msg, **kw: seen.append(kw.get("action")))
        ex = _exchange([SHORT_STOP, SHORT_TP])
        e = _executor(tmp_path, ex, hedge_mode=True)
        await e._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0, 95.0, 110.0)
        assert _cancelled(ex) == [] and "plan_order_cleanup" not in seen and "plan_order_kept" not in seen

    def test_the_sweep_goes_through_the_rule(self):
        # The shape, beside the drives: the loop cancels what the rule handed
        # back, and nothing else reads `existing_plans` as the list to cancel.
        src = code_only(inspect.getsource(LiveExecutor._place_sl_tp))
        i = src.index("plan_rows_to_cancel(")
        assert "for plan in to_cancel:" in src[i:]
        assert "for plan in existing_plans:" not in src

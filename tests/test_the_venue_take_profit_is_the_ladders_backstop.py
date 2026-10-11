"""The venue's take-profit is the ladder's backstop, so live scales out as the cards do.

Live placed the exchange TP at the idea's target, 1.25-1.67R, and the partial
ladder takes 50% at 1.5R, 30% at 2.5R and trails the rest: the venue closed
the whole position before TP1 every time, and every card -- whose backtest
runs the ladder and ignores that target -- measured an exit the live bot
never made. The operator's decision of 11 October: make live match the cards.

`position.take_profit` is now the backstop (`PARTIAL_TP_BACKSTOP_R` R from
entry, or the plan's target where farther), decided once when the position is
first recorded, so the placement, every re-placement, the local check and
reconcile read the one value; the plan's target and its 1R ride beside it,
saved. Typed tickets, a ladder that is off, an unmeasurable 1R and a ladder
whose slices the market cannot place keep the idea's target.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core import partial_tp as ptp
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.models import Direction, TradeIdea
from tests.test_an_estimated_entry_says_so import TICKER, Venue

ENTRY, STOP, PLAN = TICKER, TICKER * 0.98, TICKER * 1.025   # 1R = 80; plan 1.25R


# ── the arithmetic ────────────────────────────────────────────────────────

class TestTheBackstop:

    def test_four_r_beyond_the_ladder_or_the_plan_where_farther(self):
        assert ptp.scale_out_levels("LONG", 100.0, 2.0) == (103.0, 105.0)
        assert ptp.scale_out_levels("SHORT", 100.0, 2.0) == (97.0, 95.0)
        assert ptp.venue_backstop("LONG", 100.0, 2.0, 102.5) == 108.0
        assert ptp.venue_backstop("SHORT", 100.0, 2.0, 97.5) == 92.0
        assert ptp.venue_backstop("LONG", 100.0, 2.0, 120.0) == 120.0, "a farther plan stands"
        assert ptp.venue_backstop("SHORT", 100.0, 2.0, 80.0) == 80.0

    @pytest.mark.parametrize("args", [("LONG", 100.0, 0.0, 102.5), ("LONG", 0.0, 2.0, 1.0),
                                      ("LONG", 100.0, -1.0, 102.5), ("SHORT", 10.0, 3.0, 5.0)],
                             ids=["no-risk", "no-entry", "negative-risk", "short-under-zero"])
    def test_nothing_to_arm(self, args):
        assert ptp.venue_backstop(*args) is None

    def test_a_multiple_inside_the_ladder_arms_nothing(self):
        cfg = CONFIG.partial_tp
        old = cfg.venue_backstop_r
        try:
            object.__setattr__(cfg, "venue_backstop_r", cfg.tp2_r_multiple)
            assert ptp.venue_backstop("LONG", 100.0, 2.0, 102.5) is None
            object.__setattr__(cfg, "venue_backstop_r", cfg.tp2_r_multiple + 0.5)
            assert ptp.venue_backstop("LONG", 100.0, 2.0, 102.5) == 106.0
        finally:
            object.__setattr__(cfg, "venue_backstop_r", old)


# ── the order: placed at the backstop, the plan kept ─────────────────────

def _idea(source="scan", tp=PLAN, stop=STOP):
    return TradeIdea(id="TI-BACK", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=ENTRY, stop_loss=stop, take_profit=tp,
                     confidence=0.8, reasoning="fixture", source=source)


def _execute(tmp_path, idea, size=200.0, market_min=None, fill=None):
    """execute() over the estimated-entry venue, recording what the stop and
    target were placed at. ``fill`` is the average the order states."""
    venue = Venue(order_average=fill)
    if market_min is not None:
        venue.bg.markets["ETH/USDT:USDT"]["limits"]["amount"]["min"] = market_min
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    ex._slippage_tracker = SimpleNamespace(record=lambda **k: None)
    placed: list = []
    audits: list = []

    async def _sl_tp(self, exchange, symbol, direction, qty, sl, tp, *a, **k):
        placed.append({"sl": sl, "tp": tp, "qty": qty})
        return ("sl1", "tp1")

    async def _sync(*a, **k):
        return None

    RUNTIME.leverage_override = None
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True), \
         patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
         patch.object(LiveExecutor, "sync_positions_from_exchange", _sync), \
         patch.object(LiveExecutor, "_post_fill_slippage_guard",
                      AsyncMock(return_value=(None, ""))):
        card = asyncio.run(ex.execute(idea, size_usd=size, order_type="market"))
    return ex, ex._positions.get(idea.id), placed, card, audits


class TestTheOrderCarriesTheBackstop:

    def test_the_target_placed_is_the_backstop_and_the_plan_rides_beside_it(self, tmp_path):
        ex, pos, placed, card, audits = _execute(tmp_path, _idea())
        risk = pos.entry_price - STOP
        assert pos.take_profit == pytest.approx(pos.entry_price + 4 * risk)
        assert placed and placed[0]["tp"] == pos.take_profit, placed
        assert placed[0]["sl"] == STOP
        assert pos.plan_take_profit == PLAN and pos.ladder_risk == pytest.approx(risk)
        assert any(a.get("result") == "ARMED" for a in audits)
        # The fill card prints the venue's TP and where it scales out.
        assert f"${pos.take_profit:,.4f}" in card and "the backstop" in card, card
        tp1, tp2 = ptp.scale_out_levels("LONG", pos.entry_price, risk)
        assert f"TP1 ${tp1:,.4f}" in card and f"TP2 ${tp2:,.4f}" in card

    @pytest.mark.parametrize("case", ["typed", "ladder-off", "no-risk", "too-small"])
    def test_the_idea_keeps_its_target_and_says_why(self, tmp_path, case):
        idea = _idea(source="manual") if case == "typed" else _idea()
        cfg = CONFIG.partial_tp
        old = cfg.enabled
        if case == "ladder-off":
            object.__setattr__(cfg, "enabled", False)
        stop = STOP
        if case == "no-risk":
            # The fill lands past the stop: no 1R can be measured from it.
            stop = ENTRY * 0.999
            idea = _idea(stop=stop)
        try:
            # 0.12 ETH is placed; TP2's 30% (0.03) is under a 0.05 minimum.
            ex, pos, placed, card, audits = _execute(
                tmp_path, idea, market_min=(0.05 if case == "too-small" else None),
                fill=(ENTRY * 0.998 if case == "no-risk" else None))
        finally:
            object.__setattr__(cfg, "enabled", old)
        assert pos is not None, card
        assert pos.take_profit == PLAN and not hasattr(pos, "plan_take_profit")
        assert placed and placed[0]["tp"] == PLAN
        (row,) = [a for a in audits if a.get("result") == "NOT_ARMED"]
        why = row["data"]["why"]
        assert {"typed": "typed ticket", "ladder-off": "ladder is off",
                "no-risk": "no 1R", "too-small": "minimum"}[case] in why, why
        assert "the backstop" not in card


# ── every later reader reads the one value ───────────────────────────────

def _armed(direction="LONG"):
    sign = 1 if direction == "LONG" else -1
    pos = LivePosition(trade_id="P1", symbol="ETH/USDT", direction=direction,
                       entry_price=100.0, quantity=1.0, cost_usd=20.0,
                       stop_loss=100.0 - sign * 2.0, take_profit=100.0 + sign * 8.0,
                       leverage=5)
    pos.plan_take_profit = 100.0 + sign * 2.5
    pos.ladder_risk = 2.0
    return pos


class TestTheLocalCheckClosesOnlyAtTheBackstop:

    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_the_plan_target_is_not_a_close_and_the_backstop_is(self, tmp_path, direction):
        ex = LiveExecutor(state_dir=str(tmp_path))
        pos = _armed(direction)
        sign = 1 if direction == "LONG" else -1
        assert ex._local_stop_breached(pos, 100.0 + sign * 2.6) == (False, "")
        assert ex._local_stop_breached(pos, 100.0 + sign * 5.0) == (False, ""), "TP2 is the ladder's"
        assert ex._local_stop_breached(pos, 100.0 + sign * 8.0) == (True, "TP HIT")


class TestThePlanSurvivesARestart:

    def test_saved_and_restored(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        pos = _armed()
        ex._positions[pos.trade_id] = pos
        ex._save_positions()
        back = LiveExecutor(state_dir=str(tmp_path))._positions["P1"]
        assert back.take_profit == 108.0
        assert back.plan_take_profit == 102.5 and back.ladder_risk == 2.0
        assert le.scale_out_reading(back) == {"tp1": 103.0, "tp2": 105.0,
                                              "plan_take_profit": 102.5}

    def test_a_record_without_the_plan_restores_as_its_own_target(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        pos = _armed()
        del pos.plan_take_profit
        ex._positions[pos.trade_id] = pos
        ex._save_positions()
        back = LiveExecutor(state_dir=str(tmp_path))._positions["P1"]
        assert not hasattr(back, "plan_take_profit")
        assert le.scale_out_reading(back) is None and le.take_profit_note(back) == ""


class TestTheCardsSayWhereItScalesOut:

    def test_the_note_names_both_levels_and_the_plan(self):
        note = le.take_profit_note(_armed())
        assert note == (" (the backstop: 50% closes at TP1 $103.0000, 30% at TP2 $105.0000, "
                        "the rest trails; the plan's target was $102.5000)")

    def test_a_plan_target_has_no_note(self):
        plain = LivePosition(trade_id="P2", symbol="ETH/USDT", direction="LONG",
                             entry_price=100.0, quantity=1.0, cost_usd=20.0,
                             stop_loss=98.0, take_profit=102.5, leverage=5)
        assert le.take_profit_note(plain) == "" and le.scale_out_reading(plain) is None

    def test_the_fill_notice_puts_it_after_the_tp(self):
        line = LiveExecutor._fmt_fill_protection(98.0, 108.0, "s", "t", None,
                                                 tp_note=le.take_profit_note(_armed()))
        assert line.startswith(" | SL: 98 | TP: 108 (the backstop: 50% closes at TP1")

    def test_the_web_row_carries_the_reading(self):
        from bot.web.user_gateway import _live_position_row
        row = _live_position_row(_armed())
        assert row["take_profit"] == 108.0
        assert row["scale_out"] == {"tp1": 103.0, "tp2": 105.0, "plan_take_profit": 102.5}
        plain = _armed()
        del plain.plan_take_profit
        assert _live_position_row(plain)["scale_out"] is None


class TestTheEdgesOfArming:

    def test_a_market_nobody_could_read_places_no_slice(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        bare = SimpleNamespace(amount_to_precision=lambda sym, q: q)
        assert ex._ladder_slice_placeable(bare, "ETH/USDT", 1.0) is False
        venue = Venue()
        assert ex._ladder_slice_placeable(venue, "ETH/USDT", 0.03) is True
        venue.bg.markets["ETH/USDT:USDT"]["limits"]["amount"]["min"] = 0.05
        assert ex._ladder_slice_placeable(venue, "ETH/USDT", 0.03) is False
        assert ex._ladder_slice_placeable(venue, "ETH/USDT", 0.004) is False, "rounds to nothing"

    def test_the_stop_retry_places_the_backstop_too(self, tmp_path):
        """The first placement loses its stop; the retry places only what is
        missing, beside the same target -- the backstop, not the plan's."""
        retried: list = []

        async def _first(self, exchange, symbol, direction, qty, sl, tp, *a, **k):
            return (None, "tp1")

        async def _legs(self, exchange, symbol, direction, qty, sl, tp, sl_id, tp_id):
            retried.append(tp)
            return ("sl2", tp_id)

        with patch.object(LiveExecutor, "_protect_legs", _legs):
            venue = Venue()
            ex = LiveExecutor(state_dir=str(tmp_path))
            ex._exchange = venue
            ex._slippage_tracker = SimpleNamespace(record=lambda **k: None)

            async def _sync(*a, **k):
                return None

            RUNTIME.leverage_override = None
            with patch.object(le, "audit", lambda *a, **k: None), \
                 patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
                 patch.object(type(CONFIG), "is_live", return_value=True), \
                 patch.object(LiveExecutor, "_place_sl_tp", _first), \
                 patch.object(LiveExecutor, "sync_positions_from_exchange", _sync), \
                 patch.object(LiveExecutor, "_post_fill_slippage_guard",
                              AsyncMock(return_value=(None, ""))):
                asyncio.run(ex.execute(_idea(), size_usd=200.0, order_type="market"))
        pos = ex._positions["TI-BACK"]
        assert retried == [pos.take_profit] and pos.take_profit != PLAN

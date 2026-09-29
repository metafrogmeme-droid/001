"""A scan card's ✅ places the levels the card shows, and so does a typed entry.

The scan card's action header says, in its own words, "✅ places the entry
shown, as a limit order, with its stop and target." Three sites moved a
limit's levels, and each exempted only a hand-typed ticket (`source ==
"manual"`): the engine's confirm-time re-price (a limit at or through the
market moved to current - 0.5*ATR, stop and target shifted with it), the
executor's confluence re-price (which can also turn the limit into a MARKET
order), and the drift fallback that chases a resting limit at the market with
its stop and target shifted by the same percentage. A post-only limit the
market had reached was refused by the venue and re-priced by the retry, which
is a fourth. Driven on the unfixed tree through the real confirm: a card
showing SOL LONG 99.4 / stop 95 / target 106 with the market at 99.3 was
handed to the executor as 98.3 / 93.9 / 104.9.

The Limit button is the same promise one door over: it asks the person to
type their limit price, replaces the idea's entry with it and confirms, and
the typed entry of an engine idea took the engine's re-price. It also took a
price outside the idea's own stop and target, by plain assignment the model's
directional check never sees: a LONG with its stop at 95 took a typed 94,
which rests at 94, fills there, and cannot place the stop above its fill.

`limit_entry.levels_as_shown` is the one reading, asked at every site. What
it deliberately does not decide: the clock a resting order rests on, and
whether drift may CANCEL it. A scan card's idea is the engine's analysis and
rests on the engine's clock; a level the market ran away from may still be
cancelled. Cancelling places nothing; moving the levels places an order
nobody confirmed.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.engine as eng_mod
import bot.core.live_executor as le
from bot.compat import UTC
from bot.config import CONFIG
from bot.core.limit_entry import LEVELS_AS_SHOWN_SOURCES, levels_as_shown
from bot.core.limit_input import typed_limit_outside_levels
from bot.core.live_executor import LiveExecutor
from bot.skills.scan_skill import scan_action_rows, scan_row_idea
from bot.utils.models import Direction, TradeIdea
from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot
from tests.test_a_seal_failure_does_not_unplace_a_trade import _confirm, _engine
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    _no_website_sync as _seal_no_website_sync,
)
from tests.test_a_typed_ticket_is_placed_at_the_levels_it_typed import (
    _ex_idea,
    _FakeExchange,
    _live_idea,
    _place,
    _run,
)
from tests.test_a_typed_ticket_is_placed_at_the_levels_it_typed import (
    _post_only_on as _typed_post_only_on,
)
from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update
from tests.test_the_drift_fallback_places_what_was_approved import (
    _executor,
    _pending_check,
    _resting,
    _Venue,
)

_no_website_sync = _seal_no_website_sync      # autouse binds per module
_post_only_on = _typed_post_only_on           # post_only ON for the placement drives

CARD_ROW = {"sym": "SOL/USDT", "dir": "LONG", "entry": 99.4, "sl": 95.0, "tp": 106.0,
            "score": 0.72, "atr": 2.0}


@pytest.fixture
def audits(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(eng_mod, "audit", lambda log, message, **kw: seen.append({"message": message, **kw}))
    return seen


def _by(rows, action):
    return [a for a in rows if a.get("action") == action]


# ── the one reading ──────────────────────────────────────────────────────

class TestTheReading:

    @pytest.mark.parametrize("obj,expected", [
        (SimpleNamespace(source="manual"), True),
        (SimpleNamespace(source="scan_skill"), True),
        (SimpleNamespace(source="unknown"), False),
        (SimpleNamespace(source="auto_reanalyze"), False),
        (SimpleNamespace(source="unknown", entry_typed=True), True),
        (SimpleNamespace(source="unknown", entry_typed=1), False),
        (SimpleNamespace(source="unknown", entry_typed="yes"), False),
        (SimpleNamespace(idea_source="scan_skill"), True),
        (SimpleNamespace(idea_source="manual"), True),
        (SimpleNamespace(idea_source="unknown", entry_typed=True), True),
        (SimpleNamespace(idea_source=None, source="scan_skill"), True),
        (SimpleNamespace(source=None), False),
        (SimpleNamespace(source=3), False),
        (SimpleNamespace(source=["scan_skill"]), False),
        (SimpleNamespace(), False),
        (MagicMock(), False),
    ], ids=["manual", "scan", "engine", "drift-reoffer", "entry-typed", "typed-1",
            "typed-str", "row-scan", "row-manual", "row-typed", "row-none-idea-scan",
            "none", "not-a-string", "unhashable", "nothing", "mock"])
    def test_whose_levels(self, obj, expected):
        assert levels_as_shown(obj) is expected

    def test_the_sources_are_the_two_that_show_their_levels(self):
        assert LEVELS_AS_SHOWN_SOURCES == frozenset({"manual", "scan_skill"})

    def test_the_card_still_makes_the_promise_this_reading_keeps(self):
        idea, _ = scan_row_idea(CARD_ROW)
        header, _rows = scan_action_rows([dict(CARD_ROW, idea_id=idea.id)], {"blocked": False}, "1")
        assert "places the entry shown, as a limit order, with its stop and target" in header
        assert idea.source in LEVELS_AS_SHOWN_SOURCES


# ── the engine's confirm ─────────────────────────────────────────────────

def _pending(tmp_path, idea, market):
    engine, _seal_idea = _engine(tmp_path)
    engine._pending_ideas.clear()
    engine._manual_margin_override.clear()
    engine._pending_ideas[idea.id] = idea
    engine._pending_atr[idea.id] = 2.0
    engine.scanner._get_exchange.return_value.fetch_ticker = AsyncMock(return_value={"last": market})
    handed: list = []

    async def _exec(i, *a, **kw):
        handed.append(i)
        return "✅ LIMIT ORDER PLACED"

    engine.live_executor.execute = _exec
    return engine, handed


def _engine_idea(**over):
    base = dict(id="TI-ENG", asset="SOL/USDT", direction=Direction.LONG, entry_price=99.4,
                stop_loss=95.0, take_profit=106.0, confidence=0.72, reasoning="engine",
                signals_used=["x"], timestamp=datetime.now(UTC), order_type="limit")
    base.update(over)
    return TradeIdea(**base)


class TestTheConfirmPlacesTheLevelsShown:

    def test_a_scan_card_through_the_market_reaches_the_executor_as_shown(self, tmp_path, audits):
        idea, why = scan_row_idea(CARD_ROW)
        assert idea is not None, why
        engine, handed = _pending(tmp_path, idea, 99.3)
        assert _confirm(engine, idea).startswith("✅")
        (h,) = handed
        assert (h.entry_price, h.stop_loss, h.take_profit, h.order_type) == (99.4, 95.0, 106.0, "limit")
        assert _by(audits, "limit_price_update") == []
        (row,) = _by(audits, "manual_limit_as_typed")
        assert row["result"] == "CROSSES_MARKET"
        assert row["data"]["source"] == "scan_skill" and row["data"]["entry_typed"] is False

    def test_an_entry_typed_through_the_limit_button_is_placed_as_typed(self, tmp_path, audits):
        idea = _engine_idea(entry_typed=True)
        engine, handed = _pending(tmp_path, idea, 99.3)
        assert _confirm(engine, idea).startswith("✅")
        (h,) = handed
        assert (h.entry_price, h.stop_loss, h.take_profit) == (99.4, 95.0, 106.0)
        (row,) = _by(audits, "manual_limit_as_typed")
        assert row["data"]["entry_typed"] is True and row["data"]["source"] == "unknown"

    def test_the_engines_own_idea_through_the_market_is_still_re_priced(self, tmp_path, audits):
        """The control: the analyzer's idea (source left at its default)
        keeps the re-price to rest as a maker. Without it the two tests above
        would pass against a confirm that re-prices nothing."""
        idea = _engine_idea()
        engine, handed = _pending(tmp_path, idea, 99.3)
        assert _confirm(engine, idea).startswith("✅")
        (h,) = handed
        assert (h.entry_price, h.stop_loss, h.take_profit) == (98.3, 93.9, 104.9)
        assert _by(audits, "limit_price_update")[0]["result"] == "UPDATED"
        assert _by(audits, "manual_limit_as_typed") == []

    def test_a_scan_card_the_market_is_already_past_the_stop_of_is_refused(self, tmp_path, audits):
        """Placed as shown would fill and stop out at once; the past-stop
        check runs for every limit and refuses before placement."""
        idea, _ = scan_row_idea(CARD_ROW)
        engine, handed = _pending(tmp_path, idea, 94.0)
        answer = _confirm(engine, idea)
        assert answer.startswith("Trade REJECTED") and "already below" in answer
        assert handed == []


# ── the executor ─────────────────────────────────────────────────────────

@pytest.fixture
def ex_audits(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(le, "audit", lambda log, message, **kw: seen.append({"message": message, **kw}))
    return seen


class TestTheExecutorLeavesShownLevelsAlone:

    @pytest.mark.parametrize("idea", [
        _ex_idea(source="scan_skill"),
        SimpleNamespace(**{**vars(_ex_idea(source="unknown")), "entry_typed": True}),
    ], ids=["scan", "entry-typed"])
    def test_a_crossing_limit_is_returned_as_shown(self, tmp_path, ex_audits, monkeypatch, idea):
        def _never(**kw):
            raise AssertionError("the confluence re-price ran on levels a person confirmed")
        monkeypatch.setattr(le, "calculate_entry", _never)
        ex = LiveExecutor(state_dir=str(tmp_path))
        fx = _FakeExchange()
        out = _run(ex._recalculate_limit_entry(fx, "BTC/USDT:USDT", idea, "buy", None,
                                               True, 101.0, 100.0, 100.0, 5.0, 5, 2.0))
        assert out == (True, 101.0, 100.0, 5.0)
        assert (idea.stop_loss, idea.take_profit) == (98.0, 104.0)
        assert fx.calls == []
        (row,) = ex_audits
        assert (row["action"], row["result"]) == ("manual_limit_as_typed", "CROSSES_MARKET")
        assert row["data"]["source"] == idea.source

    def test_a_crossing_scan_limit_with_no_atr_is_not_downgraded_to_market(self, tmp_path, ex_audits):
        ex = LiveExecutor(state_dir=str(tmp_path))
        out = _run(ex._recalculate_limit_entry(_FakeExchange(), "BTC/USDT:USDT",
                                               _ex_idea(source="scan_skill"), "buy", None,
                                               True, 101.0, 100.0, 100.0, 5.0, 5, 0.0))
        assert out == (True, 101.0, 100.0, 5.0)
        assert [a["action"] for a in ex_audits] == ["manual_limit_as_typed"]

    def test_a_scan_limit_is_sent_gtc_never_post_only(self, tmp_path):
        """Post-only rejects the order the moment the market reaches the price
        it names, and the retry then re-prices the shown levels."""
        sent = _place(tmp_path, _live_idea(3990.0, "scan_skill"), 4000.0)
        assert sent["type"] == "limit" and sent["price"] == 3990.0
        assert sent["params"].get("timeInForce") == "GTC"

    def test_a_crossing_scan_limit_goes_out_at_its_shown_price(self, tmp_path):
        sent = _place(tmp_path, _live_idea(4010.0, "scan_skill"), 4000.0)
        assert sent["type"] == "limit" and sent["price"] == 4010.0
        assert sent["params"].get("timeInForce") == "GTC"

    def test_an_entry_typed_engine_limit_is_sent_gtc(self, tmp_path):
        idea = _live_idea(3990.0, "unknown")
        idea.entry_typed = True
        sent = _place(tmp_path, idea, 4000.0)
        assert sent["params"].get("timeInForce") == "GTC"

    def test_the_engines_own_limit_keeps_post_only(self, tmp_path):
        sent = _place(tmp_path, _live_idea(3990.0, "unknown"), 4000.0)
        assert sent["params"].get("timeInForce") == "post_only"


# ── the drift: cancel, never chase ──────────────────────────────────────

class TestTheDriftCancelsAndNeverChasesShownLevels:

    @pytest.fixture(autouse=True)
    def _premises(self):
        assert CONFIG.limit_orders.price_drift_cancel_pct > 0
        assert CONFIG.limit_orders.drift_market_fallback is True

    @pytest.mark.parametrize("extra", [
        {"idea_source": "scan_skill"},
        {"idea_source": "unknown", "entry_typed": True},
    ], ids=["scan", "entry-typed"])
    def test_a_drifted_row_is_cancelled_and_never_chased(self, tmp_path, extra):
        ex = _executor(tmp_path)
        pos = _resting(**extra)
        venue = _Venue(104.9)                                  # 4.9% past the 2% band, momentum on
        msg, audits = _pending_check(ex, pos, venue, momentum=True)
        assert ex._check_drift_market_fallback.await_count == 0, "momentum is never asked"
        assert ex._execute_drift_market_fallback.await_count == 0
        assert venue.orders == []
        assert venue.cancels == ["L1"]
        assert msg and msg.startswith("LIMIT CANCELLED (price drift)"), msg
        (row,) = [a for a in audits if a.get("result") == "NOT_CHASED"]
        assert row["action"] == "limit_drift_market_fallback"
        assert row["data"]["source"] == extra["idea_source"]

    def test_a_row_inside_the_band_keeps_resting(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="scan_skill")
        venue = _Venue(101.0)
        msg, audits = _pending_check(ex, pos, venue)
        assert msg is None and venue.cancels == [] and pos.status == "pending_fill"
        assert not [a for a in audits if a.get("result") == "NOT_CHASED"]

    def test_the_engines_own_row_still_reaches_the_fallback(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="unknown")
        msg, audits = _pending_check(ex, pos, _Venue(104.9))
        assert msg == "FALLBACK RAN"
        assert not [a for a in audits if a.get("result") == "NOT_CHASED"]


# ── the stamp rides on the position, and survives a restart ─────────────

class TestTheTypedEntryIsPersisted:

    def test_it_survives_a_restart(self, tmp_path):
        ex = _executor(tmp_path)
        ex._positions["T1"] = _resting(idea_source="unknown", entry_typed=True)
        ex._save_positions()
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions["T1"]
        assert getattr(back, "entry_typed", None) is True
        assert levels_as_shown(back) is True

    @pytest.mark.parametrize("saved", ["absent", False, "true", 1, None])
    def test_anything_but_true_restores_as_not_typed(self, tmp_path, saved):
        ex = _executor(tmp_path)
        ex._positions["T1"] = _resting(idea_source="unknown", entry_typed=True)
        ex._save_positions()
        (f,) = [p for p in tmp_path.iterdir() if p.name.startswith("live_positions")]
        rows = json.loads(f.read_text())
        for row in rows.values() if isinstance(rows, dict) else rows:
            if saved == "absent":
                row.pop("entry_typed", None)
            else:
                row["entry_typed"] = saved
        f.write_text(json.dumps(rows))
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions["T1"]
        assert getattr(back, "entry_typed", None) is not True
        assert levels_as_shown(back) is False

    def test_execute_stamps_it_off_the_idea(self, tmp_path):
        """Driven through the real `execute` to a venue that fills at the
        market; the position it builds carries the idea's typed entry."""
        from bot.config import RUNTIME
        from bot.core import bounds_shadow
        from tests.test_the_placed_order_is_the_checked_order import _idea, _market
        from tests.test_the_placed_order_is_the_checked_order import _Venue as _PlacedVenue

        class _Filling(_PlacedVenue):
            async def create_order(self, symbol=None, type=None, side=None, amount=None,
                                   price=None, params=None, **k):
                self.orders.append({"amount": float(amount), "type": type})
                return {"id": "M1", "status": "closed", "filled": float(amount),
                        "average": self.price, "cost": float(amount) * self.price}

            async def fetch_order(self, oid, symbol=None, params=None):
                return {"id": oid, "status": "closed", "filled": self.orders[-1]["amount"],
                        "average": self.price}

            async def fetch_my_trades(self, *a, **k):
                return []

        was = RUNTIME.leverage_override
        RUNTIME.leverage_override = None
        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._exchange = _Filling(_market(), 4000.0)
        ex._place_sl_tp = AsyncMock(return_value=("S1", "T1"))
        ex._reattempt_post_fill_sl = AsyncMock(
            side_effect=lambda exchange, pos, direction, qty, sl, tp, tid: (sl, tp, None))
        ex.sync_positions_from_exchange = AsyncMock(return_value=None)
        ex._guard_fill_leverage = AsyncMock(return_value=None)
        idea = _idea(4000.0)
        idea.entry_typed = True
        try:
            with patch.object(le, "audit", lambda *a, **k: None), \
                 patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
                 patch.object(type(CONFIG), "is_live", return_value=True):
                result = asyncio.run(ex.execute(idea, size_usd=50.0, order_type="market"))
        finally:
            RUNTIME.leverage_override = was
        assert "LIVE BUY" in result, result
        (pos,) = ex._positions.values()
        assert getattr(pos, "entry_typed", None) is True


# ── the Limit button ─────────────────────────────────────────────────────

class TestTheTypedPriceMustSitInsideTheSetup:

    @pytest.mark.parametrize("direction,price,inside", [
        ("LONG", 99.0, True), ("LONG", 95.01, True), ("LONG", 105.99, True),
        ("LONG", 95.0, False), ("LONG", 94.0, False), ("LONG", 106.0, False), ("LONG", 107.0, False),
        ("SHORT", 101.0, True), ("SHORT", 94.01, True), ("SHORT", 105.99, True),
        ("SHORT", 106.0, False), ("SHORT", 107.0, False), ("SHORT", 94.0, False), ("SHORT", 93.0, False),
    ])
    def test_inside_the_stop_and_the_target(self, direction, price, inside):
        sl, tp = (95.0, 106.0) if direction == "LONG" else (106.0, 94.0)
        idea = SimpleNamespace(direction=SimpleNamespace(value=direction), stop_loss=sl, take_profit=tp)
        said = typed_limit_outside_levels(idea, price)
        if inside:
            assert said is None
        else:
            assert said and "outside this setup" in said and "Nothing was placed" in said
            assert f"A {direction} limit must sit" in said

    def test_the_sentence_names_both_levels_on_the_right_sides(self):
        long_ = SimpleNamespace(direction="LONG", stop_loss=95.0, take_profit=106.0)
        assert "above its stop 95 and below its target 106" in typed_limit_outside_levels(long_, 94.0)
        short = SimpleNamespace(direction="SHORT", stop_loss=106.0, take_profit=94.0)
        assert "below its stop 106 and above its target 94" in typed_limit_outside_levels(short, 107.0)

    @pytest.mark.parametrize("direction", [None, "", "hold", MagicMock()])
    def test_an_unreadable_direction_is_refused_before_the_levels_are_read(self, direction):
        idea = SimpleNamespace(direction=direction)                  # no stop, no target
        said = typed_limit_outside_levels(idea, 99.0)
        assert said and "direction could not be read" in said and "Nothing was placed" in said


@pytest.fixture(name="bot")
def _bot(tmp_path):
    """The halt suite's handler, through its own fixture function."""
    yield from _halt_bot.__wrapped__(tmp_path)


def _armed(bot, idea):
    bot._pending_limit_input = {str(OPERATOR): {"trade_id": idea.id, "pair": "SOL/USDT",
                                                "direction": "LONG", "asset": idea.asset,
                                                "current_entry": idea.entry_price,
                                                "timestamp": time.time()}}
    bot.engine._pending_ideas = {idea.id: idea}
    bot.engine.confirm_trade = AsyncMock(return_value="✅ LIMIT ORDER PLACED")


class TestTheLimitButtonDoor:

    @pytest.mark.asyncio
    async def test_a_price_below_a_longs_stop_is_refused_and_stays_armed(self, bot):
        idea = _engine_idea()
        _armed(bot, idea)
        await bot._handle_message(_update(OPERATOR, "94"), None)
        bot.engine.confirm_trade.assert_not_awaited()
        assert idea.entry_price == 99.4 and idea.entry_typed is False
        assert str(OPERATOR) in bot._pending_limit_input, "the prompt stays armed for another price"
        assert "outside this setup" in bot.sent[-1] and "Nothing was placed" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_a_price_inside_the_setup_is_typed_and_confirmed(self, bot):
        idea = _engine_idea()
        _armed(bot, idea)
        await bot._handle_message(_update(OPERATOR, "99.0"), None)
        bot.engine.confirm_trade.assert_awaited_once()
        assert idea.entry_price == 99.0 and idea.order_type == "limit"
        assert idea.entry_typed is True and levels_as_shown(idea) is True
        assert str(OPERATOR) not in bot._pending_limit_input

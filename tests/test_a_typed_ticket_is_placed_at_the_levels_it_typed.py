"""A hand-typed ticket is placed at the levels the person typed.

Two re-price sites treated a typed limit as the engine's own stale level.
`_confirm_trade_inner` moved a limit at or through the market to
current - 0.5*ATR and shifted the stop and target the same distance; the
executor's `_recalculate_limit_entry` then ran the confluence re-price on
whatever crossed at placement (a Tier D turns the limit into a market order,
a Tier C cuts the typed margin by 0.3), and a post-only limit the market had
reached was rejected by the venue and re-priced by the retry. Driven through
the real `confirm_trade` on the unfixed tree, `/trade long ETH 3000 sl 2950
tp 3100` with ETH at 2990:

    handed to execute: entry 2965  sl 2915  tp 3065   answer: "LIMIT ORDER PLACED"

and at 3000 == the market, 2975 / 2925 / 3075. The dashboard's ticket card
says "Limit -- rest at entry $3,000". A limit at or through the market is the
person's price cap: buy now, at most 3000. It is placed as typed, sent GTC so
the venue fills it at the market up to that price (post-only would refuse it),
and the crossing is audited rather than acted on. The engine's own ideas keep
the re-price they always had. `limit_crosses_market` is the one reading.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import bot.core.engine as eng_mod
import bot.core.live_executor as le
from bot.compat import UTC
from bot.config import CONFIG
from bot.core import bounds_shadow
from bot.core.limit_entry import limit_crosses_market
from bot.core.live_executor import LiveExecutor
from bot.skills.manual_trade import build_manual_idea
from bot.utils.models import Direction, TradeIdea
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    _confirm,
    _engine,
)
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    _no_website_sync as _seal_no_website_sync,
)
from tests.test_the_placed_order_is_the_checked_order import _market, _Sent, _Venue

_no_website_sync = _seal_no_website_sync  # autouse binds per module


# ── the one reading ──────────────────────────────────────────────────────

@pytest.mark.parametrize("side,limit,market,expected", [
    ("buy", 3000, 2990, True), ("buy", 3000, 3000, True), ("buy", 3000, 3020, False),
    ("LONG", 3000, 2990, True), ("long", 3000, 3020, False),
    ("sell", 3000, 3010, True), ("sell", 3000, 3000, True), ("sell", 3000, 2980, False),
    ("SHORT", 3000, 3010, True), ("short", 3000, 2980, False),
])
def test_a_limit_at_or_through_the_market_crosses(side, limit, market, expected):
    assert limit_crosses_market(side, limit, market) is expected


@pytest.mark.parametrize("side,limit,market", [
    ("buy", 0, 2990), ("buy", 3000, 0), ("buy", None, 2990), ("buy", "junk", 2990),
    ("buy", math.nan, 2990), ("buy", 3000, math.inf), ("hold", 3000, 2990), ("", 3000, 2990),
])
def test_an_unreadable_price_or_side_is_not_a_reading(side, limit, market):
    assert limit_crosses_market(side, limit, market) is None


# ── the engine's confirm ─────────────────────────────────────────────────

def _typed(direction, entry, sl, tp):
    idea = build_manual_idea(direction, "ETH", entry, sl, tp)
    return idea.model_copy(update={"id": "TI-TYPED", "timestamp": datetime.now(UTC)})


def _at_market(tmp_path, idea, market, audits):
    """The seal suite's engine, with THIS idea pending, the market at ``market``
    and an executor that records the idea it is handed."""
    engine, _seal_idea = _engine(tmp_path)
    engine._pending_ideas.clear()
    engine._pending_ideas[idea.id] = idea
    engine._manual_margin_override[idea.id] = 50.0
    engine.scanner._get_exchange.return_value.fetch_ticker = AsyncMock(return_value={"last": market})
    handed = []

    async def _exec(i, *a, **kw):
        handed.append(i)
        return "✅ LIMIT ORDER PLACED"

    engine.live_executor.execute = _exec
    return engine, handed


@pytest.fixture
def audits(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(eng_mod, "audit", lambda log, message, **kw: seen.append({"message": message, **kw}))
    return seen


def _by(audits, action):
    return [a for a in audits if a.get("action") == action]


class TestATypedTicketReachesTheExecutorAsTyped:

    @pytest.mark.parametrize("market", [2990.0, 3000.0], ids=["through", "at"])
    def test_a_long_at_or_through_the_market_keeps_its_levels(self, tmp_path, audits, market):
        idea = _typed("LONG", 3000, 2950, 3100)
        engine, handed = _at_market(tmp_path, idea, market, audits)
        assert _confirm(engine, idea).startswith("✅")
        h = handed[0]
        assert (h.entry_price, h.stop_loss, h.take_profit, h.order_type) == (3000, 2950, 3100, "limit")
        assert _by(audits, "limit_price_update") == []
        crossing = _by(audits, "manual_limit_as_typed")
        assert len(crossing) == 1 and crossing[0]["result"] == "CROSSES_MARKET"
        assert crossing[0]["data"] == {"trade_id": idea.id, "entry": 3000.0, "current_price": market}

    def test_a_short_through_the_market_keeps_its_levels(self, tmp_path, audits):
        idea = _typed("SHORT", 3000, 3050, 2900)
        engine, handed = _at_market(tmp_path, idea, 3010.0, audits)
        assert _confirm(engine, idea).startswith("✅")
        h = handed[0]
        assert (h.entry_price, h.stop_loss, h.take_profit) == (3000, 3050, 2900)
        assert _by(audits, "manual_limit_as_typed")[0]["result"] == "CROSSES_MARKET"

    def test_a_resting_typed_limit_is_neither_moved_nor_audited(self, tmp_path, audits):
        idea = _typed("LONG", 3000, 2950, 3100)
        engine, handed = _at_market(tmp_path, idea, 3020.0, audits)
        assert _confirm(engine, idea).startswith("✅")
        h = handed[0]
        assert (h.entry_price, h.stop_loss, h.take_profit) == (3000, 2950, 3100)
        assert _by(audits, "manual_limit_as_typed") == []
        assert _by(audits, "limit_price_update") == []

    def test_a_typed_long_whose_stop_the_market_is_already_past_is_refused(self, tmp_path, audits):
        """Placed as typed would fill at once and be stopped out at once; the
        past-stop check runs for a typed ticket and refuses before placement."""
        idea = _typed("LONG", 3000, 2950, 3100)
        engine, handed = _at_market(tmp_path, idea, 2940.0, audits)
        answer = _confirm(engine, idea)
        assert answer.startswith("Trade REJECTED") and "already below" in answer
        assert handed == []


class TestTheEnginesOwnIdeaIsStillRePriced:

    def _signal(self, entry, sl, tp):
        return TradeIdea(id="TI-SIG", asset="ETH/USDT:USDT", direction=Direction.LONG,
                         entry_price=entry, stop_loss=sl, take_profit=tp, confidence=0.8,
                         reasoning="signal", signals_used=["x"], source="scan_skill",
                         timestamp=datetime.now(UTC), order_type="limit")

    def test_a_signal_limit_through_the_market_moves_to_half_an_atr_below(self, tmp_path, audits):
        idea = self._signal(3000, 2950, 3100)
        engine, handed = _at_market(tmp_path, idea, 2990.0, audits)
        engine._manual_margin_override.clear()
        engine._pending_atr[idea.id] = 50.0
        assert _confirm(engine, idea).startswith("✅")
        h = handed[0]
        assert (h.entry_price, h.stop_loss, h.take_profit) == (2965.0, 2915.0, 3065.0)
        assert _by(audits, "limit_price_update")[0]["result"] == "UPDATED"
        assert _by(audits, "manual_limit_as_typed") == []

    def test_a_signal_limit_that_rests_is_left_where_it_was(self, tmp_path, audits):
        idea = self._signal(3000, 2950, 3100)
        engine, handed = _at_market(tmp_path, idea, 3020.0, audits)
        engine._manual_margin_override.clear()
        engine._pending_atr[idea.id] = 50.0
        assert _confirm(engine, idea).startswith("✅")
        assert handed[0].entry_price == 3000
        assert _by(audits, "limit_price_update") == []


def test_the_engine_reads_the_one_crossing_reading():
    """Driven by planting: a reading that answers False for a crossing typed
    limit leaves the audit unwritten, and one that answers True for a resting
    one writes it -- so the block asks the leaf and spells no comparison."""
    import inspect

    from tests.source_scan import code_only
    src = code_only(inspect.getsource(eng_mod.RuneClawEngine._confirm_trade_inner))
    assert "limit_crosses_market(idea.direction.value" in src
    assert "idea.entry_price >= current_price" not in src
    assert "idea.entry_price <= current_price" not in src


# ── the executor's re-price ──────────────────────────────────────────────

class _FakeExchange:
    def __init__(self):
        self.calls: list[str] = []

    async def fetch_ohlcv(self, symbol, tf, limit=50):
        self.calls.append("fetch_ohlcv")
        return []

    def price_to_precision(self, symbol, price):
        return str(price)

    def amount_to_precision(self, symbol, amount):
        return str(amount)


def _ex_idea(source="manual", sl=98.0, tp=104.0):
    return SimpleNamespace(id="t1", asset="BTC/USDT", direction=Direction.LONG, entry_price=101.0,
                           stop_loss=sl, take_profit=tp, strategy_type="swing", source=source)


@pytest.fixture
def ex_audits(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(le, "audit", lambda log, message, **kw: seen.append({"message": message, **kw}))
    return seen


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestTheExecutorLeavesATypedTicketAlone:

    def test_a_crossing_typed_limit_is_returned_as_typed_and_audited(self, tmp_path, ex_audits, monkeypatch):
        def _never(**kw):
            raise AssertionError("the confluence re-price ran on a typed ticket")
        monkeypatch.setattr(le, "calculate_entry", _never)
        ex = LiveExecutor(state_dir=str(tmp_path))
        fx = _FakeExchange()
        idea = _ex_idea()
        out = _run(ex._recalculate_limit_entry(fx, "BTC/USDT:USDT", idea, "buy", None,
                                               True, 101.0, 100.0, 100.0, 5.0, 5, 2.0))
        assert out == (True, 101.0, 100.0, 5.0)
        assert (idea.stop_loss, idea.take_profit) == (98.0, 104.0)
        assert fx.calls == []
        row = ex_audits[0]
        assert (row["action"], row["result"]) == ("manual_limit_as_typed", "CROSSES_MARKET")
        assert row["data"] == {"symbol": "BTC/USDT:USDT", "limit_price": 101.0, "market_price": 100.0}

    def test_a_crossing_typed_limit_with_no_atr_is_not_downgraded_to_market(self, tmp_path, ex_audits):
        ex = LiveExecutor(state_dir=str(tmp_path))
        out = _run(ex._recalculate_limit_entry(_FakeExchange(), "BTC/USDT:USDT", _ex_idea(), "buy",
                                               None, True, 101.0, 100.0, 100.0, 5.0, 5, 0.0))
        assert out == (True, 101.0, 100.0, 5.0)
        assert [a["action"] for a in ex_audits] == ["manual_limit_as_typed"]

    def test_a_resting_typed_limit_is_returned_as_typed_and_not_audited(self, tmp_path, ex_audits):
        ex = LiveExecutor(state_dir=str(tmp_path))
        out = _run(ex._recalculate_limit_entry(_FakeExchange(), "BTC/USDT:USDT", _ex_idea(), "buy",
                                               None, True, 99.0, 100.0, 100.0, 5.0, 5, 2.0))
        assert out == (True, 99.0, 100.0, 5.0)
        assert ex_audits == []

    def test_a_typed_market_order_is_not_this_branch(self, tmp_path, ex_audits):
        ex = LiveExecutor(state_dir=str(tmp_path))
        out = _run(ex._recalculate_limit_entry(_FakeExchange(), "BTC/USDT:USDT", _ex_idea(), "buy",
                                               None, False, None, 100.0, 100.0, 5.0, 5, 2.0))
        assert out == (False, None, 100.0, 5.0)
        assert ex_audits == []

    def test_a_signal_limit_still_takes_the_confluence_re_price(self, tmp_path, ex_audits, monkeypatch):
        seen = []
        monkeypatch.setattr(le, "calculate_entry", lambda **kw: seen.append(kw) or SimpleNamespace(
            limit_price=99.0, tier="A", size_multiplier=1.0, natural_sl=None,
            explanation="planted", confluence_count=2, levels_used=["vwap"]))
        monkeypatch.setattr(le, "recalc_sl_tp_for_shifted_entry", lambda **kw: (97.0, 103.0, True, None))
        ex = LiveExecutor(state_dir=str(tmp_path))
        idea = _ex_idea(source="scan_skill")
        out = _run(ex._recalculate_limit_entry(_FakeExchange(), "BTC/USDT:USDT", idea, "buy", None,
                                               True, 101.0, 100.0, 100.0, 5.0, 5, 2.0))
        assert out[:2] == (True, 99.0) and seen
        assert (idea.stop_loss, idea.take_profit) == (97.0, 103.0)

    @pytest.mark.parametrize("side,limit", [("buy", 100.0), ("sell", 100.0)], ids=["buy-at", "sell-at"])
    def test_a_signal_limit_exactly_at_the_market_is_re_priced_too(self, tmp_path, ex_audits, monkeypatch, side, limit):
        """Equality crosses: a limit AT the market fills at once (and post-only
        would refuse it), so the executor reads the one leaf rather than a
        strict comparison of its own. The round is what asked for this row."""
        seen = []
        monkeypatch.setattr(le, "calculate_entry", lambda **kw: seen.append(kw) or SimpleNamespace(
            limit_price=99.0 if side == "buy" else 101.0, tier="A", size_multiplier=1.0,
            natural_sl=None, explanation="planted", confluence_count=2, levels_used=["vwap"]))
        monkeypatch.setattr(le, "recalc_sl_tp_for_shifted_entry", lambda **kw: (97.0, 103.0, True, None))
        ex = LiveExecutor(state_dir=str(tmp_path))
        _run(ex._recalculate_limit_entry(_FakeExchange(), "BTC/USDT:USDT", _ex_idea(source="scan_skill"),
                                         side, None, True, limit, 100.0, 100.0, 5.0, 5, 2.0))
        assert seen, "a limit at the market was left to fill as a taker"


# ── the order that reaches the venue ─────────────────────────────────────

class _ParamVenue(_Venue):
    """The recording venue, keeping the type, price and params of the order."""

    async def create_order(self, symbol=None, type=None, side=None, amount=None,
                           price=None, params=None, **k):
        self.orders.append({"type": type, "side": side, "amount": float(amount),
                            "price": price, "params": dict(params or {})})
        raise _Sent()


def _place(tmp_path, idea, price):
    venue = _ParamVenue(_market(), price)
    ex = LiveExecutor(state_dir=tmp_path)
    ex._exchange = venue
    with patch.object(le, "audit", lambda log, msg, **kw: None), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        try:
            asyncio.run(ex.execute(idea, size_usd=100.0, order_type="limit"))
        except _Sent:
            pass
    assert venue.orders, "the order never reached the venue"
    return venue.orders[0]


def _live_idea(entry, source):
    return TradeIdea(id="TI-TIF", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=entry, stop_loss=entry * 0.98, take_profit=entry * 1.06,
                     confidence=0.8, reasoning="fixture", source=source, order_type="limit")


@pytest.fixture(autouse=True)
def _post_only_on():
    """`post_only` ON for every drive here. The config is frozen, so this is
    a write outside monkeypatch's bookkeeping -- and the first draft never
    handed it back, which left `post_only=True` on every test after this
    file for the rest of the session (the shape CLAUDE.md records for the
    gateway secret). Restored in a finally."""
    assert CONFIG.limit_orders.enabled is True, "the premise: limit orders are on"
    old = CONFIG.limit_orders.post_only
    object.__setattr__(CONFIG.limit_orders, "post_only", True)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.limit_orders, "post_only", old)


class TestATypedLimitIsSentGtcAtThePriceTyped:

    def test_a_crossing_typed_limit_goes_out_at_its_price_and_never_post_only(self, tmp_path):
        sent = _place(tmp_path, _live_idea(4010.0, "manual"), 4000.0)
        assert sent["type"] == "limit" and sent["price"] == 4010.0
        assert sent["params"].get("timeInForce") == "GTC"
        assert "post_only" not in str(sent["params"]).lower() and "postonly" not in str(sent["params"]).lower()

    def test_a_resting_typed_limit_is_gtc_too(self, tmp_path):
        """Post-only rejects the order the moment the market reaches the price
        it names, and the retry then re-prices the typed levels."""
        sent = _place(tmp_path, _live_idea(3990.0, "manual"), 4000.0)
        assert sent["price"] == 3990.0
        assert sent["params"].get("timeInForce") == "GTC"

    def test_a_signal_limit_keeps_post_only(self, tmp_path):
        sent = _place(tmp_path, _live_idea(3990.0, "scan_skill"), 4000.0)
        assert sent["price"] == 3990.0
        assert sent["params"].get("timeInForce") == "post_only"


def test_the_fixture_hands_post_only_back():
    """Driven as the generator pytest drives, through its finally; a restore
    written without one is skipped by `gen.close()`, which is the leak."""
    before = CONFIG.limit_orders.post_only
    object.__setattr__(CONFIG.limit_orders, "post_only", False)
    try:
        gen = _post_only_on.__wrapped__()
        next(gen)
        assert CONFIG.limit_orders.post_only is True
        gen.close()
        assert CONFIG.limit_orders.post_only is False, "the fixture must hand back what it found"
    finally:
        object.__setattr__(CONFIG.limit_orders, "post_only", before)

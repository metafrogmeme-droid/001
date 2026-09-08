"""The order-placement core of execute(), DRIVEN — slice 2 of the extraction.

Seven more phases left ``LiveExecutor.execute`` verbatim: the limit re-price,
tick rounding, the SUBMISSION itself with its last-mile kill switch and the
POST_ONLY retry, the post-fill slippage guard, the leverage-overshoot guard,
SL/TP placement with its flatten-on-failure, and the card the operator reads.
Every one of them used to be reachable only through a full ``execute()`` with
a venue stubbed end to end, which is why the retry's double-fill guard, the
"halt arrived during the retry" path, and "the stop could not be placed, so
the position was closed" had never been observed by a test.

Each test plants a state, calls the phase, and reads the verdict AND the
audit. The end-to-end harnesses (tests/test_pretrade_slippage_gate.py,
tests/test_halt_holds_at_order_submission.py) still drive the real
``execute()`` through all of them, so the composition is checked as well.

``CONFIG`` is a frozen dataclass; the phases that read it are given a
namespace carrying only the fields they read, so each test states exactly
which configuration it is exercising.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor
from bot.utils.models import Direction

SYM = "BTC/USDT:USDT"


def _idea(direction=Direction.LONG, entry=100.0, sl=98.0, tp=104.0, **extra):
    ns = SimpleNamespace(id="t1", asset="BTC/USDT", direction=direction, entry_price=entry,
                         stop_loss=sl, take_profit=tp, strategy_type="swing")
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


def _cfg(*, post_only=True, split=False, split_threshold=50_000.0, margin_mode="isolated",
         slippage_guard=True, max_slip_ratio=0.3, overshoot_ratio=1.5):
    return SimpleNamespace(
        exchange=SimpleNamespace(margin_mode=margin_mode),
        limit_orders=SimpleNamespace(post_only=post_only),
        execution=SimpleNamespace(order_split_enabled=split, order_split_threshold_usd=split_threshold,
                                  slippage_guard_enabled=slippage_guard,
                                  max_slippage_edge_ratio=max_slip_ratio,
                                  leverage_overshoot_max_ratio=overshoot_ratio),
    )


class FakeExchange:
    def __init__(self, balance_free=1000.0, precision="echo", ohlcv=None):
        self.balance_free = balance_free
        self.precision = precision
        self.ohlcv = ohlcv
        self.calls: list[str] = []

    async def fetch_balance(self, params=None):
        self.calls.append("fetch_balance")
        return {"USDT": {"free": self.balance_free}}

    async def fetch_ohlcv(self, symbol, tf, limit=50):
        self.calls.append("fetch_ohlcv")
        if isinstance(self.ohlcv, Exception):
            raise self.ohlcv
        return self.ohlcv

    def price_to_precision(self, symbol, price):
        self.calls.append("price_to_precision")
        if isinstance(self.precision, Exception):
            raise self.precision
        if self.precision is None:
            return None
        return str(price)

    def amount_to_precision(self, symbol, amount):
        return str(amount)


@pytest.fixture
def ex(tmp_path, monkeypatch):
    e = LiveExecutor(state_dir=str(tmp_path))
    # The Bitget pricePlace/priceEndStep snap is its own tested helper; identity
    # here keeps these tests about the phase, not about tick arithmetic.
    monkeypatch.setattr(e, "_bitget_tick_safety_net", lambda market, price: price)
    return e


@pytest.fixture
def audits(monkeypatch):
    seen: list[dict] = []

    def _spy(log, message, **kw):
        seen.append({"message": message, **kw})

    monkeypatch.setattr(le, "audit", _spy)
    return seen


def _by(audits, action):
    return [a for a in audits if a.get("action") == action]


# ── 1. limit re-price ────────────────────────────────────────────────────


def _entry_result(tier="A", limit=99.0, mult=1.0, natural_sl=None):
    return SimpleNamespace(limit_price=limit, tier=tier, size_multiplier=mult, natural_sl=natural_sl,
                           explanation="planted", confluence_count=2, levels_used=["vwap"])


@pytest.mark.asyncio
async def test_a_resting_limit_is_left_alone(ex, audits):
    # A buy limit BELOW market rests on the book: nothing to recalculate.
    out = await ex._recalculate_limit_entry(FakeExchange(), SYM, _idea(), "buy", None,
                                            True, 99.0, 100.0, 100.0, 5.0, 5, 2.0)
    assert out == (True, 99.0, 100.0, 5.0)
    assert audits == []


@pytest.mark.asyncio
async def test_a_crossing_limit_with_no_atr_downgrades_to_market(ex, audits):
    # A buy limit ABOVE market would fill as a taker; with no ATR there is no
    # offset to compute, so it becomes a market order and says so.
    use_limit, limit, size, qty = await ex._recalculate_limit_entry(
        FakeExchange(), SYM, _idea(), "buy", None, True, 101.0, 100.0, 100.0, 5.0, 5, 0.0)
    assert (use_limit, limit) == (False, None)
    assert _by(audits, "limit_downgrade")[0]["result"] == "MARKET_FALLBACK"


@pytest.mark.asyncio
async def test_tier_d_confluence_downgrades_to_market(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "calculate_entry", lambda **kw: _entry_result(tier="D", limit=98.5))
    fx = FakeExchange(ohlcv=RuntimeError("no candles"))   # the OHLCV fetch failing is tolerated
    use_limit, limit, size, qty = await ex._recalculate_limit_entry(
        fx, SYM, _idea(), "buy", None, True, 101.0, 100.0, 100.0, 5.0, 5, 2.0)
    assert (use_limit, limit) == (False, None)
    assert _by(audits, "limit_tier_d")[0]["result"] == "MARKET_FALLBACK"
    assert "fetch_ohlcv" in fx.calls


@pytest.mark.asyncio
async def test_tier_c_reduces_size_and_resizes_the_quantity(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "calculate_entry", lambda **kw: _entry_result(tier="C", limit=99.0, mult=0.5))
    monkeypatch.setattr(le, "recalc_sl_tp_for_shifted_entry", lambda **kw: (98.0, 104.0, False, None))
    use_limit, limit, size, qty = await ex._recalculate_limit_entry(
        FakeExchange(), SYM, _idea(), "buy", None, True, 101.0, 100.0, 100.0, 5.0, 5, 2.0)
    assert (use_limit, limit) == (True, 99.0)
    assert size == 50.0
    assert qty == pytest.approx(50.0 * 5 / 100.0), "the quantity must follow the reduced size"
    assert _by(audits, "limit_tier_c")[0]["result"] == "SIZE_REDUCED"


@pytest.mark.asyncio
async def test_a_moved_entry_shifts_the_stop_and_target_with_it(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "calculate_entry", lambda **kw: _entry_result(tier="A", limit=99.0))
    monkeypatch.setattr(le, "recalc_sl_tp_for_shifted_entry", lambda **kw: (97.0, 103.0, True, None))
    idea = _idea(sl=98.0, tp=104.0)
    await ex._recalculate_limit_entry(FakeExchange(), SYM, idea, "buy", None, True, 101.0, 100.0, 100.0, 5.0, 5, 2.0)
    assert (idea.stop_loss, idea.take_profit) == (97.0, 103.0), "the idea the order is built from must carry the shift"
    assert _by(audits, "limit_recalc_sltp")[0]["result"] == "SHIFTED"
    assert _by(audits, "limit_recalc_exec")[0]["result"] == "RECALCULATED"


# ── 2. tick rounding ─────────────────────────────────────────────────────


def test_the_venues_own_precision_wins(ex):
    fx = FakeExchange(precision="echo")
    assert ex._round_limit_price_to_tick(fx, {"precision": {"price": 0.01}}, SYM, 100.123) == 100.123
    assert fx.calls == ["price_to_precision"]


def test_a_failing_precision_call_falls_back_to_the_market_tick(ex):
    fx = FakeExchange(precision=RuntimeError("no such market"))
    out = ex._round_limit_price_to_tick(fx, {"precision": {"price": 0.5}}, SYM, 100.3)
    assert out == pytest.approx(100.5), "rounded to the 0.5 step"


def test_no_market_at_all_rounds_by_magnitude(ex):
    out = ex._round_limit_price_to_tick(FakeExchange(precision=None), None, SYM, 12345.6789)
    assert out == 12345.7


# ── 3. the submission ────────────────────────────────────────────────────


def _submit_args(ex, fx, *, use_limit=False, limit_price=None, atr=2.0, asset_class="Crypto",
                 size=100.0, quantity=5.0, current=100.0, idea=None):
    return (fx, fx, SYM, "buy", quantity, "coid-1", current, limit_price, use_limit,
            size, 5, True, {"info": {}}, idea or _idea(), atr, asset_class)


@pytest.mark.asyncio
async def test_a_halt_at_the_last_mile_refuses_before_any_order(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    monkeypatch.setattr(le, "trading_halted", lambda: True)
    create = AsyncMock(return_value={"id": "never"})
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    blk, order, limit, ac = await ex._submit_entry_order(*_submit_args(ex, FakeExchange()))
    assert blk and "No exposure was opened" in blk
    assert order is None
    create.assert_not_awaited()
    assert _by(audits, "live_execute")[0]["result"] == "BLOCKED_HALTED"


@pytest.mark.asyncio
async def test_an_oversized_market_order_is_blocked_rather_than_faked_as_split(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(split=True, split_threshold=50.0))
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    create = AsyncMock(return_value={"id": "never"})
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    blk, order, _, _ = await ex._submit_entry_order(*_submit_args(ex, FakeExchange(), size=100.0))
    assert blk and "split threshold" in blk
    assert order is None
    create.assert_not_awaited()
    assert _by(audits, "order_split")[0]["result"] == "BLOCKED_NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_a_market_order_is_submitted_once_with_the_sized_quantity(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    create = AsyncMock(return_value={"id": "o1", "status": "closed"})
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    blk, order, limit, ac = await ex._submit_entry_order(*_submit_args(ex, FakeExchange(), quantity=5.0))
    assert blk is None and order == {"id": "o1", "status": "closed"}
    assert create.await_count == 1
    kw = create.await_args.kwargs
    assert (kw["symbol"], kw["type"], kw["side"], kw["amount"], kw["coid"]) == (SYM, "market", "buy", 5.0, "coid-1")
    assert kw["params"].get("tradeSide") == "open", "Bitget one-way mode still tags the open side"
    assert ac == "Crypto", "a market order does not re-classify; the caller's class comes back untouched"


@pytest.mark.asyncio
async def test_a_low_balance_warns_but_does_not_stop_the_order(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    monkeypatch.setattr(ex, "_create_order_idempotent", AsyncMock(return_value={"id": "o1"}))
    blk, order, _, _ = await ex._submit_entry_order(*_submit_args(ex, FakeExchange(balance_free=10.0), size=100.0))
    assert blk is None and order["id"] == "o1"
    assert _by(audits, "live_execute")[0]["result"] == "BALANCE_WARN"


@pytest.mark.asyncio
async def test_a_limit_entry_hands_back_the_perp_symbols_class(ex, audits, monkeypatch):
    """The hand-back that keeps the deferred-stops audit byte-identical.

    On the limit path the time-in-force step re-classifies the PERP symbol
    and rebinds `asset_class`; the audit that later says which class deferred
    its stops read that rebound value inline. The method returns it so
    execute() records the same thing it always did.
    """
    monkeypatch.setattr(le, "CONFIG", _cfg(post_only=False))
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    monkeypatch.setattr(le, "_classify_symbol", lambda s: "Metal" if s.endswith(":USDT") else "Crypto")
    create = AsyncMock(return_value={"id": "o1", "status": "open"})
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    blk, order, limit, ac = await ex._submit_entry_order(
        *_submit_args(ex, FakeExchange(), use_limit=True, limit_price=99.0, asset_class="Crypto"))
    assert blk is None
    assert ac == "Metal"
    kw = create.await_args.kwargs
    assert kw["type"] == "limit" and kw["price"] == 99.0
    assert limit == 99.0


@pytest.mark.asyncio
async def test_a_post_only_rejection_reuses_an_original_that_actually_landed(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(post_only=True))
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    create = AsyncMock(side_effect=RuntimeError("post only order failed"))
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    monkeypatch.setattr(ex, "_find_order_by_client_oid",
                        AsyncMock(return_value=({"id": "orig", "status": "open"}, True)))
    blk, order, _, _ = await ex._submit_entry_order(*_submit_args(ex, FakeExchange(), use_limit=True, limit_price=99.0))
    assert blk is None and order["id"] == "orig"
    assert create.await_count == 1, "nothing was resubmitted"


@pytest.mark.asyncio
async def test_an_unverifiable_original_aborts_rather_than_risk_a_double_fill(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(post_only=True))
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    create = AsyncMock(side_effect=RuntimeError("post only order failed"))
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    monkeypatch.setattr(ex, "_find_order_by_client_oid", AsyncMock(return_value=(None, False)))
    with pytest.raises(RuntimeError, match="post only"):
        await ex._submit_entry_order(*_submit_args(ex, FakeExchange(), use_limit=True, limit_price=99.0))
    assert _by(audits, "post_only_retry")[0]["result"] == "ABORT_UNVERIFIED"
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_a_verified_absence_widens_the_offset_and_resubmits_under_a_new_coid(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(post_only=True))
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    create = AsyncMock(side_effect=[RuntimeError("post only order failed"), {"id": "retry", "status": "open"}])
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    monkeypatch.setattr(ex, "_find_order_by_client_oid", AsyncMock(return_value=(None, True)))
    idea = _idea(sl=98.0, tp=104.0)
    blk, order, limit, _ = await ex._submit_entry_order(
        *_submit_args(ex, FakeExchange(), use_limit=True, limit_price=99.5, atr=2.0, idea=idea))
    assert blk is None and order["id"] == "retry"
    assert create.await_count == 2
    retry_kw = create.await_args_list[1].kwargs
    assert retry_kw["coid"] == "coid-1-r1"
    assert retry_kw["price"] == pytest.approx(98.0), "one ATR below market for a buy"
    assert limit == pytest.approx(98.0), "the re-priced limit is handed back for the fill record"
    assert _by(audits, "post_only_retry")[0]["result"] == "WIDENING"
    # The move shifted the idea's stop and target with the entry (QC-1).
    assert idea.stop_loss < 98.0 and idea.take_profit < 104.0


@pytest.mark.asyncio
async def test_a_halt_during_the_retry_refuses_the_resubmission(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(post_only=True))
    halted = iter([False, True])
    monkeypatch.setattr(le, "trading_halted", lambda: next(halted))
    create = AsyncMock(side_effect=RuntimeError("post only order failed"))
    monkeypatch.setattr(ex, "_create_order_idempotent", create)
    monkeypatch.setattr(ex, "_find_order_by_client_oid", AsyncMock(return_value=(None, True)))
    blk, order, _, _ = await ex._submit_entry_order(*_submit_args(ex, FakeExchange(), use_limit=True, limit_price=99.5))
    assert blk and "halted while retrying" in blk
    assert order is None
    assert create.await_count == 1, "the rejected original never landed and nothing else was sent"
    halts = _by(audits, "live_execute")
    assert halts and halts[-1]["result"] == "BLOCKED_HALTED" and halts[-1]["data"]["phase"] == "post_only_retry"


@pytest.mark.asyncio
async def test_a_non_post_only_rejection_propagates_untouched(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(post_only=True))
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    monkeypatch.setattr(ex, "_create_order_idempotent", AsyncMock(side_effect=ValueError("insufficient margin")))
    with pytest.raises(ValueError, match="insufficient margin"):
        await ex._submit_entry_order(*_submit_args(ex, FakeExchange()))


@pytest.mark.asyncio
async def test_the_spot_path_is_unreachable_by_construction(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    args = list(_submit_args(ex, FakeExchange()))
    args[11] = False   # is_futures
    with pytest.raises(RuntimeError, match="Unreachable"):
        await ex._submit_entry_order(*args)


# ── 4. post-fill slippage guard ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_adverse_over_slipped_fill_is_flattened(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=True, max_slip_ratio=0.3))
    close = AsyncMock(return_value="closed at 101")
    monkeypatch.setattr(ex, "close_position", close)
    # 2% stop distance; a LONG fill 1% above entry consumes 50% of it (> 30%).
    msg, warn = await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 101.0)
    assert msg and "CLOSED for safety" in msg and "closed at 101" in msg
    assert "did NOT complete" not in msg
    assert warn == "", "a completed flatten leaves nothing for the card — the run ends here"
    close.assert_awaited_once_with("t1", reason="slippage_guard")
    assert _by(audits, "slippage_guard")[0]["result"] == "FLATTEN"


@pytest.mark.asyncio
async def test_a_flatten_that_raises_falls_through_with_an_urgent_note(ex, audits, monkeypatch):
    """It used to ABORT here: the run ended with the position open, no stop
    placed, and a card telling the operator to close it by hand. The stop is
    the only protection left on that position, so the failed arm now hands
    the fill card its warning and lets execute() carry on into placement."""
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=True, max_slip_ratio=0.3))
    monkeypatch.setattr(ex, "close_position", AsyncMock(side_effect=RuntimeError("venue down")))
    msg, warn = await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 101.0)
    assert msg is None, "an abort here is a position left with no stop"
    assert "AUTOMATIC CLOSE FAILED" in warn and "MANUALLY" in warn
    assert "venue down" not in warn, "driver text belongs in the log, not on the card"
    assert [a["result"] for a in _by(audits, "slippage_guard")] == ["FLATTEN", "CLOSE_FAILED"]


@pytest.mark.asyncio
async def test_a_close_that_answers_close_failed_is_a_failed_flatten(ex, audits, monkeypatch):
    """close_position signals a venue-side failure by RETURN VALUE, with the
    position restored to open. This answer used to be announced as 'CLOSED
    for safety', and the run aborted with no stop."""
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=True, max_slip_ratio=0.3))
    monkeypatch.setattr(ex, "close_position",
                        AsyncMock(return_value="CLOSE FAILED for t1: venue rejected"))
    msg, warn = await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 101.0)
    assert msg is None
    assert "AUTOMATIC CLOSE FAILED" in warn and "CLOSED for safety" not in warn
    assert "venue rejected" not in warn
    failed = _by(audits, "slippage_guard")[1]
    assert failed["result"] == "CLOSE_FAILED"
    assert "venue rejected" in failed["data"]["close_msg"], "the audit keeps the venue's reason"


@pytest.mark.asyncio
async def test_a_close_kept_open_is_not_announced_as_closed_by_the_slippage_guard(ex, audits, monkeypatch):
    """close_position's other non-close: it kept the position, or its
    remainder, tracked and re-protected. No second set of stops from
    execute(), and no card that says 'closed'."""
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=True, max_slip_ratio=0.3))
    kept = "⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nThe position is kept OPEN and re-protected."
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value=kept))
    msg, warn = await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 101.0)
    assert msg and "KEPT OPEN" in msg and kept in msg, msg
    assert "CLOSED for safety" not in msg
    assert warn == ""
    assert [a["result"] for a in _by(audits, "slippage_guard")] == ["FLATTEN", "NOT_CLOSED"]
    # The engine classifies execute()'s string: EXECUTION ABORTED means "no
    # position remains" and stops tracking it. This one is still there.
    assert not le.execution_indicates_failure(msg), (
        "a kept-open abort must not carry a token the engine reads as 'no position'")


@pytest.mark.asyncio
async def test_a_completed_slippage_flatten_still_reads_as_no_position(ex, audits, monkeypatch):
    """The other half of the token rule: a flatten that DID close the position
    keeps the EXECUTION ABORTED token, so the engine records no live position."""
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=True, max_slip_ratio=0.3))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value="closed at 101"))
    msg, _warn = await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 101.0)
    assert msg and le.execution_indicates_failure(msg)


@pytest.mark.asyncio
async def test_a_small_or_favourable_slip_is_kept(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=True, max_slip_ratio=0.3))
    close = AsyncMock()
    monkeypatch.setattr(ex, "close_position", close)
    assert await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 100.1) == (None, "")
    assert await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 99.0) == (None, ""), \
        "a better fill is not adverse"
    assert await ex._post_fill_slippage_guard(_idea(Direction.SHORT, entry=100.0, sl=102.0), 101.0) == (None, "")
    close.assert_not_awaited()
    assert audits == []


@pytest.mark.asyncio
async def test_the_guard_disabled_is_silent(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(slippage_guard=False))
    assert await ex._post_fill_slippage_guard(_idea(entry=100.0, sl=98.0), 110.0) == (None, "")


# ── 5. leverage overshoot guard ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_mismatch_means_nothing_happens(ex, audits):
    assert await ex._leverage_overshoot_guard(_idea(), SYM, None) == (None, False)
    assert audits == []


@pytest.mark.asyncio
async def test_an_overshoot_past_the_ratio_is_flattened_and_the_symbol_rested(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    close = AsyncMock(return_value="closed")
    rested = []
    monkeypatch.setattr(ex, "close_position", close)
    monkeypatch.setattr(ex, "_rest_symbol", lambda s: rested.append(s))
    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg and "20x" in msg and "5x" in msg and "was CLOSED" in msg
    assert "could not be set" not in msg, "the cooldown armed; the card must not say otherwise"
    assert "did NOT complete" not in msg
    assert close_failed is False
    close.assert_awaited_once_with("t1", reason="leverage_overshoot")
    assert rested == [SYM], "without the rest the engine re-signals the symbol and the venue fills at 20x again"
    assert [a["result"] for a in _by(audits, "leverage_overshoot_guard")] == ["FLATTEN"]


@pytest.mark.asyncio
async def test_an_overshoot_within_the_ratio_is_kept(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    close = AsyncMock()
    monkeypatch.setattr(ex, "close_position", close)
    assert await ex._leverage_overshoot_guard(_idea(), SYM, (5, 6)) == (None, False)
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_filling_under_the_target_never_closes(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    close = AsyncMock()
    monkeypatch.setattr(ex, "close_position", close)
    assert await ex._leverage_overshoot_guard(_idea(), SYM, (5, 3)) == (None, False)
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_flatten_reports_close_failed_and_falls_through(ex, audits, monkeypatch):
    # Falling through is the point: the SL placement that follows is the only
    # protection left on an open, over-levered position.
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(side_effect=RuntimeError("venue down")))
    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg is None and close_failed is True
    assert [a["result"] for a in _by(audits, "leverage_overshoot_guard")] == ["FLATTEN", "CLOSE_FAILED"]


@pytest.mark.asyncio
async def test_a_close_that_answers_close_failed_is_a_failed_flatten_too(ex, audits, monkeypatch):
    """THE ARM THAT WAS UNREACHABLE.

    close_position signals a venue-side failure by RETURN VALUE — 'CLOSE
    FAILED for …', with the position restored to open — and never raises for
    it. The handler above was written for a raise, so on the real failure the
    guard took the success arm: rested the symbol, headed the card CLOSED, and
    returned before the stop was placed. Same arm as the raise now.
    """
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position",
                        AsyncMock(return_value="CLOSE FAILED for t1: venue rejected"))
    rested = []
    monkeypatch.setattr(ex, "_rest_symbol", lambda s: rested.append(s))
    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg is None and close_failed is True
    assert rested == [], "a failed close is not a flatten; there is nothing to cool down"
    failed = _by(audits, "leverage_overshoot_guard")
    assert [a["result"] for a in failed] == ["FLATTEN", "CLOSE_FAILED"]
    assert "venue rejected" in failed[1]["data"]["close_msg"], "the audit keeps the venue's reason"


@pytest.mark.asyncio
async def test_an_unreadable_close_answer_is_not_a_close(ex, audits, monkeypatch):
    """close_position is typed -> str. Anything else is a reading nobody made,
    and absent is never a close."""
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value=None))
    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg is None and close_failed is True
    assert [a["result"] for a in _by(audits, "leverage_overshoot_guard")] == ["FLATTEN", "CLOSE_FAILED"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    "⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nThe position is kept OPEN and re-protected.",
    "⚠️ PARTIAL CLOSE — RESIDUAL REMAINS: LONG BTC/USDT\nPosition kept OPEN (tracking the remainder).",
    "Position t1 not found or already closed/closing.",
])
async def test_a_close_kept_open_is_neither_announced_closed_nor_given_a_second_stop(
        ex, audits, monkeypatch, answer):
    """close_position's other non-closes. The position is still there — in
    whole, in part, or under another path's close — and close_position already
    re-protected what it could. So the guard must not claim a close, and must
    not let execute() put a second set of stops on it: it aborts, honestly."""
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value=answer))
    rested = []
    monkeypatch.setattr(ex, "_rest_symbol", lambda s: rested.append(s))
    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg and "KEPT OPEN" in msg and answer in msg, msg
    assert "was CLOSED" not in msg
    assert "nothing more has been placed" not in msg, (
        "close_position's own line says whether a stop was re-placed; the card must not deny it")
    assert close_failed is True
    assert rested == [SYM], "the sticky leverage is on the symbol either way"
    assert [a["result"] for a in _by(audits, "leverage_overshoot_guard")] == ["FLATTEN", "NOT_CLOSED"]
    assert not le.execution_indicates_failure(msg), (
        "a kept-open abort must not carry a token the engine reads as 'no position'")


@pytest.mark.asyncio
async def test_a_completed_overshoot_flatten_still_reads_as_no_position(ex, audits, monkeypatch):
    """The other half of the token rule, on this guard."""
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value="closed"))
    monkeypatch.setattr(ex, "_rest_symbol", lambda s: None)
    msg, _cf = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg and le.execution_indicates_failure(msg)


@pytest.mark.asyncio
async def test_a_raise_after_a_kept_open_answer_does_not_say_closed(ex, audits, monkeypatch):
    """The flag that stops the fall-through is armed for a kept-open answer
    too, and the outer handler used to word its card from the flag: CLOSED,
    about a position close_position had just kept. Same contrived geometry as
    the closed case above — the NOT_CLOSED audit raising."""
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(
        return_value="⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nkept OPEN and re-protected."))
    monkeypatch.setattr(ex, "_rest_symbol", lambda s: None)
    real_spy = le.audit

    def _audit_that_dies_on_not_closed(log, message, **kw):
        if kw.get("result") == "NOT_CLOSED":
            raise RuntimeError("audit sink down")
        return real_spy(log, message, **kw)
    monkeypatch.setattr(le, "audit", _audit_that_dies_on_not_closed)

    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg and "KEPT OPEN" in msg and "Reporting the details afterwards failed" in msg, msg
    assert "was CLOSED" not in msg and "EXECUTION ABORTED" not in msg
    assert close_failed is True


@pytest.mark.asyncio
async def test_an_error_after_a_successful_flatten_does_not_fail_open(ex, audits, monkeypatch):
    """The one case fail-open is WRONG: the close already happened, so falling
    through would place a stop and a take-profit against a position that no
    longer exists — resting orders that can open a new one later.

    The path that reaches the outer handler with `_lev_flattened` set is a
    handler itself raising: the close succeeded, the cooldown bookkeeping
    raised into its own except, and THAT handler's audit raised. Contrived,
    but it is precisely the path the flag was added for, and the first draft
    of this test had the geometry wrong (see the test below for what a plain
    post-close failure actually does).
    """
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value="closed"))

    def _boom(s):
        raise RuntimeError("rest bookkeeping failed")
    monkeypatch.setattr(ex, "_rest_symbol", _boom)

    real_spy = le.audit

    def _audit_that_dies_on_rest_failed(log, message, **kw):
        if kw.get("result") == "REST_FAILED":
            raise RuntimeError("audit sink down")
        return real_spy(log, message, **kw)
    monkeypatch.setattr(le, "audit", _audit_that_dies_on_rest_failed)

    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg and "was CLOSED" in msg and "Reporting the details afterwards failed" in msg
    assert close_failed is False


@pytest.mark.asyncio
async def test_a_post_close_bookkeeping_failure_still_reports_the_close(ex, audits, monkeypatch, caplog):
    """The edge #299 pinned and did not fix.

    `_rest_symbol` used to run inside the close's own try, so a failure there
    AFTER a successful flatten was caught as "flatten FAILED": CLOSE_FAILED in
    the audit, OPEN on the card, and — worse than the misreport — a
    fall-through into SL/TP placement for a position that no longer existed.
    The rest has its own handler now. The close is reported as the close it
    was, the cooldown failure is said in its own words on the card and in the
    audit, and the driver's exception text stays in the log.
    """
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value="closed"))

    def _boom(s):
        raise RuntimeError("rest bookkeeping failed")
    monkeypatch.setattr(ex, "_rest_symbol", _boom)
    msg, close_failed = await ex._leverage_overshoot_guard(_idea(), SYM, (5, 20))
    assert msg and "was CLOSED" in msg and "could not be set" in msg, msg
    assert close_failed is False
    assert "rest bookkeeping failed" not in msg, "driver text belongs in the log, not on the card"
    assert "rest bookkeeping failed" in caplog.text, "…and the log is where it must be"
    rest = _by(audits, "leverage_overshoot_guard")
    assert [a["result"] for a in rest] == ["FLATTEN", "REST_FAILED"]
    assert rest[1]["data"]["error"] == "RuntimeError"
    assert (rest[1]["data"]["requested"], rest[1]["data"]["actual"]) == (5, 20), (
        "the rows on this action are shaped alike, so a reader can join them")


# ── 6. SL/TP placement and the flatten-on-failure ────────────────────────


def _position():
    return SimpleNamespace(sl_order_id="stale", tp_order_id="stale")


@pytest.mark.asyncio
async def test_deferred_stops_place_nothing_and_say_so(ex, audits, monkeypatch):
    place = AsyncMock()
    monkeypatch.setattr(ex, "_place_sl_tp", place)
    out = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), True, True, "Metal")
    assert out == (None, None, None)
    place.assert_not_awaited()
    d = _by(audits, "defer_tp_sl")[0]
    assert d["result"] == "DEFERRED" and d["data"]["class"] == "Metal"


@pytest.mark.asyncio
async def test_stops_placed_first_time_come_back_as_ids(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(return_value=("sl1", "tp1")))
    out = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), False, False, "Crypto")
    assert out == (None, "sl1", "tp1")
    assert audits == []


@pytest.mark.asyncio
async def test_a_missing_stop_is_retried_once(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(side_effect=[(None, "tp1"), ("sl2", None)]))
    out = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), False, False, "Crypto")
    assert out == (None, "sl2", "tp1"), "the retry's stop and the first pass's target are both kept"
    assert _by(audits, "sl_retry")[0]["result"] == "RETRY"


@pytest.mark.asyncio
async def test_a_stop_that_cannot_be_placed_flattens_the_position(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(side_effect=[(None, "tp1"), (None, None)]))
    close = AsyncMock(return_value="closed")
    monkeypatch.setattr(ex, "close_position", close)
    pos = _position()
    msg, sl, tp = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, pos, False, False, "Crypto")
    assert msg and "stop-loss could not be placed" in msg and "CLOSED for safety" in msg
    close.assert_awaited_once_with("t1", reason="sl_placement_failed")
    assert pos.sl_order_id is None and pos.tp_order_id == "tp1", "the record must say what was actually on the venue"
    assert _by(audits, "sl_tp_failed")[0]["result"] == "FLATTEN"


@pytest.mark.asyncio
async def test_a_stop_failure_whose_flatten_also_fails_is_urgent(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(ex, "close_position", AsyncMock(side_effect=RuntimeError("venue down")))
    msg, _, _ = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), False, False, "Crypto")
    assert msg and "URGENT" in msg and "NO stop-loss" in msg
    assert "venue down" not in msg, "driver text belongs in the log, not on the card"
    assert not le.execution_indicates_failure(msg), "a live, stop-less position must be tracked"


@pytest.mark.asyncio
async def test_a_stop_failure_whose_flatten_answers_close_failed_is_urgent_too(ex, audits, monkeypatch):
    """The fifth reader. This is the flatten that runs when the stop could NOT
    be placed, and a returned "CLOSE FAILED" used to be announced as "CLOSED
    for safety" — open, no stop, told it was closed."""
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(ex, "close_position",
                        AsyncMock(return_value="CLOSE FAILED for t1: venue rejected"))
    msg, _, _ = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), False, False, "Crypto")
    assert msg and "URGENT" in msg and "NO stop-loss" in msg, msg
    assert "CLOSED for safety" not in msg and "venue rejected" not in msg
    assert [a["result"] for a in _by(audits, "sl_tp_failed")] == ["FLATTEN", "FLATTEN_FAILED"]
    assert "venue rejected" in _by(audits, "sl_tp_failed")[1]["data"]["close_msg"]


@pytest.mark.asyncio
async def test_a_stop_failure_whose_flatten_is_kept_open_says_so(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(return_value=(None, None)))
    kept = "⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nkept OPEN and re-protected."
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value=kept))
    msg, _, _ = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), False, False, "Crypto")
    assert msg and "KEPT OPEN" in msg and kept in msg, msg
    assert "CLOSED for safety" not in msg
    assert not le.execution_indicates_failure(msg)
    assert [a["result"] for a in _by(audits, "sl_tp_failed")] == ["FLATTEN", "NOT_CLOSED"]


@pytest.mark.asyncio
async def test_a_stop_failure_whose_flatten_completed_still_reads_as_no_position(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_place_sl_tp", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(ex, "close_position", AsyncMock(return_value="closed"))
    msg, _, _ = await ex._place_entry_stops(FakeExchange(), _idea(), 5.0, _position(), False, False, "Crypto")
    assert msg and "CLOSED for safety" in msg and le.execution_indicates_failure(msg)


# ── 7. the card ──────────────────────────────────────────────────────────


def _card(ex, **over):
    kw = dict(idea=_idea(), side="buy", leverage=5, is_futures=True, fill_price=100.0, filled_qty=5.0,
              cost=100.0, order_id="o1", sl_id="sl1", tp_id="tp1", trailing_st=None, confirmed=True,
              position_confirmed=True, verify={"failure_stage": ""}, exchange_fees=0.0,
              _lev_mismatch=None, _lev_close_failed=False)
    kw.update(over)
    return ex._entry_filled_card(**kw)


def test_a_clean_fill_renders_confirmed_with_both_orders(ex, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    c = _card(ex)
    assert "LIVE BUY BTC/USDT" in c and "5x" in c
    assert "CONFIRMED (order + position)" in c
    assert "SL order: sl1" in c and "TP order: tp1" in c
    assert "FAILED" not in c and "LEVERAGE" not in c


def test_an_unprotected_fill_says_so_on_the_card(ex, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    c = _card(ex, sl_id=None, tp_id=None)
    assert "SL/TP FAILED — position unprotected" in c
    assert "SL: pending" in c and "TP: pending" in c


def test_a_kept_leverage_mismatch_is_reported_as_kept(ex, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg(overshoot_ratio=1.5))
    c = _card(ex, leverage=6, _lev_mismatch=(5, 6))
    assert "LEVERAGE: venue filled at <b>6x</b>" in c and "target was 5x" in c
    assert "the position was kept" in c
    assert "AUTOMATIC CLOSE FAILED" not in c


def test_a_mismatch_whose_close_failed_is_not_reported_as_informational(ex, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    c = _card(ex, leverage=20, _lev_mismatch=(5, 20), _lev_close_failed=True)
    assert "AUTOMATIC CLOSE FAILED" in c and "close it MANUALLY" in c
    assert "the position was kept" not in c


def test_a_slippage_flatten_that_failed_reaches_the_card(ex, monkeypatch):
    """The slippage guard hands the card its warning instead of aborting, so
    the run can place the stop; the card must then carry it, or the operator
    reads a clean fill about a position the guard tried to close."""
    monkeypatch.setattr(le, "CONFIG", _cfg())
    warn = "\n🚨 <b>SLIPPAGE 1.20% — AUTOMATIC CLOSE FAILED</b>\nclose it MANUALLY on the venue."
    c = _card(ex, _slip_warn=warn)
    assert "SLIPPAGE 1.20% — AUTOMATIC CLOSE FAILED" in c and "SL order: sl1" in c
    assert "SLIPPAGE" not in _card(ex), "no warning, no line"


def test_an_unconfirmed_fill_names_the_failure_stage(ex, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    c = _card(ex, confirmed=False, position_confirmed=False, verify={"failure_stage": "fetch_order"})
    assert "UNCONFIRMED (fetch_order)" in c


def test_fees_and_trailing_render_only_when_present(ex, monkeypatch):
    monkeypatch.setattr(le, "CONFIG", _cfg())
    assert "Fees:" not in _card(ex)
    assert "Fees: <code>$0.1234</code>" in _card(ex, exchange_fees=0.1234)
    assert "Trailing: ✅ armed" in _card(ex, trailing_st=object())

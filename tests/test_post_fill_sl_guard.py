"""
RC-AUD-001 parity on the post-fill entry paths (limit-fill + drift→market fallback).

`_reattempt_post_fill_sl` is now a full ESCALATION LADDER with the same
end-state guarantee as the synchronous market path: on SL failure it retries
once, then runs the bounded grace sub-loop (re-protect / close on breach), then
FLATTENS — the position ends protected, closed, or with an URGENT manual-close
message. It never ends silently "unprotected (monitoring active)". Returns
``(sl_id, tp_id, close_msg)``; ``close_msg`` non-None means the ladder closed
the position (or the close failed) and callers must surface it.

These cover the helper directly (no network — _place_sl_tp, the grace
sub-loop, and close_position are stubbed).
"""

import inspect
from unittest.mock import AsyncMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.models import Direction
from tests.source_scan import code_only


def _pos(sl_id=None, tp_id=None, sl=95.0, tp=110.0):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=sl, take_profit=tp,
        sl_order_id=sl_id, tp_order_id=tp_id, status="open",
    )


def _exec(tmp_path, monkeypatch, place_results, grace=None, close=None):
    """place_results: (sl, tp) tuples for successive _place_sl_tp calls.
    grace: async callable for _guard_unprotected_grace (default: no-op → None).
    close: AsyncMock for close_position (default returns "CLOSED test")."""
    e = LiveExecutor(state_dir=str(tmp_path))
    seq = list(place_results)
    calls = {"place": 0}

    async def _fake_place(exchange, symbol, direction, qty, sl, tp):
        calls["place"] += 1
        return seq.pop(0) if seq else (None, None)

    monkeypatch.setattr(e, "_place_sl_tp", _fake_place)

    async def _default_grace(exchange, pos):
        return None

    e._guard_unprotected_grace = grace or _default_grace
    e.close_position = close or AsyncMock(return_value="CLOSED test")
    return e, calls


@pytest.mark.asyncio
async def test_sl_placed_first_try_is_noop(tmp_path, monkeypatch):
    # Common case: SL placed → no retry, ids unchanged, no close, not flagged.
    e, calls = _exec(tmp_path, monkeypatch, [("sl1", "tp1")])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, "sl1", "tp1", "T1")
    assert (sl_id, tp_id, close_msg) == ("sl1", "tp1", None)
    assert calls["place"] == 0                 # no retry attempted
    assert getattr(p, "unprotected", False) is False
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_sl_fails_then_retry_succeeds(tmp_path, monkeypatch):
    # SL None on the entry attempt; the single retry gets it on → protected.
    e, calls = _exec(tmp_path, monkeypatch, [("sl_retry", "tp_retry")])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id == "sl_retry" and close_msg is None
    assert calls["place"] == 1                 # retried exactly once
    assert getattr(p, "unprotected", False) is False
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_fails_then_grace_places_stop(tmp_path, monkeypatch):
    # Retry fails → grace sub-loop gets the exchange stop on: protected, marker
    # cleared, grace's ids adopted, NO flatten.
    async def _grace_places(exchange, pos):
        pos.sl_order_id = "SL-G"
        return None

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_places)
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp1", "T1")
    assert sl_id == "SL-G" and close_msg is None
    assert getattr(p, "unprotected", False) is False   # cleared once protected
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_grace_breach_close_propagates_without_double_close(tmp_path, monkeypatch):
    # Grace closed the position on breach → its message propagates and the
    # flatten stage must NOT fire a second close.
    async def _grace_closed(exchange, pos):
        return "CLOSED_LOCAL breach msg"

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_closed)
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert close_msg == "CLOSED_LOCAL breach msg"
    e.close_position.assert_not_awaited()              # no double close


@pytest.mark.asyncio
async def test_grace_exhausted_flattens(tmp_path, monkeypatch):
    # Retry + grace both fail to protect → FLATTEN (RC-AUD-001 parity).
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    e.close_position.assert_awaited_once_with("T1", reason="sl_placement_failed")
    assert close_msg and "CLOSED for safety" in close_msg


@pytest.mark.asyncio
async def test_flatten_failure_returns_urgent_message(tmp_path, monkeypatch):
    # The safety close itself fails → URGENT manual-intervention message, no raise.
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)],
                     close=AsyncMock(side_effect=RuntimeError("venue down")))
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert close_msg and "URGENT" in close_msg and "MANUALLY" in close_msg


@pytest.mark.asyncio
async def test_retry_exception_swallowed_ladder_continues(tmp_path, monkeypatch):
    # A raising retry must not abort the ladder — grace then flatten still run.
    e = LiveExecutor(state_dir=str(tmp_path))

    async def _boom(*a, **k):
        raise RuntimeError("venue error")

    monkeypatch.setattr(e, "_place_sl_tp", _boom)

    async def _no_grace(exchange, pos):
        return None

    e._guard_unprotected_grace = _no_grace
    e.close_position = AsyncMock(return_value="CLOSED test")
    p = _pos()
    sl_id, _, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    e.close_position.assert_awaited_once()
    assert close_msg is not None


@pytest.mark.asyncio
async def test_grace_exception_swallowed_flatten_still_runs(tmp_path, monkeypatch):
    # Even a raising grace sub-loop must not leave the position naked — flatten.
    async def _grace_boom(exchange, pos):
        raise RuntimeError("ticker outage")

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_boom)
    p = _pos()
    sl_id, _, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    e.close_position.assert_awaited_once()
    assert close_msg is not None


@pytest.mark.asyncio
async def test_no_stop_level_never_flags_or_flattens(tmp_path, monkeypatch):
    # stop_loss == 0 means no stop intended → no retry, no grace, no flatten.
    e, calls = _exec(tmp_path, monkeypatch, [("x", "y")])
    p = _pos(sl=0.0, tp=0.0)
    sl_id, _, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None and close_msg is None
    assert calls["place"] == 0
    assert getattr(p, "unprotected", False) is False
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_flatten_failure_by_return_string_is_detected(tmp_path, monkeypatch):
    # THE production failure mode (adversarial review, critical): close_position
    # never raises for venue errors — it RETURNS "CLOSE FAILED for ...". The
    # ladder must detect that and go URGENT, never claim "CLOSED for safety".
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)],
                     close=AsyncMock(return_value="CLOSE FAILED for T1: venue 5xx"))
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert close_msg and "URGENT" in close_msg and "MANUALLY" in close_msg
    assert "CLOSED for safety" not in close_msg
    assert "CLOSE FAILED for T1" in close_msg     # underlying detail preserved


@pytest.mark.asyncio
async def test_a_flatten_kept_open_by_close_position_is_not_announced_as_closed(tmp_path, monkeypatch):
    # close_position's other non-close: it kept the position (or its remainder)
    # tracked and re-protected. The old check knew only "CLOSE FAILED", so
    # this answer printed "position CLOSED for safety".
    kept = "⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nkept OPEN and re-protected."
    e, _ = _exec(tmp_path, monkeypatch, [(None, None), (None, None)],
                 close=AsyncMock(return_value=kept))
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert close_msg and "KEPT OPEN" in close_msg and kept in close_msg
    assert "CLOSED for safety" not in close_msg and "URGENT" not in close_msg


@pytest.mark.asyncio
async def test_the_ladder_hands_back_what_the_close_left_not_what_it_stamped(tmp_path, monkeypatch):
    # The ladder stamps the TP leg onto the position BEFORE it flattens, so
    # close_position's cancel pass can remove it. It then used to return that
    # same pre-flatten tp_id, and every caller writes the returned pair straight
    # onto the position — re-stamping a cancelled id over the None the close
    # had left, which blocked the periodic stop check (it re-places only on
    # EMPTY ids). What comes back must be what close_position left.
    async def _close_that_clears(trade_id, reason="bot_auto", close_price=0):
        p.sl_order_id = None
        p.tp_order_id = None
        return "CLOSE FAILED for T1: venue 5xx"

    e, _ = _exec(tmp_path, monkeypatch, [(None, "tp-pre"), (None, None)],
                 close=AsyncMock(side_effect=_close_that_clears))
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp-pre", "T1")
    assert close_msg and "URGENT" in close_msg
    assert (sl_id, tp_id) == (None, None), "the cancelled TP must not come back"


@pytest.mark.asyncio
async def test_the_ladder_hands_back_the_stop_close_position_re_placed(tmp_path, monkeypatch):
    async def _close_that_reprotects(trade_id, reason="bot_auto", close_price=0):
        p.sl_order_id = "re-sl"
        p.tp_order_id = "re-tp"
        return "⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nkept OPEN, re-protected (stop re-sl)"

    e, _ = _exec(tmp_path, monkeypatch, [(None, "tp-pre"), (None, None)],
                 close=AsyncMock(side_effect=_close_that_reprotects))
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp-pre", "T1")
    assert close_msg and "KEPT OPEN" in close_msg
    assert (sl_id, tp_id) == ("re-sl", "re-tp"), (
        "the stop close_position re-placed must survive the caller's write-back")


@pytest.mark.asyncio
async def test_grace_close_failed_string_escalates_to_flatten(tmp_path, monkeypatch):
    # A failed grace breach-close (returns "CLOSE FAILED ...") must NOT be
    # treated as a completed close — the ladder continues to the flatten stage.
    async def _grace_failed_close(exchange, pos):
        return "CLOSE FAILED for T1: venue 5xx"

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_failed_close)
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    e.close_position.assert_awaited_once()        # flatten stage ran
    assert close_msg and "CLOSED for safety" in close_msg


@pytest.mark.asyncio
async def test_the_retry_hands_back_its_own_tp_not_the_first_attempts(tmp_path, monkeypatch):
    # The classic path places SL then TP in separate tries, so the first
    # attempt can return (None, tp). The retry's _place_sl_tp cancels every
    # resting plan order before it places — the first TP is GONE — and then
    # `if tp_id is None and retry_tp` kept the dead first id and dropped the
    # live retry TP: the record named a dead TP beside a live SL, the periodic
    # check (which fires on an EMPTY id) never refreshed it, and the TP the
    # retry placed went untracked.
    e, calls = _exec(tmp_path, monkeypatch, [("sl-retry", "tp-retry")])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp-first", "T1")
    assert (sl_id, tp_id, close_msg) == ("sl-retry", "tp-retry", None)
    assert calls["place"] == 1


@pytest.mark.asyncio
async def test_a_retry_that_placed_nothing_leaves_the_first_tp_named(tmp_path, monkeypatch):
    # Nothing landed on the retry: whether its cleanup ran is unknown, and the
    # ladder goes on to grace/flatten with what it has.
    e, _ = _exec(tmp_path, monkeypatch, [(None, None), (None, None)])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp-first", "T1")
    assert sl_id is None and close_msg is not None
    assert p.tp_order_id == "tp-first", "stamped before the escalation, so the flatten cancels it"


@pytest.mark.asyncio
async def test_the_other_two_retry_sites_hand_back_their_own_tp_too(tmp_path, monkeypatch):
    """The same three lines live in `_place_entry_stops` (the primary market
    entry) and in `adopt_exchange_positions`. Both call `_place_sl_tp` a
    second time, whose cleanup cancels the first attempt's TP, and both kept
    the dead first id — which is verbatim the failure the ladder's fix
    describes. Asking which OTHER surface makes the same claim is the rule;
    this pins all three."""
    src = inspect.getsource(LiveExecutor)
    body = code_only(src)
    assert "if tp_id is None:\n" not in body.replace("\r\n", "\n"), (
        "a retry site still keeps the first attempt's TP id over the retry's own")
    # …and the three sites each name what the retry placed.
    assert body.count("sl_id = retry_sl\n") + body.count("sl_id = retry_sl\r\n") >= 3
    assert body.count("tp_id = retry_tp") >= 3


@pytest.mark.asyncio
async def test_tp_leg_is_stamped_before_escalation(tmp_path, monkeypatch):
    # When the ladder escalates, the TP id must already be on the position so
    # close_position can cancel the leg — otherwise a live TP trigger survives
    # the flatten as an orphan on the exchange.
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)])
    p = _pos()
    await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp-leg-1", "T1")
    assert p.tp_order_id == "tp-leg-1"

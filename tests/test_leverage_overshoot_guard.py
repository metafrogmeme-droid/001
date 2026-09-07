"""A confirmed 4x leverage overshoot must not be answered with a warning label.

LIVE INCIDENT, 2026-08-17. A LIVE APT/USDT SHORT was approved at 5x and Bitget
filled it at 20x — the venue's sticky per-symbol leverage surviving the
set_leverage call. Real equity, ~$782. The bot detected it, wrote it to the
audit trail, and appended this to the card:

    LEVERAGE: venue filled at 20x, target was 5x (sticky per-symbol setting).
    Margin/liquidation math follows 20x.

Every word true. The position stayed open. What that costs, against that trade's
own 3.1% stop:

     5x   liquidation ~20.0% adverse   corridor 16.9 points
    20x   liquidation ~ 5.0% adverse   corridor  1.9 points

(first-order — maintenance margin and fees pull liquidation nearer still). At 5x
the stop has room to work. At 20x a gap or a wick that overshoots the stop by two
points liquidates instead of stopping out. The card told the operator, after the
fill, about risk the risk engine never approved.

WHY THIS FILE EXISTS RATHER THAN A SOURCE SCAN

The guard sits ~3,500 lines inside `execute_trade`, past an order placement and
two verification round-trips. Nothing can drive it from a unit test. Its sibling
one block above — the slippage guard, which flattens an over-slipped fill for the
identical reason — is checked only by `assert "slippage_guard" in src`
(tests/test_roadmap_p0.py:57). That assertion passes whether or not the guard
works, which is the failure mode CLAUDE.md names: a source scan standing in for
behaviour nothing else tests.

So the decision was extracted into `leverage_overshoot_verdict`, a pure total
function, and the behaviour is tested here. What remains genuinely unreachable —
that the guard is REACHED, and that it sits above SL/TP placement — is scanned,
which is the one thing scans are for.

THE ORDERING IS LOAD-BEARING, NOT INCIDENTAL. `_place_sl_tp` runs BELOW this
guard. Closing from there therefore cannot orphan a stop or take-profit on the
venue, because none exist yet. If SL/TP placement ever moves above the guard,
closing would leave live resting orders against a position that no longer
exists — so that ordering is pinned below.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.config import CONFIG
from bot.core.live_executor import leverage_overshoot_verdict as verdict
from tests.source_scan import code_only

SRC = (Path(__file__).resolve().parent.parent
       / "bot" / "core" / "live_executor.py").read_text(encoding="utf-8")
CODE = code_only(SRC)

DEFAULT = 1.5


def _method_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """Every class-level method as (name, def line, end line), in file order."""
    heads = [(m.group(1), i) for i, ln in enumerate(lines)
             if (m := re.match(r"    (?:async )?def (\w+)\(", ln))]
    ends = [i for _, i in heads[1:]] + [len(lines)]
    return [(name, start, end) for (name, start), end in zip(heads, ends)]


def _venue():
    """The live_executor suite's mock venue, borrowed the way
    test_halt_holds_at_order_submission borrows it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_le_fixtures_overshoot",
        Path(__file__).resolve().parent / "test_live_executor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._mock_exchange()


# ── the incident itself ───────────────────────────────────────────────────────

def test_the_live_incident_closes():
    """5x approved, 20x filled — ratio 4.0. This is the case that happened."""
    v = verdict(5, 20, DEFAULT)
    assert v["decision"] == "close", v
    assert v["ratio"] == 4.0
    assert "20x" in v["why"] and "5x" in v["why"], (
        "the reason must name both leverages — an operator reading the audit "
        "trail should not have to reconstruct which was which")


def test_venue_rounding_does_not_flatten_a_good_position():
    """A 5x target filling at 6x is ordinary venue behaviour, and a guard that
    closes on it will be turned off within a day — which is how the fail-closed
    pre-order path died on 2026-07-21."""
    assert verdict(5, 6, DEFAULT)["decision"] == "keep"
    assert verdict(5, 7, DEFAULT)["decision"] == "keep"   # 1.4, just inside
    assert verdict(10, 15, DEFAULT)["decision"] == "keep"  # exactly 1.5, not >


def test_an_undershoot_is_never_closed():
    """Filling UNDER the target is less risk than approved, not more. Closing
    here would be the guard causing the loss it exists to prevent."""
    for want, got in ((5, 3), (20, 5), (10, 1)):
        v = verdict(want, got, DEFAULT)
        assert v["decision"] == "keep", (want, got, v)


def test_exactly_at_the_limit_is_kept_not_closed():
    """Boundary. The comparison is `>`, so ratio == limit keeps — stated here
    because flipping it to `>=` is a one-character change that silently starts
    flattening positions at the tolerance the operator configured as acceptable."""
    assert verdict(10, 15, 1.5)["decision"] == "keep"
    assert verdict(10, 16, 1.5)["decision"] == "close"


# ── the house rule: unreadable is not "fine" ─────────────────────────────────

def test_an_unreadable_leverage_is_unknown_and_not_keep():
    """`unknown` must be its own verdict.

    Collapsing it into `keep` would read a leverage nobody could parse as a
    leverage that was approved — absence presented as a measurement. The caller
    keeps the position either way (the pre-order path governs unverifiable
    leverage and is deliberately fail-open), but the two must be distinguishable
    or the audit trail cannot tell them apart afterwards.
    """
    for bad in (None, "", "twenty", float("nan")):
        v = verdict(5, bad, DEFAULT)
        assert v["decision"] == "unknown", (bad, v)
        assert v["ratio"] is None
    assert verdict(0, 20, DEFAULT)["decision"] == "unknown"
    assert verdict(5, 0, DEFAULT)["decision"] == "unknown"
    assert verdict(-5, 20, DEFAULT)["decision"] == "unknown"


def test_unknown_never_claims_a_ratio():
    """A ratio computed from an unreadable input is a manufactured number, and
    it would be printed in the audit record beside real ones."""
    for args in ((None, 20), (5, None), (0, 0)):
        assert verdict(args[0], args[1], DEFAULT)["ratio"] is None


def test_the_function_is_total():
    """It runs inside the fill path. Raising would take out a live execution
    that has already placed an order."""
    for want in (None, 0, -1, 5, "5", 1e9, float("inf")):
        for got in (None, 0, -1, 20, "20", 1e9, float("inf")):
            v = verdict(want, got, DEFAULT)
            assert v["decision"] in ("close", "keep", "unknown"), (want, got, v)


# ── the knob ─────────────────────────────────────────────────────────────────

def test_the_limit_is_configurable_and_can_disable_the_guard():
    assert verdict(5, 20, 100.0)["decision"] == "keep", (
        "an operator must be able to stand the guard down without editing code")
    assert verdict(5, 6, 1.0)["decision"] == "close", (
        "a limit of 1.0 means close on ANY overshoot")


def test_the_configured_default_is_the_one_documented():
    assert CONFIG.execution.leverage_overshoot_max_ratio == DEFAULT, (
        "this file's cases are written against the shipped default; if it "
        "moves, the boundary tests above are measuring a limit nobody runs")


# ── wiring: the parts a unit test genuinely cannot reach ─────────────────────

def test_the_guard_is_actually_reached_from_the_fill_path():
    """A pure function nothing calls is indistinguishable from one that does not
    work — and this whole file would still pass."""
    assert "leverage_overshoot_verdict(" in CODE
    calls = [m.start() for m in re.finditer(r"leverage_overshoot_verdict\(", CODE)]
    assert len(calls) >= 2, (
        "expected the definition plus at least one call site, found "
        f"{len(calls)} occurrence(s) — the guard is defined and never invoked")


def test_the_guard_runs_before_sl_tp_orders_are_placed():
    """THE ORDERING THAT MAKES CLOSING SAFE.

    If `_place_sl_tp` ever moves above the guard, a flatten would leave resting
    stop/take-profit orders on the venue against a position that no longer
    exists. Nothing else in the suite would notice.
    """
    # CHECKED AT EVERY CALL SITE, and conditional on what that method does.
    #
    # The first version took the FIRST verdict call in the file and required
    # _place_sl_tp in its method. That held while `execute` was the only caller.
    # When the guard was extended to the other three fill paths
    # (_check_pending_limit, _adopt_partial_fill, _execute_drift_market_fallback)
    # via the shared `_guard_fill_leverage` helper, the first call moved into
    # that helper — which contains no _place_sl_tp at all — and the test failed
    # with its own "the fill path was restructured" message. It was right: the
    # arrangement had changed and the check no longer described it.
    #
    # The real invariant is per site:
    #
    #   verdict AND _place_sl_tp in one method  -> the verdict must come FIRST,
    #                                              so a flatten cannot orphan
    #                                              orders that do not exist yet
    #   verdict WITHOUT _place_sl_tp            -> the stop is already live, so
    #                                              the flatten must go through
    #                                              close_position, which cancels
    #                                              it (pinned in
    #                                              test_fill_leverage_guard_all_paths)
    lines = CODE.splitlines()
    call_lns = [i for i, ln in enumerate(lines)
                if "leverage_overshoot_verdict(" in ln
                and not ln.lstrip().startswith("def ")]
    assert call_lns, "the verdict is never called — the guard is unwired"

    checked_ordering = 0
    for guard_ln in call_lns:
        start = max(i for i in range(guard_ln, -1, -1)
                    if re.match(r"\s*(async )?def ", lines[i]))
        end = next((i for i in range(guard_ln + 1, len(lines))
                    if re.match(r"    (async )?def ", lines[i])), len(lines))
        placements = [i for i in range(start, end)
                      if "await self._place_sl_tp(" in lines[i]]
        if not placements:
            continue          # the already-protected shape; covered elsewhere
        checked_ordering += 1
        assert all(guard_ln < p for p in placements), (
            f"the leverage guard (line ~{guard_ln}) now runs AFTER SL/TP "
            f"placement (lines ~{placements}) — closing from it would orphan "
            "live stop and take-profit orders on the venue")

    # THE ENTRY PATH, ONE LEVEL DOWN. Slice 2 of the execute() extraction
    # moved the verdict into `_leverage_overshoot_guard` and the placement
    # into `_place_entry_stops`, so no single method holds both any more and
    # the loop above has nothing to check — which is exactly the vacuity its
    # guard below was written to catch, and it did. The invariant did not
    # move: execute() must reach the verdict before it reaches a placement.
    # So the scan follows each call ONE level: whichever method execute()
    # calls that itself calls the verdict is the guard site, and whichever it
    # calls that itself awaits _place_sl_tp is a placement site. A renamed or
    # re-cut helper is still found; a placement moved back inline is too.
    spans = _method_spans(lines)
    verdict_methods = {n for n, s, e in spans
                       if any("leverage_overshoot_verdict(" in lines[i] for i in range(s + 1, e))}
    placing_methods = {n for n, s, e in spans
                       if any("await self._place_sl_tp(" in lines[i] for i in range(s + 1, e))}
    entry = [(s, e) for n, s, e in spans if n == "execute"]
    assert len(entry) == 1, f"expected exactly one execute(), found {len(entry)}"
    ex_start, ex_end = entry[0]
    guard_calls = [i for i in range(ex_start + 1, ex_end)
                   if any(f"self.{m}(" in lines[i] for m in verdict_methods)]
    placements = [i for i in range(ex_start + 1, ex_end)
                  if "await self._place_sl_tp(" in lines[i]
                  or any(f"self.{m}(" in lines[i] for m in placing_methods)]
    assert guard_calls, (
        "execute() no longer reaches the leverage-overshoot verdict, directly "
        "or through a helper — the entry fill is unguarded")
    assert placements, (
        "execute() no longer reaches _place_sl_tp, directly or through a "
        "helper — an entry that places no stop is a different bug")
    checked_ordering += 1
    late = [p for p in placements if any(g > p for g in guard_calls)]
    assert not late, (
        f"execute() reaches SL/TP placement (lines ~{late}) BEFORE the "
        f"leverage guard (lines ~{guard_calls}) — a flatten from the guard "
        "would orphan live stop and take-profit orders on the venue")

    assert checked_ordering >= 1, (
        "no site was checked, so this test asserted nothing. Either the entry "
        "path was restructured past one level of indirection, or every path "
        "now flattens through close_position — if the latter is deliberate, "
        "delete this test rather than let it pass vacuously")


async def test_a_flatten_from_the_entry_path_places_no_stop(tmp_path):
    """THE ORDERING, DRIVEN.

    The scan above pins where the calls sit; this runs the real `execute()`
    through them. The venue reports the fill at four times the largest
    leverage the executor may ask for — the 2026-08-17 ratio, at any target
    the risk engine picks for this fixture — and the position must be closed
    WITHOUT a stop or take-profit ever having been placed. A placement that
    moved above the guard shows up here as `_place_sl_tp` awaited on the way
    to the close: the orphaned-order hazard the module docstring names,
    observed rather than inferred from line numbers.
    """
    from bot.core.live_executor import LiveExecutor
    from bot.utils.models import Direction, TradeIdea

    venue = _venue()
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    venue_lev = 4 * int(CONFIG.exchange.max_leverage)
    ex._verify_position_exists = AsyncMock(return_value={
        "confirmed": True, "leverage": venue_lev,
        "exchange_entry": 100_000.0, "exchange_qty": 0.0001})
    ex._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    ex.close_position = AsyncMock(return_value="closed")
    idea = TradeIdea(
        id="TI-OVERSHOOT-001", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100_000.0, stop_loss=98_000.0, take_profit=105_000.0,
        confidence=0.85, reasoning="overshoot fixture")

    with patch.object(type(CONFIG), "is_live", return_value=True):
        result = await ex.execute(idea, size_usd=10.0)

    venue.create_order.assert_called()      # the entry went in: this guard is post-fill
    ex.close_position.assert_awaited_once_with("TI-OVERSHOOT-001", reason="leverage_overshoot")
    ex._place_sl_tp.assert_not_awaited()
    assert "EXECUTION ABORTED" in result and f"{venue_lev}x" in result, result
    assert ex._preflight_check(10.0, symbol="BTC/USDT") is not None, (
        "the flattened symbol must be resting, or the engine re-enters it at "
        "the same sticky leverage — the fee pump")


async def test_a_cooldown_failure_after_the_flatten_places_no_stop(tmp_path):
    """THE RESURRECTION HAZARD, DRIVEN THROUGH execute().

    The flatten succeeds and the cooldown bookkeeping raises. Until the
    follow-up to #299 the close's own handler caught that: it audited
    CLOSE_FAILED, told the card the position was OPEN, and let execute()
    carry on into SL/TP placement — a stop and a take-profit on the venue for
    a position that no longer existed, which can fill later and open a new
    one in the opposite direction. `_lev_flattened` was meant to stop exactly
    that, and never got read, because the handler that swallowed the error
    sits before the one that consults the flag.

    Same run now: it ends at the close. No stop placed, the card says
    CLOSED, the cooldown failure is said in its own words, and the driver's
    exception text stays in the log.
    """
    from bot.core.live_executor import LiveExecutor
    from bot.utils.models import Direction, TradeIdea

    venue = _venue()
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    venue_lev = 4 * int(CONFIG.exchange.max_leverage)
    ex._verify_position_exists = AsyncMock(return_value={
        "confirmed": True, "leverage": venue_lev,
        "exchange_entry": 100_000.0, "exchange_qty": 0.0001})
    ex._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    ex.close_position = AsyncMock(return_value="closed")

    def _rest_bookkeeping_dies(symbol):
        raise RuntimeError("rest store unavailable")
    ex._rest_symbol = _rest_bookkeeping_dies
    idea = TradeIdea(
        id="TI-OVERSHOOT-002", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100_000.0, stop_loss=98_000.0, take_profit=105_000.0,
        confidence=0.85, reasoning="overshoot fixture, cooldown failing")

    with patch.object(type(CONFIG), "is_live", return_value=True):
        result = await ex.execute(idea, size_usd=10.0)

    ex.close_position.assert_awaited_once_with("TI-OVERSHOOT-002", reason="leverage_overshoot")
    ex._place_sl_tp.assert_not_awaited()
    assert "EXECUTION ABORTED" in result and "was CLOSED" in result, result
    assert "could not be set" in result, "the operator must hear that the symbol is not resting"
    # The abort path never reaches the fill card, so this cannot fail on its
    # own; it pins the old behaviour's visible symptom beside the await count.
    assert "AUTOMATIC CLOSE FAILED" not in result
    assert "rest store unavailable" not in result, "driver text belongs in the log, not on the card"


def _venue_that_rejects_the_close(venue):
    """The entry order fills; every order after it — the guard's reduce-only
    close — is refused. Returns the list of orders the venue was asked for."""
    entry_fill = {"id": "ORD-001", "average": 100_000.0, "filled": 0.0001,
                  "cost": 10.0, "status": "filled"}
    orders = []

    async def _create_order(*args, **kwargs):
        orders.append((args, kwargs))
        if len(orders) == 1:
            return entry_fill
        raise RuntimeError("venue rejected the close")

    venue.create_order = AsyncMock(side_effect=_create_order)
    return orders


async def test_a_venue_that_rejects_the_flatten_still_gets_a_stop(tmp_path):
    """THE FAILURE THE GUARD WAS WRITTEN FOR, THROUGH THE REAL close_position.

    close_position answers a venue-side rejection by RETURN VALUE — 'CLOSE
    FAILED for …', status restored to open — and never raises for it. The
    guard's failed-close arm waited for a raise, so on this exact run it took
    the success arm instead: rested the symbol, headed the card CLOSED, and
    returned before any stop was placed. An open, over-levered position with
    nothing protecting it, announced as closed.

    Here the venue accepts the entry and refuses the close, nothing about
    close_position is mocked, and the run must carry on into stop placement
    with the card saying the close FAILED.
    """
    from bot.core.live_executor import LiveExecutor
    from bot.utils.models import Direction, TradeIdea

    venue = _venue()
    orders = _venue_that_rejects_the_close(venue)
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    venue_lev = 4 * int(CONFIG.exchange.max_leverage)
    ex._verify_position_exists = AsyncMock(return_value={
        "confirmed": True, "leverage": venue_lev,
        "exchange_entry": 100_000.0, "exchange_qty": 0.0001})
    ex._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    idea = TradeIdea(
        id="TI-OVERSHOOT-003", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100_000.0, stop_loss=98_000.0, take_profit=105_000.0,
        confidence=0.85, reasoning="overshoot fixture, venue refusing the close")

    with patch.object(type(CONFIG), "is_live", return_value=True):
        result = await ex.execute(idea, size_usd=10.0)

    assert len(orders) == 2, "the entry, then the attempted close"
    ex._place_sl_tp.assert_awaited_once()
    assert "AUTOMATIC CLOSE FAILED" in result and f"{venue_lev}x" in result, result
    assert "was CLOSED" not in result and "EXECUTION ABORTED" not in result
    assert "venue rejected the close" not in result, "driver text belongs in the log, not on the card"
    assert ex._positions["TI-OVERSHOOT-003"].status == "open"
    assert ex._positions["TI-OVERSHOOT-003"].sl_order_id == "SL-1"
    # Read the rest map itself: _preflight_check refuses this symbol anyway,
    # because the position is (correctly) still open and tracked.
    assert not ex._leverage_blocked_until, (
        "a failed close is not a flatten; there is nothing to cool down")


async def test_an_over_slipped_fill_whose_flatten_fails_still_gets_a_stop(tmp_path):
    """The sibling guard one block above, same contract, and a stricter old
    behaviour: it ABORTED on a failed close, so the position never reached
    stop placement at all. Now the failed arm hands the card its warning and
    the run carries on.
    """
    from bot.core.live_executor import LiveExecutor
    from bot.utils.models import Direction, TradeIdea

    assert CONFIG.execution.slippage_guard_enabled, (
        "the shipped default is ON; if it moved, this test is not driving the guard")
    ratio = float(CONFIG.execution.max_slippage_edge_ratio)
    # A 2% stop; the venue reports a fill well past `ratio` of it, still
    # inside the stop.
    venue_entry = 100_000.0 * (1 + min(0.019, 2 * ratio * 0.02))

    venue = _venue()
    orders = _venue_that_rejects_the_close(venue)
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    ex._verify_position_exists = AsyncMock(return_value={
        "confirmed": True, "leverage": 0,          # no read-back: only the slip trips
        "exchange_entry": venue_entry, "exchange_qty": 0.0001})
    ex._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    idea = TradeIdea(
        id="TI-SLIP-001", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100_000.0, stop_loss=98_000.0, take_profit=105_000.0,
        confidence=0.85, reasoning="slippage fixture, venue refusing the close")

    with patch.object(type(CONFIG), "is_live", return_value=True):
        result = await ex.execute(idea, size_usd=10.0)

    assert len(orders) == 2, "the entry, then the attempted close"
    ex._place_sl_tp.assert_awaited_once()
    assert "SLIPPAGE" in result and "AUTOMATIC CLOSE FAILED" in result, result
    assert "CLOSED for safety" not in result and "EXECUTION ABORTED" not in result
    assert "venue rejected the close" not in result
    assert ex._positions["TI-SLIP-001"].sl_order_id == "SL-1"


async def test_a_close_the_venue_accepted_but_could_not_confirm_places_no_stop(tmp_path):
    """THE OTHER WAY "CLOSE FAILED" USED TO BE WRONG — through the real close.

    close_position's final handler catches everything after the close order
    was ACCEPTED too: a raise while verifying or booking the result used to
    come back as "CLOSE FAILED" with the record restored to open. On a book
    the venue has most likely just flattened, the guard's failed arm would
    then place a stop and a take-profit — resting orders against nothing.
    That answer is CLOSE NOT CONFIRMED now: the guard keeps the record for the
    next reconcile, places nothing, and the card does not carry the token the
    engine reads as "no position remains".
    """
    from bot.core.live_executor import LiveExecutor, execution_indicates_failure
    from bot.utils.models import Direction, TradeIdea

    venue = _venue()                       # create_order answers every order, entry and close
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    venue_lev = 4 * int(CONFIG.exchange.max_leverage)
    ex._verify_position_exists = AsyncMock(return_value={
        "confirmed": True, "leverage": venue_lev,
        "exchange_entry": 100_000.0, "exchange_qty": 0.0001})
    ex._verify_position_closed = AsyncMock(side_effect=RuntimeError("book unreadable"))
    ex._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    idea = TradeIdea(
        id="TI-OVERSHOOT-004", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100_000.0, stop_loss=98_000.0, take_profit=105_000.0,
        confidence=0.85, reasoning="overshoot fixture, close accepted but unconfirmed")

    with patch.object(type(CONFIG), "is_live", return_value=True):
        result = await ex.execute(idea, size_usd=10.0)

    assert venue.create_order.await_count == 2, "the entry, then the close the venue accepted"
    ex._place_sl_tp.assert_not_awaited()
    assert "KEPT OPEN" in result and "CLOSE NOT CONFIRMED" in result, result
    assert "was CLOSED" not in result and "AUTOMATIC CLOSE FAILED" not in result
    assert "book unreadable" not in result, "driver text belongs in the log, not on the card"
    assert not execution_indicates_failure(result), (
        "the record is kept for reconcile; the engine must keep tracking it")
    assert ex._positions["TI-OVERSHOOT-004"].status == "open"
    assert ex._leverage_blocked_until, "the sticky leverage is on the symbol either way"


def test_the_pre_order_fail_open_path_is_untouched():
    """The 2026-07-21 reversion must survive this change.

    Failing closed on UNVERIFIABLE leverage blocked BTC/ETHFI entirely
    ("trades can not open") and was deliberately reverted. This guard fires only
    on a CONFIRMED read-back, which is the case with no false positives.
    """
    assert 'os.environ.get("LEVERAGE_FAIL_OPEN", "1")' in CODE, (
        "the pre-order path's fail-OPEN default changed — that is a separate "
        "mechanism with its own live history, not this guard's to alter")


def test_the_guard_does_not_become_a_fee_pump(tmp_path):
    """THE LOOP THE FIX ITSELF WOULD CREATE.

    Bitget's sticky per-symbol leverage does not heal because we closed. Flatten
    alone means: engine re-signals -> venue fills at 20x again -> guard flattens
    again, paying a round-trip fee every cycle and looking from outside exactly
    like a bot trading badly. The block is the difference between a guard and a
    fee pump, so it is tested as behaviour rather than trusted as a comment.

    It used to plant `_leverage_blocked_until[sym]` and read back the same
    literal, which is why it never noticed that the guard rests the PERP symbol
    while the check is called with the SPOT one. It goes through the write path
    now, and asks under the spelling `execute` really uses.
    """
    from bot.core.live_executor import LiveExecutor

    ex = LiveExecutor(state_dir=str(tmp_path))
    perp = "APT/USDT:USDT"     # what `symbol` has become by the guard
    asset = "APT/USDT"         # idea.asset, what _preflight_check is given

    assert ex._preflight_check(10.0, symbol=asset) is None, (
        "an unblocked symbol must pass — if this fails the test below proves "
        "nothing, because everything is blocked")

    ex._rest_symbol(perp)
    err = ex._preflight_check(10.0, symbol=asset)
    assert err is not None, "a flattened symbol must be refused, not re-entered"
    assert "resting" in err and "minute" in err, (
        f"the refusal must say how long and why, not just BLOCKED: {err!r}")
    assert ex._preflight_check(10.0, symbol="BTC/USDT:USDT") is None, (
        "the block is PER SYMBOL — one sticky-leverage symbol must not halt "
        "the whole book")


def test_the_block_expires_and_is_not_permanent(tmp_path):
    """A block that never lifts is a symbol quietly delisted by a bug."""
    from bot.core.live_executor import LiveExecutor

    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._rest_symbol("APT/USDT:USDT", seconds=-1.0)      # already expired
    assert ex._preflight_check(10.0, symbol="APT/USDT") is None
    assert not ex._leverage_blocked_until, (
        "an expired block should be dropped, not left to accumulate for the "
        "lifetime of the process — and dropped under the key it was stored "
        "under, or it never leaves")


def test_a_raise_after_a_successful_flatten_does_not_place_sl_tp():
    """THE RESURRECTION HAZARD.

    The guard's outer handler fails open — correct for a guard that did
    nothing, wrong for one that already closed the position. Falling through
    would call _place_sl_tp and leave a live stop and take-profit on the venue
    for a position that no longer exists; those can fill later and open a NEW
    position in the opposite direction.
    """
    assert "_lev_flattened" in CODE, "the flag that makes fail-open safe is gone"
    handler = CODE[CODE.find("except Exception as _lev_guard_exc"):]
    handler = handler[:handler.find("self.record_api_success()")]
    assert "if _lev_flattened:" in handler, (
        "the guard's outer except no longer checks whether the position was "
        "already closed before continuing into SL/TP placement")
    assert "return" in handler, (
        "having flattened, the handler must return rather than fall through")


def test_a_failed_close_does_not_silently_hold_an_over_levered_position():
    """The dangerous branch: the guard decided the position should not exist
    and could not remove it. It must not fall back to the ordinary
    'venue filled at Nx' note, which reads as informational."""
    assert "_lev_close_failed" in CODE
    assert "AUTOMATIC CLOSE FAILED" in SRC, (
        "a failed flatten must say so in the notification, not reuse the "
        "informational mismatch wording")

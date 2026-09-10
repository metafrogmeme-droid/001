"""The corollary sweep of the close-verification fix, on the two reads left.

Seventeen sites in the tree read `contracts` by hand as
`float(p.get("contracts", 0) or 0)` — the expression `position_presence`'s
docstring quotes as the defect. Most are display listings where a wrong claim
costs a missing row on a card. Two of them decide money, and those are here.

WHAT `contracts: null` MEANS AND WHY IT IS NOT ZERO. `float(None or 0)` is
0.0, and every one of these sites branches on `> 0`, so a row the venue
declined to size is indistinguishable from a row stating no exposure. The
venue returning a position row AT ALL is evidence of a position; the size
being absent is evidence about the payload, not about the book.

────────────────────────────────────────────────────────────────────────────
SITE 1 — `_verify_position_exists`, the entry read.

An unsized row on our symbol and side matched nothing, so the loop finished
with `state` still "absent". That is the LOUD wrong answer:

    🚨 order confirmed, but the venue reports NO POSITION for it —
       check the venue before trading again

plus the leverage-overshoot guard silently skipped (`_lev_mismatch` is set
only under `if position_confirmed:`) and `leverage_went_unverified` returning
False, so not even the audit line that exists for exactly this. The retry
added alongside it cannot help: a row that is consistently unsized is a
stable wrong answer, not a transient one.

The method already had the right word — `unreadable` — and a row-level
unreadable simply could not reach it.

────────────────────────────────────────────────────────────────────────────
SITE 2 — `reconcile_positions`, and this one had already written down the rule
it was breaking.

    # Fail-safe: if ccxt doesn't report a usable side, treat it
    # as present (broad check) so we never falsely close a live
    # position.

That comment sits three lines above:

    if abs(float(p.get("contracts", 0) or 0)) <= 0:
        return False

An unreadable SIDE is treated as present, exactly as the comment says. An
unreadable SIZE, one line up, is treated as absent. Same row, two fields,
opposite handling. The non-hedge branch had neither guard.

"The downstream real-close-data requirement backstops this either way" is
true and BOUNDED: close data is required, retried three times a tick and
deferred for ten ticks — and then the close is booked anyway, under a comment
reading "The position IS gone from the venue". That sentence is derived from
`has_position`, so a row nobody could size ends as a booked close on a live
position that is no longer tracked.

`rows_for_side` turns out to BE the rule the comment states: it drops a row
only when its side is stated and different, so ours-or-ambiguous is exactly
what survives.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import (
    LiveExecutor,
    leverage_went_unverified,
)
from bot.core.order_state import position_presence, rows_for_side

SYMBOL = "APT/USDT:USDT"


def _sized(qty=3.0, side="long", **over):
    row = {"symbol": SYMBOL, "side": side, "contracts": qty,
           "entryPrice": 5.0, "markPrice": 5.1, "unrealizedPnl": 0.3,
           "initialMargin": 0.75, "leverage": 20}
    row.update(over)
    return row


# ── SITE 1: the entry read ───────────────────────────────────────────────

class _Book:
    def __init__(self, rows):
        self._rows = rows
        self.calls = 0

    async def fetch_positions(self, symbols, params=None):
        self.calls += 1
        return self._rows


def _probe(rows, direction="LONG"):
    import asyncio
    ex = LiveExecutor.__new__(LiveExecutor)
    return asyncio.run(LiveExecutor._verify_position_exists(
        ex, _Book(rows), SYMBOL, direction, max_attempts=1, delay=0))


class TestTheEntryReadStopsCallingAnUnsizedRowAbsent:
    @pytest.mark.parametrize("unsized", [
        {"symbol": SYMBOL, "side": "long", "contracts": None},
        {"symbol": SYMBOL, "side": "long", "contracts": ""},
        {"symbol": SYMBOL, "side": "long"},
        {"symbol": SYMBOL, "side": "long", "contracts": "n/a"},
    ])
    def test_it_reads_unreadable_not_absent(self, unsized):
        """The 🚨 card says 'check the venue before trading again'. Off a row
        we could not size, that is a confident negative from no data."""
        got = _probe([unsized])
        assert got["state"] == "unreadable", (
            "an unsized row still reports the venue holds NO POSITION")
        assert got["confirmed"] is False

    @pytest.mark.parametrize("unsized", [
        {"symbol": SYMBOL, "side": "long", "contracts": None},
        {"symbol": SYMBOL, "side": "long", "contracts": "n/a"},
    ])
    def test_and_that_reaches_the_audit_the_gap_already_had(self, unsized):
        """`unreadable` is the state `leverage_went_unverified` keys on, so
        the disarmed guard finally leaves a trace. Under `absent` it did not:
        the predicate is False and nothing is written."""
        got = _probe([unsized])
        assert leverage_went_unverified(got["state"], got["confirmed"])

    def test_a_genuinely_empty_book_is_still_absent(self):
        """The control, and the reason this is not just "fail unreadable".
        An empty list IS the venue answering."""
        got = _probe([])
        assert got["state"] == "absent"
        assert not leverage_went_unverified(got["state"], got["confirmed"])

    def test_a_measured_zero_is_still_absent(self):
        """`contracts: 0` is the venue stating no exposure — a reading."""
        got = _probe([_sized(qty=0)])
        assert got["state"] == "absent"

    def test_the_other_side_is_still_absent(self):
        """A hedge-mode short is not our long. Dropping it is what lets a
        genuinely-absent long report absent rather than unreadable."""
        got = _probe([_sized(side="short")])
        assert got["state"] == "absent"

    def test_another_symbol_is_still_absent(self):
        got = _probe([_sized(symbol="ETH/USDT:USDT")])
        assert got["state"] == "absent"

    def test_a_sized_row_still_confirms_with_its_numbers(self):
        got = _probe([_sized()])
        assert got["state"] == "found"
        assert got["confirmed"] is True
        assert got["leverage"] == 20
        assert got["exchange_qty"] == 3.0
        assert got["exchange_entry"] == 5.0

    def test_a_sized_row_wins_over_an_unsized_sibling(self):
        """One readable position settles the question; the unreadable row
        beside it must not downgrade a real confirmation."""
        got = _probe([{"symbol": SYMBOL, "side": "long", "contracts": None},
                      _sized()])
        assert got["state"] == "found"
        assert got["leverage"] == 20

    def test_a_row_that_will_not_say_whose_it_is_is_not_adopted(self):
        """Sized, could be ours — but its numbers would become the position
        record, and "there is exposure here" is a weaker claim than "these
        are its figures". Unreadable, not found."""
        got = _probe([{"contracts": 3.0, "entryPrice": 5.0, "leverage": 20}])
        assert got["state"] == "unreadable"
        assert got["confirmed"] is False

    def test_a_non_dict_row_is_unreadable_rather_than_skipped(self):
        got = _probe(["nonsense"])
        assert got["state"] == "unreadable"

    def test_the_short_side_reads_the_same_way(self):
        got = _probe([{"symbol": SYMBOL, "side": "short", "contracts": None}],
                     direction="SHORT")
        assert got["state"] == "unreadable"


# ── SITE 2: reconcile_positions ──────────────────────────────────────────

class TestTheSweepAppliesItsOwnFailSafeToTheSizeToo:
    """The rule is the comment's: ours-or-ambiguous survives, and anything
    but a confident flat keeps the position."""

    def test_rows_for_side_is_the_rule_the_comment_states(self):
        """The hedge branch's `_present` was `side == want or side not in
        ("long","short")`. That is exactly "drop only what is definitely
        somebody else's", which is what the shared helper does."""
        want = "long"
        ours = {"symbol": SYMBOL, "side": "long", "contracts": 1}
        theirs = {"symbol": SYMBOL, "side": "short", "contracts": 1}
        ambiguous = {"symbol": SYMBOL, "contracts": 1}
        junk_side = {"symbol": SYMBOL, "side": "", "contracts": 1}
        kept = rows_for_side([ours, theirs, ambiguous, junk_side], SYMBOL, want)
        assert ours in kept and ambiguous in kept
        assert theirs not in kept
        # An empty-string side is stated and is not ours — the old expression
        # kept it (`"" not in ("long","short")`). Either reading is defensible;
        # what matters is that the CONTRACTS half no longer decides it.
        assert position_presence(kept)["state"] == "present"

    @pytest.mark.parametrize("rows,expected", [
        ([], "flat"),
        ([{"symbol": SYMBOL, "side": "long", "contracts": 0}], "flat"),
        ([{"symbol": SYMBOL, "side": "long", "contracts": 2}], "present"),
        ([{"symbol": SYMBOL, "side": "long", "contracts": None}], "unreadable"),
        ([{"symbol": SYMBOL, "side": "long", "contracts": "n/a"}], "unreadable"),
        (["nonsense"], "unreadable"),
    ])
    def test_the_three_answers_the_sweep_now_gets(self, rows, expected):
        assert position_presence(
            rows_for_side(rows, SYMBOL, "long"))["state"] == expected

    def test_only_a_confident_flat_may_book_a_close(self):
        """`has_position = state != "flat"`, so present AND unreadable both
        keep the position. That is the asymmetry the file already states:
        keeping a position that DID close is recoverable; booking a close that
        did NOT happen is not."""
        for rows in ([{"symbol": SYMBOL, "side": "long", "contracts": None}],
                     [{"symbol": SYMBOL, "side": "long", "contracts": 2}],
                     ["nonsense"]):
            state = position_presence(rows_for_side(rows, SYMBOL, "long"))["state"]
            assert state != "flat", (
                f"{rows} would book a close on a position nobody could rule out")


class TestTheStopSyncTakesOurSideRatherThanTheFirstRow:
    """A SECOND defect, found by the source assertion below failing.

    The sweep's "position still on exchange" branch syncs SL/TP from the
    venue, and it took the FIRST row with size out of an unfiltered list.
    `fetch_positions([symbol])` in hedge mode returns BOTH sides, so with a
    long and a short open at once the short's `stopLoss`, `takeProfit` and
    order ids were written onto the long — whichever the venue listed first.

    Three hundred lines above, the branch deciding whether this position still
    exists is entirely about matching the tracked side. The branch that
    overwrites its stop was not, and `pos.stop_loss` is what the local monitor
    enforces.
    """

    def _reconciled(self, rows, direction="LONG", tmp=None):
        import asyncio

        from bot.core.live_executor import LivePosition
        ex = LiveExecutor()
        ex._hedge_mode = True
        pos = LivePosition(
            trade_id="T1", symbol="APT/USDT", direction=direction,
            entry_price=5.0, quantity=3.0, cost_usd=0.75,
            stop_loss=4.0, take_profit=7.0, status="open",
            sl_order_id="OURS-SL", tp_order_id="OURS-TP")
        ex._positions = {"T1": pos}
        venue = AsyncMock()
        venue.fetch_positions = AsyncMock(return_value=rows)
        ex._get_exchange = AsyncMock(return_value=venue)
        ex._save_positions = lambda *a, **k: None
        with patch.object(live_executor_mod, "_POSITIONS_FILE", "/dev/null"):
            asyncio.run(ex.reconcile_positions())
        return pos

    def test_the_opposite_sides_stop_is_not_written_onto_ours(self):
        short_first = {"symbol": "APT/USDT:USDT", "side": "short",
                       "contracts": 2.0,
                       "info": {"stopLoss": "9.9", "takeProfit": "1.1",
                                "stopLossId": "THEIRS-SL",
                                "takeProfitId": "THEIRS-TP"}}
        ours = {"symbol": "APT/USDT:USDT", "side": "long", "contracts": 3.0,
                "info": {"stopLoss": "4.5", "takeProfit": "7.5",
                         "stopLossId": "REAL-SL", "takeProfitId": "REAL-TP"}}
        pos = self._reconciled([short_first, ours])
        assert pos.stop_loss != 9.9, (
            "the SHORT's stop was written onto the long — the local monitor "
            "now enforces a stop from the other side of the book")
        assert pos.sl_order_id != "THEIRS-SL"
        assert pos.stop_loss == 4.5 and pos.sl_order_id == "REAL-SL"
        assert pos.take_profit == 7.5 and pos.tp_order_id == "REAL-TP"

    def test_an_unsized_row_syncs_nothing_rather_than_zero(self):
        """It also must not sync FROM a row it could not size — and must not
        wipe what we already had."""
        pos = self._reconciled([
            {"symbol": "APT/USDT:USDT", "side": "long", "contracts": None,
             "info": {"stopLoss": "4.5", "stopLossId": "REAL-SL"}}])
        assert pos.stop_loss == 4.0 and pos.sl_order_id == "OURS-SL"
        assert pos.status == "open", (
            "an unsized row must not book the close either")


class TestTheSweepIsWiredToThatReading:
    """Reachability — the drives above prove the RULE; only the caller shows
    the rule is the one in force, which is the whole `_lev_mismatch` lesson."""

    def test_reconcile_asks_the_shared_reading(self):
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(
            LiveExecutor.reconcile_positions))
        assert "position_presence(" in src, (
            "the sweep went back to counting contracts itself")
        assert 'p.get("contracts", 0) or 0' not in src, (
            "the expression position_presence's docstring quotes as the defect "
            "is back in the sweep that books closes")

    def test_it_keeps_the_position_on_anything_but_flat(self):
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor.reconcile_positions))
        assert '!= "flat"' in src, (
            "has_position no longer fails safe: only a confident flat may "
            "conclude the position is gone")

    def test_the_entry_read_asks_it_too(self):
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(
            LiveExecutor._verify_position_exists))
        assert "rows_for_side(" in src and "read_amount(" in src
        assert 'p.get("contracts", 0) or 0' not in src


class TestTheDisplaySitesWereLeftAloneDeliberately:
    """Fourteen more sites share the shape and are NOT fixed here.

    *Check reachability before fixing.* A wrong claim on a listing costs a
    missing row on a card, which is visible; a wrong claim on these two costs
    a booked close on a live position and a 🚨 about a position that is fine.
    Sweeping all seventeen would be a large refactor bought with no safety —
    the `track.js` / `arena_trades.pnl` lesson.

    Pinned so the omission is a decision on the record rather than something
    that looks like an oversight to the next reader.
    """

    def test_the_shape_still_exists_elsewhere_and_that_is_known(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        hits = []
        for path in list((root / "bot").rglob("*.py")):
            if "order_state.py" in str(path):
                continue          # the docstring QUOTES it, by design
            for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r'get\(["\']contracts["\'](, 0)?\)\s*or\s*0', line):
                    hits.append(f"{path.relative_to(root)}:{i}")
        assert hits, (
            "every hand-rolled contracts read is gone — delete this test and "
            "the paragraph above it, which is now describing nothing")
        for decided in ("live_executor.py:26", "live_executor.py:114"):
            assert not any(h.startswith(f"bot/core/{decided}") for h in hits), (
                f"a site this PR fixed is back: {hits}")

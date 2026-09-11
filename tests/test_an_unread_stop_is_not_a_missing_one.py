"""The stop-protection verdict, and the seven states it got wrong.

`_stop_live_on_exchange` carries a three-valued contract in its own docstring::

    True = a stop is attached (position protected); False = the exchange
    reports NO stop (genuinely unprotected); None = could not verify.
    ...  Gate re-placement on a POSITIVE False.

and computed all three from::

    for p in positions:
        if abs(float(p.get("contracts", 0) or 0)) <= 0:
            continue
        info = p.get("info", {}) or {}
        return float(info.get("stopLoss") or 0) > 0

which is an expression with TWO outputs, over a list nobody filtered.

WHAT FALSE COSTS. The caller re-places on a positive False, and `_place_sl_tp`
CANCELS existing plan orders before it places. So a False manufactured from an
absence tears down a working stop and rebuilds it, opening the naked window
every self-heal cycle — the audit-HIGH failure the whole branch was written to
close. That makes "unreadable" the one input that must never resolve to False,
which is the ordinary rule of this repo pointed at the most expensive claim in
the product.

WHAT TRUE COSTS. A naked position told it is protected is simply left naked.

Both were reachable, from one unfiltered loop:

    hedge: the long's stop answers for the short   -> True   (naked, "protected")
    hedge: the short's stop answers for the long   -> False  (protected, torn down)
    a row for a DIFFERENT symbol answers           -> True
    my row unsized, the other side's row answers   -> True
    stopLoss null / key absent / no info at all    -> False  (torn down)

`fetch_positions([sym])` is a REQUEST filter, not a promise: `exchange_sync`'s
UTA fallback exists precisely because this venue's answer and the question
disagree. And three hundred lines up, the branch deciding whether a position is
still OPEN is entirely about matching the tracked side; the branch deciding
whether it still has a STOP was not.

THE ID IS WHAT KEEPS THE FIX FROM DISABLING THE CURE. If a blank `stopLoss`
were simply unreadable, a position that genuinely has no stop would read None
for ever and never be repaired — honest and useless, which is its own kind of
wrong. `stop_attached` is two-field: a positive price OR a non-empty
`stopLossId` is a stop; the fields being PRESENT and empty is the venue
reporting an unprotected position; only fields that are absent, or hold
something unparseable, are a silence.

AND ONE MUTATION SURVIVED BY BEING EQUIVALENT, which is worth separating from
a survivor that is a gap. Replacing `stop_attached`'s ``if not
isinstance(info, dict): return None`` with ``info = info if isinstance(info,
dict) else {}`` survived — and it should have, because ``{}`` already answers
None by every path below it. DELETING the guard, rather than respelling it,
killed five tests. A surviving mutation is a question, not a verdict: the
answer here was "the guard is covered and the mutation was a no-op", and the
answer one case over (the or-zero size read) was a real hole. Both are written
up below rather than only the one that cost a test.
"""

import asyncio

import pytest

from bot.core.live_executor import LiveExecutor
from bot.core.order_state import stop_attached

# ── the reading, driven directly ──────────────────────────────────────────

class TestAPositiveStopIsTrue:
    def test_a_price(self):
        assert stop_attached({"stopLoss": "95.0"}) is True

    def test_a_numeric_price(self):
        assert stop_attached({"stopLoss": 95.0}) is True

    def test_an_id_alone_is_enough(self):
        """The second witness. A venue that reports the attached order but not
        its trigger price still says, unambiguously, that a stop is there."""
        assert stop_attached({"stopLoss": "", "stopLossId": "1180012"}) is True

    def test_an_id_outvotes_a_zero_price(self):
        """Contradictory, and only one direction of it is safe: tearing down an
        order the venue just named is the expensive mistake."""
        assert stop_attached({"stopLoss": "0", "stopLossId": "1180012"}) is True


class TestAnEmptyFieldIsAReport:
    """PRESENT AND EMPTY IS A MEASUREMENT. This is the half that keeps the
    self-heal able to repair anything at all."""

    def test_a_stated_zero(self):
        assert stop_attached({"stopLoss": "0"}) is False

    def test_a_numeric_zero(self):
        assert stop_attached({"stopLoss": 0}) is False

    @pytest.mark.parametrize("blank", ["", None])
    def test_a_blank_price_beside_a_blank_id(self, blank):
        assert stop_attached({"stopLoss": blank, "stopLossId": blank}) is False

    @pytest.mark.parametrize("blank", ["", None])
    def test_a_blank_price_alone(self, blank):
        """The venue sent the field. That it left it empty is what it has to
        say about this position's stop."""
        assert stop_attached({"stopLoss": blank}) is False

    def test_a_blank_id_alone(self):
        assert stop_attached({"stopLossId": ""}) is False

    def test_a_zero_id_is_a_sentinel_not_an_order(self):
        assert stop_attached({"stopLossId": "0"}) is False


class TestASilenceIsNone:
    """THE THIRD VALUE. Every one of these was False before."""

    def test_no_stop_fields_at_all(self):
        assert stop_attached({"symbol": "BTC/USDT", "holdSide": "long"}) is None

    def test_an_empty_payload(self):
        assert stop_attached({}) is None

    @pytest.mark.parametrize("junk", [None, "", [], 0, "not a dict"])
    def test_no_payload(self, junk):
        assert stop_attached(junk) is None

    @pytest.mark.parametrize("junk", ["n/a", "null", "--", object()])
    def test_an_unparseable_price_with_nothing_to_corroborate_it(self, junk):
        assert stop_attached({"stopLoss": junk}) is None

    def test_an_unparseable_price_is_still_outvoted_by_a_real_id(self):
        assert stop_attached({"stopLoss": "n/a", "stopLossId": "118"}) is True

    def test_an_unparseable_price_beside_a_blank_id_is_a_report(self):
        """The id field was sent and is empty, which is the venue saying there
        is no stop order — the garbage in the price field does not un-say it."""
        assert stop_attached({"stopLoss": "n/a", "stopLossId": ""}) is False


def test_nan_is_not_a_reading():
    assert stop_attached({"stopLoss": float("nan")}) is None


# ── the caller, driven end to end ─────────────────────────────────────────

class _Pos:
    symbol = "BTC/USDT"
    trade_id = "TI-x"
    direction = "SHORT"


class _Exchange:
    def __init__(self, rows):
        self._rows = rows
        self.asked = []

    async def fetch_positions(self, symbols=None, params=None):
        self.asked.append(symbols)
        return self._rows


def _ask(tmp_path, rows, direction="SHORT"):
    lx = LiveExecutor(state_dir=str(tmp_path))

    async def _get_exchange():
        return _Exchange(rows)

    lx._get_exchange = _get_exchange
    pos = _Pos()
    pos.direction = direction
    return asyncio.run(lx._stop_live_on_exchange(pos))


# The symbol the venue answers with, as the venue spells it. `swap_symbol`
# maps BTC/USDT to the perp form, and a row has to carry THAT to be ours.
def _swap(tmp_path, symbol="BTC/USDT"):
    return LiveExecutor(state_dir=str(tmp_path))._venue.swap_symbol(symbol)


class TestHedgeModeReadsOurOwnSide:
    """BOTH DIRECTIONS WERE REACHABLE, and they are different incidents."""

    def test_the_other_sides_stop_does_not_protect_us(self, tmp_path):
        sym = _swap(tmp_path)
        rows = [
            {"symbol": sym, "side": "long", "contracts": 1.0,
             "info": {"stopLoss": "95.0", "stopLossId": "THEIRS"}},
            {"symbol": sym, "side": "short", "contracts": 2.0,
             "info": {"stopLoss": "0", "stopLossId": ""}},
        ]
        assert _ask(tmp_path, rows, "SHORT") is False, (
            "the long's stop answered for the short, so a naked short was "
            "reported protected and the self-heal left it naked")

    def test_our_own_stop_is_not_torn_down_by_the_other_sides_absence(self, tmp_path):
        sym = _swap(tmp_path)
        rows = [
            {"symbol": sym, "side": "long", "contracts": 1.0,
             "info": {"stopLoss": "0", "stopLossId": ""}},
            {"symbol": sym, "side": "short", "contracts": 2.0,
             "info": {"stopLoss": "95.0", "stopLossId": "OURS"}},
        ]
        assert _ask(tmp_path, rows, "SHORT") is True, (
            "the long's missing stop answered for the short, and a positive "
            "False is what makes _place_sl_tp cancel a working stop")

    def test_a_row_that_states_no_side_is_still_ours(self, tmp_path):
        """One-way mode, and the rule `rows_for_side` states: a row is dropped
        only when it is DEFINITELY somebody else's."""
        rows = [{"contracts": 1.0, "info": {"stopLoss": "95.0"}}]
        assert _ask(tmp_path, rows, "SHORT") is True


class TestTheSymbolIsCheckedToo:
    def test_another_symbols_row_does_not_answer(self, tmp_path):
        rows = [{"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 1.0,
                 "info": {"stopLoss": "95.0", "stopLossId": "X"}}]
        assert _ask(tmp_path, rows, "SHORT") is None, (
            "fetch_positions([sym]) is a request filter, not a promise — "
            "exchange_sync's UTA fallback exists because of that")

    def test_ours_is_found_among_others(self, tmp_path):
        sym = _swap(tmp_path)
        rows = [
            {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 9.0,
             "info": {"stopLoss": "1.0", "stopLossId": "NOT-OURS"}},
            {"symbol": sym, "side": "short", "contracts": 2.0,
             "info": {"stopLoss": "0", "stopLossId": ""}},
        ]
        assert _ask(tmp_path, rows, "SHORT") is False


class TestAnUnreadableRowDoesNotHandOverTheQuestion:
    def test_our_unsized_row_does_not_fall_through_to_the_other_side(self, tmp_path):
        sym = _swap(tmp_path)
        rows = [
            {"symbol": sym, "side": "short", "contracts": None,
             "info": {"stopLoss": "0", "stopLossId": ""}},
            {"symbol": sym, "side": "long", "contracts": 3.0,
             "info": {"stopLoss": "95.0", "stopLossId": "THEIRS"}},
        ]
        assert _ask(tmp_path, rows, "SHORT") is None

    @pytest.mark.parametrize("rows", [
        [],
        [{"contracts": 0.0, "info": {"stopLoss": "0"}}],
        "not a list",
        None,
    ])
    def test_nothing_we_can_size_is_never_a_missing_stop(self, tmp_path, rows):
        assert _ask(tmp_path, rows) is None

    def test_a_junk_size_does_not_abort_the_rows_behind_it(self, tmp_path):
        """WHAT `read_amount` BUYS HERE, and it took a surviving mutation to
        find it. Restoring `float(p.get("contracts", 0) or 0)` changed no
        verdict in any other case — null, blank and a real 0 all skip to the
        same None either way — so the first draft of this file did not kill it.

        The difference is that the old shape RAISES on a non-numeric size, and
        the raise is caught 8 lines down by the method's own `except`, which
        abandons the whole loop. So one junk row costs the answer that the row
        BEHIND it was carrying. Two rows for one side is not exotic:
        `exchange_sync` merges v3 rows in beside the ccxt ones by symbol+side
        precisely because the venue under-reports, and a merge is where a
        differently-shaped payload turns up.
        """
        sym = _swap(tmp_path)
        rows = [
            {"symbol": sym, "side": "short", "contracts": "n/a",
             "info": {"stopLoss": "95.0", "stopLossId": "OURS"}},
            {"symbol": sym, "side": "short", "contracts": 2.0,
             "info": {"stopLoss": "95.0", "stopLossId": "OURS"}},
        ]
        assert _ask(tmp_path, rows, "SHORT") is True

    def test_a_raising_venue_is_not_a_missing_stop(self, tmp_path):
        lx = LiveExecutor(state_dir=str(tmp_path))

        async def _get_exchange():
            raise RuntimeError("venue timeout")

        lx._get_exchange = _get_exchange
        assert asyncio.run(lx._stop_live_on_exchange(_Pos())) is None


class TestAnUnreadStopFieldIsNotAMissingStop:
    """The five payloads that each used to say 'genuinely unprotected'."""

    @pytest.mark.parametrize("row", [
        {"contracts": 1.0, "info": {}},
        {"contracts": 1.0},
        {"contracts": 1.0, "info": None},
        {"contracts": 1.0, "info": {"takeProfit": "110.0"}},
        {"contracts": 1.0, "info": {"stopLoss": "n/a"}},
    ])
    def test_it_reads_as_unverifiable(self, tmp_path, row):
        assert _ask(tmp_path, [row]) is None

    def test_and_a_venue_that_says_no_stop_is_still_believed(self, tmp_path):
        """The regression this fix must NOT introduce: a gate that can never
        say False can never repair anything."""
        assert _ask(tmp_path, [{"contracts": 1.0,
                                "info": {"stopLoss": "", "stopLossId": ""}}]) is False


# ── the consequence, at the caller that spends it ─────────────────────────

def test_only_a_positive_false_re_places(tmp_path):
    """THE REACHABILITY HALF. The unit drives above prove the reading; this is
    the one thing they cannot show — that the self-heal branch still spends it
    the way its own comment says, and that None does not reach `needs_fix`.

    A SCAN, and saying so matters: the branch lives inside a 90-line method
    that walks open positions and calls `_place_sl_tp`, so there is no seam to
    drive. It is anchored to the comparison rather than to the helper's name,
    because `if _stop_live:` and `if not _stop_live:` both keep the name and
    both spend None — which is the mutation that matters.
    """
    import inspect
    import re

    from tests.source_scan import code_only
    src = code_only(inspect.getsource(LiveExecutor.verify_and_fix_sltp))
    assert "_stop_live_on_exchange" in src
    assert re.search(r"_stop_live\s+is\s+False", src), (
        "the self-heal must re-place only on a POSITIVE False — `if not "
        "_stop_live` spends None as a confirmed-missing stop and cancels a "
        "live one")
    assert not re.search(r"if\s+not\s+_stop_live\b", src)


def test_the_dead_duplicate_is_gone():
    """`_sync_sl_tp_from_exchange` read the same two fields onto the local
    record off an unfiltered list, had no caller anywhere in the tree, and was
    recorded as dead in docs/DEEP_AUDIT_2026.md in 2026-06. Its live twin in
    reconcile_positions does the job side-aware. A second copy of a reading is
    a second answer; the right number of copies of a wrong one is zero."""
    assert not hasattr(LiveExecutor, "_sync_sl_tp_from_exchange")

"""A failed position read and a confirmed absence were the same dict.

`_verify_position_exists` starts from a zero-filled `result`, fills it in on a
match, and its `except` handler falls through to `return result` — the SAME
object. So after an order that FILLED:

    fetch_positions RAISED   -> {"confirmed": False, ..., "leverage": 0}
    venue holds no position  -> {"confirmed": False, ..., "leverage": 0}

byte-identical, for two opposite events. One says the fill did not become a
position; the other says nobody looked.

WHAT IT COST IS NOT THE WORDING. `execute()` sets `_lev_mismatch` only inside
`if position_confirmed:`, and `_leverage_overshoot_guard` opens with
`if _lev_mismatch is not None:`. So ONE failed `fetch_positions` turns the
entire leverage-overshoot guard into a no-op:

  * the Bitget sticky-per-symbol incident it exists for (a 5x target filled at
    20x) is neither detected, audited, nor flattened;
  * `cost = raw_cost / leverage` then runs on the REQUESTED leverage, so
    `cost_usd` — the margin, and since PR #319 the denominator the published
    return is computed against — is four times too large at that ratio;
  * and the card said `⚠️ position check pending`, which reads as "it will
    settle".

Three states now: `found` / `absent` / `unreadable`.
"""

import asyncio

import pytest

from bot.core.live_executor import (
    LiveExecutor,
    entry_verify_line,
    leverage_went_unverified,
)


class _Raises:
    """The venue read fails — a timeout, a 429, an auth hiccup."""

    async def fetch_positions(self, symbols):
        raise RuntimeError("venue timeout")


class _Empty:
    """The venue answers, and holds no such position."""

    async def fetch_positions(self, symbols):
        return []


class _Holds:
    def __init__(self, leverage=20):
        self._lev = leverage

    async def fetch_positions(self, symbols):
        return [{"symbol": "APT/USDT:USDT", "contracts": 3.0, "side": "long",
                 "entryPrice": 5.0, "markPrice": 5.1, "unrealizedPnl": 0.3,
                 "initialMargin": 0.75, "leverage": self._lev}]


def _probe(exchange):
    ex = LiveExecutor.__new__(LiveExecutor)
    return asyncio.run(LiveExecutor._verify_position_exists(
        ex, exchange, "APT/USDT:USDT", "LONG"))


class TestTheReadTellsTheThreeApart:
    def test_a_raised_read_is_not_an_absence(self):
        """The whole finding, in one assertion."""
        raised = _probe(_Raises())
        empty = _probe(_Empty())
        assert raised != empty, (
            "a failed read is still indistinguishable from a confirmed absence")
        assert raised["state"] == "unreadable"
        assert empty["state"] == "absent"

    def test_a_found_position_says_so(self):
        got = _probe(_Holds())
        assert got["state"] == "found"
        assert got["confirmed"] is True
        assert got["leverage"] == 20

    @pytest.mark.parametrize("exchange", [_Raises(), _Empty()])
    def test_neither_failure_claims_confirmation(self, exchange):
        assert _probe(exchange)["confirmed"] is False

    def test_zero_leverage_is_kept_deliberately(self):
        """`> 0` at every reader is already the right reading of "not reported".

        Asserted so a later pass does not "improve" these to None and break
        the guards that depend on the comparison. Check reachability before
        fixing applies to fields as well as to lines.
        """
        for exchange in (_Raises(), _Empty()):
            got = _probe(exchange)
            assert got["leverage"] == 0
            assert got["margin"] == 0.0


class TestTheCardSaysWhichHappened:
    """`entry_verify_line` is a seam because the state it reports could not be
    driven while it was three inline branches in a 60-line formatter."""

    def test_an_absence_after_a_fill_is_alarming_not_pending(self):
        line = entry_verify_line(True, False, "pending", "absent")
        assert "NO POSITION" in line
        assert "pending" not in line, "a missing position read as 'it will settle'"

    def test_an_unreadable_position_says_the_guard_did_not_run(self):
        """The operator's only chance to learn the guard was skipped."""
        line = entry_verify_line(True, False, "pending", "unreadable")
        assert "COULD NOT BE READ" in line
        assert "overshoot guard did not run" in line
        assert "pending" not in line

    def test_the_two_failures_no_longer_render_alike(self):
        assert (entry_verify_line(True, False, "pending", "absent")
                != entry_verify_line(True, False, "pending", "unreadable"))

    def test_a_confirmed_pair_is_unchanged(self):
        assert entry_verify_line(True, True, "pending", "found") == (
            "- Verified: ✅ CONFIRMED (order + position)")

    def test_an_unconfirmed_order_outranks_the_position_state(self):
        """No order, no position question — and the stage is quoted."""
        line = entry_verify_line(False, False, "fill_timeout", "unreadable")
        assert "UNCONFIRMED" in line and "fill_timeout" in line

    def test_an_unknown_state_falls_back_rather_than_inventing_one(self):
        """A caller predating the field genuinely cannot tell which it was.

        Saying so beats picking either verdict — and picking `absent` would
        put a 🚨 on every older call site at once.
        """
        line = entry_verify_line(True, False, "pending", None)
        assert "pending" in line
        assert "NO POSITION" not in line and "COULD NOT BE READ" not in line


class TestTheGuardGapHasAName:
    """`_lev_mismatch` is set only under `if position_confirmed:`, and
    `_leverage_overshoot_guard` opens with `if _lev_mismatch is not None:`, so
    an unreadable position silently disarms the guard.

    The FIRST version of this class reimplemented the branch inside the test
    and asserted on its own call — which proves nothing about `execute()`. The
    decision is a predicate now, so it can simply be asked.
    """

    def test_a_failed_read_means_the_leverage_went_unverified(self):
        state = _probe(_Raises())
        assert leverage_went_unverified(state["state"], state["confirmed"])

    def test_a_venue_that_answered_no_is_a_different_problem(self):
        """`absent` is louder and is not a leverage question."""
        state = _probe(_Empty())
        assert not leverage_went_unverified(state["state"], state["confirmed"])

    def test_a_read_position_is_verified(self):
        state = _probe(_Holds())
        assert not leverage_went_unverified(state["state"], state["confirmed"])

    def test_a_confirmed_position_outranks_a_stale_state(self):
        """Confirmed means the leverage WAS read, whatever the state says."""
        assert not leverage_went_unverified("unreadable", True)

    def test_an_unknown_state_is_not_asserted_either_way(self):
        assert not leverage_went_unverified(None, False)

    def test_execute_asks_the_predicate_and_audits(self):
        """Reachability — the one thing a driven test of a pure function
        cannot show, and the reason #999 shipped a card that never rendered.

        Paired with the drives above rather than standing in for them.
        """
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor))
        assert "leverage_went_unverified(" in src, (
            "execute() no longer consults the predicate")
        assert "leverage_unverified_on_fill" in src, (
            "the disarmed guard would leave no audit trail")

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

AND THEN IT STILL ASKED ONCE, which is the half that was deferred and is the
subject of the second half of this file. Reporting the gap honestly does not
close it: after one failed `fetch_positions` the card said so, the audit said
so, and the guard was still a no-op for the life of the position.
`_verify_order_fill` asks the same venue the same shape of question twenty
lines earlier in `execute()` and retries three times at 1.5s. The position
read — the one a safety control depends on — had no patience at all.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from bot.core.live_executor import (
    _VENUE_SETTLE_SECONDS,
    LiveExecutor,
    entry_verify_line,
    leverage_went_unverified,
    position_read_needs_another_look,
)


class _Counting:
    """Base: every double records how many times the venue was asked."""

    def __init__(self):
        self.calls = 0


class _Raises(_Counting):
    """The venue read fails — a timeout, a 429, an auth hiccup."""

    async def fetch_positions(self, symbols):
        self.calls += 1
        raise RuntimeError("venue timeout")


class _Empty(_Counting):
    """The venue answers, and holds no such position."""

    async def fetch_positions(self, symbols):
        self.calls += 1
        return []


class _Holds(_Counting):
    def __init__(self, leverage=20):
        super().__init__()
        self._lev = leverage

    async def fetch_positions(self, symbols):
        self.calls += 1
        return [{"symbol": "APT/USDT:USDT", "contracts": 3.0, "side": "long",
                 "entryPrice": 5.0, "markPrice": 5.1, "unrealizedPnl": 0.3,
                 "initialMargin": 0.75, "leverage": self._lev}]


class _HoldsTwo(_Counting):
    """Two entries matching the same symbol AND side, at different leverage.

    Not hypothetical: ccxt payloads carry per-margin-mode rows, and the
    filter here is (symbol, contracts > 0, side) — nothing in it makes a
    second match impossible.
    """

    async def fetch_positions(self, symbols):
        self.calls += 1
        return [{"symbol": "APT/USDT:USDT", "contracts": 3.0, "side": "long",
                 "entryPrice": 5.0, "markPrice": 5.1, "unrealizedPnl": 0.3,
                 "initialMargin": 0.75, "leverage": 20},
                {"symbol": "APT/USDT:USDT", "contracts": 1.0, "side": "long",
                 "entryPrice": 9.0, "markPrice": 9.1, "unrealizedPnl": 0.1,
                 "initialMargin": 0.25, "leverage": 3}]


class _Unparseable(_Counting):
    """A matching position whose numbers do not survive `float()`.

    ccxt normally normalises these, but the raw payload is passed through on
    fields it does not know, and `float("n/a")` raises AFTER the match branch
    has already written `confirmed: True`.
    """

    async def fetch_positions(self, symbols):
        self.calls += 1
        return [{"symbol": "APT/USDT:USDT", "contracts": 3.0, "side": "long",
                 "entryPrice": "n/a", "markPrice": 5.1, "unrealizedPnl": 0.3,
                 "initialMargin": 0.75, "leverage": 20}]


class _Sequence(_Counting):
    """Answers a scripted list of venues in order, repeating the last.

    The retry cases are all "it was broken and then it wasn't", which needs a
    double that CHANGES — a fixed one can only ever prove the terminal state.
    """

    def __init__(self, *stages):
        super().__init__()
        self._stages = list(stages)

    async def fetch_positions(self, symbols):
        stage = self._stages[min(self.calls, len(self._stages) - 1)]
        self.calls += 1
        return await stage.fetch_positions(symbols)


def _probe(exchange, max_attempts=3, delay=0):
    """`delay=0` by default: these drive the RULE, not the clock.

    The real defaults are asserted separately, in
    `TestTheDefaultsAreTheOnesClaimed` — a suite that quietly ran the
    production timings would take a minute, and one that quietly ran different
    ones would pin nothing.
    """
    ex = LiveExecutor.__new__(LiveExecutor)
    return asyncio.run(LiveExecutor._verify_position_exists(
        ex, exchange, "APT/USDT:USDT", "LONG",
        max_attempts=max_attempts, delay=delay))


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


# ── and then it still asked once ─────────────────────────────────────


class TestTheGuardComesBackWhenTheBlipPasses:
    """The point of the retry, stated as the outcome rather than the mechanism.

    Everything above makes a disarmed guard VISIBLE. None of it re-arms one.
    """

    def test_a_read_that_fails_then_works_verifies_the_leverage(self):
        venue = _Sequence(_Raises(), _Raises(), _Holds(leverage=20))
        got = _probe(venue)
        assert got["state"] == "found"
        assert got["confirmed"] is True
        assert got["leverage"] == 20, (
            "the venue's ACTUAL leverage — the number the overshoot guard "
            "compares against the target, and the whole reason to look twice")
        assert not leverage_went_unverified(got["state"], got["confirmed"]), (
            "the guard is still disarmed after a read that eventually worked")

    def test_a_book_that_lags_the_fill_is_not_a_missing_position(self):
        """`absent` retries too, and this is why.

        `_place_sl_tp` sleeps `_VENUE_SETTLE_SECONDS` before touching this same
        position book — "Prevents error 31008 ('no position') on fast fills" —
        and in `execute()` that sleep is four hundred lines BELOW this read. So
        the read ran at the one moment the code under it treats as too early,
        and a lagging book printed 🚨 NO POSITION about a position that was
        about to have a stop placed on it.
        """
        venue = _Sequence(_Empty(), _Empty(), _Holds())
        got = _probe(venue)
        assert got["state"] == "found"
        assert got["confirmed"] is True

    def test_a_venue_that_is_genuinely_down_still_says_unreadable(self):
        got = _probe(_Raises())
        assert got["state"] == "unreadable"
        assert got["confirmed"] is False
        assert leverage_went_unverified(got["state"], got["confirmed"]), (
            "the retry swallowed the finding it was built on top of")

    def test_a_position_that_is_genuinely_gone_still_says_absent(self):
        got = _probe(_Empty())
        assert got["state"] == "absent"
        assert got["confirmed"] is False


class TestItAsksTheRightNumberOfTimes:
    def test_a_confirmation_costs_exactly_one_round_trip(self):
        """The money path must not pay for the failure path."""
        venue = _Holds()
        got = _probe(venue)
        assert venue.calls == 1
        assert got["attempts"] == 1

    @pytest.mark.parametrize("venue_factory", [_Raises, _Empty])
    def test_a_failure_uses_the_whole_budget_and_no_more(self, venue_factory):
        venue = venue_factory()
        got = _probe(venue, max_attempts=3)
        assert venue.calls == 3
        assert got["attempts"] == 3

    def test_it_stops_the_moment_it_succeeds(self):
        venue = _Sequence(_Raises(), _Holds())
        _probe(venue, max_attempts=5)
        assert venue.calls == 2, (
            "it kept asking after the venue had answered — three more round "
            "trips on the latency-sensitive path, for nothing")

    def test_one_attempt_is_the_old_behaviour_exactly(self):
        """The budget is a parameter, so the pre-retry behaviour stays
        expressible — and stays asserted, because that is what every caller
        that has not opted in would get."""
        venue = _Raises()
        got = _probe(venue, max_attempts=1)
        assert venue.calls == 1
        assert got["state"] == "unreadable"

    @pytest.mark.parametrize("bad", [0, -3, None])
    def test_a_nonsense_budget_still_asks_once(self, bad):
        """`range(0)` would return the initial dict with `attempts: 0` — a
        confident `absent` for a venue nobody asked. Floor of one."""
        venue = _Empty()
        got = _probe(venue, max_attempts=bad)
        assert venue.calls == 1
        assert got["attempts"] == 1

    @pytest.mark.parametrize("bad", ["three", object(), [2]])
    def test_an_unusable_budget_does_not_raise_into_a_filled_position(self, bad):
        """This method's contract is that it NEVER raises — it runs after
        capital is committed, and `_leverage_overshoot_guard` says in as many
        words that a guard which raises must not become the reason a filled
        position goes unmanaged. Before the retry, every statement in here was
        inside the try; `int(max_attempts)` is the first that was not.
        """
        venue = _Empty()
        got = _probe(venue, max_attempts=bad)
        assert got["state"] == "absent"
        assert venue.calls == 1

    @pytest.mark.parametrize("bad", ["soon", object(), None])
    def test_an_unusable_gap_does_not_raise_either(self, bad):
        """`asyncio.sleep("soon")` raises, and it would do so between two
        attempts — after the first read has already failed."""
        venue = _Raises()
        got = _probe(venue, max_attempts=2, delay=bad)
        assert got["state"] == "unreadable"
        assert venue.calls == 2, (
            "a bad gap swallowed the retry instead of being coerced away")

    def test_a_negative_gap_is_not_passed_to_sleep(self):
        got = _probe(_Raises(), max_attempts=2, delay=-5)
        assert got["attempts"] == 2

    def test_it_does_not_sleep_after_the_last_attempt(self, monkeypatch):
        """A trailing sleep is invisible in the result and costs 1.5s on every
        failed fill — the case that is already slow and already alarming."""
        from bot.core import live_executor as le

        slept = []

        async def _record(seconds):
            slept.append(seconds)

        monkeypatch.setattr(le.asyncio, "sleep", _record)
        ex = LiveExecutor.__new__(LiveExecutor)
        asyncio.run(LiveExecutor._verify_position_exists(
            ex, _Raises(), "APT/USDT:USDT", "LONG",
            max_attempts=3, delay=0.25))
        assert slept == [0.25, 0.25], (
            f"expected a gap BETWEEN attempts only, got {slept}")

    def test_a_confirmation_sleeps_not_at_all(self, monkeypatch):
        from bot.core import live_executor as le

        slept = []

        async def _record(seconds):
            slept.append(seconds)

        monkeypatch.setattr(le.asyncio, "sleep", _record)
        ex = LiveExecutor.__new__(LiveExecutor)
        asyncio.run(LiveExecutor._verify_position_exists(
            ex, _Holds(), "APT/USDT:USDT", "LONG",
            max_attempts=3, delay=0.25))
        assert slept == []


class TestTheLastAnswerIsTheAnswer:
    """`state` resets per attempt, and the mixed orders are why.

    Neither of these is a "flake" the loop can average away: they are two
    different readings of the same venue, and only one of them is current.
    """

    def test_raised_then_answered_no_is_an_absence(self):
        """The venue has now spoken. Reporting `unreadable` would suppress a
        real 🚨 on the grounds that an earlier attempt broke."""
        got = _probe(_Sequence(_Raises(), _Empty()))
        assert got["state"] == "absent"

    def test_answered_no_then_raised_is_unreadable(self):
        """The reverse is NOT symmetric, and the asymmetry is deliberate.

        `absent` is the one reading we retried BECAUSE we distrusted it — a
        book that lags a fresh fill answers exactly that way. Having then
        failed to get a settled second look, the honest word is that we never
        settled it, and it is also the safer of the two: `unreadable` sends
        the operator to look and records the guard skip, where `absent` would
        assert a missing position on evidence we deliberately doubted.
        """
        got = _probe(_Sequence(_Empty(), _Raises()))
        assert got["state"] == "unreadable"
        assert leverage_went_unverified(got["state"], got["confirmed"])

    def test_the_first_match_still_wins_when_the_venue_sends_two(self):
        """The restructure could have changed WHICH match is taken, quietly.

        The pre-retry code `return`ed on a match, so the first matching entry
        won. Making the policy decide every exit turned that into a `break`,
        and dropping the `break` — the mutation that SURVIVED the first round —
        silently makes the LAST entry win instead. Nothing else here noticed,
        because every other double sends exactly one position, and 20x against
        3x is the difference between the overshoot guard flattening and not.
        """
        got = _probe(_HoldsTwo())
        assert got["leverage"] == 20, "the second entry overwrote the first"
        assert got["exchange_qty"] == 3.0
        assert got["exchange_entry"] == 5.0

    def test_a_raise_mid_write_does_not_leave_a_confirmation_behind(self):
        """The narrow case that makes the wider reset load-bearing.

        `confirmed` is the FIRST field the match branch sets and the float
        conversions come after it, so a value the venue passed through
        unnormalised raises with `confirmed: True` already written. The pair
        `confirmed: True` + `state: "unreadable"` is worse than either alone:
        `leverage_went_unverified` reads `not confirmed` so it audits nothing,
        `execute()` takes the `if position_confirmed:` branch, and `leverage`
        is one of the fields that never got written — so the mismatch check
        silently does not run, off a read that half failed.
        """
        got = _probe(_Unparseable())
        assert got["state"] == "unreadable"
        assert got["confirmed"] is False, (
            "a read that raised part-way through still claims a confirmed "
            "position")
        assert got["exchange_qty"] == 0.0 and got["leverage"] == 0

    def test_the_shared_blank_is_never_written_through(self):
        """`_UNANSWERED_POSITION_READ` is module-level and reset into `result`
        on every attempt. `result = _UNANSWERED_POSITION_READ` would look
        identical at the call site and poison every later read in the process
        with the last position's numbers — including a stale `confirmed: True`
        for a venue that has not been asked."""
        from bot.core.live_executor import _UNANSWERED_POSITION_READ as BLANK
        before = dict(BLANK)
        _probe(_Holds(leverage=20))
        _probe(_Raises())
        assert BLANK == before, f"the blank was mutated: {BLANK}"
        assert "attempts" not in BLANK, (
            "the counter must not live in the blank — it is the one field that "
            "has to survive a reset")

    def test_no_two_reads_share_a_result_dict(self):
        got_a = _probe(_Holds(leverage=20))
        got_b = _probe(_Empty())
        assert got_a["confirmed"] is True and got_b["confirmed"] is False
        assert got_a["leverage"] == 20 and got_b["leverage"] == 0

    def test_a_late_confirmation_carries_the_venues_numbers(self):
        """Not just the state: a stale zero-filled result behind a `found`
        state is the same defect one field over."""
        got = _probe(_Sequence(_Empty(), _Holds(leverage=7)))
        assert got["leverage"] == 7
        assert got["exchange_qty"] == 3.0
        assert got["exchange_entry"] == 5.0
        assert got["margin"] == 0.75


class TestTheRetryRuleIsAskedRatherThanInlined:
    """`position_read_needs_another_look` is a seam for the same reason
    `leverage_went_unverified` is: what it decides leaves no trace. A loop that
    silently stopped retrying looks exactly like one that never started."""

    @pytest.mark.parametrize("state", ["absent", "unreadable"])
    def test_both_non_confirming_states_get_another_look(self, state):
        assert position_read_needs_another_look(state, 0, 3)

    def test_a_confirmation_never_does(self):
        assert not position_read_needs_another_look("found", 0, 3)

    @pytest.mark.parametrize("state", ["absent", "unreadable", "found"])
    def test_the_budget_is_respected_whatever_the_state(self, state):
        assert not position_read_needs_another_look(state, 2, 3)

    def test_a_budget_of_one_never_retries(self):
        assert not position_read_needs_another_look("unreadable", 0, 1)

    def test_an_unknown_state_is_treated_as_not_confirmed(self):
        """Fail toward looking again. The cost is one round trip; the cost of
        the other default is a safety control that does not run."""
        assert position_read_needs_another_look(None, 0, 3)

    def test_the_read_asks_the_predicate(self):
        """Reachability. The drives above prove the RULE; only the caller can
        show the rule is the one in force — which is the exact thing the
        `_lev_mismatch` gap was, one level up.
        """
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(
            LiveExecutor._verify_position_exists))
        assert "position_read_needs_another_look(" in src, (
            "the read grew its own retry condition; the policy and the loop "
            "can now disagree about when to stop")


class TestTheDefaultsAreTheOnesClaimed:
    """Every drive above passes `delay=0`, so nothing else here would notice a
    default of 60 seconds or of none at all."""

    def test_three_attempts_matching_the_order_check(self):
        sig = inspect.signature(LiveExecutor._verify_position_exists)
        assert sig.parameters["max_attempts"].default == 3, (
            "the position read and the order fill check no longer agree on "
            "how patient to be with the same venue")
        order_sig = inspect.signature(LiveExecutor._verify_order_fill)
        assert (sig.parameters["max_attempts"].default
                == order_sig.parameters["max_retries"].default)

    def test_the_gap_is_the_settle_constant_this_file_already_has(self):
        sig = inspect.signature(LiveExecutor._verify_position_exists)
        assert sig.parameters["delay"].default == _VENUE_SETTLE_SECONDS, (
            "a second copy of the venue-settle interval is a second answer")

    def test_the_default_NAMES_the_constant_rather_than_matching_its_value(self):
        """The assertion above SURVIVED the mutation it exists to catch.

        `_VENUE_SETTLE_SECONDS` is 1.5, so a hand-written `delay: float = 1.5`
        compares equal to it and the value check passes — while being exactly
        the second copy the check is named after. A default's value is
        readable at runtime; which name it came from is not, so this is one of
        the shapes CLAUDE.md keeps source scanning for. Anchored to the
        signature's own line, because a comment quoting the constant is
        indistinguishable from code using it.
        """
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(
            LiveExecutor._verify_position_exists))
        decl = [ln for ln in src.split("\n") if ln.strip().startswith("delay")]
        assert decl, "the `delay` parameter was renamed — re-point this check"
        assert any("_VENUE_SETTLE_SECONDS" in ln for ln in decl), (
            "the retry gap is a literal now. The venue-settle interval has one "
            f"definition and this is not it: {decl}")


class TestTheAuditSaysHowHardItLooked:
    """"Could not be read" after one try and after three over three seconds are
    different operational facts, and the audit trail is where the difference is
    recoverable afterwards."""

    def test_the_count_reaches_the_audit(self):
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor))
        assert 'pos_verify.get("attempts")' in src, (
            "execute() no longer reads how many times the venue was asked")
        assert '"reads": _reads' in src, (
            "the count is rendered but not recorded; the forensic half is the "
            "one that outlives the card")

    def test_a_missing_count_is_omitted_rather_than_invented(self):
        """A double or an older caller has no `attempts` field, and "failed 1
        read(s)" manufactured from its absence is the shape this whole file is
        about. OMIT is the second strategy in CLAUDE.md's table."""
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(LiveExecutor))
        assert 'pos_verify.get("attempts", 1)' not in src
        assert "_reads_note" in src, (
            "the note is no longer conditional, so absence renders as a count")

    def _guard_log(self, tmp_path, caplog, probe_result):
        """`_guard_fill_leverage`'s unknown branch, driven.

        `execute()` is one of TWO callers. The other covers the three fill
        paths that had no verdict at all — a limit fill, a partial-fill
        adoption, a drift→market fallback — and its one-line `logger.info` is
        the only trace any of them leaves. A fix that landed in one caller and
        not the other is the shape CLAUDE.md names about the assessor and the
        renderer.
        """
        import logging
        from unittest.mock import AsyncMock

        from bot.core.live_executor import LiveExecutor as LE
        ex = LE(state_dir=str(tmp_path))
        ex._verify_position_exists = AsyncMock(return_value=probe_result)
        pos = SimpleNamespace(symbol="APT/USDT:USDT", direction="SHORT",
                              leverage=5, sl_order_id=None, tp_order_id=None)
        with caplog.at_level(logging.INFO):
            out = asyncio.run(ex._guard_fill_leverage(
                object(), "t1", pos, 5, "limit fill"))
        assert out is None, "an unreadable leverage must keep the position"
        return caplog.text

    def test_the_other_caller_reports_the_count_too(self, tmp_path, caplog):
        text = self._guard_log(tmp_path, caplog, {
            "confirmed": False, "state": "unreadable", "attempts": 3,
            "leverage": 0, "exchange_entry": 0.0, "exchange_qty": 0.0})
        assert "after 3 read(s)" in text, (
            "the three fill paths still record 'unverified' with no measure of "
            f"how hard anyone looked: {text!r}")

    def test_the_other_caller_omits_it_when_absent(self, tmp_path, caplog):
        text = self._guard_log(tmp_path, caplog, {
            "confirmed": False, "leverage": 0,
            "exchange_entry": 0.0, "exchange_qty": 0.0})
        assert "Leverage unverified" in text, "the branch stopped logging"
        assert "read(s)" not in text, (
            f"a count was invented from a missing field: {text!r}")

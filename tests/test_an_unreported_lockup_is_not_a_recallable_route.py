"""A hard gate that cannot read its input must refuse, not assume the safe answer.

`bot/guardian/yield_plan.py` compiles one cross-chain stablecoin move into an
operator-signed plan. Its own docstring calls non-custodial + recallable
REQUIRED, locked v1 scope, and says the evaluator is "fail-closed: any
missing/malformed input → skip with a reason, never a guess."

DRIVEN BEFORE THE FIX, two of the three hard-gate inputs failed OPEN:

    lockup_days = 30 (measured)        -> fail  "destination has a lockup"
    lockup_days missing / None / 'n/a' -> PASS
    custodial = True (measured)        -> fail
    custodial missing / None           -> PASS
    amount_usd = 9999 (measured)       -> fail (both caps)
    amount_usd missing / 'n/a'         -> PASS, first_leg.notional_usd = 0.00

`(_num(...) or 0.0) > 0` made an unreported lockup byte-identical to a measured
"withdraw anytime", `bool(move.get("custodial"))` made an unreported route
non-custodial, and `_num(...) or 0.0` made an unread size clear every cap there
is — while `checked` counted 8 in all three cases, so the coverage number
claimed eight rules were evaluated over inputs three of them could not read.
That is `integrity_veto.assess({})`'s recorded shape ("clear over checked == 0")
on a gate whose next step is a real transfer.

`max_breakeven_days` is the ONE rule that read None correctly from the start
(`if d is None or d > v`), which is the proof the distinction was known and
applied once. It only SAID it badly — "breakeven None days exceeds the 30-day
horizon" interpolated the absence as a figure — and says it plainly now.

AND THE COERCION IS AT THE NORMALIZER, one runtime earlier. The JS half of this
is `app/test/cross_yield_reports_what_it_was_told.test.js`; the two guards are
deliberately split because the claim spans a process boundary and each side can
only drive its own.
"""
from __future__ import annotations

import pytest

from bot.guardian import yield_plan as yp

DEST = "0x" + "ab" * 20


def _move(**over):
    """A move that clears every gate — so a single unreadable field is the only
    difference between the row under test and a pass."""
    m = {
        "asset": "USDC", "amount_usd": 40.0, "from_chain": "sepolia",
        "current_apy": 3.0, "best_apy": 6.0, "delta_apy": 3.0,
        "custodial": False, "lockup_days": 0,
        "breakeven_days": 12, "net_horizon_usd": 1.20, "worth": "yes",
    }
    m.update(over)
    return m


def _without(field):
    return {k: v for k, v in _move().items() if k != field}


def _envelope(**over):
    e = {
        "envelope_id": "env-1", "revoked": False, "withdraw_allowed": True,
        "withdraw_allowlist": [DEST], "max_notional_daily_usd": 150.0,
    }
    e.update(over)
    return e


def _policy(move):
    return yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY, move)


# ── the readings ─────────────────────────────────────────────────────────
class TestTheReadings:
    def test_a_flag_is_a_boolean_the_move_reported_or_nothing(self):
        assert yp._flag(True) is True
        assert yp._flag(False) is False
        assert yp._flag(1) is True and yp._flag(0) is False
        for absent in (None, "", "no"):
            assert yp._flag(absent) is None, f"{absent!r} read as a reported flag"

    def test_and_the_string_spelling_is_not_a_reading(self):
        """`bool("false")` is True — the UNSAFE answer from a value nobody
        should have trusted either way. Neither spelling is a reading."""
        assert bool("false") is True          # the trap, pinned
        assert yp._flag("false") is None
        assert yp._flag("true") is None


# ── require_recallable ───────────────────────────────────────────────────
class TestTheRecallableGate:
    def test_a_measured_lockup_still_refuses(self):
        r = _policy(_move(lockup_days=30))
        assert r["verdict"] == "fail"
        assert any("has a lockup" in x for x in r["reasons"])

    def test_a_measured_zero_still_passes(self):
        """The cure must not turn a read "withdraw anytime" into an absence."""
        assert _policy(_move(lockup_days=0))["verdict"] == "pass"

    @pytest.mark.parametrize("move, label", [
        (_without("lockup_days"), "the field is absent"),
        (_move(lockup_days=None), "the field is null"),
        (_move(lockup_days="n/a"), "the field is junk"),
    ])
    def test_an_unreported_lockup_is_refused_by_name(self, move, label):
        r = _policy(move)
        assert r["verdict"] == "fail", label
        assert any("does not say whether the destination locks" in x
                   for x in r["reasons"]), (label, r["reasons"])

    def test_and_the_refusal_says_it_was_not_shown_rather_than_that_there_is_one(self):
        """The two refusals are different facts: a lockup that WAS read, and a
        route nobody described. A reader sent to argue with the venue about a
        lockup that was never reported is a reader sent to the wrong place."""
        unread = _policy(_without("lockup_days"))["reasons"]
        assert not any("has a lockup" in x for x in unread), unread


# ── require_noncustodial ─────────────────────────────────────────────────
class TestTheNonCustodialGate:
    def test_a_measured_custodial_route_still_refuses(self):
        assert _policy(_move(custodial=True))["verdict"] == "fail"

    def test_a_measured_noncustodial_route_still_passes(self):
        assert _policy(_move(custodial=False))["verdict"] == "pass"

    @pytest.mark.parametrize("move, label", [
        (_without("custodial"), "the field is absent"),
        (_move(custodial=None), "the field is null"),
        (_move(custodial="false"), "the field is the string spelling"),
    ])
    def test_an_unreported_route_is_refused_by_name(self, move, label):
        r = _policy(move)
        assert r["verdict"] == "fail", label
        assert any("does not say whether the route is custodial" in x
                   for x in r["reasons"]), (label, r["reasons"])


# ── the size ─────────────────────────────────────────────────────────────
class TestTheSize:
    def test_a_measured_oversize_move_still_refuses(self):
        r = _policy(_move(amount_usd=9999.0))
        assert r["verdict"] == "fail"
        assert any("exceeds the $50.00 per-move cap" in x for x in r["reasons"])

    def test_a_measured_size_under_the_cap_still_passes(self):
        assert _policy(_move(amount_usd=40.0))["verdict"] == "pass"

    @pytest.mark.parametrize("move", [_without("amount_usd"), _move(amount_usd="n/a")])
    def test_an_unread_size_clears_no_cap(self, move):
        """$0 is under every cap there is, so the coercion did not merely print
        wrong — it PASSED both money limits for a move of unknown size."""
        r = _policy(move)
        assert r["verdict"] == "fail"
        assert any("per-move cap could not be checked" in x for x in r["reasons"])
        assert any("daily cap could not be checked" in x for x in r["reasons"])

    def test_the_preview_an_operator_signs_from_shows_no_figure(self):
        d = yp.evaluate_yield_move(move=_without("amount_usd"), to_chain="base-sepolia",
                                   dest=DEST, envelope=_envelope(), now_ts=1000.0)
        assert d["first_leg"]["notional_usd"] is None, (
            "$0.00 on the first-leg preview reads as a measured size")

    def test_a_read_size_is_still_on_the_preview(self):
        d = yp.evaluate_yield_move(move=_move(), to_chain="base-sepolia",
                                   dest=DEST, envelope=_envelope(), now_ts=1000.0)
        assert d["first_leg"]["notional_usd"] == 40.0

    def test_the_envelope_is_never_asked_to_authorise_a_size_nobody_read(self):
        """The envelope's limits are NOTIONAL limits. Passing 0.0 asked it to
        authorise a $0 transfer and read the allow as authority for a move of
        unknown size."""
        d = yp.evaluate_yield_move(move=_without("amount_usd"), to_chain="base-sepolia",
                                   dest=DEST, envelope=_envelope(), now_ts=1000.0)
        assert d["gates"]["authority"] is False
        assert any("notional limits could not be checked" in x for x in d["reasons"])


# ── the rule that always read None, and said it badly ────────────────────
class TestTheBreakevenRuleSaysTheAbsence:
    def test_it_still_refuses(self):
        assert _policy(_without("breakeven_days"))["verdict"] == "fail"

    def test_and_no_longer_prints_the_absence_as_a_figure(self):
        r = _policy(_without("breakeven_days"))
        assert not any("None days" in x for x in r["reasons"]), r["reasons"]
        assert any("breakeven was not reported" in x for x in r["reasons"])


# ── the whole gate, and the healthy path ─────────────────────────────────
class TestTheTripleGate:
    def test_a_fully_reported_worth_move_still_executes(self):
        """The load-bearing negative control: every refusal above must come
        from the unreadable field and not from the cure being too wide."""
        d = yp.evaluate_yield_move(move=_move(), to_chain="base-sepolia", dest=DEST,
                                   envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "execute", d["reasons"]
        assert d["gates"] == {"scanner": True, "policy": True, "authority": True}

    @pytest.mark.parametrize("field", ["lockup_days", "custodial", "amount_usd"])
    def test_and_one_unreported_safety_field_is_enough_to_skip(self, field):
        d = yp.evaluate_yield_move(move=_without(field), to_chain="base-sepolia",
                                   dest=DEST, envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "skip", (field, d["reasons"])
        assert d["gates"]["policy"] is False, field

    def test_checked_no_longer_claims_a_rule_it_could_not_read(self):
        """`checked` was 8 whether or not three of the inputs were readable, so
        the count was the same over data and over nothing. It is honest now
        because an unreadable input produces a REASON rather than a pass."""
        for field in ("lockup_days", "custodial", "amount_usd"):
            r = _policy(_without(field))
            assert r["checked"] == 8, field
            assert r["reasons"], f"{field}: counted as checked and said nothing"

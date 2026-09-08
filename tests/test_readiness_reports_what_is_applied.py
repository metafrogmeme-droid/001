"""The readiness card said a learner was ACCUMULATING and switched ON, and
recommended nothing about it.

From a live card:

    ⏳ calibration: ACCUMULATING (23/30)
       apply flag: AUTO_CONFIRM_USE_CALIBRATED — ON

That is a learner adjusting confidence on real trades from a curve its own gate
says is too thin, printed on the report whose header says it answers "the
question the operator has to answer before flipping". The recommendations loop
had two branches and both keyed on READY:

    READY + not applied  -> "consider <FLAG>=true"
    READY + applied      -> "applied and validated ✓"

so the fourth combination — APPLIED and NOT validated — fell through to
silence, and it is the only one of the four that means something is already
wrong. The renderer's bare " — ON" beside it reads as approval.

TWO MORE ON THE SAME CARD, same rule:

  * "Resolved outcomes: 23" was `resolved_samples`, which is the CALIBRATOR's
    extraction and nothing else's — printed above a component claiming 46
    unseen trades and another counting 168. `assess_readiness` had already
    added `decisions_on_record` for exactly this, with a comment naming a live
    6 / 17 / 61 card; the renderer went on printing the other number. The fix
    reached the assessor and never reached the surface anyone reads, and the
    guard that landed with it (`test_the_raw_decision_pool_is_reported_under_
    its_own_name`) asserts the KEY EXISTS IN THE DICT — one step short of the
    card.

  * A store that could not be opened reported `decisions_on_record: 0`, which
    is the same sentence a genuinely fresh install prints. The pool is what
    every component's evidence is judged against; a zero nobody measured is the
    worst value it can carry.
"""
from __future__ import annotations

import pytest

from bot.learning.readiness import (
    assess_readiness,
    recommendations_for,
    render_report,
)


def _assessment(**components):
    return {"decisions_on_record": 168, "resolved_samples": 23,
            "components": components, "recommendations": []}


def _recommend(**components):
    """The REAL rule over planted component states.

    The first draft of this helper reimplemented the loop it was testing, so
    all three tests below passed against a copy and would have passed against
    any production code at all. `recommendations_for` exists so the subject can
    be reached: the rule needs a store, a fitted calibrator and a config to get
    at through `assess_readiness`, which is why the test wrote its own instead.
    """
    return {"recommendations": recommendations_for(components)}


# ── the missing fourth case, driven end to end ────────────────────────────

class _Store:
    """A store with nothing in it, so every component lands ACCUMULATING."""

    def get_decisions(self, symbol=None, limit=100):
        return []


@pytest.mark.parametrize("flag_on", [True, False])
def test_a_learner_applied_while_not_ready_is_called_out(monkeypatch, flag_on):
    """Driven through the real assessor: flip the calibration apply flag and
    read what the report says. With the flag ON and the component ACCUMULATING
    there must be a recommendation, and it must not be a compliment."""
    from bot.config import CONFIG
    before = CONFIG.auto_confirm_use_calibrated
    object.__setattr__(CONFIG, "auto_confirm_use_calibrated", flag_on)
    try:
        a = assess_readiness(store=_Store())
    finally:
        object.__setattr__(CONFIG, "auto_confirm_use_calibrated", before)

    cal = a["components"]["calibration"]
    assert cal["state"] == "ACCUMULATING", "the fixture stopped reproducing"
    # SCOPED TO THE COMPONENT UNDER TEST. A bare "no warnings" was a claim about
    # every learner, and it started failing honestly the day `setup_expectancy`
    # stopped reporting `applied` from `is_ready()` — its flag is on by default,
    # so it raises this same warning while it accumulates. That is a true line
    # about a different component, not a regression in this one.
    warnings = [r for r in a["recommendations"]
                if "NOT validated" in r and "calibration" in r]
    if flag_on:
        assert warnings, (
            "the flag is ON and the evidence bar is unmet, and the report that "
            "exists to govern that decision said nothing")
        assert "AUTO_CONFIRM_USE_CALIBRATED" in warnings[0]
        assert "NOT validated" in a["recommendations"][0], (
            "a warning must lead: these are the only lines that mean something "
            "is already wrong, and under an 'applied and validated ✓' for some "
            "other component is where they would not be read")
    else:
        assert not warnings


def test_the_other_three_combinations_are_unchanged():
    """The fourth branch must not have swallowed the three that worked."""
    out = _recommend(
        a={"state": "READY", "applied": False, "flag": "F_A"},
        b={"state": "READY", "applied": True, "flag": "F_B"},
        c={"state": "ACCUMULATING", "applied": False, "flag": "F_C"},
    )
    recs = "\n".join(out["recommendations"])
    assert "consider F_A=true" in recs
    assert "b: applied and validated ✓" in recs
    assert "F_C" not in recs, "an unapplied, unready component needs no line"
    assert "NOT validated" not in recs


def test_the_warning_sorts_above_the_compliments():
    """It has to be read, and under "x: applied and validated ✓" for some other
    component is exactly where it would not be.

    THE FIRST VERSION OF THIS ASSERTION LIVED IN THE TEST ABOVE, where only one
    component was applied and no READY one produced a note — so `warnings +
    notes` and `notes + warnings` returned the identical list and a mutation
    swapping them SURVIVED. An ordering claim needs both kinds present.
    """
    out = _recommend(
        good={"state": "READY", "applied": True, "flag": "F_GOOD"},
        bad={"state": "ACCUMULATING", "applied": True, "flag": "F_BAD"},
        idle={"state": "READY", "applied": False, "flag": "F_IDLE"},
    )
    recs = out["recommendations"]
    assert len(recs) == 3, recs
    assert "NOT validated" in recs[0], f"the warning is not first: {recs}"
    assert any("applied and validated" in r for r in recs[1:])
    assert any("consider F_IDLE=true" in r for r in recs[1:])


def test_validating_counts_too_not_just_accumulating():
    """VALIDATING means the OOS check did not clear the bar — applying that is
    the same mistake as applying an under-sampled one, and the first draft of
    this branch only named ACCUMULATING."""
    out = _recommend(v={"state": "VALIDATING", "applied": True, "flag": "F_V"})
    assert any("NOT validated (VALIDATING)" in r for r in out["recommendations"])


# ── the header number ─────────────────────────────────────────────────────

def test_the_card_prints_the_pool_not_the_calibrators_subset():
    """`resolved_samples` is one component's extraction. Heading the card with
    it invites reading three disagreeing denominators as one — which is what
    `decisions_on_record` was added to prevent, in the assessor, a fix the
    renderer never picked up."""
    txt = render_report(_assessment(
        calibration={"state": "ACCUMULATING", "samples": 23, "needed": 30,
                     "applied": True, "flag": "AUTO_CONFIRM_USE_CALIBRATED"}))
    head = txt.split("\n")[2]
    assert "168" in head, f"the header is not the decision pool: {head!r}"
    assert "23" not in head, (
        "the calibrator's own subset is still the card's headline number")
    assert "Resolved outcomes" not in txt, (
        "the label promised a shared denominator that does not exist")
    # And the card says the counts below are different populations, because
    # three numbers on one card with no note is what caused the misreading.
    assert "not this one" in txt


def test_an_unreadable_store_is_not_zero_decisions():
    """A store that could not be opened printed the same number as a fresh
    install. The pool is what every component's evidence is judged against."""
    a = _assessment()
    a["decisions_on_record"] = None
    txt = render_report(a)
    assert "unknown" in txt and "could not be read" in txt
    head = txt.split("\n")[2]
    assert "0" not in head, f"an unmeasured pool rendered as a count: {head!r}"


def test_a_broken_store_reports_unknown_end_to_end():
    """Through the real assessor, not a planted dict — `decisions` starts None
    and only a successful read replaces it."""
    class _Boom:
        def get_decisions(self, symbol=None, limit=100):
            raise RuntimeError("disk gone")

    a = assess_readiness(store=_Boom())
    assert a["decisions_on_record"] is None, (
        "an unreachable learning store reported a measured zero")
    assert "unknown" in render_report(a)


def test_a_readable_empty_store_is_a_real_zero():
    """The control, and the distinction the whole fix is about: nothing on
    record is a MEASUREMENT, and must not be reported as unknown."""
    a = assess_readiness(store=_Store())
    assert a["decisions_on_record"] == 0
    assert "unknown" not in render_report(a)


# ── the flag line ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("state, expect", [
    ("READY", " — ON"),
    ("ACCUMULATING", "ON, NOT VALIDATED"),
    ("VALIDATING", "ON, NOT VALIDATED"),
])
def test_on_alone_does_not_stand_beside_an_unvalidated_component(state, expect):
    """A bare affirmative is a claim. Beside a state the reader has already
    skimmed past, " — ON" reads as approval of exactly the thing that has not
    been approved."""
    txt = render_report(_assessment(
        calibration={"state": state, "applied": True, "flag": "F"}))
    flag_line = next(ln for ln in txt.splitlines() if "apply flag" in ln)
    assert expect in flag_line, flag_line
    if state != "READY":
        assert "NOT VALIDATED" in flag_line


def test_an_unapplied_flag_claims_nothing():
    txt = render_report(_assessment(
        calibration={"state": "READY", "applied": False, "flag": "F"}))
    flag_line = next(ln for ln in txt.splitlines() if "apply flag" in ln)
    assert "ON" not in flag_line

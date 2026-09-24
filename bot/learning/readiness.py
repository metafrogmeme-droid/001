"""Learning-loop readiness: is the learned stack validated enough to apply?

The learners fit continuously (auto-refit every N closes) but their
APPLICATION stays behind default-OFF flags until a human flips them. This
module answers, from evidence, the question the operator has to answer
before flipping: "do we have enough resolved outcomes, and does the learned
adjustment hold out-of-sample?" — and turns state changes into a proactive
alert so nobody has to remember to check.

Components assessed:
  - confidence calibration  -> AUTO_CONFIRM_USE_CALIBRATED
  - voter-weight learning   -> VOTER_WEIGHT_LEARNING_ENABLED
  - per-setup expectancy    -> (applies via confidence nudge when ready)

States per component:
  ACCUMULATING  — not enough resolved samples yet (n/needed shown)
  VALIDATING    — enough samples but the OOS check does not clear the bar
  READY         — fitted, enough samples, OOS evidence clears the bar

The walk-forward result on production data (0/6 profitable folds) makes the
learning loop the deciding evidence source for where the edge is — which is
exactly why readiness is assessed from resolved LIVE/paper outcomes, not
from backtest curves.
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger("runeclaw.readiness")

# The OOS bar for voter weights: the fraction of learned voters whose
# adjustment direction must hold on unseen trades. 0.5 = coin flip; demand
# clearly better before recommending live application.
_VW_HOLD_RATE_BAR = 0.6

# ...AND ENOUGH UNSEEN TRADES TO MEAN IT. The bar alone was the whole test, and
# the only sample requirement beside it was `n_test > 0` — so a single held
# voter on one unseen trade reported READY with a confident "100%". Observed
# live at n_test=17: 67% against a 60% bar, which is 2 voters of 3, and the
# 95% interval on that spans roughly a coin flip to near-certainty. A bar
# whose own comment says "demand clearly better than a coin flip" cannot be
# read off a sample that cannot distinguish one.
_VW_MIN_TEST_TRADES = 40
# Voters are the unit hold_rate is a fraction OF. Two of three is 67% and is
# not evidence; the count has to be on the card next to the percentage.
_VW_MIN_VOTERS = 3
#: …AND THE MARGIN HAS TO SURVIVE THE ARITHMETIC. The floors above bound the
#: SAMPLE; they say nothing about whether the rate clears the bar by more than
#: noise. A live card read "62% of 34 voter(s) … (bar 60%)" and recommended
#: enabling the flag — 21 of 34, which a coin flip clears about one time in ten.
#: The bar's own comment demands "clearly better than a coin flip", so the
#: WHOLE interval has to be, not the point estimate.
_VW_CHANCE = 0.5
_VW_Z = 1.96          # ~95%

#: SETUP EXPECTANCY'S TEST, and the floor under it. The favoured and the
#: disfavoured unseen trades are compared; under fifteen in either group the
#: interval on the difference is wide enough to straddle any real effect, and a
#: group that is empty (every unseen trade nudged the same way) cannot tell a
#: learned difference from the base rate at all.
_SE_MIN_EACH = 15

#: What the learners read, and so what the pool is counted over. The card used
#: to read the most recent 5000 and print their LENGTH, so a store of any size
#: past that read "Decisions on record: 5000" -- a read's limit presented as a
#: count -- under a sentence saying every component judges a subset of it,
#: while voter weights and setup expectancy each read up to this many.
_POOL_LIMIT = 100000
#: What the calibrator is handed, unchanged: its sample count was taken over
#: the most recent 5000, and moving that is a decision about calibration.
_CAL_DECISIONS = 5000


def wilson_lower_bound(successes: int, n: int, z: float = _VW_Z) -> float:
    """Lower end of the Wilson score interval for `successes / n`.

    Wilson rather than the textbook normal interval because the normal one is
    badly behaved exactly where this is used — small n and a rate near the
    edges, where it can hand back a bound below 0 or above 1. Pure arithmetic;
    this repo takes no stats dependency for one number.

    `n <= 0` returns 0.0: no sample cannot clear any bar, and returning the
    point estimate for an empty one is the fabrication this whole file exists
    to prevent. `p` is clamped because both counts are read off a report dict
    rather than computed here, so a stale or hand-written record must be
    bounded rather than propagated.

    ZERO SUCCESSES RETURNS EXACTLY ZERO, and that early exit is not a
    tidiness. At p == 0 the interval's two halves cancel algebraically, but in
    float64 they do not: `wilson_lower_bound(0, 11)` came out at 2e-17, so a
    voter set that held on NOTHING carried a positive lower bound. It is a
    rounding error and it renders as `0%`, and it is still a positive number
    standing where a measured zero belongs.
    """
    if n <= 0:
        return 0.0
    p = max(0.0, min(1.0, successes / n))
    if p <= 0.0:
        return 0.0
    z2 = z * z
    centre = p + z2 / (2 * n)
    # `math.sqrt`, not `** 0.5`: the latter is typed `Any` (a float power can
    # be complex), which propagates through `margin` and makes the return an
    # unmeasured Any on a number that gates a live-money flag.
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


# Calibration readiness rides on the fitter's own min_samples (30), but for
# a RECOMMENDATION we want a fuller curve than the bare minimum.
_CAL_RECOMMEND_SAMPLES = 50


def assess_readiness(store=None) -> dict:
    """Assess every learner. Never raises — a component that errors reports
    state 'ERROR' with the message, and the others still assess."""
    out: dict = {"components": {}, "resolved_samples": 0,
                 "decisions_on_record": None, "recommendations": []}

    # Every learner extracts its OWN samples from these decisions, and they do
    # not agree: one live card showed 6 / 17 / 61 for calibration, voter
    # weights and setup expectancy. Calling any one of them "the" resolved
    # count — as this line used to — invites reading three different
    # denominators as one number. Each component reports its own `samples`;
    # `decisions_on_record` is the raw pool they are drawn from, named for
    # what it is.
    # None until a read succeeds — an unreachable store is not an empty one.
    # This was `[]`, so a store that could not be opened reported
    # `decisions_on_record: 0` and the card said the bot had no decisions on
    # record, which is the same sentence it prints on a genuinely fresh
    # install. The pool is the number every component's evidence is judged
    # against; a zero nobody measured is the worst value it can carry.
    decisions = None
    pool = None
    try:
        from bot.learning.store import LearningStore
        pool = (store or LearningStore()).get_decisions(limit=_POOL_LIMIT)
        decisions = pool[-_CAL_DECISIONS:]
    except Exception as exc:
        log.debug("readiness: store unavailable: %s", exc)
    # A fact about the STORE, so it is set here and not inside a component's
    # `try`: it used to sit in calibration's, and a calibration fault blanked
    # the pool every other component is judged against. True capped = the
    # read stopped at its limit, so the count is a floor.
    out["decisions_on_record"] = None if pool is None else len(pool)
    out["decisions_capped"] = pool is not None and len(pool) >= _POOL_LIMIT

    # -- confidence calibration ------------------------------------------------
    comp: dict = {"flag": "AUTO_CONFIRM_USE_CALIBRATED"}
    try:
        from bot.config import CONFIG
        # TWO FLAGS APPLY THIS CURVE and the card named one. The auto-confirm
        # bar reads it behind AUTO_CONFIRM_USE_CALIBRATED; the analyzer moves
        # every idea's confidence through it, before the entry floor, behind
        # CONFIDENCE_CALIBRATION_ENABLED -- the one that decides what trades at
        # all. The card reported the curve as not applied while that flag moved
        # every entry: the flag read as the state.
        on = [name for name, v in (
            ("CONFIDENCE_CALIBRATION_ENABLED",
             CONFIG.analyzer.confidence_calibration_enabled),
            ("AUTO_CONFIRM_USE_CALIBRATED", CONFIG.auto_confirm_use_calibrated))
            if v]
        if on:
            comp["flag"] = " + ".join(on)
        from bot.learning.confidence_calibration import ConfidenceCalibrator
        from bot.learning.outcome_join import counted_under_current_rule
        rows = ConfidenceCalibrator.rows_from_decisions(decisions or [])
        samples = rows.samples
        # Kept for callers that already read it, but it is the CALIBRATOR's
        # extraction and nothing else's.
        out["resolved_samples"] = len(samples)
        left_out = _calibration_left_out(rows)
        cal = ConfidenceCalibrator.load()
        n = getattr(cal, "_n_samples", 0) if cal else 0
        need = getattr(cal, "min_samples", 30) if cal else 30
        # The fit's own count can exceed this read's (it reads a longer
        # history), which is why the larger is shown -- but only a fit counted
        # under the CURRENT rule has a count that means the same thing. One
        # counted under an older reading rests on samples this rule refuses.
        current = cal is None or counted_under_current_rule(cal)
        counted = max(n, len(samples)) if current else len(samples)
        comp.update(samples=counted, needed=need, applied=bool(on))
        if cal is None or not cal.is_ready():
            comp["state"] = "ACCUMULATING"
        elif counted < _CAL_RECOMMEND_SAMPLES:
            comp["state"] = "VALIDATING"
            comp["note"] = (f"fitted, but curve rests on {n} samples — "
                            f"recommend >= {_CAL_RECOMMEND_SAMPLES} before applying")
        else:
            comp["state"] = "READY"
            comp["note"] = cal.summary()
        stale = "" if current else (
            f"the fitted curve on disk rests on {n} samples counted under an older "
            "rule; the bot refits it when it starts (LEARNING_AUTO_REFIT_ENABLED), "
            "or /calibration refit does it now")
        extra = [x for x in (comp.get("note"), stale, left_out) if x]
        if extra:
            comp["note"] = "; ".join(extra)
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["calibration"] = comp

    # -- voter weights -----------------------------------------------------------
    comp = {"flag": "VOTER_WEIGHT_LEARNING_ENABLED"}
    try:
        from bot.config import CONFIG
        from bot.learning.voter_weights import VoterWeightLearner
        learner = VoterWeightLearner()
        samples = learner.load_samples(store)
        comp.update(samples=len(samples), needed=learner.min_samples,
                    applied=CONFIG.analyzer.voter_weight_learning_enabled)
        if len(samples) < learner.min_samples:
            comp["state"] = "ACCUMULATING"
        else:
            oos = learner.validate_oos(samples)
            comp["oos_hold_rate"] = oos.get("hold_rate", 0.0)
            comp["oos_n_test"] = oos.get("n_test", 0)
            n_test = int(oos.get("n_test", 0) or 0)
            # THE RATE'S OWN DENOMINATOR. `hold_rate` is holds/JUDGED, and this
            # read `len(voters)` — every learned voter, including the ones no
            # unseen trade agreed with. "62% of 34 voter(s)" stated a fraction
            # over one population beside a count of another.
            n_voters = int(oos.get("n_judged", 0) or 0)
            n_holds = int(oos.get("n_holds", 0) or 0)
            comp["oos_n_voters"] = n_voters
            comp["oos_n_learned"] = len(oos.get("voters") or {})
            rate = float(oos.get("hold_rate", 0.0) or 0.0)
            lower = wilson_lower_bound(n_holds, n_voters)
            comp["oos_hold_rate_lower"] = round(lower, 4)
            # "67%" alone reads as a trade-level win rate. It is the fraction
            # of learned VOTERS whose direction held, so the card says which
            # unit it is and how many there were — 2 of 3 and 27 of 40 are the
            # same percentage and not the same evidence.
            evidence = (f"{rate:.0%} of {n_voters} voter(s) held direction on "
                        f"{n_test} unseen trade(s)")
            if n_test < _VW_MIN_TEST_TRADES or n_voters < _VW_MIN_VOTERS:
                comp["state"] = "VALIDATING"
                comp["note"] = (
                    f"{evidence} — not enough to judge yet "
                    f"(need >= {_VW_MIN_TEST_TRADES} trades and "
                    f">= {_VW_MIN_VOTERS} voters before the {_VW_HOLD_RATE_BAR:.0%} "
                    "bar means anything)")
            elif rate >= _VW_HOLD_RATE_BAR and lower <= _VW_CHANCE:
                # Clears the bar on the point estimate and NOT on the interval:
                # the margin is inside the noise the floors were added to keep
                # out. Not READY, and the note says which of the two it failed.
                comp["state"] = "VALIDATING"
                comp["note"] = (
                    f"{evidence} clears the {_VW_HOLD_RATE_BAR:.0%} bar, but "
                    f"its 95% lower bound is {lower:.0%} — a coin flip reaches "
                    "this margin often enough that it is not yet evidence")
            elif rate >= _VW_HOLD_RATE_BAR:
                comp["state"] = "READY"
                comp["note"] = (f"{evidence} (bar {_VW_HOLD_RATE_BAR:.0%}, "
                                f"95% lower bound {lower:.0%})")
            else:
                comp["state"] = "VALIDATING"
                comp["note"] = (f"{evidence} < bar {_VW_HOLD_RATE_BAR:.0%} — "
                                "learned directions do not generalize yet")
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["voter_weights"] = comp

    # -- setup expectancy ----------------------------------------------------------
    # `applied` USED TO BE `se.is_ready()`, which is a different question and
    # made two of the four (state, applied) combinations unreachable: READY
    # implied applied, so "validated but not applied — consider enabling" could
    # never fire for this component and "applied and validated ✓" always did.
    # The flag was labelled "(auto-applies when ready)", true before
    # SETUP_EXPECTANCY_ENABLED existed and false since.
    #
    # It matters more now: evidence can qualify at a BACKED-OFF tier while
    # SETUP_EXPECTANCY_BACKOFF_ENABLED is off, in which case the analyzer
    # shadow-logs the nudge and applies nothing — and the card would have said
    # "applied and validated ✓" over it.
    comp = {"flag": "SETUP_EXPECTANCY_ENABLED"}
    try:
        from bot.config import CONFIG
        from bot.learning.setup_expectancy import get_setup_expectancy
        se = get_setup_expectancy(reload=True)
        _tiers = se.learned_at_tier()
        # Ready, but with nothing at setup level: whatever is moving trades is
        # coming from the wider tier, so THAT is the switch to name.
        _coarse_only = se.is_ready() and _tiers.get("setup", 0) == 0
        _on = bool(CONFIG.analyzer.setup_expectancy_enabled)
        _backoff = bool(getattr(CONFIG.analyzer,
                                "setup_expectancy_backoff_enabled", False))
        if _coarse_only:
            comp["flag"] = "SETUP_EXPECTANCY_BACKOFF_ENABLED"
        comp.update(setups=len(getattr(se, "_table", {}) or {}),
                    tiers=_tiers,
                    applied=_on and (_backoff or not _coarse_only))
        if not se.is_ready():
            comp["state"] = "ACCUMULATING"
            comp["note"] = se.summary()
        else:
            # A BUCKET WITH TEN TRADES IS A FLOOR, NOT A TEST: READY used to be
            # exactly that, and the recommendation below it says "validated".
            # The record has to tell unseen trades apart first.
            comp["state"], why = _setup_expectancy_verdict(pool, se.min_samples)
            comp["note"] = f"{se.summary()}\n   {why}"
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["setup_expectancy"] = comp

    # -- recommendations -------------------------------------------------------
    out["recommendations"] = recommendations_for(out["components"])
    return out


def _setup_expectancy_verdict(pool, min_samples: int) -> tuple:
    """``(state, sentence)`` from the out-of-sample test on the decisions read.

    A store that could not be read is VALIDATING with that said: the record
    loaded, and whether it predicts anything is exactly what went unmeasured.
    """
    if pool is None:
        return ("VALIDATING", "not tested: the learning store could not be read, "
                              "so nothing checked whether the record predicts "
                              "unseen trades")
    from bot.learning.setup_expectancy import SetupExpectancy, validate_oos
    oos = validate_oos(SetupExpectancy.samples_from_decisions(pool),
                       min_samples=min_samples)
    nf, nd = oos["n_favoured"], oos["n_disfavoured"]
    head = (f"on {oos['n_test']} unseen trade(s), {nf} nudged up and {nd} "
            "nudged down")
    if nf < _SE_MIN_EACH or nd < _SE_MIN_EACH:
        return ("VALIDATING", f"{head} — need >= {_SE_MIN_EACH} of each before "
                              "comparing them means anything")
    pf, pd, low = oos["win_favoured"], oos["win_disfavoured"], oos["lower"]
    evidence = (f"{head}: won {pf:.0%} against {pd:.0%}, a difference whose "
                f"95% interval starts at {low:+.0%}")
    if low <= 0:
        return ("VALIDATING", f"{evidence} — the record does not yet tell "
                              "winners from losers")
    return ("READY", evidence)


#: The states that mean a component's evidence bar has NOT been cleared.
_UNVALIDATED = ("ACCUMULATING", "VALIDATING")


def recommendations_for(components: dict) -> list:
    """The four (state, applied) combinations, as lines for the card.

    A FUNCTION SO IT CAN BE DRIVEN. It was a loop at the end of
    `assess_readiness`, which needs a store, a fitted calibrator and a config
    to reach — so a test of the rule either reimplemented it (and then tested
    its own copy) or did not exist. Both happened.

    THE FOURTH CASE WAS THE MISSING ONE. Both original branches keyed on READY:

        READY + not applied  -> consider <FLAG>=true
        READY + applied      -> applied and validated ✓

    so APPLIED-and-NOT-validated fell through to silence — the only one of the
    four that means something is already wrong. A live card showed
    `calibration: ACCUMULATING (23/30)` with `AUTO_CONFIRM_USE_CALIBRATED — ON`
    directly beneath it and recommended nothing: a learner adjusting confidence
    on real trades from a curve its own gate calls too thin, on the report whose
    header says it answers "the question the operator has to answer before
    flipping".

    It sorts FIRST for the same reason — under an "applied and validated ✓" for
    some other component is exactly where it would not be read.
    """
    warnings: list = []
    notes: list = []
    for name, c in (components or {}).items():
        state, applied = c.get("state"), c.get("applied")
        flag = c.get("flag")
        if applied is True and state in _UNVALIDATED:
            verb = "are" if " + " in str(flag) else "is"
            warnings.append(f"⚠️ {name} is APPLIED but NOT validated ({state}) — "
                            f"{flag} {verb} ON and the evidence bar is not met")
        elif state == "READY" and applied is False:
            notes.append(f"{name} is validated but not applied — "
                         f"consider {flag}=true")
        elif state == "READY" and applied is True:
            notes.append(f"{name}: applied and validated ✓")
    return warnings + notes


def _calibration_left_out(rows) -> str:
    """What the calibrator's reading left out, or "" when it left out nothing.

    Said only when it bites: a permanent "0 left out" under every healthy card
    is the line that trains a reader to skip the next one.
    """
    parts = []
    if rows.not_measured:
        parts.append(f"{rows.not_measured} whose confidence was a stamp or "
                     f"carried from another idea")
    if rows.unattributed:
        parts.append(f"{rows.unattributed} recorded before the bot marked "
                     f"which confidences were measured, with no analyzer "
                     f"figure to tell")
    if rows.not_opened:
        parts.append(f"{rows.not_opened} failed attempt(s) before a retry "
                     f"that opened")
    if not parts:
        return ""
    return "not counted: " + "; ".join(parts)


def render_report(assessment: dict) -> str:
    """Telegram-HTML readiness report."""
    icon = {"READY": "✅", "VALIDATING": "\U0001f7e0",
            "ACCUMULATING": "⏳", "ERROR": "⚠️"}
    # THE RAW POOL, UNDER ITS OWN NAME. This printed `resolved_samples`, which
    # is the CALIBRATOR's extraction and nothing else's — so the card headed
    # itself "Resolved outcomes: 23" above a component claiming 46 unseen
    # trades and another counting 168. `assess_readiness` created
    # `decisions_on_record` for exactly this, with a comment naming a live
    # 6/17/61 card, and the renderer went on printing the other number: the
    # fix reached the assessor and never reached the surface anyone reads.
    #
    # `None` is a store that could not be read, and it says so rather than
    # printing the 0 that a genuinely fresh install also prints.
    pool = assessment.get("decisions_on_record")
    if pool is None:
        pool_text = "unknown — the learning store could not be read"
    elif assessment.get("decisions_capped"):
        pool_text = f"at least {pool} (the read stops there)"
    else:
        pool_text = str(pool)
    lines = ["\U0001f9e0 <b>Learning Readiness</b>", "─" * 28,
             "Decisions on record: <code>" + pool_text + "</code>",
             "<i>each component judges its own subset of these; the counts "
             "below are not this one.</i>", ""]
    for name, c in assessment.get("components", {}).items():
        state = c.get("state", "?")
        head = f"{icon.get(state, '')} <b>{name}</b>: {state}"
        if "samples" in c and "needed" in c and state == "ACCUMULATING":
            head += f" ({c['samples']}/{c['needed']})"
        lines.append(head)
        if c.get("note"):
            lines.append(f"   {c['note']}")
        # "— ON" ALONE READS AS APPROVAL, and beside a component that has not
        # cleared its bar it is the opposite. Colour is a claim, and so is a
        # bare affirmative next to a state the reader has already skimmed past.
        if c.get("applied") is True:
            flag_note = (" — ON" if state == "READY"
                         else " — <b>ON, NOT VALIDATED</b>")
        else:
            flag_note = ""
        lines.append(f"   apply flag: <code>{c.get('flag')}</code>{flag_note}")
    recs = assessment.get("recommendations", [])
    if recs:
        lines += ["", "<b>Recommended:</b>"]
        lines += [f"• {r}" for r in recs]
    else:
        lines += ["", "No action yet — keep accumulating closes."]
    return "\n".join(lines)

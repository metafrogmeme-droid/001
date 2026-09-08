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
    try:
        from bot.learning.store import LearningStore
        decisions = (store or LearningStore()).get_decisions(limit=5000)
    except Exception as exc:
        log.debug("readiness: store unavailable: %s", exc)

    # -- confidence calibration ------------------------------------------------
    comp: dict = {"flag": "AUTO_CONFIRM_USE_CALIBRATED"}
    try:
        from bot.config import CONFIG
        from bot.learning.confidence_calibration import ConfidenceCalibrator
        samples = ConfidenceCalibrator.samples_from_decisions(decisions or [])
        # Kept for callers that already read it, but it is the CALIBRATOR's
        # extraction and nothing else's.
        out["resolved_samples"] = len(samples)
        out["decisions_on_record"] = None if decisions is None else len(decisions)
        cal = ConfidenceCalibrator.load()
        n = getattr(cal, "_n_samples", 0) if cal else 0
        need = getattr(cal, "min_samples", 30) if cal else 30
        comp.update(samples=max(n, len(samples)), needed=need,
                    applied=CONFIG.auto_confirm_use_calibrated)
        if cal is None or not cal.is_ready():
            comp["state"] = "ACCUMULATING"
        elif max(n, len(samples)) < _CAL_RECOMMEND_SAMPLES:
            comp["state"] = "VALIDATING"
            comp["note"] = (f"fitted, but curve rests on {n} samples — "
                            f"recommend >= {_CAL_RECOMMEND_SAMPLES} before applying")
        else:
            comp["state"] = "READY"
            comp["note"] = cal.summary()
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
            n_voters = len(oos.get("voters") or {})
            comp["oos_n_voters"] = n_voters
            rate = float(oos.get("hold_rate", 0.0) or 0.0)
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
            elif rate >= _VW_HOLD_RATE_BAR:
                comp["state"] = "READY"
                comp["note"] = f"{evidence} (bar {_VW_HOLD_RATE_BAR:.0%})"
            else:
                comp["state"] = "VALIDATING"
                comp["note"] = (f"{evidence} < bar {_VW_HOLD_RATE_BAR:.0%} — "
                                "learned directions do not generalize yet")
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["voter_weights"] = comp

    # -- setup expectancy ----------------------------------------------------------
    comp = {"flag": "(auto-applies when ready)"}
    try:
        from bot.learning.setup_expectancy import get_setup_expectancy
        se = get_setup_expectancy(reload=True)
        comp.update(setups=len(getattr(se, "_table", {}) or {}),
                    applied=se.is_ready())
        comp["state"] = "READY" if se.is_ready() else "ACCUMULATING"
        comp["note"] = se.summary()
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["setup_expectancy"] = comp

    # -- recommendations -------------------------------------------------------
    out["recommendations"] = recommendations_for(out["components"])
    return out


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
            warnings.append(f"⚠️ {name} is APPLIED but NOT validated ({state}) — "
                            f"{flag} is ON and the evidence bar is not met")
        elif state == "READY" and applied is False:
            notes.append(f"{name} is validated but not applied — "
                         f"consider {flag}=true")
        elif state == "READY" and applied is True:
            notes.append(f"{name}: applied and validated ✓")
    return warnings + notes


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
    lines = ["\U0001f9e0 <b>Learning Readiness</b>", "─" * 28,
             "Decisions on record: <code>"
             + ("unknown — the learning store could not be read" if pool is None
                else str(pool)) + "</code>",
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

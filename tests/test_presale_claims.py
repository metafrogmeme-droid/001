"""A presale is a pile of assertions with no chain behind them yet.

"Audited by a top firm." "Team fully doxxed." "Liquidity locked for two years."
Most of that cannot be checked at presale time because the thing it describes
does not exist yet — and the ones that CAN be checked are the only purchase a
detective has.

The whole job is keeping a checked claim and an unchecked one visibly apart, so
the second never borrows the credibility of the first. Which makes the failure
mode specific and severe: a claim listed without a verdict reads as verified.
A dossier printing "Audit: CertiK" has, to every reader, asserted the audit.

FOUR STATES, NOT TWO. Collapsing to pass/fail is exactly how "we did not look"
becomes "we looked and it was fine":

    confirmed     checked, held
    refuted       checked, did not hold
    unchecked     checkable in principle, nobody checked it
    unverifiable  nothing could check this — "the team is experienced"

`unchecked` and `unverifiable` are different words on purpose. The first is a
gap in OUR work and closing it is a to-do; the second is a property of the claim
and no effort moves it. Someone deciding whether to wait for more diligence
needs to know which they are looking at.
"""
from __future__ import annotations


from bot.core.presale_claims import (CONFIRMED, MISREPRESENTED,
                                     PARTLY_SUBSTANTIATED, REFUTED, UNCHECKED,
                                     UNSUBSTANTIATED, UNVERIFIABLE, classify,
                                     human_readable, verify)


def _status(claim: dict) -> str:
    return classify(claim)["status"]


# ── the four states ──────────────────────────────────────────────────

def test_a_checked_claim_that_held_is_confirmed():
    assert _status({"kind": "lp_lock", "holds": True}) == CONFIRMED


def test_a_checked_claim_that_failed_is_refuted():
    assert _status({"kind": "lp_lock", "holds": False}) == REFUTED


def test_an_unchecked_claim_is_never_confirmed():
    """The founding rule of this file. A claim listed with no verdict reads as
    verified to every reader — 'Audit: CertiK' on a page asserts the audit."""
    assert _status({"kind": "audit_report"}) == UNCHECKED


def test_a_missing_check_is_not_a_failed_one():
    """`holds` is tri-state on purpose. If None collapsed to False, a timed-out
    endpoint would have this module reporting a project as lying."""
    assert _status({"kind": "audit_report", "holds": None}) == UNCHECKED
    assert _status({"kind": "audit_report", "holds": False}) == REFUTED


def test_a_claim_nothing_can_check_is_unverifiable_not_unchecked():
    """Different words for different things: `unchecked` is a to-do, and saying
    it here would promise a check that was never possible."""
    assert _status({"kind": "team", "text": "20 years experience"}) == UNVERIFIABLE


def test_the_two_absences_are_never_merged():
    r = verify([{"kind": "lp_lock"}, {"kind": "roadmap", "text": "CEX Q4"}])
    assert r["counts"][UNCHECKED] == 1
    assert r["counts"][UNVERIFIABLE] == 1
    assert len(r["outstanding"]) == 1, (
        "the to-do list must hold only what can actually be done")


# ── refuted outranks everything ──────────────────────────────────────

def test_one_refuted_claim_outranks_any_number_of_confirmations():
    """A project claiming an audit that does not exist has not erred, it has
    lied — and one lie is worth more evidence than any amount of corroboration.
    Same rule as a hard flag in token_safety."""
    r = verify([
        {"kind": "audit_report", "holds": False, "basis": "no report names this bytecode"},
        {"kind": "lp_lock", "holds": True},
        {"kind": "vesting_contract", "holds": True},
        {"kind": "supply_split", "holds": True},
    ])
    assert r["verdict"] == MISREPRESENTED


def test_the_refuted_claim_is_named_with_its_reason():
    r = verify([{"kind": "audit_report", "text": "Audited by X", "holds": False,
                 "basis": "no report names this bytecode"}])
    text = human_readable(r)
    assert "REFUTED" in text and "Audited by X" in text
    assert "no report names this bytecode" in text


# ── the ceiling ──────────────────────────────────────────────────────

def test_the_best_available_verdict_is_only_partly_substantiated():
    r = verify([{"kind": "audit_report", "holds": True},
                {"kind": "lp_lock", "holds": True}])
    assert r["verdict"] == PARTLY_SUBSTANTIATED


def test_there_is_no_fully_verified_verdict():
    """Even with every checkable claim confirmed, the unverifiable ones are as
    unknown as they started. A presale cannot earn more than 'partly' from the
    outside, and the word keeps saying so."""
    r = verify([{"kind": k, "holds": True} for k in
                ("audit_report", "lp_lock", "vesting_contract",
                 "supply_split", "contract_address")])
    assert r["verdict"] == PARTLY_SUBSTANTIATED
    assert "verified" not in r["verdict"]


def test_the_render_says_what_partly_leaves_out():
    r = verify([{"kind": "lp_lock", "holds": True}])
    assert "exactly as unknown as before" in human_readable(r)


def test_an_outstanding_check_blocks_the_ceiling():
    """One confirmed and one still outstanding is not a substantiated presale —
    the unchecked one could be the refuted one."""
    r = verify([{"kind": "audit_report", "holds": True}, {"kind": "lp_lock"}])
    assert r["verdict"] == UNSUBSTANTIATED


# ── the all-talk presale ─────────────────────────────────────────────

def test_a_presale_of_pure_marketing_is_unsubstantiated():
    """Nothing checkable was claimed, so nothing was substantiated. The answer
    is deliberately unflattering: an absence of checkable claims is itself the
    finding."""
    r = verify([{"kind": "team", "text": "Team fully doxxed"},
                {"kind": "roadmap", "text": "CEX listings Q4"}])
    assert r["verdict"] == UNSUBSTANTIATED
    assert r["counts"][CONFIRMED] == 0


def test_unverifiable_claims_are_listed_not_summarised_away():
    """A presale whose entire pitch is unverifiable should LOOK like one. A
    count alone lets a reader assume the substance was somewhere else."""
    text = human_readable(verify([
        {"kind": "team", "text": "Team fully doxxed"},
        {"kind": "roadmap", "text": "CEX listings Q4"},
    ]))
    assert "Team fully doxxed" in text and "CEX listings Q4" in text


def test_an_empty_claim_set_is_not_a_pass():
    r = verify([])
    assert r["verdict"] == UNSUBSTANTIATED


def test_it_survives_junk():
    assert verify(None)["verdict"] == UNSUBSTANTIATED
    assert classify(None)["status"] == UNVERIFIABLE
    assert human_readable(None)


# ── the checkable list is the honest boundary ────────────────────────

def test_only_named_kinds_can_ever_be_unchecked():
    """CHECKABLE_KINDS is the set of checks that actually exist. A kind absent
    from it is one nobody has built, and calling that 'unchecked' would imply
    the check exists and was skipped."""
    from bot.core.presale_claims import CHECKABLE_KINDS
    for kind in CHECKABLE_KINDS:
        assert _status({"kind": kind}) == UNCHECKED
    for kind in ("team", "roadmap", "partnership", "marketing", None):
        assert _status({"kind": kind}) == UNVERIFIABLE


def test_provenance_survives_classification():
    got = classify({"kind": "lp_lock", "holds": True, "source": "locker-contract"})
    assert got["source"] == "locker-contract"


# ── vocabulary ───────────────────────────────────────────────────────

def test_the_verdicts_do_not_collide_with_the_other_scorers():
    from bot.core import deployer_history as dh
    from bot.core import token_dossier as td
    from bot.core import token_safety as ts
    ours = {MISREPRESENTED, UNSUBSTANTIATED, PARTLY_SUBSTANTIATED}
    others = ({ts.SAFE, ts.CAUTION, ts.DANGER}
              | {dh.CLEAN, dh.UNPROVEN, dh.SUSPECT, dh.KNOWN_BAD}
              | {td.WATCH, td.UNPROVEN, td.CAUTION, td.STAND_DOWN})
    assert not (ours & others)


def test_no_verdict_reads_as_an_endorsement():
    for v in (MISREPRESENTED, UNSUBSTANTIATED, PARTLY_SUBSTANTIATED):
        assert v not in ("safe", "verified", "trusted", "good", "buy")

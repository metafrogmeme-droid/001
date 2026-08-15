"""A presale is a pile of assertions with no chain behind them yet.

"Audited by a top firm." "Team fully doxxed." "Liquidity locked for two years."
"90% of supply to the community." At presale time most of that cannot be checked
because the thing it describes does not exist yet — and the ones that CAN be
checked are the only place a detective has any purchase at all.

So this module does one job: keep a checked claim and an unchecked one visibly
apart, and never let the second borrow the credibility of the first.

FOUR STATES, NOT TWO

A claim is not pass/fail. Collapsing it to two is how "we did not look" becomes
"we looked and it was fine":

    confirmed     checked against evidence, and it held
    refuted       checked against evidence, and it did not
    unchecked     checkable in principle, nobody checked it
    unverifiable  nothing could check this — "the team is experienced"

`unchecked` and `unverifiable` are deliberately different words. The first is a
gap in OUR work and closing it is a to-do; the second is a property of the claim
itself and no amount of effort will move it. A reader deciding whether to wait
for more diligence needs to know which they are looking at.

REFUTED OUTRANKS EVERYTHING

A project that claims an audit which does not exist has not made an error, it
has lied — and one lie is worth more evidence than any number of confirmations.
So a single refuted claim produces `misrepresented` regardless of how much else
checked out, and no quantity of confirmed claims can lift it. That is the same
rule as a hard flag in `token_safety`, for the same reason.

THERE IS NO "VERIFIED" VERDICT

The ceiling is `partly_substantiated`, and it is reached only when every
CHECKABLE claim was checked and held. Even then the unverifiable ones remain
exactly as unknown as they started, which is why the word says "partly" and
keeps saying it however good the checkable half looks. A presale cannot earn
more than that from the outside.
"""
from __future__ import annotations

from typing import Optional

# Per-claim states.
CONFIRMED = "confirmed"
REFUTED = "refuted"
UNCHECKED = "unchecked"
UNVERIFIABLE = "unverifiable"

# Overall verdicts. A fifth distinct vocabulary — see token_dossier on why the
# scorers deliberately do not share words.
MISREPRESENTED = "misrepresented"      # at least one claim is false
UNSUBSTANTIATED = "unsubstantiated"    # nothing checkable has been confirmed
PARTLY_SUBSTANTIATED = "partly_substantiated"  # every checkable claim held

#: Claim kinds this module knows how to check, and therefore the only ones that
#: may ever be `unchecked` rather than `unverifiable`. Naming them is the point:
#: a claim type absent from here is one nobody has built a check for, and
#: calling that "unchecked" would imply the check exists and was skipped.
CHECKABLE_KINDS = frozenset({
    "audit_report",      # does the cited audit exist, and does it name this bytecode
    "lp_lock",           # is there a lock contract, for how much, until when
    "vesting_contract",  # does the vesting contract exist and match the schedule
    "supply_split",      # do the wallet allocations match the published table
    "contract_address",  # is there a deployed contract at the address they gave
})


def classify(claim: Optional[dict]) -> dict:
    """Resolve one claim into ``{kind, text, status, basis, source}``.

    ``claim`` carries ``kind``, ``text``, and optionally ``holds`` — the result
    of an actual check. ``holds`` is tri-state ON PURPOSE: True confirmed, False
    refuted, and **None means nobody checked**, which must never collapse into
    False. A missing check is not a failed one; treating them alike would let a
    detective report a project as lying because an endpoint timed out.
    """
    c = claim or {}
    kind = c.get("kind")
    text = c.get("text") or ""
    holds = c.get("holds")
    source = c.get("source")

    if kind not in CHECKABLE_KINDS:
        # No check exists for this shape of claim. Not a gap in our diligence —
        # a property of the claim, and saying "unchecked" would promise a check
        # that was never possible.
        return {"kind": kind, "text": text, "status": UNVERIFIABLE,
                "basis": "no check exists for this kind of claim", "source": source}
    if holds is True:
        return {"kind": kind, "text": text, "status": CONFIRMED,
                "basis": c.get("basis") or "checked", "source": source}
    if holds is False:
        return {"kind": kind, "text": text, "status": REFUTED,
                "basis": c.get("basis") or "checked and did not hold",
                "source": source}
    return {"kind": kind, "text": text, "status": UNCHECKED,
            "basis": c.get("basis") or "checkable, not checked", "source": source}


def verify(claims: Optional[list]) -> dict:
    """Assess a presale's claims. Returns::

        {verdict, claims:[...], counts:{...}, refuted:[...], outstanding:[...]}

    ``outstanding`` is the checkable-but-unchecked set — the to-do list, kept
    separate from the unverifiable ones so nobody works the wrong queue.
    """
    resolved = [classify(c) for c in (claims or [])]
    counts = {s: sum(1 for r in resolved if r["status"] == s)
              for s in (CONFIRMED, REFUTED, UNCHECKED, UNVERIFIABLE)}

    if counts[REFUTED]:
        # One lie outranks any amount of corroboration.
        verdict = MISREPRESENTED
    elif counts[UNCHECKED] or not counts[CONFIRMED]:
        # Either something checkable is still outstanding, or nothing checkable
        # was confirmed at all. Both mean the claims are not yet substantiated —
        # and a presale consisting ENTIRELY of unverifiable claims lands here,
        # which is the correct and deliberately unflattering answer.
        verdict = UNSUBSTANTIATED
    else:
        verdict = PARTLY_SUBSTANTIATED

    return {
        "verdict": verdict,
        "claims": resolved,
        "counts": counts,
        "refuted": [r for r in resolved if r["status"] == REFUTED],
        "outstanding": [r for r in resolved if r["status"] == UNCHECKED],
    }


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render (no markup)."""
    if not report or not isinstance(report, dict):
        return "No presale claim report."
    v = report.get("verdict", UNSUBSTANTIATED)
    icon = {MISREPRESENTED: "⛔", UNSUBSTANTIATED: "?",
            PARTLY_SUBSTANTIATED: "○"}.get(v, "·")
    c = report.get("counts") or {}
    lines = [f"{icon} PRESALE CLAIMS: {v.upper().replace('_', ' ')}",
             f"   {c.get(CONFIRMED, 0)} confirmed · {c.get(REFUTED, 0)} refuted · "
             f"{c.get(UNCHECKED, 0)} unchecked · "
             f"{c.get(UNVERIFIABLE, 0)} unverifiable"]

    for r in report.get("refuted", []):
        lines.append(f"   ✗ REFUTED — {r['text'] or r['kind']}: {r['basis']}")
    for r in report.get("outstanding", []):
        lines.append(f"   … unchecked — {r['text'] or r['kind']}")

    # The claims nobody can check are LISTED, not summarised away. A presale
    # whose entire pitch is unverifiable should look like one on the page, and
    # a count alone lets a reader assume the substance was elsewhere.
    unver = [r for r in report.get("claims", []) if r["status"] == UNVERIFIABLE]
    for r in unver:
        lines.append(f"   ? unverifiable — {r['text'] or r['kind']}")

    if v == PARTLY_SUBSTANTIATED:
        lines.append("   every checkable claim held; the unverifiable ones are "
                     "exactly as unknown as before")
    return "\n".join(lines)

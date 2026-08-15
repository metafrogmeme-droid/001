"""Deployer provenance — who shipped this contract, and how their last ones ended.

The detective's core question, and the one a scam cannot cheaply answer. A token
can fake its website, its audit badge, its holder count and its volume. It cannot
retroactively give its deployer a two-year history of contracts that still trade.

Same shape and same discipline as ``token_safety``: pure, deterministic, no
network and no clock — the caller fetches the facts and passes them in. Detection
only; there is no positive/buy output, and the best verdict available is
``clean``, which means "nothing found against them", never "endorsed".

THE ARITHMETIC TRAP THIS MODULE IS BUILT AROUND

The obvious model is ``survivors = deployments - rugs``. That is
``losses = len(all) - wins`` from CLAUDE.md, and it is wrong for exactly the
reason recorded there: a deployment whose fate could not be determined is not a
survivor. An indexer that returns four of a deployer's nine contracts must not
produce "five survivors".

So the three counts are read INDEPENDENTLY and their sum is checked against the
total. Whatever is left over is ``unresolved`` and is reported, never absorbed
into either column.

AND THE ONE THAT MATTERS MORE: ZERO IS THREE DIFFERENT ANSWERS

``prior_rugs == 0`` can mean

  * this deployer has shipped nine contracts and none of them rugged — evidence;
  * this deployer has shipped nothing before — a first-timer, which is the
    normal state of every honest new project AND of every scammer using a fresh
    wallet, so it is not evidence of anything;
  * we could not read their history at all — not a measurement.

Only the first is a fact about the deployer. The other two are facts about our
visibility, and a scorer that treats them alike hands a brand-new burner wallet
the same clean sheet as a two-year veteran. That is the single most dangerous
output this module could produce, so ``no_prior_deployments`` is a distinct
state that reaches the verdict as ``unproven`` — never as ``clean``.
"""
from __future__ import annotations

from typing import Optional

from bot.core.token_safety import (FLAG, HARD, OK, UNKNOWN, _b, _num, coverage)

# Verdicts. Deliberately NOT token_safety's {safe, caution, danger}: this scores
# a person's track record, not a contract's mechanics, and reusing the words
# would invite a caller to compare or merge two different judgements.
CLEAN = "clean"          # history read, nothing against them
UNPROVEN = "unproven"    # no history to read — the first-timer case
SUSPECT = "suspect"      # soft flags accumulated
KNOWN_BAD = "known_bad"  # a disqualifying reading

_SUSPECT_SCORE = 1.5
_KNOWN_BAD_SCORE = 3.5
#: Below this fraction of readable checks we cannot certify anything.
_MIN_EVIDENCE_FRAC = 0.5


def resolve_outcomes(facts: Optional[dict]) -> dict:
    """Split a deployer's prior contracts into outcomes that add up honestly::

        {total, rugged, alive, unresolved}

    ``unresolved`` is what the caller could not determine, and it is a first-
    class number rather than a rounding error. Any of these may be None when the
    input is missing — None is not zero.
    """
    f = facts or {}
    total = _num(f.get("prior_deployments"))
    rugged = _num(f.get("prior_rugged"))
    alive = _num(f.get("prior_alive"))
    # `dead` is a THIRD outcome and not a synonym for rugged. A market that is
    # gone is a fact a price feed can establish; that somebody took the money
    # is not, and `prior_rugged` hard-fails a deployer on the strength of the
    # word "confirmed". A project that simply died would otherwise be scored as
    # theft, which is the most damaging output this module can produce.
    dead = _num(f.get("prior_dead"))
    if total is None:
        return {"total": None, "rugged": rugged, "alive": alive, "dead": dead,
                "unresolved": None}
    known = sum(v for v in (rugged, alive, dead) if v is not None)
    # Never negative: a caller reporting more outcomes than deployments has an
    # inconsistent source, and silently clamping to 0 would hide that.
    unresolved = total - known
    return {"total": total, "rugged": rugged, "alive": alive, "dead": dead,
            "unresolved": unresolved}


#: A record can only be called clean if most of it was actually read. Same
#: number as _MIN_EVIDENCE_FRAC and the same argument: a majority of the thing
#: you are certifying has to be in evidence.
_MIN_RESOLVED_FRAC = 0.5


def _outcomes_resolved(outcomes: dict) -> bool:
    """True when enough of a deployer's prior contracts have a known fate to
    say anything about their record.

    SOMEBODY MUST HAVE COUNTED THE BAD OUTCOMES.

    This used to read `if outcomes["rugged"] is None: return False`, on the
    reasoning that a source returning deployments but no rug count has said
    nothing about the column that matters — and the arithmetic would otherwise
    treat its absence as a zero. That reasoning is exactly right and is kept;
    what changed is that `rugged` is no longer the only way to count a bad
    outcome. A per-contract fate check that finds a dead market has looked for
    trouble and reported what it found.

    So the test is: did anyone look? `rugged` and `dead` both None means no, and
    a record of nine survivors nobody checked for failures is the trap this
    module's own first run produced — 9 deployments, 0 recorded rugs, 4
    confirmed alive, scored CLEAN with five fates unread.

    The ratio still governs the rest: a fate must be DETERMINED, per contract,
    for at least half the record. A rug cannot hide in the `alive` bucket,
    because a rug's market is gone and a gone market classifies as dead.
    """
    total = outcomes.get("total")
    if not total or total <= 0:
        return False
    if outcomes.get("rugged") is None and outcomes.get("dead") is None:
        return False
    unresolved = outcomes.get("unresolved")
    if unresolved is None:
        return False
    return ((total - unresolved) / total) >= _MIN_RESOLVED_FRAC


def assess_deployer(facts: Optional[dict]) -> dict:
    """Score a deployer's provenance. Returns::

        {verdict, score, checks:[{name,status,detail}], flags, outcomes,
         coverage, evidence, unknowns}

    ``verdict`` ∈ {clean, unproven, suspect, known_bad} — never a buy signal.
    """
    f = facts or {}
    checks: list[dict] = []
    score = 0.0
    hard = False

    def boolean(name: str, danger_when: bool, weight: float, danger_hard: bool,
                on_bad: str) -> None:
        nonlocal score, hard
        v = _b(f.get(name))
        if v is None:
            checks.append({"name": name, "status": UNKNOWN, "detail": "not provided"})
            return
        if v is not danger_when:
            checks.append({"name": name, "status": OK, "detail": "ok"})
            return
        if danger_hard:
            hard = True
            checks.append({"name": name, "status": HARD, "detail": on_bad})
        else:
            score += weight
            checks.append({"name": name, "status": FLAG, "detail": on_bad})

    def numeric(name: str, direction: str, soft: float, hard_th: Optional[float],
                weight: float, on_soft: str, on_hard: str,
                value: Optional[float] = None) -> None:
        nonlocal score, hard
        v = _num(f.get(name)) if value is None else value
        if v is None:
            checks.append({"name": name, "status": UNKNOWN, "detail": "not provided"})
            return
        high = direction == "high"
        hard_hit = (hard_th is not None) and ((v >= hard_th) if high else (v <= hard_th))
        soft_hit = (v >= soft) if high else (v <= soft)
        if hard_hit:
            hard = True
            checks.append({"name": name, "status": HARD, "detail": on_hard})
        elif soft_hit:
            score += weight
            checks.append({"name": name, "status": FLAG, "detail": on_soft})
        else:
            checks.append({"name": name, "status": OK, "detail": "ok"})

    outcomes = resolve_outcomes(f)

    # ── the record itself ──
    # A single confirmed rug is disqualifying. Not a weight: somebody who has
    # taken money once is not offset by shipping four contracts that happened
    # to survive, and a scoring model that averages says otherwise.
    numeric("prior_rugged", "high", 1.0, 1.0, 0,
            "deployer has a prior rug", "deployer has a prior rug",
            value=outcomes["rugged"])

    # The dead RATIO, and softly. Deliberately not a second rug check:
    #
    #  * it is a ratio, not a count, because one dead token out of one and one
    #    out of thirty are different facts and the count alone prints them the
    #    same;
    #  * it has no hard threshold at all. A market that ended is not a market
    #    that was stolen — projects die honestly, and the one output this
    #    module must never manufacture is `known_bad` for a deployer whose
    #    crime was shipping something nobody kept buying.
    dead_ratio = None
    if outcomes["total"] and outcomes["dead"] is not None:
        dead_ratio = outcomes["dead"] / outcomes["total"]
    numeric("prior_dead_ratio", "high", 0.5, None, 1.5,
            "most of this deployer's prior tokens have no market left", "",
            value=dead_ratio)

    boolean("funded_by_mixer", True, 2.0, False,
            "deployer wallet funded through a mixer")
    boolean("reused_rug_bytecode", True, 0, True,
            "contract bytecode matches a known rug template")
    boolean("contract_verified", False, 1.0, False,
            "contract source is not published")
    numeric("wallet_age_days", "low", 7.0, None, 1.5,
            "deployer wallet is less than a week old", "")
    numeric("deployer_supply_pct", "high", 0.2, 0.5, 1.5,
            "deployer holds a large share of supply",
            "deployer holds ≥50% of supply")
    numeric("concurrent_launches_24h", "high", 3.0, None, 1.5,
            "deployer launched several tokens in 24h", "")

    evidence = sum(1 for c in checks if c["status"] in (OK, FLAG, HARD))
    unknowns = sum(1 for c in checks if c["status"] == UNKNOWN)
    cov = coverage(checks)

    # ── verdict ──
    #
    # Order matters, and `unproven` sits ABOVE the evidence test on purpose.
    #
    # A deployer with no prior deployments can produce a fully-readable check
    # set — wallet age, funding, supply share all answer fine — and every
    # answer can be benign. That is a first-timer, which describes every honest
    # new project and every scammer with a fresh wallet equally. Letting it fall
    # through to `clean` would print the same word for "nine contracts, none
    # rugged" and "no track record whatsoever", which is the distinction this
    # module exists to draw.
    if hard or score >= _KNOWN_BAD_SCORE:
        verdict = KNOWN_BAD
    elif score >= _SUSPECT_SCORE:
        verdict = SUSPECT
    elif outcomes["total"] is None:
        verdict = UNPROVEN          # history unreadable
    elif not _outcomes_resolved(outcomes):
        # Covers BOTH ways a record fails to support a clean verdict: a
        # first-timer (`total <= 0` — nothing to read) and a partially-read
        # history. There was a separate `total <= 0` branch above this; a
        # mutation deleting it passed every test, because `_outcomes_resolved`
        # already returns False for it. Removed rather than kept as commentary:
        # a branch that cannot change the outcome reads as load-bearing to the
        # next person, and the distinction that MATTERS — "no history" versus
        # "unreadable history" — is a difference in what the user is told, which
        # `human_readable` draws from `outcomes` and is tested there.
        #
        # THE TRAP, caught in this module's own first run. A deployer with 9
        # prior contracts, 0 recorded rugs and only 4 confirmed alive scored
        # CLEAN — while five of their contracts had fates nobody had read, any
        # of which could be a rug. `prior_rugged == 0` was standing in for "no
        # rugs" when it meant "we did not look at five of them", which is the
        # rule this file's own docstring is written around.
        verdict = UNPROVEN
    elif cov["total"] and (evidence / cov["total"]) < _MIN_EVIDENCE_FRAC:
        verdict = UNPROVEN          # too little readable to certify
    else:
        verdict = CLEAN

    return {
        "verdict": verdict,
        "score": round(score, 3),
        "checks": checks,
        "flags": [c["detail"] for c in checks if c["status"] in (FLAG, HARD)],
        "outcomes": outcomes,
        "coverage": cov,
        "evidence": evidence,
        "unknowns": unknowns,
    }


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render (no markup). The basis travels with the verdict, for
    the same reason it does in token_safety: a reader who stops at the headline
    is most readers."""
    if not report or not isinstance(report, dict):
        return "No deployer report."
    v = report.get("verdict", UNPROVEN)
    icon = {CLEAN: "✓", UNPROVEN: "?", SUSPECT: "⚠", KNOWN_BAD: "⛔"}.get(v, "·")
    cov = report.get("coverage") or {}
    lines = [f"{icon} DEPLOYER: {v.upper().replace('_', ' ')} "
             f"[{cov.get('basis')} basis — {cov.get('readable')}/{cov.get('total')} "
             f"checks readable]"]

    o = report.get("outcomes") or {}
    total = o.get("total")
    if total is None:
        lines.append("   prior contracts: could not be read")
    elif total <= 0:
        # Said in words, not as "0 rugs". A first-timer's clean sheet is the
        # absence of a record, and printing it as a score reads as a good one.
        lines.append("   no prior deployments — nothing to judge them on")
    else:
        bits = [f"{int(total)} prior"]
        if o.get("rugged") is not None:
            bits.append(f"{int(o['rugged'])} rugged")
        if o.get("alive") is not None:
            bits.append(f"{int(o['alive'])} still trading")
        if o.get("unresolved"):
            bits.append(f"{int(o['unresolved'])} unresolved")
        lines.append("   " + ", ".join(bits))

    for fl in report.get("flags", []):
        lines.append(f"   – {fl}")
    return "\n".join(lines)

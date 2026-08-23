"""Tier weight — `√(staked) × lock_multiplier × standing`, as pure functions.

The design is `docs/TIER_MODEL.md`. This module is the arithmetic half of it,
kept separate from :mod:`bot.token.tier_gate` so it can be reasoned about and
tested without an RPC, a wallet, or a mint.

WHAT THIS DOES NOT DO YET, AND WHY THAT IS SAID HERE RATHER THAN DISCOVERED

`tier_gate` calls this only when ``RCLAW_TIER_WEIGHT_ENABLED`` is set, which is
**off by default**. TIER_MODEL.md §11 asks for exactly that: compute the number,
publish it, and let it decide nothing until it has survived scrutiny. A weight
that quietly started re-ranking live users the moment it was merged would be the
opposite of the posture the document argues for.

THE NO-OP THAT THE PLAN CALLED PHASE ONE

TIER_MODEL.md originally said to ship `√` weighting against the existing
absolute bands first, "zero migration", as a step that ships value alone. It
ships none: `√` is monotonic, so ``√(staked) ≥ √(threshold)`` is the same
predicate as ``staked ≥ threshold`` at every holding. That is a **feature** here
— it is what makes turning the flag on safe — but it is not a fix for
plutocracy, which is a statement about shares of a total and needs the relative
bands of Phase D. ``tests/test_tier_weight.py`` pins the identity so nobody
ships it twice believing otherwise.

CONCAVITY CUTS BOTH WAYS, INCLUDING INSIDE ONE WALLET

`√` is concave, so it is *sub*-additive: ``√(a+b) < √a + √b``. Across accounts
that is the sybil defence TIER_MODEL.md §10 describes — splitting lowers each
account's own weight and nothing ever adds weights together. Within one wallet
it is a trap in the other direction: a wallet with three stake records must be
scored as ``√(total)``, never as ``√(r1) + √(r2) + √(r3)``, or opening extra
records against the same program would manufacture weight from nothing. The
staking program creates one PDA per (owner, mint), so this is not reachable
today; it becomes reachable the moment a second mint or a record-per-deposit
layout arrives, and by then the summing would look like ordinary code.
:func:`stake_weight_inputs` exists so the total is formed before the root is
taken, and a test drives the three-record case.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

#: Lock duration, in months, at which the multiplier reaches its ceiling.
#: Must equal ``rclaw_staking::MAX_LOCK_SECONDS`` (24 * 30 days) — pinned by
#: ``tests/test_tier_weight.py``, because a model ceiling above what the program
#: will accept advertises a premium nobody can reach.
LOCK_CEILING_MONTHS = 24.0
#: Slope of the lock multiplier: 1.0 at no lock, 1.0 + LOCK_SLOPE at the ceiling.
LOCK_SLOPE = 1.5
#: Hard ceiling, applied after the slope so a longer lock cannot exceed it.
LOCK_MAX_MULTIPLIER = 2.5
#: Seconds in the month this model counts in (30 days), matching the staking
#: program's ``LOCKUP_SECONDS = 30 * 24 * 60 * 60``.
SECONDS_PER_MONTH = 30 * 24 * 60 * 60

#: Standing floor, ceiling, and the value a caller means by "no standing input".
STANDING_MIN = 0.2
STANDING_MAX = 2.0
#: TIER_MODEL.md §9 proposes 1.0 — the neutral midpoint — for an account with no
#: history, "so newcomers are neither punished nor advantaged". That is a POLICY
#: default for a *known* absence, not a stand-in for a standing read that failed.
#: A caller that could not reach the scorer must not pass this: it would report
#: a measurement nobody took. Pass the failure up instead.
STANDING_NEUTRAL = 1.0


def _finite(value) -> Optional[float]:
    """``value`` as a finite float, or ``None`` if it is not a real number.

    ``is None`` rather than falsiness throughout this module: a stake of exactly
    ``0.0`` is a real, measured, empty position and must be scored as one, not
    treated as an unreadable input.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def lock_multiplier(lock_seconds_remaining) -> float:
    """Commitment premium for a position locked for ``lock_seconds_remaining``.

    ``1.0`` at no lock, rising linearly to :data:`LOCK_MAX_MULTIPLIER` at
    :data:`LOCK_CEILING_MONTHS`. An expired lock (negative remainder), a missing
    one, or an unreadable one all yield ``1.0`` — the *unpremiumed* value, which
    is the safe direction: an unreadable lock must never buy the premium.

    :data:`LOCK_CEILING_MONTHS` must equal the staking program's
    ``MAX_LOCK_SECONDS``. It did not until ``stake_for`` existed: every record
    carried the fixed 30-day ``LOCKUP_SECONDS``, so the largest reachable
    multiplier was ``1 + 1.5/24 ≈ 1.06`` and the 2.5× in the published table was
    a number no depositor could obtain. ``stake_for`` takes a caller-chosen
    duration bounded by ``[LOCKUP_SECONDS, MAX_LOCK_SECONDS]``, and a plain
    ``stake`` still writes the 30-day default, so the bottom of this range is
    what an ordinary position gets.
    """
    secs = _finite(lock_seconds_remaining)
    if secs is None or secs <= 0:
        return 1.0
    months = secs / SECONDS_PER_MONTH
    return min(1.0 + LOCK_SLOPE * (months / LOCK_CEILING_MONTHS), LOCK_MAX_MULTIPLIER)


def clamp_standing(standing) -> float:
    """``standing`` held inside [:data:`STANDING_MIN`, :data:`STANDING_MAX`].

    ``None`` means "this account has no standing history", and resolves to
    :data:`STANDING_NEUTRAL` per TIER_MODEL.md §9. It does **not** mean "the
    standing scorer was unreachable" — see the note on :data:`STANDING_NEUTRAL`.
    """
    value = _finite(standing)
    if value is None:
        return STANDING_NEUTRAL
    return max(STANDING_MIN, min(STANDING_MAX, value))


def stake_weight_inputs(records: Iterable[tuple]) -> Optional[tuple[float, float]]:
    """Fold ``(amount, lock_seconds_remaining)`` records into ``(total, lock)``.

    Returns the summed amount and the **amount-weighted mean** remaining lock, so
    a wallet holding 1,000 tokens for a day and 1,000,000 for a year is not
    credited with the average of *durations* as though the two mattered equally.

    Summing before the root is the point, not an implementation detail: `√` is
    sub-additive, so rooting each record and adding would pay a wallet for
    holding its stake in more pieces. See this module's docstring.

    ``None`` if there is nothing to fold or a record is unreadable — a partial
    total is a wrong total, and silently dropping the record we could not parse
    would under-report someone's stake and deny them a tier they hold.
    """
    total = 0.0
    weighted_lock = 0.0
    seen = False
    for record in records:
        try:
            amount, lock_secs = record
        except (TypeError, ValueError):
            return None
        amt = _finite(amount)
        secs = _finite(lock_secs)
        if amt is None or secs is None or amt < 0:
            return None
        seen = True
        total += amt
        weighted_lock += amt * max(0.0, secs)
    if not seen:
        return None
    mean_lock = (weighted_lock / total) if total > 0 else 0.0
    return total, mean_lock


def tier_weight(staked, *, lock_seconds_remaining=0.0, standing=None) -> Optional[float]:
    """``√(staked) × lock_multiplier × standing``, or ``None`` if unreadable.

    ``None`` in means ``None`` out: a stake we could not read is not a stake of
    zero, and the caller has to keep those apart — a zero weight is a real
    verdict about an empty position, and returning it for an RPC failure would
    demote a holder on evidence nobody has.
    """
    amount = _finite(staked)
    if amount is None or amount < 0:
        return None
    return (math.sqrt(amount)
            * lock_multiplier(lock_seconds_remaining)
            * clamp_standing(standing))


def weight_of_threshold(tokens) -> Optional[float]:
    """A token threshold expressed in weight units, for absolute bands.

    ``√(tokens)`` at neutral lock and standing — which is what makes turning the
    weight path on a no-op until a lock or a standing score actually exists.
    """
    amount = _finite(tokens)
    if amount is None or amount < 0:
        return None
    return math.sqrt(amount)


def tier_for_weight(weight, pro_min_weight, elite_min_weight) -> Optional[str]:
    """Map a weight to ``basic`` / ``pro`` / ``elite`` against absolute bands.

    ``None`` for an unreadable weight or unreadable bands, so a caller cannot
    accidentally read "we could not compute this" as ``basic``. Basic is a real
    tier that a real holder can be in, and it is also what an error looks like
    if this returned a string for everything.
    """
    w = _finite(weight)
    pro = _finite(pro_min_weight)
    elite = _finite(elite_min_weight)
    if w is None or pro is None or elite is None:
        return None
    if w >= elite:
        return "elite"
    if w >= pro:
        return "pro"
    return "basic"

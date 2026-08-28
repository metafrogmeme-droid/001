"""A user's DECLARED risk appetite, turned into a position-size multiplier.

The roadmap row is "per-user agent memory — remembers risk appetite, watchlist,
past decisions; **sizes to your calibrated confidence**". The remembering half
shipped in `user_profile_store` and `user_memory_store`; the sizing half did
nothing at all. A user could pick "conservative", see the agent acknowledge it
in chat, and get positions identical to an "aggressive" user's — a preference
that changes the prose and not the money, which is the shape of promise this
repository exists to stop making.

TIGHTEN-ONLY, AND THAT IS NOT A LIMITATION
------------------------------------------
`aggressive` returns 1.0. A self-declared appetite must never RAISE risk: the
caps, breakers and the 23-check gate are what bound losses, and a preference
field on a profile — writable from a web form — is not evidence that a larger
position is safe. It can ask for less. It cannot ask for more. Every other
multiplier in `_evaluate_locked` is written to the same rule and the comments
there say so ("Only reductions (mult < 1.0) applied pre-cap; never increases").

UNREADABLE IS 1.0, AND THAT IS A DELIBERATE CHOICE, NOT A DEFAULT
-----------------------------------------------------------------
A profile that cannot be read yields no adjustment. That deserves stating
because this repo's usual rule points the other way: absent is not a
measurement, so fail toward safety. Here "safety" would mean shrinking a
position on the strength of a file read that failed, which silently changes
how much money moves because of a disk error — a size nobody chose, from
evidence nobody has. The honest reading of an unreadable preference is that we
do not know it, and not knowing it is the state every user was in before this
existed. `reason` says which of the two it was, so a shrunk position and an
unadjusted one are never confused in the audit record.
"""

from __future__ import annotations

from typing import Tuple

#: risk_pref -> multiplier. Only reductions; see the module docstring.
MULTIPLIERS = {
    "conservative": 0.70,
    "balanced": 1.0,
    "aggressive": 1.0,
}


def size_multiplier(risk_pref) -> Tuple[float, str]:
    """``(multiplier, reason)`` for a declared preference.

    Never raises and never returns above 1.0. The reason is a short, fixed
    phrase — it reaches an audit record and an operator-facing check line, so
    it carries no user text and no driver message.
    """
    pref = str(risk_pref or "").strip().lower()
    if pref in MULTIPLIERS:
        mult = MULTIPLIERS[pref]
        if mult >= 1.0:
            return 1.0, f"declared {pref}: no reduction"
        return mult, f"declared {pref}: size x{mult:.2f}"
    if pref:
        # A word we do not recognise is not a preference we can act on, and
        # guessing which of the three it resembles would be a size chosen by a
        # string match. `user_profile_store.normalize` should have dropped it
        # already; if one arrives anyway, it changes nothing and says so.
        return 1.0, "unrecognised preference: no reduction"
    return 1.0, "no preference on file: no reduction"


def multiplier_for_user(user_id, store=None) -> Tuple[float, str]:
    """`size_multiplier` for a user id, reading the profile store.

    A store fault yields ``(1.0, ...)`` with a reason naming the fault, never
    an exception and never a silent reduction — sizing must not change because
    a file could not be opened.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return 1.0, "no user: no reduction"
    try:
        if store is None:
            from bot.core import user_profile_store as store  # type: ignore[no-redef]
        profile = store.get(uid)
    except Exception:
        return 1.0, "profile unreadable: no reduction"
    if not profile:
        return 1.0, "no preference on file: no reduction"
    return size_multiplier(profile.get("risk_pref"))

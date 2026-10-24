"""Depth-weighted blend and the session share rules around it.

Book weights arrive already haircut by slippage, breadth and concentration.
The external coefficient is then set so that a confirmed regular session
keeps at least the configured external share. That floor is applied to the
*full* external coefficient and only afterwards multiplied by the handoff
decay, so an unconfirmed print does not inherit the floor.

The closed-session book cap is the other direction, and it is not applied
to a candidate: restoring external weight there would put the unconfirmed
print back into the blend.
"""
from __future__ import annotations


def full_external_coefficient(book_sum: float, floor: float | None) -> float:
    """External coefficient that meets ``floor`` against ``book_sum``.

    ``floor`` is a share in (0, 1). ``None`` leaves the coefficient at 1,
    and the closed-session cap (applied later) is what bounds the books.
    """
    ext = 1.0
    if floor is None or book_sum <= 0:
        return ext
    if floor <= 0 or floor >= 1:
        return ext
    needed = (floor / (1.0 - floor)) * book_sum
    if ext < needed:
        return needed
    return ext


def session_floor(session: str, regular_floor: float) -> float | None:
    """Share floor once a print is confirmed. ``None`` means no floor.

    Regular is the mechanism's own floor. Pre and post use a lower floor
    so a confirmed print is not ignored in favour of a deep book. Closed
    and overnight have no floor; closed is capped from the book side.
    """
    if session == "regular":
        return regular_floor
    if session in ("pre", "post"):
        return 0.7
    return None


def shares(*, external_coeff: float, book_weights: list[float],
           cap: float | None) -> tuple[float, list[float]] | None:
    """``(external share, per-book shares)`` or ``None`` when nothing weighs.

    ``cap`` limits total book share. It is the caller's job to pass ``None``
    for a candidate window.
    """
    if external_coeff < 0:
        return None
    book_sum = sum(w for w in book_weights if w > 0)
    if external_coeff == 0 and book_sum <= 0:
        return None
    if cap is not None and external_coeff > 0 and book_sum > 0:
        total = external_coeff + book_sum
        if book_sum / total > cap:
            per = [(w / book_sum) * cap if w > 0 else 0.0 for w in book_weights]
            return (1.0 - cap, per)
    total = external_coeff + book_sum
    if total <= 0:
        return None
    return (external_coeff / total, [w / total for w in book_weights])


def fair_value(external: float | None, ext_share: float,
               mids: list[float | None], book_shares: list[float]) -> float | None:
    """The blend. A missing mid is left out. Shares of missing books are
    not given to the others, and they are not treated as a zero price."""
    acc = 0.0
    used = 0.0
    if external is not None and ext_share > 0:
        acc += ext_share * external
        used += ext_share
    for mid, share in zip(mids, book_shares):
        if mid is None or share <= 0:
            continue
        acc += share * mid
        used += share
    if used <= 0:
        return None
    return acc / used

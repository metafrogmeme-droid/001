"""One person's totals across every venue they trade.

Phase 3 of multi-venue (``docs/MULTI_VENUE_RISK_SPLIT.md``), and the decision
it implements, made deliberately rather than inherited from whichever was
easier to code:

    **Caps and drawdown per PERSON. Breakers per VENUE.**

A cap is a statement about how much of your money is at risk, and money is not
per-venue. A breaker is a statement about a book behaving badly, and a book is.
So the circuit breaker and the loss streak stay in the per-(user, venue) engine
Phase 2 built, and max-open-positions, daily loss, drawdown and margin are
counted here, once, across everything.

WHAT GOES WRONG IF THIS IS SKIPPED. Nothing, visibly. Two venues each holding
their own "max 5 open positions" is ten positions against one person's money,
and nobody chose ten — the cap still passes on both engines, every test stays
green, and the only symptom is twice the exposure the operator asked for. The
scope names this as the phase where a cap gets silently loosened, and this is
the mechanism.

THE HARD PART IS NOT THE ADDITION — IT IS THE MISSING ADDEND.

A venue whose balance cannot be read is ``unknown``. Not zero. Zero equity
reads as "no margin here", and a *missing* venue in a sum reads as a smaller
total, which is a LOOSER cap. That is the repo's central rule ("unreadable is
never zero, and absent is never a measurement") arriving where it costs money
rather than credibility: the honest sum over an incomplete set is not a sum, it
is a FLOOR, and this module never lets the two be confused.

A floor is still worth having, because it is enough to answer one of the two
questions:

  * "is this person already over the cap?" — a floor that exceeds the cap
    proves it, whatever the unreadable venue holds. REJECT is safe.
  * "is this person under the cap?" — a floor below the cap proves nothing.
    The unreadable venue could hold anything. ACCEPT is not safe.

So ``cap_verdict`` answers ALLOW only over a complete reading, and its refusal
names the venue it could not read rather than reporting a number nobody
measured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class VenueReading:
    """What one venue's book says, or ``None`` where it could not be read.

    ``None`` is load-bearing on every field. A venue that answered "0 open
    positions" and a venue that did not answer are different facts, and the
    only place that difference can be preserved is at the point of reading.
    """
    venue: str
    open_positions: Optional[int] = None
    equity_usd: Optional[float] = None
    daily_pnl_usd: Optional[float] = None
    #: Why it could not be read, for the operator-facing reason string. Never
    #: a driver message — see the repo rule about /readyz reason codes.
    unreadable_reason: str = ""


@dataclass(frozen=True)
class PersonTotals:
    """The person-level view, with its own completeness attached.

    ``complete`` is not a nicety. Every number below is a FLOOR when it is
    False, and a floor compared against a cap can only ever justify a refusal.
    """
    open_positions: int = 0
    equity_usd: Optional[float] = None
    daily_pnl_usd: Optional[float] = None
    venues_read: int = 0
    unreadable: tuple = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        return not self.unreadable


def aggregate(readings) -> PersonTotals:
    """Sum a person's venues into one view, keeping track of what was missing.

    Each field is summed over the venues that ANSWERED for that field, and a
    venue that failed to answer ANY of them lands in ``unreadable``. The
    per-field granularity matters: a venue can report its open positions while
    its equity read times out, and discarding the position count because the
    balance was unreadable would throw away a measurement that was taken.

    ``equity_usd`` and ``daily_pnl_usd`` are ``None`` — not 0.0 — when nothing
    could be read at all. A total of zero dollars over zero venues is the
    "absent is never a measurement" shape, and downstream it would read as a
    real, measured, empty account.
    """
    positions = 0
    equity: Optional[float] = None
    daily: Optional[float] = None
    read = 0
    missing: list = []

    for r in readings or []:
        venue = str(getattr(r, "venue", "") or "?")
        got_any = False
        if getattr(r, "open_positions", None) is not None:
            positions += int(r.open_positions)
            got_any = True
        if getattr(r, "equity_usd", None) is not None:
            equity = (equity or 0.0) + float(r.equity_usd)
            got_any = True
        if getattr(r, "daily_pnl_usd", None) is not None:
            daily = (daily or 0.0) + float(r.daily_pnl_usd)
            got_any = True
        if got_any:
            read += 1
        # A venue that answered NOTHING is unreadable. A venue that answered
        # some fields is still incomplete for the ones it missed — recorded
        # the same way, because a partial answer cannot complete a total.
        if (getattr(r, "open_positions", None) is None
                or getattr(r, "equity_usd", None) is None
                or getattr(r, "daily_pnl_usd", None) is None):
            missing.append(venue)

    return PersonTotals(
        open_positions=positions,
        equity_usd=equity,
        daily_pnl_usd=daily,
        venues_read=read,
        unreadable=tuple(missing),
    )


def person_daily_loss_pct(totals: PersonTotals) -> Optional[float]:
    """Today's loss as a percentage of the person's TOTAL equity, or ``None``.

    BOTH HALVES OR NEITHER. A person-level numerator over a single venue's
    equity overstates the loss; a single venue's PnL over person-level equity
    UNDERSTATES it, and that is the direction that spends money. There is no
    correct way to mix them, so this returns ``None`` unless it has both.

    ``None`` when the reading is incomplete, because — and this is where a
    daily loss differs from a position count — **an incomplete signed quantity
    has no bound in either direction.** A position count only rises as venues
    are added, so a partial count is a floor and a floor above the cap still
    proves a breach. A P&L can go either way: the venue that did not answer
    might have made money, which would make the real loss smaller, or lost
    money, which would make it larger. A partial daily loss is not a floor, not
    a ceiling, and not usable. The caller must refuse rather than guess, and
    ``cap_verdict``'s floor language would be a lie here.
    """
    if not totals.complete:
        return None
    equity = totals.equity_usd
    daily = totals.daily_pnl_usd
    if equity is None or daily is None or equity <= 0:
        return None
    return abs(daily / equity * 100.0)


def cap_verdict(measured: Optional[float], cap: Optional[float],
                totals: PersonTotals, label: str) -> tuple:
    """``(allowed: bool, reason: str)`` for one person-level cap.

    Three outcomes, not two, because "could not read it" is not one of the
    other two — the same correction ``/risk`` needed when an absent drawdown
    reading scored ``0.0 < limit`` and printed HEALTHY.

      * over the cap            → REFUSE, and the floor proves it
      * under it, complete      → ALLOW
      * under it, INCOMPLETE    → REFUSE, naming the venue that went unread

    That last line is the whole point. Allowing on a floor is how a cap gets
    loosened by a timeout: the venue that did not answer is exactly the one
    that might carry the position putting this person over.
    """
    if cap is None:
        return True, f"{label}: no cap configured"
    if measured is None:
        return False, (f"{label}: not measured — nothing could be read across "
                       f"{len(totals.unreadable) or 'any'} venue(s)")
    if measured >= cap:
        # True whatever the unreadable venues hold: a floor over the cap is
        # over the cap. Say so plainly rather than hedging on completeness.
        return False, f"{label}: {measured:g} >= {cap:g}"
    if not totals.complete:
        return False, (f"{label}: {measured:g} < {cap:g} but this is a FLOOR — "
                       f"could not read {', '.join(totals.unreadable)}")
    return True, f"{label}: {measured:g} < {cap:g}"

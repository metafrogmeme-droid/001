"""Lending marks while a print is still a candidate.

R is the last confirmed price. P_c is the new candidate print.

Collateral (the equity was posted):

* Unconfirmed up-move: borrow-power and liquidation both stay at R.
* Unconfirmed down-move: borrow-power is P_c, and liquidation is
  ``max(P_c, FV_lo)``, until the print is confirmed or forced through
  after the accept window.
* After a forced up-move, borrow power ramps from R up to the forced
  fair value over ``ramp_s``. Liquidation is that fair value for the
  whole ramp, so borrow power never sits above it.

Borrowed asset (the equity is the debt), the mirror:

* Borrow-check is ``max(R, P_c)``.
* Liquidation is ``min(P_c, FV_hi)`` on an up-move, otherwise R.

A confirmed print uses the fair value for both prices on that side.
A missing input is omitted. It is not filled with zero, and it is not
filled with the new print alone when the move cannot be classified.

The invariant is borrow-power <= liquidation, so a loan sized on the
borrow-power price is not instantly liquidatable. On the debt side the
mirror is liquidation <= borrow-check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CollateralMarks:
    borrow_power: float | None
    liquidation: float | None


@dataclass(frozen=True)
class BorrowedMarks:
    borrow_check: float | None
    liquidation: float | None


def _pos(price: float | None) -> bool:
    return price is not None and isinstance(price, (int, float)) and math.isfinite(price) and price > 0


def collateral_marks(*, last_confirmed: float | None, new_print: float | None,
                     band_lo: float | None, confirmed: bool, fv: float | None,
                     forced: bool = False, ramp_elapsed_s: float | None = None,
                     ramp_s: float = 900.0) -> CollateralMarks:
    """Collateral borrow-power and liquidation for one print."""
    if confirmed:
        if not _pos(fv):
            return CollateralMarks(None, None)
        return CollateralMarks(fv, fv)
    if not _pos(last_confirmed) or not _pos(new_print):
        return CollateralMarks(None, None)
    # The comparisons above reject None. The checker does not keep that.
    assert last_confirmed is not None and new_print is not None
    if new_print > last_confirmed:
        return _forced_up(last_confirmed, fv, forced, ramp_elapsed_s, ramp_s)
    if new_print < last_confirmed:
        if forced:
            if not _pos(fv):
                return CollateralMarks(None, None)
            return CollateralMarks(fv, fv)
        if band_lo is None or not math.isfinite(band_lo):
            return CollateralMarks(None, None)
        liquidation = band_lo if band_lo > new_print else new_print
        return CollateralMarks(new_print, liquidation)
    return CollateralMarks(last_confirmed, last_confirmed)


def _forced_up(last: float, fv: float | None, forced: bool,
               ramp_elapsed_s: float | None, ramp_s: float) -> CollateralMarks:
    if not forced:
        return CollateralMarks(last, last)
    if not _pos(fv) or ramp_s <= 0:
        return CollateralMarks(last, last)
    assert fv is not None
    if fv <= last:
        return CollateralMarks(last, last)
    elapsed = 0.0 if ramp_elapsed_s is None else ramp_elapsed_s
    if elapsed < 0:
        elapsed = 0.0
    progress = elapsed / ramp_s
    if progress > 1.0:
        progress = 1.0
    borrow = last + (fv - last) * progress
    return CollateralMarks(borrow, fv)


def borrowed_asset_marks(*, last_confirmed: float | None, new_print: float | None,
                         band_hi: float | None, confirmed: bool,
                         fv: float | None) -> BorrowedMarks:
    """Borrow-check and liquidation when the equity is the debt."""
    if confirmed:
        if not _pos(fv):
            return BorrowedMarks(None, None)
        return BorrowedMarks(fv, fv)
    if not _pos(last_confirmed) or not _pos(new_print):
        return BorrowedMarks(None, None)
    assert last_confirmed is not None and new_print is not None
    borrow_check = last_confirmed if last_confirmed > new_print else new_print
    if new_print > last_confirmed:
        if band_hi is None or not math.isfinite(band_hi):
            return BorrowedMarks(None, None)
        liquidation = new_print if new_print < band_hi else band_hi
        return BorrowedMarks(borrow_check, liquidation)
    return BorrowedMarks(borrow_check, last_confirmed)


def perp_mark(*, last_confirmed: float | None, confirmed: bool,
              fv: float | None) -> float | None:
    """Symmetric clamp for a perp mark.

    An unconfirmed print does not move the mark off R in either direction.
    Open interest is capped separately, and that cap is lower while the
    print is unconfirmed.
    """
    if confirmed:
        if not _pos(fv):
            return None
        return fv
    if not _pos(last_confirmed):
        return None
    return last_confirmed


def oi_scale_ratio(*, confirmed: bool, band_lo: float | None, band_hi: float | None,
                   fv: float | None, unconfirmed_cap: float, width_ref: float) -> float | None:
    """Open-interest scale in (0, 1]. A ratio, never a notional.

    Wider bands scale it down. While the print is unconfirmed it is also
    held at or under ``unconfirmed_cap``. A missing band is omitted, not
    reported as a full cap and not reported as zero.
    """
    if not _pos(fv) or band_lo is None or band_hi is None:
        return None
    if not math.isfinite(band_lo) or not math.isfinite(band_hi) or band_hi < band_lo:
        return None
    if width_ref <= 0 or unconfirmed_cap <= 0 or unconfirmed_cap > 1:
        return None
    assert fv is not None
    half = (band_hi - band_lo) / 2.0
    frac = half / fv
    if frac < 0:
        return None
    scale = 1.0 / (1.0 + (frac / width_ref))
    if not confirmed and scale > unconfirmed_cap:
        scale = unconfirmed_cap
    if scale <= 0 or scale > 1:
        return None
    return scale


def invariant_holds(collateral: CollateralMarks, borrowed: BorrowedMarks) -> bool:
    """Borrow-power <= liquidation, and the debt mirror.

    A side that is omitted (either price missing) makes no claim.
    """
    if (collateral.borrow_power is not None and collateral.liquidation is not None
            and collateral.borrow_power > collateral.liquidation):
        return False
    if (borrowed.borrow_check is not None and borrowed.liquidation is not None
            and borrowed.liquidation > borrowed.borrow_check):
        return False
    return True

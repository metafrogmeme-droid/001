"""Hyperliquid builder-fee units.

``f`` is in tenths of a basis point. ``f`` of 10 is 1 bp. So ``f`` of 1 is
0.1 bp, which is 0.001 percent, and ``maxBuilderFee`` of 100 is a 0.1 percent
cap. Headroom is the approved cap minus the rate actually charged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

# One f-unit, in basis points. f=10 is 1 bp, so one unit is 0.1 bp.
_BP_PER_F = Decimal("0.1")
# One f-unit, in percent. 0.1 bp is 0.001 percent.
_PERCENT_PER_F = Decimal("0.001")
# One f-unit charged on 10,000 USDC of notional. 0.001 percent of 10,000 is 0.1.
_USDC_PER_F_ON_10K = Decimal("0.1")


def _as_f(value) -> Optional[int]:
    """An integer f-unit. A bool, a float, or a blank is unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit() and text not in ("", "-", "+"):
            return int(text)
    return None


def _text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in ("", "-0"):
        return "0"
    return rendered


def bp_of(f_units: int) -> Decimal:
    return Decimal(f_units) * _BP_PER_F


def percent_of(f_units: int) -> Decimal:
    return Decimal(f_units) * _PERCENT_PER_F


def per_10k_usdc(f_units: int) -> Decimal:
    return Decimal(f_units) * _USDC_PER_F_ON_10K


def headroom(cap_f, charged_f) -> Optional[dict]:
    """Cap minus charged, in bp and per 10,000 USDC notional.

    ``None`` when either rate was not read. A measured zero cap or a measured
    zero charge is a real number and stays in the arithmetic.
    """
    cap = _as_f(cap_f)
    charged = _as_f(charged_f)
    if cap is None or charged is None:
        return None
    gap = cap - charged
    return {
        "cap_f": cap,
        "charged_f": charged,
        "headroom_f": gap,
        "cap_bp": _text(bp_of(cap)),
        "charged_bp": _text(bp_of(charged)),
        "headroom_bp": _text(bp_of(gap)),
        "cap_percent": _text(percent_of(cap)),
        "charged_percent": _text(percent_of(charged)),
        "headroom_per_10k_usdc": _text(per_10k_usdc(gap)),
        "cap_per_10k_usdc": _text(per_10k_usdc(cap)),
    }

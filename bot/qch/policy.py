"""Curator actions. Caps may fall. Nothing here liquidates.

A trigger is the quantum canary, a QES jump above the configured
threshold, or a deadline with no issuer attestation. SaR flag bits for
peg deviation and the quantum canary carry the haircut onto collateral.
An unread flag is not a zero haircut.
"""

from __future__ import annotations

from typing import Optional

POLICY_METHOD = (
    "Cut the supply cap and schedule an LTV cut when the canary bit is set, "
    "QES rises by more than the placeholder jump, or the deadline passes "
    "without an attestation. LTV waits out the SaR signer timelock. "
    "No action liquidates. Haircut bps equals QES when SaR flag 16 or 32 is set."
)


def _bit(flags: Optional[int], bit: Optional[int]) -> Optional[bool]:
    if flags is None or bit is None:
        return None
    if isinstance(flags, bool) or isinstance(bit, bool):
        return None
    return (flags & bit) != 0


def collateral_haircut(
    qes_bps: Optional[int],
    sar_flags: Optional[int],
    flag_bits: Optional[dict],
) -> dict:
    """The haircut those two SaR bits carry. Clear flags are a real zero."""
    if flag_bits is None or sar_flags is None or isinstance(sar_flags, bool):
        return {"haircut_bps": None, "reason": "flags_unreadable"}
    peg = _bit(sar_flags, flag_bits.get("peg") if isinstance(flag_bits.get("peg"), int) else None)
    canary = _bit(
        sar_flags,
        flag_bits.get("canary") if isinstance(flag_bits.get("canary"), int) else None,
    )
    if peg is None or canary is None:
        return {"haircut_bps": None, "reason": "flags_unreadable"}
    if peg is not True and canary is not True:
        return {"haircut_bps": 0, "reason": "flags_clear"}
    if qes_bps is None:
        return {"haircut_bps": None, "reason": "score_unreadable"}
    return {"haircut_bps": qes_bps, "reason": "flag_peg" if peg else "flag_canary"}


def decide(
    *,
    qes_bps: Optional[int],
    prev_qes_bps: Optional[int],
    canary: Optional[bool],
    jump_bps: Optional[int],
    deadline_unix: Optional[int],
    now: Optional[int],
    attested: Optional[bool],
    sar_flags: Optional[int],
    flag_bits: Optional[dict],
    cap_cut_bps: int,
    data_source: str,
) -> dict:
    """One decision. ``liquidate`` is false on every path."""
    haircut = collateral_haircut(qes_bps, sar_flags, flag_bits)
    reasons: list[str] = []
    cut = False
    if canary is True:
        cut = True
        reasons.append("canary")
    elif canary is None:
        reasons.append("canary_unreadable")
    if (
        qes_bps is not None
        and prev_qes_bps is not None
        and jump_bps is not None
        and qes_bps - prev_qes_bps > jump_bps
    ):
        cut = True
        reasons.append("qes_jump")
    elif prev_qes_bps is None or jump_bps is None or qes_bps is None:
        reasons.append("jump_not_measured")
    deadline_hit = False
    if deadline_unix is None:
        reasons.append("deadline_unset")
    elif now is None or attested is None:
        reasons.append("deadline_unreadable")
    elif now >= deadline_unix and attested is False:
        deadline_hit = True
        reasons.append("deadline")
    else:
        reasons.append("deadline_clear")
    if deadline_hit:
        cut = True
    action = "cut_supply_cap" if cut else "hold"
    return {
        "action": action,
        "schedule_ltv_cut": cut,
        "liquidate": False,
        "cap_cut_bps": cap_cut_bps if cut else 0,
        "haircut_bps": haircut["haircut_bps"],
        "haircut_reason": haircut["reason"],
        "reasons": reasons,
        "estimate": True,
        "label": "estimate",
        "method": POLICY_METHOD,
        "data_source": data_source,
    }

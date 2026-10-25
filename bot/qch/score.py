"""Quantum exposure score, in basis points of the reserve.

QES = weighted exposed sats * 10000 // total sats. A missing total is
not 0 bps. A single observation is not reported as a time average.
"""

from __future__ import annotations

from typing import Optional

METHOD = (
    "QES bps = weighted_exposed_sats * 10000 // total_sats. "
    "P2TR counts as exposed because the key is in the output. "
    "Any other type counts as exposed only after a spent output. "
    "Single-key P2TR and spent P2WPKH keep weight 1. "
    "An m-of-n bucket keeps weight 1 until a threshold is configured, "
    "then weight m/n. MPC ECDSA/TSS stays classical and does not reduce the score. "
    "A fresh issuer attestation subtracts the configured reduction. "
    "One snapshot is that snapshot, not a time average."
)


def _int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def is_exposed(row: object) -> Optional[bool]:
    """P2TR, or at least one spent output. Missing inputs stay None."""
    if not isinstance(row, dict):
        return None
    kind = row.get("type")
    if not isinstance(kind, str) or not kind:
        return None
    if kind == "P2TR":
        return True
    spent = _int(row.get("spent_txo"))
    if spent is None or spent < 0:
        return None
    return spent >= 1


def qes_bps(exposed_sats: object, total_sats: object) -> Optional[int]:
    """Raw ratio. ``None`` when either side cannot be used. Zero total is not 0 bps."""
    exposed = _int(exposed_sats)
    total = _int(total_sats)
    if exposed is None or total is None:
        return None
    if total <= 0 or exposed < 0 or exposed > total:
        return None
    return exposed * 10000 // total


def multisig_factor_bps(state: str, m: Optional[int], n: Optional[int]) -> dict:
    """Weight for an m-of-n bucket. Unset keeps full weight and says so."""
    if state == "unset":
        return {"applied": False, "factor_bps": 10000, "reason": "unverified_threshold"}
    if state != "value" or m is None or n is None or n <= 0 or m > n or m < 1:
        return {"applied": False, "factor_bps": None, "reason": "unreadable"}
    return {"applied": True, "factor_bps": m * 10000 // n, "reason": "configured_m_over_n"}


def weighted_exposed(buckets: object, factor_bps: Optional[int]) -> Optional[int]:
    """Exposed sats after the multisig weight. A bad bucket drops the whole sum."""
    if not isinstance(buckets, list) or not buckets:
        return None
    if factor_bps is None or isinstance(factor_bps, bool) or not isinstance(factor_bps, int):
        return None
    if factor_bps < 0 or factor_bps > 10000:
        return None
    total_exposed = 0
    for bucket in buckets:
        if not isinstance(bucket, dict):
            return None
        exposed = _int(bucket.get("exposed_sats"))
        if exposed is None or exposed < 0:
            return None
        weight = factor_bps if bucket.get("multisig") is True else 10000
        total_exposed += exposed * weight // 10000
    return total_exposed


def bucket_total(buckets: object) -> Optional[int]:
    if not isinstance(buckets, list) or not buckets:
        return None
    total = 0
    for bucket in buckets:
        if not isinstance(bucket, dict):
            return None
        sats = _int(bucket.get("total_sats"))
        exposed = _int(bucket.get("exposed_sats"))
        if sats is None or exposed is None or sats < 0 or exposed < 0 or exposed > sats:
            return None
        total += sats
    return total


def time_weighted(points: object, as_of: object) -> dict:
    """Hold each reading until the next one. A single point is not averaged."""
    if not isinstance(points, list) or not points:
        return {"tw_bps": None, "observations": 0, "averaged": False, "reason": "no_points"}
    parsed: list[tuple[int, int]] = []
    for point in points:
        if not isinstance(point, dict):
            return {"tw_bps": None, "observations": 0, "averaged": False, "reason": "unreadable"}
        ts = _int(point.get("ts"))
        qes = _int(point.get("qes_bps"))
        if ts is None or qes is None or qes < 0 or qes > 10000:
            return {"tw_bps": None, "observations": 0, "averaged": False, "reason": "unreadable"}
        if parsed and ts <= parsed[-1][0]:
            return {"tw_bps": None, "observations": len(parsed), "averaged": False, "reason": "unreadable"}
        parsed.append((ts, qes))
    if len(parsed) == 1:
        return {
            "tw_bps": parsed[0][1],
            "observations": 1,
            "averaged": False,
            "reason": "single_observation",
        }
    end = _int(as_of)
    if end is None or end <= parsed[-1][0]:
        return {
            "tw_bps": None,
            "observations": len(parsed),
            "averaged": False,
            "reason": "window_open",
        }
    weighted = 0
    span = 0
    for index, (ts, qes) in enumerate(parsed):
        nxt = parsed[index + 1][0] if index + 1 < len(parsed) else end
        dt = nxt - ts
        weighted += qes * dt
        span += dt
    if span <= 0:
        return {"tw_bps": None, "observations": len(parsed), "averaged": False, "reason": "unreadable"}
    return {
        "tw_bps": weighted // span,
        "observations": len(parsed),
        "averaged": True,
        "reason": "time_weighted",
    }


def reserve_gap(reserve_sats: object, other_sats: object, other_name: str) -> dict:
    """Difference between the reserve and another counted supply. Unread is not 0."""
    reserve = _int(reserve_sats)
    other = _int(other_sats)
    if reserve is None or other is None:
        return {
            "gap_sats": None,
            "gap_bps": None,
            "flag": None,
            "reason": "unread",
            "against": other_name,
        }
    if reserve < 0 or other < 0:
        return {
            "gap_sats": None,
            "gap_bps": None,
            "flag": None,
            "reason": "unreadable",
            "against": other_name,
        }
    diff = reserve - other if reserve >= other else other - reserve
    gap_bps = None if reserve == 0 else diff * 10000 // reserve
    return {
        "gap_sats": diff,
        "gap_bps": gap_bps,
        "flag": diff != 0,
        "reason": "compared",
        "against": other_name,
    }


def apply_attestation(
    qes: Optional[int],
    attestation: object,
    reduction_bps: Optional[int],
    reduction_state: str,
    now: Optional[int],
) -> dict:
    """Subtract a configured reduction only for a fresh signed plan."""
    base = {"qes_bps": qes, "applied": False}
    if qes is None:
        return {**base, "reason": "score_unreadable"}
    if reduction_state == "unreadable" or reduction_bps is None:
        return {**base, "reason": "reduction_unreadable"}
    if attestation is None:
        return {**base, "reason": "no_attestation"}
    if not isinstance(attestation, dict):
        return {**base, "reason": "attestation_unreadable"}
    if attestation.get("signed") is not True:
        return {**base, "reason": "attestation_unsigned"}
    expires = _int(attestation.get("expires_unix"))
    if expires is None or now is None:
        return {**base, "reason": "attestation_unreadable"}
    if now >= expires:
        return {**base, "reason": "attestation_expired"}
    reduced = qes - reduction_bps
    if reduced < 0:
        reduced = 0
    return {"qes_bps": reduced, "applied": reduction_bps > 0, "reason": "configured_reduction"}

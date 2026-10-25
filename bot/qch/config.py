"""Placeholder parameters. Unverified facts stay unset.

An empty variable is unset. A variable that does not parse is
unreadable, which is not the placeholder and not zero.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

_WBTC_BASIS = (
    "UNVERIFIED placeholder. The multisig threshold was not in the fixture. "
    "Unset does not apply an m-of-n discount."
)
_UNIT_BASIS = (
    "UNVERIFIED placeholder. The treasury fixture address is P2TR (bc1p). "
    "A bc1q deposit example was not in the fixture and is not treated as the format."
)
_JUMP_BASIS = "Placeholder design constant, not a measured curator policy."
_REDUCTION_BASIS = "No attested reduction until a signed migration plan is recorded."
_SCAN_BASIS = "UNVERIFIED. LBTC and tBTC reserves were not scanned. Absent is not zero."


@dataclass(frozen=True)
class QchConfig:
    wbtc_multisig_m: Optional[int]
    wbtc_multisig_n: Optional[int]
    wbtc_multisig_state: str
    wbtc_multisig_basis: str
    unit_deposit_prefix: str
    unit_deposit_basis: str
    lbtc_scanned: bool
    tbtc_scanned: bool
    scan_basis: str
    qes_jump_bps: Optional[int]
    qes_jump_state: str
    qes_jump_basis: str
    policy_deadline_unix: Optional[int]
    policy_deadline_state: str
    attestation_reduction_bps: Optional[int]
    attestation_reduction_state: str
    attestation_reduction_basis: str
    cap_cut_bps: int


def _blank(raw: object) -> bool:
    return raw is None or str(raw).strip() == ""


def _uint(raw: object) -> tuple[Optional[int], str]:
    """``(value, state)``. State is unset, value, or unreadable."""
    if _blank(raw):
        return None, "unset"
    text = str(raw).strip()
    if not text.isdigit():
        return None, "unreadable"
    return int(text), "value"


def load_config(env: Optional[Mapping[str, str]] = None) -> QchConfig:
    """Defaults are the labelled placeholders. A passed mapping is the only source."""
    source = {} if env is None else env
    m, m_state = _uint(source.get("QCH_WBTC_MULTISIG_M"))
    n, n_state = _uint(source.get("QCH_WBTC_MULTISIG_N"))
    if m_state == "unreadable" or n_state == "unreadable":
        multi_state = "unreadable"
    elif m_state == "unset" and n_state == "unset":
        multi_state = "unset"
    elif m_state == "value" and n_state == "value":
        multi_state = "value" if m is not None and n is not None and 1 <= m <= n else "unreadable"
    else:
        multi_state = "unreadable"
    jump, jump_state = _uint(source.get("QCH_QES_JUMP_BPS"))
    if jump_state == "unset":
        jump, jump_state = 50, "placeholder"
    elif jump_state == "value" and (jump is None or jump <= 0 or jump > 10000):
        jump, jump_state = None, "unreadable"
    deadline, deadline_state = _uint(source.get("QCH_POLICY_DEADLINE_UNIX"))
    reduction, reduction_state = _uint(source.get("QCH_ATTESTATION_REDUCTION_BPS"))
    if reduction_state == "unset":
        reduction, reduction_state = 0, "placeholder"
    elif reduction_state == "value" and (reduction is None or reduction > 10000):
        reduction, reduction_state = None, "unreadable"
    prefix_raw = source.get("QCH_UNIT_DEPOSIT_PREFIX")
    if _blank(prefix_raw):
        prefix = "unverified"
    else:
        prefix = str(prefix_raw).strip().lower()
    lbtc = str(source.get("QCH_LBTC_SCANNED", "")).strip().lower() in {"1", "true"}
    tbtc = str(source.get("QCH_TBTC_SCANNED", "")).strip().lower() in {"1", "true"}
    return QchConfig(
        wbtc_multisig_m=m if multi_state == "value" else None,
        wbtc_multisig_n=n if multi_state == "value" else None,
        wbtc_multisig_state=multi_state,
        wbtc_multisig_basis=_WBTC_BASIS,
        unit_deposit_prefix=prefix,
        unit_deposit_basis=_UNIT_BASIS,
        lbtc_scanned=lbtc,
        tbtc_scanned=tbtc,
        scan_basis=_SCAN_BASIS,
        qes_jump_bps=jump,
        qes_jump_state=jump_state,
        qes_jump_basis=_JUMP_BASIS,
        policy_deadline_unix=deadline,
        policy_deadline_state=deadline_state,
        attestation_reduction_bps=reduction,
        attestation_reduction_state=reduction_state,
        attestation_reduction_basis=_REDUCTION_BASIS,
        cap_cut_bps=1000,
    )


def active_env() -> Mapping[str, str]:
    return os.environ

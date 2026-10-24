"""The published custody reading.

Every risk score carries ``method``, ``data_source``, ``label`` of
``estimate``, and ``estimate: true``. Supply counts are snapshot
counts, not scores. This module does not sign.
"""

from __future__ import annotations

from typing import Optional

from bot.qch.config import QchConfig, load_config
from bot.qch.graph import custody_node
from bot.qch.policy import POLICY_METHOD, decide
from bot.qch.sar_bind import flag_bits, timelock_sec
from bot.qch.score import (
    METHOD,
    apply_attestation,
    bucket_total,
    multisig_factor_bps,
    qes_bps,
    reserve_gap,
    time_weighted,
    weighted_exposed,
)

_NOT_SCANNED = ("LBTC", "tBTC")


def _score_fields(qes: Optional[int], source: str, extra: dict) -> dict:
    return {
        "qes_bps": qes,
        "estimate": True,
        "label": "estimate",
        "method": METHOD,
        "data_source": source,
        **extra,
    }


def _issuer(row: dict, cfg: QchConfig, as_of: Optional[int]) -> dict:
    raw_asset = row.get("asset")
    asset = raw_asset if isinstance(raw_asset, str) else "unread"
    raw_source = row.get("data_source")
    source = raw_source if isinstance(raw_source, str) else ""
    if not source:
        return _score_fields(None, "none", {"asset": asset, "reason": "source_unreadable", "node": None})
    buckets = row.get("buckets")
    factor = multisig_factor_bps(cfg.wbtc_multisig_state, cfg.wbtc_multisig_m, cfg.wbtc_multisig_n)
    use_factor = factor if row.get("multisig") is True else {
        "applied": False, "factor_bps": 10000, "reason": "not_multisig",
    }
    if row.get("multisig") is True and factor["factor_bps"] is None:
        weighted = None
    else:
        weighted = weighted_exposed(buckets, use_factor["factor_bps"])
    total = bucket_total(buckets)
    raw = qes_bps(weighted, total)
    attestation = apply_attestation(
        raw,
        row.get("pq_attestation"),
        cfg.attestation_reduction_bps,
        cfg.attestation_reduction_state,
        as_of,
    )
    points = row.get("points")
    if raw is None:
        tw = {"tw_bps": None, "observations": 0, "averaged": False, "reason": "score_unreadable"}
    elif not isinstance(points, list):
        tw = time_weighted([{"ts": as_of, "qes_bps": attestation["qes_bps"]}], as_of)
    else:
        tw = time_weighted(points, as_of)
    gap = reserve_gap(total, row.get("compared_sats"), str(row.get("compared_name") or "unread"))
    raw_verif = row.get("verif")
    verif = raw_verif if isinstance(raw_verif, str) else "none"
    node = custody_node(asset, attestation["qes_bps"], verif, source, METHOD)
    supply = row.get("supply_by_chain")
    chains = supply if isinstance(supply, list) else None
    return _score_fields(attestation["qes_bps"], source, {
        "asset": asset,
        "reason": attestation["reason"],
        "raw_qes_bps": raw,
        "tw_bps": tw["tw_bps"],
        "tw_averaged": tw["averaged"],
        "tw_reason": tw["reason"],
        "tw_observations": tw["observations"],
        "multisig_factor": use_factor,
        "signer_scheme": row.get("signer_scheme"),
        "spend_path": row.get("spend_path"),
        "unit_deposit_prefix": cfg.unit_deposit_prefix if asset == "UBTC" else None,
        "unit_deposit_basis": cfg.unit_deposit_basis if asset == "UBTC" else None,
        "reserve_sats": total,
        "gap": gap,
        "supply_by_chain": chains,
        "supply_kind": "snapshot_count",
        "node": node,
        "address_count": (
            row.get("address_count")
            if isinstance(row.get("address_count"), int) and not isinstance(row.get("address_count"), bool)
            else None
        ),
    })


def _unscanned(asset: str, cfg: QchConfig, scanned: bool) -> dict:
    reason = "scanned_without_a_reserve" if scanned else "not_scanned"
    source = "none"
    return {
        "asset": asset,
        "qes_bps": None,
        "tw_bps": None,
        "estimate": True,
        "label": "estimate",
        "method": cfg.scan_basis,
        "data_source": source,
        "reason": reason,
        "node": custody_node(asset, None, "none", source, cfg.scan_basis),
    }


def _one(
    *,
    qes: Optional[int],
    prev: Optional[int],
    canary: Optional[bool],
    deadline: Optional[int],
    now: Optional[int],
    attested: Optional[bool],
    flags: Optional[int],
    cfg: QchConfig,
    bits: Optional[dict],
    source: str,
) -> dict:
    return decide(
        qes_bps=qes,
        prev_qes_bps=prev,
        canary=canary,
        jump_bps=cfg.qes_jump_bps,
        deadline_unix=deadline,
        now=now,
        attested=attested,
        sar_flags=flags,
        flag_bits=bits,
        cap_cut_bps=cfg.cap_cut_bps,
        data_source=source,
    )


def _bit(bits: Optional[dict], name: str) -> Optional[int]:
    if not isinstance(bits, dict):
        return None
    value = bits.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _cases(qes: Optional[int], source: str, cfg: QchConfig, bits: Optional[dict], now: Optional[int]) -> dict:
    jump = cfg.qes_jump_bps
    prev = None if qes is None or jump is None else qes - (jump + 1)
    deadline = 0 if now is not None else None
    return {
        "clear": _one(
            qes=qes, prev=qes, canary=False, deadline=None, now=now,
            attested=False, flags=0, cfg=cfg, bits=bits, source=source,
        ),
        "canary": _one(
            qes=qes, prev=qes, canary=True, deadline=None, now=now,
            attested=False, flags=_bit(bits, "canary"), cfg=cfg, bits=bits, source=source,
        ),
        "jump": _one(
            qes=qes, prev=prev, canary=False, deadline=None, now=now,
            attested=False, flags=0, cfg=cfg, bits=bits, source=source,
        ),
        "deadline": _one(
            qes=qes, prev=qes, canary=False, deadline=deadline, now=now,
            attested=False, flags=0, cfg=cfg, bits=bits, source=source,
        ),
        "flags_clear": _one(
            qes=qes, prev=qes, canary=False, deadline=None, now=now,
            attested=None, flags=0, cfg=cfg, bits=bits, source=source,
        ),
        "flag_peg": _one(
            qes=qes, prev=qes, canary=False, deadline=None, now=now,
            attested=None, flags=_bit(bits, "peg"), cfg=cfg, bits=bits, source=source,
        ),
        "flags_unread": _one(
            qes=qes, prev=qes, canary=False, deadline=None, now=now,
            attested=None, flags=None, cfg=cfg, bits=bits, source=source,
        ),
        "score_unread": _one(
            qes=None, prev=None, canary=True, deadline=None, now=now,
            attested=False, flags=_bit(bits, "canary"), cfg=cfg, bits=bits, source=source,
        ),
    }


def publish(env: Optional[dict] = None, snapshot: Optional[dict] = None) -> dict:
    """The admin reading. ``env`` defaults to the placeholder config, not the process."""
    from bot.qch.crawler import load_snapshot

    cfg = load_config({} if env is None else env)
    loaded = snapshot if snapshot is not None else load_snapshot()["snapshot"]
    bits = flag_bits()
    lock = timelock_sec()
    if not isinstance(loaded, dict):
        return {
            "readable": False,
            "reason": "unreadable",
            "estimate": True,
            "label": "estimate",
            "method": METHOD,
            "data_source": "none",
            "issuers": None,
            "signs": False,
            "key_held": False,
        }
    as_of = loaded.get("as_of_unix")
    if isinstance(as_of, bool) or not isinstance(as_of, int):
        as_of = None
    issuers = []
    seen = set()
    for row in loaded.get("issuers") or []:
        if not isinstance(row, dict):
            issuers.append(_score_fields(None, "none", {"asset": "unread", "reason": "row_unreadable", "node": None}))
            continue
        built = _issuer(row, cfg, as_of)
        issuers.append(built)
        seen.add(built["asset"])
    for asset in _NOT_SCANNED:
        if asset in seen:
            continue
        scanned = cfg.lbtc_scanned if asset == "LBTC" else cfg.tbtc_scanned
        issuers.append(_unscanned(asset, cfg, scanned))
    wbtc = next((row for row in issuers if row.get("asset") == "WBTC" and row.get("qes_bps") is not None), None)
    source = wbtc["data_source"] if wbtc else "none"
    qes = wbtc["qes_bps"] if wbtc else None
    return {
        "readable": True,
        "reason": None,
        "product": "QCH",
        "as_of_unix": as_of,
        "as_of": loaded.get("as_of"),
        "rule": loaded.get("rule"),
        "estimate": True,
        "label": "estimate",
        "method": METHOD,
        "policy_method": POLICY_METHOD,
        "data_source": "bot/qch/fixtures/snapshot.json",
        "signs": False,
        "key_held": False,
        "network": "testnet",
        "parameters": {
            "wbtc_multisig_m": cfg.wbtc_multisig_m,
            "wbtc_multisig_n": cfg.wbtc_multisig_n,
            "wbtc_multisig_state": cfg.wbtc_multisig_state,
            "wbtc_multisig_basis": cfg.wbtc_multisig_basis,
            "unit_deposit_prefix": cfg.unit_deposit_prefix,
            "unit_deposit_basis": cfg.unit_deposit_basis,
            "qes_jump_bps": cfg.qes_jump_bps,
            "qes_jump_state": cfg.qes_jump_state,
            "qes_jump_basis": cfg.qes_jump_basis,
            "policy_deadline_state": cfg.policy_deadline_state,
            "attestation_reduction_bps": cfg.attestation_reduction_bps,
            "attestation_reduction_basis": cfg.attestation_reduction_basis,
            "cap_cut_bps": cfg.cap_cut_bps,
            "ltv_timelock_sec": lock,
            "sar_flags": bits,
            "scan_basis": cfg.scan_basis,
        },
        "issuers": issuers,
        "simulator": {
            "asset": "WBTC" if wbtc else None,
            "qes_bps": qes,
            "estimate": True,
            "label": "estimate",
            "method": POLICY_METHOD,
            "data_source": source,
            "cases": _cases(qes, source, cfg, bits, as_of),
        },
    }

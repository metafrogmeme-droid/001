"""Unsigned revokes. The user signs these in their own wallet.

The selectors match Panic Button's encodings. Nothing here signs, and a
7702 clear leaves the nonce and the signature empty.
"""

from __future__ import annotations

from typing import Optional

from bot.claw_watch_heuristics.allowlist import PERMIT2

ZERO = "0x" + "0" * 40
_APPROVE = "095ea7b3"
_APPROVAL_FOR_ALL = "a22cb465"
_LOCKDOWN = "cc53287f"

_PERMIT_RULES = frozenset({
    "PERMIT_SUBMITTED_BY_THIRD_PARTY",
    "APPROVAL_TO_EOA",
    "APPROVAL_FOR_ALL_TO_UNKNOWN",
})
_DELEGATION_RULES = frozenset({
    "DELEGATION_TO_LISTED_SWEEPER",
    "DELEGATION_TO_UNKNOWN_CONTRACT",
})


def _word(body: str) -> str:
    return body.lower().removeprefix("0x").rjust(64, "0")


def _approve(token: str, spender: str, chain_id: object) -> dict:
    data = "0x" + _APPROVE + _word(spender) + _word("0")
    return {
        "kind": "transaction",
        "to": token,
        "data": data,
        "value": "0x0",
        "chain_id": chain_id,
        "signature": None,
    }


def _approval_for_all(token: str, operator: str, chain_id: object) -> dict:
    data = "0x" + _APPROVAL_FOR_ALL + _word(operator) + _word("0")
    return {
        "kind": "transaction",
        "to": token,
        "data": data,
        "value": "0x0",
        "chain_id": chain_id,
        "signature": None,
    }


def _lockdown(token: str, spender: str, chain_id: object) -> dict:
    # lockdown((address,address)[]): offset, length, token, spender.
    data = "0x" + _LOCKDOWN + _word("20") + _word("1") + _word(token) + _word(spender)
    return {
        "kind": "transaction",
        "to": PERMIT2,
        "data": data,
        "value": "0x0",
        "chain_id": chain_id,
        "signature": None,
    }


def _clear_delegation(chain_id: object) -> dict:
    return {
        "kind": "authorization",
        "to": None,
        "data": "0x",
        "value": "0x0",
        "chain_id": chain_id,
        "authorization": {
            "chainId": chain_id,
            "address": ZERO,
            "nonce": None,
            "signature": None,
        },
        "signature": None,
    }


def _addr(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
        return None
    return value


def prepare_revokes(finding: dict, chain_id: object) -> list[dict]:
    """One unsigned revoke per spender, operator, or delegate on the finding.

    A lookalike has nothing to revoke. The same pair is not repeated.
    """
    out: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    signals_raw = finding.get("signals")
    signals = signals_raw if isinstance(signals_raw, list) else []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        rule = sig.get("rule")
        ev_raw = sig.get("evidence")
        ev = ev_raw if isinstance(ev_raw, dict) else {}
        if rule in _DELEGATION_RULES:
            key: tuple[str, ...] = ("delegation",)
            if key not in seen:
                seen.add(key)
                out.append(_clear_delegation(chain_id))
            continue
        if rule not in _PERMIT_RULES:
            continue
        standard = ev.get("standard")
        token = _addr(ev.get("token") or ev.get("collection"))
        spender = _addr(ev.get("spender") or ev.get("operator"))
        if token is None or spender is None:
            continue
        if rule == "APPROVAL_FOR_ALL_TO_UNKNOWN":
            key = ("afa", token, spender)
            if key in seen:
                continue
            seen.add(key)
            out.append(_approval_for_all(token, spender, chain_id))
            continue
        if standard == "Permit2" or standard == "Permit2-SignatureTransfer":
            key = ("permit2", token, spender)
            if key in seen:
                continue
            seen.add(key)
            out.append(_lockdown(token, spender, chain_id))
            continue
        key = ("erc20", token, spender)
        if key in seen:
            continue
        seen.add(key)
        out.append(_approve(token, spender, chain_id))
    return out

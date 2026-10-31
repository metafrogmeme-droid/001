"""Canonical order identity and the payload the Guard would sign.

``order_hash`` is the order itself, so a confirm still matches the same
order a few seconds later. ``expiresAfter`` is stamped at sign time and is
not part of that identity. ``payload_hash`` is the hash of the exact
payload, including ``expiresAfter``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

EXPIRES_AFTER_S = 10


def order_hash(action: Mapping[str, Any]) -> str:
    body = _identity(action)
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def signed_payload(action: Mapping[str, Any], now: float, endpoint: str) -> dict[str, Any]:
    """The payload a signature covers. ``expiresAfter`` is ``now + 10`` seconds."""
    body = _identity(action)
    body["expiresAfter"] = stamp_expiry(now)
    body["endpoint"] = endpoint
    return body


def canonical(body: Mapping[str, Any]) -> str:
    """Stable JSON of a payload. The signer hashes these bytes."""
    return _canonical(dict(body))


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def expires_after_ok(payload: Mapping[str, Any], now: float) -> bool:
    """True when ``expiresAfter`` is set and is not after ``now + 10``."""
    raw = payload.get("expiresAfter")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return False
    return float(raw) <= float(now) + EXPIRES_AFTER_S


def expired(payload: Mapping[str, Any], now: float) -> bool:
    raw = payload.get("expiresAfter")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return True
    return float(now) > float(raw)


def _identity(action: Mapping[str, Any]) -> dict[str, Any]:
    skip = {"endpoint", "chain_id", "expiresAfter", "client_tag", "cloid", "origin", "ts"}
    out: dict[str, Any] = {}
    for key in sorted(action):
        if key in skip:
            continue
        out[key] = _scrub(action[key])
    return out


def _scrub(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    return str(value)


def _canonical(body: Mapping[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stamp_expiry(now: float) -> float:
    if isinstance(now, float) and now != int(now):
        return now + EXPIRES_AFTER_S
    return float(int(now) + EXPIRES_AFTER_S)

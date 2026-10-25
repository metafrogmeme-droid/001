"""Decode a signing request into public signals.

``decode`` does not open a socket, does not sign, and does not say whether
a request is acceptable. Every result carries the same label.
"""
from __future__ import annotations

import json
import re
import time

from .evm import decode_3009, decode_7702, decode_calldata, decode_message, decode_typed_data
from .hl import decode_hl_action
from .solana import decode_solana

DISCLAIMER = "AI-assisted. Public signals, not a verdict."
ASSISTED = "ai"
MAX_INPUT_CHARS = 65536

_KINDS = frozenset({"auto", "typed", "7702", "3009", "hl", "solana", "message", "calldata"})


class InputTooLarge(ValueError):
    """The request is longer than ``MAX_INPUT_CHARS``."""


def detect(obj):
    if isinstance(obj, str):
        text = obj.strip()
        if re.fullmatch(r"[A-Za-z0-9+/=\s]+", text) and len(text) > 80:
            return "solana"
        return "message"
    if not isinstance(obj, dict):
        return "unknown"
    if "types" in obj and "primaryType" in obj:
        return "typed"
    if "action" in obj or obj.get("type") in (
        "approveAgent", "approveBuilderFee", "withdraw3", "usdSend", "spotSend",
    ):
        return "hl"
    if "validBefore" in obj and "to" in obj:
        return "3009"
    if ("address" in obj or "delegate" in obj) and "chainId" in obj and "nonce" in obj:
        return "7702"
    if "to" in obj and isinstance(obj.get("data"), str) and "types" not in obj:
        return "calldata"
    if "data" in obj and ("method" in obj or isinstance(obj.get("data"), str)):
        return "message"
    if "solana_tx_base64" in obj:
        return "solana"
    return "unknown"


def _stamp(result: dict) -> dict:
    result["attention_count"] = sum(1 for row in result["signals"] if row["level"] == "attention")
    result["disclaimer"] = DISCLAIMER
    result["assisted"] = ASSISTED
    result["reads_network"] = False
    return result


def _calldata_as_result(obj, now):
    if not isinstance(obj, dict):
        return {
            "kind": "calldata",
            "summary": ["Calldata decode needs an object with to and data."],
            "fields": {},
            "signals": [{
                "code": "UNRECOGNISED_INPUT",
                "level": "attention",
                "text": "Could not recognise this request. Do not sign what you cannot read.",
            }],
        }
    native = obj.get("value")
    decoded = decode_calldata(obj.get("to", ""), obj.get("data") or "", native)
    summary = [f"Calldata {decoded.get('fn')} on {decoded.get('contract') or decoded.get('to')}."]
    return {
        "kind": "calldata",
        "summary": summary,
        "fields": {k: v for k, v in decoded.items() if k != "signals"},
        "signals": list(decoded.get("signals") or []),
    }


def decode(obj, kind="auto", now=None):
    now = int(now or time.time())
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "chainId" in obj[0]:
        kind = "7702" if kind == "auto" else kind
    if kind not in _KINDS:
        kind = "auto"
    chosen = detect(obj) if kind == "auto" and not isinstance(obj, list) else kind
    if chosen == "typed":
        result = decode_typed_data(obj, now)
    elif chosen == "7702":
        result = decode_7702(obj, now)
    elif chosen == "3009":
        result = decode_3009(obj, now)
    elif chosen == "hl":
        result = decode_hl_action(obj, now)
    elif chosen == "solana":
        payload = obj["solana_tx_base64"] if isinstance(obj, dict) and "solana_tx_base64" in obj else obj
        result = decode_solana(payload, now)
    elif chosen == "message":
        result = decode_message(obj if isinstance(obj, dict) else {"data": obj}, now)
    elif chosen == "calldata":
        result = _calldata_as_result(obj, now)
    else:
        result = {
            "kind": "unknown",
            "summary": ["Input format not recognised."],
            "fields": {},
            "signals": [{
                "code": "UNRECOGNISED_INPUT",
                "level": "attention",
                "text": "Could not recognise this request. Do not sign what you cannot read.",
            }],
        }
    return _stamp(result)


def decode_text(raw: str, kind: str = "auto", now=None):
    """Decode a text payload. A vector file or an API envelope unwraps to its input."""
    if not isinstance(raw, str):
        raise TypeError("decode_text wants text")
    if len(raw) > MAX_INPUT_CHARS:
        raise InputTooLarge()
    text = raw.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = text
    use_kind = kind
    use_now = now
    if isinstance(obj, dict) and "input" in obj and set(obj) <= {"input", "kind", "now", "expect", "name", "source"}:
        if use_kind == "auto" and isinstance(obj.get("kind"), str):
            use_kind = obj["kind"]
        if use_now is None and isinstance(obj.get("now"), int) and not isinstance(obj.get("now"), bool):
            use_now = obj["now"]
        obj = obj["input"]
    return decode(obj, use_kind, use_now)


def render_text(result: dict) -> str:
    lines = [*result["summary"], "", "Signals (public facts, not a verdict):"]
    for row in result["signals"]:
        lines.append(f"  [{row['level'].upper()}] {row['code']}: {row['text']}")
    if result.get("eip712_digest"):
        lines.append(f"EIP-712 digest: {result['eip712_digest']}")
    lines += ["", result["disclaimer"], "AI-assisted."]
    return "\n".join(lines)

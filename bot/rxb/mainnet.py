"""Mainnet sentinel config. This process does not hold a key and does not sign.

The role, if the owner grants one, is an address the owner controls: a key
or a multisig. ``ACTIVATED`` is false in this build. No environment variable
turns it on. Going live needs the owner's explicit go, after an external
audit, and a change that is not this file's env reader.

``RXB_SENTINEL_PAUSED`` is the pause switch. ``1`` or ``true`` pauses.
Anything else, including unset, is not paused. Pause is not activation.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

ACTIVATED = False
_ON = frozenset({"1", "true"})


def pause_on(env: Optional[Mapping[str, str]] = None) -> bool:
    src = os.environ if env is None else env
    raw = src.get("RXB_SENTINEL_PAUSED")
    if raw is None:
        return False
    return str(raw).strip().lower() in _ON


def _holder(raw: object) -> dict:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return {"address": None, "reason": "unset"}
    if not isinstance(raw, str):
        return {"address": None, "reason": "not_an_address"}
    text = raw.strip()
    if len(text) == 42 and text.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in text[2:]):
        return {"address": text, "reason": None}
    return {"address": None, "reason": "not_an_address"}


def mainnet_config(env: Optional[Mapping[str, str]] = None) -> dict:
    """The path a mainnet decrease would take. Activation stays false."""
    src = os.environ if env is None else env
    holder = _holder(src.get("RXB_MAINNET_SENTINEL"))
    return {
        "chain_id": 1,
        "activated": ACTIVATED,
        "paused": pause_on(src),
        "holds_keys": False,
        "signs": False,
        "sentinel": holder["address"],
        "sentinel_reason": holder["reason"],
    }

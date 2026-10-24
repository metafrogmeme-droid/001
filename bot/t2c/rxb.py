"""RXB signers and RXBSentinel. Neither is on main, so both are stubs.

``holds_keys`` is false. No environment variable is read for a key.
``attempt_sign`` returns the exception class name and nothing else.
"""
from __future__ import annotations

from bot.t2c.chain_guard import SigningRefused, refuse_sign


def holds_keys() -> bool:
    return False


def attempt_sign(payload: object) -> dict:
    del payload
    try:
        refuse_sign()
    except SigningRefused:
        return {"signed": False, "error": "SigningRefused", "holds_keys": holds_keys()}
    return {"signed": False, "error": "SigningRefused", "holds_keys": False}


def sentinel_submit(payload: object) -> dict:
    del payload
    return {"sink": "rxb_sentinel_stub", "applied": False}

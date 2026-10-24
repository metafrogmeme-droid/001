"""``FVB_ENABLED``. Off unless the environment says otherwise.

The name of the reader is ``flag_on``. A module-level ``enabled`` would
share a method name the reachability sweep already cannot attribute, and
the recorded ambiguity count would move.
"""
from __future__ import annotations

import os

FLAG = "FVB_ENABLED"


def flag_on(env: os._Environ[str] | dict[str, str] | None = None) -> bool:
    """True only for ``1`` or ``true``, ignoring case and surrounding space."""
    src = os.environ if env is None else env
    raw = src.get(FLAG)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def gate(flag_is_on: bool, is_operator: bool) -> str:
    """``open`` for everyone, ``preview`` for an operator while the flag is
    off, ``deny`` for everyone else while it is off.

    The door that asks who the caller is is ``admin_preview.flag_on_for``.
    This function only names the three answers once those two bits are known.
    ``deny`` is 404. ``preview`` is the banner.
    """
    if flag_is_on:
        return "open"
    if is_operator:
        return "preview"
    return "deny"

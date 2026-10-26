"""AUDIT_ROUTER_ENABLED. Off unless an operator sets it.

Read at call time through ``bot.config._env_switch``, the same switch reader
the rest of the tree uses for on/off env vars. An unrecognised value stays
off. There is no second reader.
"""

from __future__ import annotations

from bot.config import _env_switch

FLAG = "AUDIT_ROUTER_ENABLED"


def enabled() -> bool:
    """True only when AUDIT_ROUTER_ENABLED is on. Unset stays off."""
    return _env_switch(FLAG, False)

"""The trade-only key. Read here, and nowhere else.

The value comes from the environment the caller passes. It is not logged,
not put on a result, and not written to disk. An empty value means there
is no key. A non-string value is empty, not a key.
"""

from __future__ import annotations

from typing import Mapping, Optional

HL_KEY_ENV = "CLAW_GUARD_HL_AGENT_KEY"
PM_KEY_ENV = "CLAW_GUARD_PM_SESSION_KEY"


def trade_key(env: Optional[Mapping[str, str]], venue: object) -> str:
    """The trade-only key for this venue, or ``""`` when it is not set."""
    if env is None:
        return ""
    name = PM_KEY_ENV if str(venue or "") == "polymarket" else HL_KEY_ENV
    raw = env.get(name, "")
    if not isinstance(raw, str):
        return ""
    return raw.strip()

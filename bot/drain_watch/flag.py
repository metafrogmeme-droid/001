"""Whether Claw Watch is on for the public.

Production reads ``_env_switch`` so an operator can write on/off. Unset is
off. An unrecognised value is off, which is the default ``_env_switch``
uses when it does not recognise the word. A mapping passed in is for tests;
it uses the same two word lists and does not read the process environment.
"""

from __future__ import annotations

from typing import Mapping, Optional

from bot.config import _env_switch

FLAG = "DRAIN_WATCH_ENABLED"

_OFF = frozenset({"off", "false", "0", "no", "disabled"})
_ON = frozenset({"on", "true", "1", "yes", "enabled"})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when DRAIN_WATCH_ENABLED is one of the on-words."""
    if env is None:
        return _env_switch("DRAIN_WATCH_ENABLED", False)
    if FLAG not in env:
        return False
    raw = str(env.get(FLAG) or "").strip().lower()
    if raw == "":
        return False
    if raw in _OFF:
        return False
    if raw in _ON:
        return True
    return False

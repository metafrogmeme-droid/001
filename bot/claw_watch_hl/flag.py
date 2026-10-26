"""Flags for the Hyperliquid account watch. Each one defaults off.

Only the strings ``1`` and ``true`` turn a flag on. Unset, empty, ``yes``,
``on`` and ``0`` stay off. The mainnet switch selects a read-only info host.
It does not enable signing.

Not named ``enabled``. A module-level function of that name collides with
the method of the same name and drops it out of the ambiguity sweep.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

_ON = frozenset({"1", "true"})

FLAG = "CLAW_WATCH_HL_ENABLED"
MAINNET_FLAG = "CLAW_WATCH_HL_MAINNET_INFO"


def _on(name: str, env: Optional[Mapping[str, str]]) -> bool:
    src = env if env is not None else os.environ
    return str(src.get(name, "")).strip().lower() in _ON


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the watch is on for everyone. Anything other than 1 or true is off."""
    return _on(FLAG, env)


def mainnet_read_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the info host may be mainnet. Still read-only."""
    return _on(MAINNET_FLAG, env)

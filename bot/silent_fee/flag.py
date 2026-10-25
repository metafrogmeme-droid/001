"""Flags for Silent Fee Check. Each one defaults off.

Only the strings ``1`` and ``true`` turn a flag on. Unset, empty, ``yes``,
``on`` and ``0`` stay off. The website reader in ``app/lib/silent_fee_flag.js``
uses the same two strings for the main flag. The other two flags are read
here only.

Not named ``enabled``. A module-level function of that name collides with
the method of the same name and drops it out of the ambiguity sweep.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

_ON = frozenset({"1", "true"})

FLAG = "SILENT_FEE_CHECK_ENABLED"
EXPLORER_FLAG = "SILENT_FEE_EXPLORER_ENABLED"
MAINNET_FLAG = "SFC_ALLOW_MAINNET_READ"


def _on(name: str, env: Optional[Mapping[str, str]]) -> bool:
    src = env if env is not None else os.environ
    return str(src.get(name, "")).strip().lower() in _ON


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the fee check is on. Anything other than 1 or true is off."""
    return _on(FLAG, env)


def explorer_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether undocumented explorer and extraAgents reads may run."""
    return _on(EXPLORER_FLAG, env)


def mainnet_read_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the info host may be mainnet. Still read-only."""
    return _on(MAINNET_FLAG, env)

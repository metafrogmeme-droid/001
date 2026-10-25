"""``GROUNDED_REPUTATION_ENABLED``. Off unless the value is ``1`` or ``true``.

The HTTP door requires this reader and an operator. While the flag is off
every caller is refused. ``yes`` and ``on`` stay off.

A module-level ``enabled`` would share a name the reachability sweep cannot
attribute. The public reader is ``flag_on``.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "GROUNDED_REPUTATION_ENABLED"
MAINNET_READ_FLAG = "GROUNDED_REPUTATION_ALLOW_MAINNET_READ"
WINDOW_DAYS = "GROUNDED_REPUTATION_WINDOW_DAYS"
BUCKETS = "GROUNDED_REPUTATION_BUCKETS"
DEFAULT_WINDOW_DAYS = 30
DEFAULT_BUCKETS = "0.01,1,10"


def _on(raw: object) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``, ignoring case and surrounding space."""
    src = env if env is not None else os.environ
    return _on(src.get(FLAG))


def mainnet_read_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Mainnet log reads stay off unless this is ``1`` or ``true``."""
    src = env if env is not None else os.environ
    return _on(src.get(MAINNET_READ_FLAG))

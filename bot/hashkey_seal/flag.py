"""``HASHKEY_SEAL_ENABLED``. Off unless the value is ``1`` or ``true``.

The public reader is ``flag_on``. A module-level ``enabled`` would share a
name the reachability sweep cannot attribute.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "HASHKEY_SEAL_ENABLED"


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``, ignoring case and surrounding space."""
    src = env if env is not None else os.environ
    raw = src.get(FLAG)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")

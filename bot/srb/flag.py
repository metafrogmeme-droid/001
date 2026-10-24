"""SRB_ENABLED. Off unless the environment says otherwise.

Read on each call. A process that booted with the variable unset stays off,
and a missing value is off — it is not a default of on.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "SRB_ENABLED"
_ON = frozenset({"1", "true"})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when SRB_ENABLED is ``1`` or ``true``.

    ``false``, ``0``, empty and absent are off. The name is ``flag_on``
    because a module-level ``enabled`` is the spelling the method sweep
    already gave up on.
    """
    src = env if env is not None else os.environ
    raw = src.get(FLAG)
    if raw is None:
        return False
    return str(raw).strip().lower() in _ON

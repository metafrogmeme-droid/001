"""T2C_ENABLED. Off unless the value is ``1`` or ``true``.

``bot.config._env_bool`` also treats ``yes`` as on. This flag does not.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


def t2c_flag_on(env: Mapping[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    raw = src.get("T2C_ENABLED")
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")

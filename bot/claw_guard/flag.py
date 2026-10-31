"""CLAW_GUARD_ENABLED. Off unless the value is exactly ``1`` or ``true``.

``yes`` and ``on`` stay off. Unset is off. While the flag is off the check
is admin-only. The trade-only key stays admin-only either way.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "CLAW_GUARD_ENABLED"
_ON = frozenset({"1", "true"})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the check is on for everyone. Unset is off."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip().lower() in _ON

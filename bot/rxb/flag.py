"""RXB_ENABLED. Off unless the value is exactly ``1`` or ``true``.

``on`` and ``yes`` do not count. The function is not named ``enabled``:
a method-ambiguity sweep treats that word as a different question.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

_ON = frozenset({"1", "true"})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    src = os.environ if env is None else env
    raw = src.get("RXB_ENABLED")
    if raw is None:
        return False
    return raw.strip().lower() in _ON

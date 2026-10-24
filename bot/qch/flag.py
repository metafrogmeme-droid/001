"""QCH_ENABLED. Off unless the value is exactly ``1`` or ``true``.

``yes`` and ``on`` stay off. Unset is off. The web door is
``flag_on_for`` in ``app/lib/admin_preview.js``. A caller who is not an
admin is answered 404 while the flag is off and 404 while it is on.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "QCH_ENABLED"
BANNER = "Admin preview. Not public."
_ON = frozenset({"1", "true"})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether QCH_ENABLED is on for everyone. Unset is off."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip().lower() in _ON

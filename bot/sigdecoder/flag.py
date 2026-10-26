"""``SIG_DECODER_ENABLED``. Off unless the value is ``1`` or ``true``.

While the flag is off the doors answer only an operator. ``yes`` and ``on``
stay off. The decoder does not sign, does not read a key, and does not
open a network connection. Lists are the bundled copies.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "SIG_DECODER_ENABLED"


def _on(raw: object) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``, ignoring case and surrounding space."""
    src = env if env is not None else os.environ
    return _on(src.get(FLAG))

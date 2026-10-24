"""Whether HRC is on for the public.

Only the strings ``1`` and ``true`` turn it on. Unset, empty, ``yes``,
``on`` and ``0`` stay off, so a deploy that forgets the variable serves
the public the same 404 as a path this process does not have.

Not named ``enabled``. A module-level function of that name collides with
the method of the same name and drops it out of the ambiguity sweep.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

_ON = frozenset({"1", "true"})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether HRC_ENABLED is on. Anything other than 1 or true is off."""
    src = env if env is not None else os.environ
    return str(src.get("HRC_ENABLED", "")).strip().lower() in _ON

"""Cover flags. Off unless the variable is exactly \"1\" or \"true\".

An empty dict is a clean slate: it is not the process environment. A missing
value is off. The same rule is implemented in ``flags.cjs``; the test that
drives both is what keeps them from becoming two answers.

Not offered to customers. Licence review pending. Do not enable in production.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

ADL_FLAG = "COVER_ADL_ENABLED"
STOPGAP_FLAG = "COVER_STOPGAP_ENABLED"


def flag_on(name: str, env: Mapping[str, str] | None = None) -> bool:
    src: Mapping[str, str] = os.environ if env is None else env
    raw = src.get(name)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def adl_on(env: Mapping[str, str] | None = None) -> bool:
    return flag_on(ADL_FLAG, env)


def stop_gap_on(env: Mapping[str, str] | None = None) -> bool:
    return flag_on(STOPGAP_FLAG, env)

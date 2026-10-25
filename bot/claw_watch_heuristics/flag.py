"""``CLAW_WATCH_HEURISTICS_ENABLED``. Off unless the value is ``1`` or ``true``.

``yes`` and ``on`` stay off. The Etherscan key is read only from the
environment, and only by ``etherscan_key``. Nothing in this module writes
it to a log.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

FLAG = "CLAW_WATCH_HEURISTICS_ENABLED"
INTERNAL_FLAG = "CLAW_WATCH_ETHERSCAN_INTERNAL_ENABLED"
ETHERSCAN_KEY = "CLAW_WATCH_ETHERSCAN_API_KEY"
RPC_URL = "CLAW_WATCH_HEURISTICS_RPC_URL"
CHAIN_ID = "CLAW_WATCH_HEURISTICS_CHAIN_ID"
SALT = "CLAW_WATCH_HEURISTICS_ID_SALT"
STORE = "CLAW_WATCH_HEURISTICS_DB"
MIN_POLL_SEC = "CLAW_WATCH_HEURISTICS_MIN_POLL_SEC"

# One poll is a few reads. Four hours keeps a week of polls under the
# 220-call budget when each poll spends about four calls.
DEFAULT_MIN_POLL_SEC = 4 * 60 * 60
CALLS_PER_WALLET_WEEK = 220
WEEK_SECONDS = 7 * 24 * 60 * 60


def _on(raw: object) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def _src(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    if env is None:
        return os.environ
    return env


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``, ignoring case and surrounding space."""
    return _on(_src(env).get(FLAG))


def internal_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """The internal-transaction read. Off unless this flag is on and a key is set."""
    src = _src(env)
    if not _on(src.get(INTERNAL_FLAG)):
        return False
    return etherscan_key(src) != ""


def etherscan_key(env: Optional[Mapping[str, str]] = None) -> str:
    """The key, or ``""`` when it is unset. Callers must not log the return."""
    return str(_src(env).get(ETHERSCAN_KEY) or "").strip()


def rpc_url(env: Optional[Mapping[str, str]] = None) -> str:
    return str(_src(env).get(RPC_URL) or "").strip()


def configured_chain_id(env: Optional[Mapping[str, str]] = None) -> Optional[int]:
    """The chain the poller may read. Unset is Sepolia. A non-integer is None."""
    raw = str(_src(env).get(CHAIN_ID) or "").strip()
    if raw == "":
        return 11155111
    if not raw.isdigit():
        return None
    return int(raw)


def salt_of(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    raw = str(_src(env).get(SALT) or "").strip()
    if len(raw) < 16:
        return None
    return raw


def min_poll_sec(env: Optional[Mapping[str, str]] = None) -> int:
    raw = str(_src(env).get(MIN_POLL_SEC) or "").strip()
    if raw == "":
        return DEFAULT_MIN_POLL_SEC
    if not raw.isdigit():
        return DEFAULT_MIN_POLL_SEC
    n = int(raw)
    if n < 0:
        return DEFAULT_MIN_POLL_SEC
    return n

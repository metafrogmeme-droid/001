"""HTTP reads for the Hyperliquid info API. The transport is injected in tests.

The info host is testnet unless ``CLAW_WATCH_HL_MAINNET_INFO`` is on. This
module does not sign and does not read a key.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Mapping, Optional

from bot.claw_watch_hl.flag import mainnet_read_on

TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"
MAINNET_INFO = "https://api.hyperliquid.xyz/info"
_MAX_BODY = 8 * 1024 * 1024


class PayloadTooLarge(Exception):
    """The response was larger than the cap. The body is not kept."""

    pass


def info_url(env: Optional[Mapping[str, str]] = None) -> str:
    """Testnet unless the mainnet read flag is on."""
    if mainnet_read_on(env):
        return MAINNET_INFO
    return TESTNET_INFO


def post_info(payload: dict, *, env: Optional[Mapping[str, str]] = None, timeout: float = 20.0,
              url: Optional[str] = None):
    """One POST. No key header. The caller maps a failure to its class name."""
    target = url or info_url(env)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_BODY + 1)
    if len(raw) > _MAX_BODY:
        raise PayloadTooLarge()
    return json.loads(raw.decode("utf-8"))

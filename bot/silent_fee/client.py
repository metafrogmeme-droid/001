"""HTTP reads for Silent Fee Check. The transport is injected in tests.

The info host is testnet unless ``SFC_ALLOW_MAINNET_READ`` is on. The
explorer host is a different, undocumented RPC and is only requested when
the caller asks for it. This module does not sign and does not read a key.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

INFO_URL = {
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
    "mainnet": "https://api.hyperliquid.xyz/info",
}
EXPLORER_URL = "https://rpc.hyperliquid.xyz/explorer"
_MAX_BODY = 8 * 1024 * 1024

Transport = Callable[[str, dict], object]


class PayloadTooLarge(Exception):
    """The response was larger than the cap. The body is not kept."""


def info_url(network: str) -> str:
    if network not in INFO_URL:
        raise ValueError(network)
    return INFO_URL[network]


def explorer_url() -> str:
    return EXPLORER_URL


def urllib_transport(url: str, body: dict, *, timeout: float = 8.0):
    """One POST. No key header. The caller maps a failure to its class name."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_BODY + 1)
    if len(raw) > _MAX_BODY:
        raise PayloadTooLarge()
    return json.loads(raw.decode("utf-8"))

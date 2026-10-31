"""Testnet endpoints and chain ids. Mainnet is refused before a signature.

Hyperliquid testnet only, and the Polymarket paper setup. A mainnet host,
the word Mainnet, or a mainnet chain id is a refusal. The check does not
open a socket.
"""

from __future__ import annotations

from typing import Optional

#: Hyperliquid testnet exchange. The info host is the same origin.
HL_TESTNET_EXCHANGE = "https://api.hyperliquid-testnet.xyz/exchange"
HL_TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"
#: Arbitrum Sepolia, the signature chain Hyperliquid testnet uses.
HL_TESTNET_CHAIN_ID = 421614
#: Polymarket paper. Not a mainnet host. Orders are not posted to Polygon.
PM_PAPER = "paper://polymarket-clob"
#: Polygon Amoy, the public test chain. Polygon mainnet (137) is refused.
PM_TEST_CHAIN_ID = 80002

_ALLOWED_ENDPOINTS = frozenset({
    HL_TESTNET_EXCHANGE,
    HL_TESTNET_INFO,
    PM_PAPER,
})

_ALLOWED_CHAIN_IDS = frozenset({HL_TESTNET_CHAIN_ID, PM_TEST_CHAIN_ID})

#: Hosts and chain ids the refusal test names. None of these are allowed.
_MAINNET_ENDPOINTS = frozenset({
    "https://api.hyperliquid.xyz/exchange",
    "https://api.hyperliquid.xyz/info",
    "https://clob.polymarket.com",
    "https://clob.polymarket.com/order",
})

_MAINNET_CHAIN_IDS = frozenset({1, 137, 42161, 8453, 10, 56, 43114})


def endpoint_refusal(endpoint: object, chain_id: object, venue: object = None) -> Optional[str]:
    """A reason when this target is not the testnet setup. None when it is.

    A missing endpoint is the venue's own testnet default, which is allowed.
    A missing chain id is allowed only together with an allowed endpoint.
    """
    if _is_mainnet_word(chain_id) or _is_mainnet_word(endpoint) or _is_mainnet_word(venue):
        return "mainnet is refused."
    cid = _chain_id(chain_id)
    if cid is not None and cid not in _ALLOWED_CHAIN_IDS:
        return "mainnet is refused."
    if endpoint is None or str(endpoint).strip() == "":
        if cid is None or cid in _ALLOWED_CHAIN_IDS:
            return None
        return "mainnet is refused."
    text = str(endpoint).strip()
    if text in _MAINNET_ENDPOINTS or text not in _ALLOWED_ENDPOINTS:
        return "mainnet is refused."
    return None


def default_endpoint(venue: object) -> str:
    if str(venue or "") == "polymarket":
        return PM_PAPER
    return HL_TESTNET_EXCHANGE


def _is_mainnet_word(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() == "mainnet"


def _chain_id(value: object) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.startswith("0x"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    if text.isdigit():
        return int(text)
    return None

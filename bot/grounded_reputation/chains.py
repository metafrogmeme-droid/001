"""Registries and the USDC allowlist.

Testnets are the default. A mainnet chain id is refused by ``chain_allowed``
unless ``GROUNDED_REPUTATION_ALLOW_MAINNET_READ`` is on. That switch allows a
read. It does not enable a key or a signature.
"""
from __future__ import annotations

from typing import Mapping, Optional

from bot.grounded_reputation.addrs import norm_addr
from bot.grounded_reputation.flag import mainnet_read_on

# Same address on the testnets named in the ERC-8004 registry deploy.
_TEST_IDENTITY = "0x8004A818BFB912233c491871b3d84c89A494BD9e"
_TEST_REPUTATION = "0x8004B663056A597Dffe9eCcC1965A193B7388713"
_MAIN_IDENTITY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
_MAIN_REPUTATION = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

# x402 exact Permit2 proxy. One canonical address.
PERMIT2_PROXY = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001"

# ACP PaymentManager on Base. Attribution of this payee is not in v0.1.
ACP_PAYMENT_MANAGER = "0xEF4364Fe4487353dF46eb7c811D4FAc78b856c7F"
ACP_CHAIN_ID = 8453

BASE_SEPOLIA = 84532
BASE = 8453
ETH_SEPOLIA = 11155111
ETHEREUM = 1

# USDC, 6 decimals. No other token is accepted.
_USDC: dict[int, dict[str, int]] = {
    BASE_SEPOLIA: {"0x036cbd53842c5426634e7929541ec2318f3dcf7e": 6},
    ETH_SEPOLIA: {"0x1c7d4b196cb0c7b01d743fbc6116a902379c7238": 6},
    BASE: {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6},
    ETHEREUM: {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6},
}

_CHAINS: dict[int, dict[str, object]] = {
    BASE_SEPOLIA: {
        "identity": _TEST_IDENTITY,
        "reputation": _TEST_REPUTATION,
        "mainnet": False,
        "name": "base-sepolia",
    },
    ETH_SEPOLIA: {
        "identity": _TEST_IDENTITY,
        "reputation": _TEST_REPUTATION,
        "mainnet": False,
        "name": "ethereum-sepolia",
    },
    BASE: {
        "identity": _MAIN_IDENTITY,
        "reputation": _MAIN_REPUTATION,
        "mainnet": True,
        "name": "base",
    },
    ETHEREUM: {
        "identity": _MAIN_IDENTITY,
        "reputation": _MAIN_REPUTATION,
        "mainnet": True,
        "name": "ethereum",
    },
}


def chain_row(chain_id: int) -> Optional[dict[str, object]]:
    row = _CHAINS.get(chain_id)
    return dict(row) if row is not None else None


def chain_allowed(chain_id: object, env: Optional[Mapping[str, str]] = None) -> bool:
    """Testnets are allowed. Mainnet needs the read switch. Unknown chains are not."""
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return False
    row = _CHAINS.get(chain_id)
    if row is None:
        return False
    if row["mainnet"] is True and mainnet_read_on(env) is not True:
        return False
    return True


def usdc_token(chain_id: int) -> Optional[str]:
    """The allowlisted USDC address on ``chain_id``, or None."""
    book = _USDC.get(chain_id)
    if not book:
        return None
    return next(iter(book))


def token_decimals(chain_id: int, token: object) -> Optional[int]:
    """Decimals for an allowlisted token, or None when the token is not allowed."""
    book = _USDC.get(chain_id)
    if book is None:
        return None
    key = norm_addr(token)
    if key is None:
        return None
    return book.get(key)


def default_escrows() -> list[dict[str, object]]:
    """The one seeded escrow. v0.1 does not attribute payments into it."""
    return [{
        "chain_id": ACP_CHAIN_ID,
        "address": norm_addr(ACP_PAYMENT_MANAGER),
        "protocol": "acp",
        "attribution_method": "unattributed",
    }]

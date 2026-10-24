"""Test networks the bridge will publish to, and the mainnets it will not.

No deploy script lives here. A chain id on the deny list is refused before
any signature is produced. HyperEVM testnet is recorded as chain id 998,
which is the id published for that network; confirm it against the RPC
before a keeper points at one. That confirmation is an open question, not
a default this module invents a second time.
"""
from __future__ import annotations

from dataclasses import dataclass


class MainnetRefused(Exception):
    """A mainnet chain id or cluster was handed to a signer or a keeper."""


# Published mainnet ids. Present so a signer can refuse them. Not targets.
# Base and Arbitrum are refused and are not lending targets: tokenized-stock
# lending for this bridge is on Ethereum and on Robinhood Chain.
MAINNET_CHAIN_IDS = frozenset({
    1,        # Ethereum
    8453,     # Base (not a target)
    42161,    # Arbitrum One (not a target)
    999,      # HyperEVM
    4663,     # Robinhood Chain
})

MAINNET_CLUSTERS = frozenset({"mainnet", "mainnet-beta"})


@dataclass(frozen=True)
class TestNetwork:
    chain: str
    network: str
    role: str
    chain_id: int | None = None
    cluster: str | None = None


# Robinhood Chain testnet is chain id 46630, from Robinhood's connecting
# doc (mainnet is 4663 and is on the deny list). No market address is
# recorded here. Ethereum Sepolia is 11155111. Morpho and Euler adapters
# are aimed at that testnet; Robinhood Chain's adapter is Morpho only.
TEST_NETWORKS: tuple[TestNetwork, ...] = (
    TestNetwork("hyperliquid", "HyperEVM testnet", "HIP-3 risk price", chain_id=998),
    TestNetwork("solana", "devnet", "Kamino xStocks", cluster="devnet"),
    TestNetwork("ethereum", "Sepolia", "Morpho and Euler adapters", chain_id=11155111),
    TestNetwork("robinhood", "Robinhood Chain testnet", "Morpho adapter", chain_id=46630),
)


def assert_testnet_chain(chain_id: int) -> None:
    if int(chain_id) in MAINNET_CHAIN_IDS:
        raise MainnetRefused(int(chain_id))


def assert_testnet_cluster(cluster: str) -> None:
    if str(cluster).strip().lower() in MAINNET_CLUSTERS:
        raise MainnetRefused(str(cluster))


def public_chains() -> list[dict[str, str]]:
    """Chain coverage for a public payload. Names and roles, no addresses."""
    out = []
    for net in TEST_NETWORKS:
        out.append({"chain": net.chain, "network": net.network, "role": net.role})
    return out

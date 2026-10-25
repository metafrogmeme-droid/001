"""Which chain ids this watch may read.

The ids come from ``bot.web.web3_exec_gate.NETWORKS`` plus the two local
fork ids Anvil and Hardhat use. A mainnet id is refused, including a fork
that kept chain id 1. This module does not open a socket and does not
read the public RPC URLs in that table.
"""

from __future__ import annotations

from typing import Optional

from bot.web.web3_exec_gate import NETWORKS

# Local forks. They are not rows in the execution-gate table.
FORK_CHAIN_IDS = frozenset({31337, 1337})

def _by_id() -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in NETWORKS.values():
        if not isinstance(row, dict):
            continue
        cid = row.get("chain_id")
        if isinstance(cid, bool) or not isinstance(cid, int):
            continue
        out[cid] = row
    return out


_BY_ID = _by_id()
TESTNET_CHAIN_IDS = frozenset(
    cid for cid, row in _BY_ID.items() if row.get("testnet") is True
)
MAINNET_CHAIN_IDS = frozenset(
    cid for cid, row in _BY_ID.items() if row.get("testnet") is not True
)


def chain_allowed(chain_id: object) -> bool:
    """True for a known testnet or a local fork. Anything else is not."""
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return False
    if chain_id in MAINNET_CHAIN_IDS:
        return False
    if chain_id in FORK_CHAIN_IDS:
        return True
    return chain_id in TESTNET_CHAIN_IDS


def chain_label(chain_id: int) -> Optional[str]:
    """The table's label, or None when this id is not one we read."""
    if not chain_allowed(chain_id):
        return None
    if chain_id in FORK_CHAIN_IDS:
        return "local fork"
    row = _BY_ID.get(chain_id)
    if row is None:
        return None
    label = row.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    return label


def internal_chain(chain_id: object) -> bool:
    """Etherscan internal txs are for a public testnet, not a local fork."""
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return False
    return chain_id in TESTNET_CHAIN_IDS

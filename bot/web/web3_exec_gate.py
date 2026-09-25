"""Web3 live-execution gate — the ONE decision for 'may this action touch chain'.

On-chain execution is built in gated slices. A TESTNET-ONLY signer ships
(``bot/web/web3_signer``: native-value transfers and contract deploys, signed
with the operator's key through the audited eth-account library); there are
still no swap/bridge/stake adapters and no mainnet signing. THIS gate is the
safety spine every slice runs through, from the preview to the signer.

This module's own entry point (``evaluate``) decides the DRY-RUN PREVIEW — it
never signs or broadcasts; the signer runs its own, stricter gate
(``web3_signer.evaluate_sign``) over the same switches. The preconditions:

    1. feature_enabled     — operator master switch (env WEB3_LIVE_EXEC_ENABLED,
                             default ON, testnet-only). Set =0 to hard-disable.
                             Being ON opens nothing on mainnet (see check 3).
    2. is_admin            — admin-only for now. On-chain execution is not opened
                             to general users in this slice.
    3. network_ok          — the target network is known AND, unless the operator
                             explicitly allowed mainnet, it is a TESTNET (real
                             live testing starts on testnet — zero mainnet risk).
    4. not_broadcast_yet   — the preview never sends; a real send is refused
                             here and goes through the signer's own gate.
    5. envelope_enforcing  — a bound Authority Envelope in ENFORCE mode caps and
                             authorizes the action (notional, asset, destination)
                             and is revocable. No on-chain action — preview or
                             (future) live — exists outside one.

All must hold. FAILS CLOSED: any missing/unknown input → deny, naming the first
unmet precondition. Pure and deterministic; the gateway sources the inputs, runs
the Authority Envelope authorize() separately, and wires the result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# Known EVM networks and whether each is a testnet. Live testing starts here.
# Every testnet below is EIP-1559 (type-2) capable — the signer builds type-2
# transactions and the prepare step falls back to gasPrice, so a chain that
# doesn't surface a base fee still works. RPC per chain: WEB3_RPC_<NETWORK>.
#
# min_tx_gas: the chain's PROTOCOL intrinsic-gas floor for a bare value transfer
# — 21,000 on Ethereum-metered chains. It is a floor the RPC enforces below
# which a transaction is rejected outright ("intrinsic gas too low"). It is NOT
# proof that a given transaction's gas is sufficient to execute: a transfer to a
# contract needs more, and this table cannot know that. Treat it as the lower
# bound it is. Every row must declare one — a row without it denies rather than
# inheriting Ethereum's, because a substituted 21,000 would be a fabricated
# number on a chain that meters differently.
#
# native: the chain's gas/value coin, which is what a native-value transfer
# moves and so what the signer prices it in (``bot/web/onchain_value``). Every
# row declares one: a transfer on a chain whose coin is unnamed cannot be
# priced, and an unpriced transfer is refused rather than treated as $0.
#
# explorer: stored WITHOUT a trailing slash. Both link builders below rstrip it,
# but the convention keeps the table readable and matches chainid.network.
NETWORKS = {
    "sepolia": {"chain_id": 11155111, "testnet": True, "label": "Ethereum Sepolia",
                "explorer": "https://sepolia.etherscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "base-sepolia": {"chain_id": 84532, "testnet": True, "label": "Base Sepolia",
                     "explorer": "https://sepolia.basescan.org", "min_tx_gas": 21000, "native": "ETH"},
    "arbitrum-sepolia": {"chain_id": 421614, "testnet": True, "label": "Arbitrum Sepolia",
                         "explorer": "https://sepolia.arbiscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "optimism-sepolia": {"chain_id": 11155420, "testnet": True, "label": "Optimism Sepolia",
                         "explorer": "https://sepolia-optimism.etherscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "polygon-amoy": {"chain_id": 80002, "testnet": True, "label": "Polygon Amoy",
                     "explorer": "https://amoy.polygonscan.com", "min_tx_gas": 21000, "native": "POL"},
    "avalanche-fuji": {"chain_id": 43113, "testnet": True, "label": "Avalanche Fuji",
                       "explorer": "https://testnet.snowtrace.io", "min_tx_gas": 21000, "native": "AVAX"},
    "scroll-sepolia": {"chain_id": 534351, "testnet": True, "label": "Scroll Sepolia",
                       "explorer": "https://sepolia.scrollscan.com", "min_tx_gas": 21000, "native": "ETH"},
    "linea-sepolia": {"chain_id": 59141, "testnet": True, "label": "Linea Sepolia",
                      "explorer": "https://sepolia.lineascan.build", "min_tx_gas": 21000, "native": "ETH"},
    "blast-sepolia": {"chain_id": 168587773, "testnet": True, "label": "Blast Sepolia",
                      "explorer": "https://sepolia.blastscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "bsc-testnet": {"chain_id": 97, "testnet": True, "label": "BNB Smart Chain Testnet",
                    "explorer": "https://testnet.bscscan.com", "min_tx_gas": 21000, "native": "BNB"},
    # MegaETH meters compute gas AND storage gas against the SINGLE gasLimit
    # field: a bare value transfer is 21,000 compute + 39,000 storage = 60,000.
    # The RPC rejects anything under that, so min_tx_gas here is NOT 21,000.
    # Its RPC host is "carrot", not "testnet.megaeth.com" — see .env.example.
    "megaeth-testnet": {"chain_id": 6343, "testnet": True, "label": "MegaETH Testnet",
                        "explorer": "https://testnet-mega.etherscan.io", "min_tx_gas": 60000, "native": "ETH"},
    "ethereum": {"chain_id": 1, "testnet": False, "label": "Ethereum",
                 "explorer": "https://etherscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "base": {"chain_id": 8453, "testnet": False, "label": "Base",
             "explorer": "https://basescan.org", "min_tx_gas": 21000, "native": "ETH"},
    "arbitrum": {"chain_id": 42161, "testnet": False, "label": "Arbitrum",
                 "explorer": "https://arbiscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "optimism": {"chain_id": 10, "testnet": False, "label": "Optimism",
                 "explorer": "https://optimistic.etherscan.io", "min_tx_gas": 21000, "native": "ETH"},
    "polygon": {"chain_id": 137, "testnet": False, "label": "Polygon",
                "explorer": "https://polygonscan.com", "min_tx_gas": 21000, "native": "POL"},
    # Mainnet — present so it is refused EXPLICITLY (a known chain behind the
    # mainnet flag) rather than incidentally (an unknown name, indistinguishable
    # from a typo). Blockscout over mega.etherscan.io: the Etherscan-branded
    # MegaETH hosts 403 every non-browser client, so a link check there cannot
    # tell "bot-blocked" from "misconfigured".
    "megaeth": {"chain_id": 4326, "testnet": False, "label": "MegaETH",
                "explorer": "https://megaeth.blockscout.com", "min_tx_gas": 60000, "native": "ETH"},
}


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _is_hex_of_len(value: str, total_len: int) -> bool:
    """A 0x-prefixed string of EXACTLY ``total_len`` characters whose body is all
    hex. The length is pinned, not a minimum: an over-long or under-long value is
    as malformed as a non-hex one, and both must be refused before they can be
    concatenated into a URL."""
    return (len(value) == total_len and value.startswith("0x")
            and all(c in _HEX_DIGITS for c in value[2:]))


def explorer_tx_url(network: str, tx_hash: str) -> str:
    """Block-explorer URL for a tx on a known network, or "" when unknown.

    The hash must be a 0x-prefixed 32-byte hex string (66 chars). An earlier
    version checked only the 0x prefix and a length of >= 6, which the docstring
    described as full hex validation — it was not: "0x../../foo" passed and
    produced a link a browser normalises to the explorer root. The check now
    matches what this docstring claims."""
    net = resolve_network(network)
    base = (net or {}).get("explorer") if net else ""
    h = str(tx_hash or "").strip()
    if not base or not _is_hex_of_len(h, 66):
        return ""
    return f"{base.rstrip('/')}/tx/{h}"


def explorer_address_url(network: str, address: str) -> str:
    """Block-explorer URL for an ADDRESS (e.g. a freshly-deployed contract) on a
    known network, or "" when unknown/malformed.

    Same real hex validation as the tx link, at the ADDRESS length (42 chars, 20
    bytes) — so a tx hash passed here by mistake is refused rather than silently
    building a link to an address that does not exist."""
    net = resolve_network(network)
    base = (net or {}).get("explorer") if net else ""
    a = str(address or "").strip()
    if not base or not _is_hex_of_len(a, 42):
        return ""
    return f"{base.rstrip('/')}/address/{a}"


_CHECKS = (
    ("feature_enabled",
     "on-chain execution is not enabled — the operator set WEB3_LIVE_EXEC_ENABLED "
     "off (it defaults ON, testnet-only)"),
    ("is_admin", "on-chain execution is admin-only in this phase"),
    ("network_ok",
     "target a supported testnet (real live testing starts on testnet; mainnet "
     "is off unless the operator explicitly allows it)"),
    ("not_broadcast",
     "the execution preview never broadcasts — signing and broadcast go through "
     "the separate, testnet-only signer"),
    ("envelope_enforcing",
     "bind an Authority Envelope in enforce mode — it caps and authorizes every "
     "on-chain action and is revocable at any time"),
)


@dataclass(frozen=True)
class Web3ExecDecision:
    allowed: bool
    reason: str
    checklist: dict = field(default_factory=dict)
    network: Optional[dict] = None


def feature_enabled(env: Optional[dict] = None) -> bool:
    """Operator master switch for the on-chain feature. Default ON — the platform
    ships with testnet execution active so live testnet testing works out of the
    box. This is SAFE because it opens nothing on mainnet: the feature is
    testnet-only unless WEB3_LIVE_EXEC_ALLOW_MAINNET is separately set (default
    OFF), signing needs its own switch + operator key + eth-account, and every
    action runs through an enforce-mode Authority Envelope. Explicit off wins:
    set WEB3_LIVE_EXEC_ENABLED=0 to hard-disable."""
    e = env if env is not None else os.environ
    raw = str(e.get("WEB3_LIVE_EXEC_ENABLED", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def mainnet_allowed(env: Optional[dict] = None) -> bool:
    """Separate, explicit opt-in to leave testnet. Default OFF — testnet-first.
    Turning the feature/signing switches ON never changes this: mainnet stays
    refused unless this is deliberately set."""
    # An explicit `{}` is "nothing set" — `env or os.environ` read it as the
    # process environment, so a caller asking about a clean slate was answered
    # with whatever this box exported.
    e = env if env is not None else os.environ
    raw = e.get("WEB3_LIVE_EXEC_ALLOW_MAINNET", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def resolve_network(name: str):
    return NETWORKS.get(str(name or "").strip().lower())


def evaluate(*, is_admin: bool, network: str, envelope_enforcing: bool,
             broadcast: bool = False, env: Optional[dict] = None) -> Web3ExecDecision:
    """Decide whether an on-chain action may proceed to (this slice: a preview).
    Fail-closed. ``broadcast=True`` is refused in this preview-only slice."""
    net = resolve_network(network)
    network_ok = bool(net) and (net["testnet"] or mainnet_allowed(env))
    state = {
        "feature_enabled": feature_enabled(env),
        "is_admin": bool(is_admin),
        "network_ok": network_ok,
        # Preview-only invariant: a real broadcast is never allowed here.
        "not_broadcast": not bool(broadcast),
        "envelope_enforcing": bool(envelope_enforcing),
    }
    for key, reason in _CHECKS:
        if not state[key]:
            return Web3ExecDecision(allowed=False, reason=reason, checklist=state,
                                    network=net)
    return Web3ExecDecision(allowed=True, reason="all preconditions met",
                            checklist=state, network=net)

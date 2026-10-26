"""Refuse mainnet signing and broadcast for any process that is the server.

On mainnet the server never holds a key and never signs. Testnet signing
stays available: a testnet chain id with a testnet key is not this refusal.

A preview (``web3_exec_gate.evaluate``) is a different door. It never signs,
and this module does not change what a preview is allowed to show.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional

from bot.web import web3_exec_gate as gate

#: Set on every server start, including a start that is allowed. A child
#: process inherits it and the operator broadcast script refuses to sign.
SERVER_MARKER = "RUNECLAW_SERVER"

#: Keys the server may hold for a TESTNET signer. ``ETH_PRIVATE_KEY`` is not
#: here: that name exists so an operator's own ``cast`` can sign Base
#: mainnet, and a server process that has it is refused outright.
_TESTNET_CAPABLE_KEYS = (
    "WEB3_SIGNER_PRIVATE_KEY",
    "HYPERLIQUID_PRIVATE_KEY",
    "SAR_ORACLE_SIGNING_KEY",
    "HRC_FEED_SIGNER_KEY",
    # HashKey seal publisher. Testnet chain 133 only; see _hashkey_seal_refusal.
    "HASHKEY_SEAL_SIGNER_KEY",
)

#: ``_env_bool`` treats only these as True. ``on`` is unrecognised and falls
#: back to the default, which for ``HYPERLIQUID_TESTNET`` is False, so the
#: venue would still be mainnet. The guard matches that vocabulary.
_ENV_BOOL_TRUE = frozenset({"true", "1", "yes"})


def _row_chain_id(row: Mapping[str, object]) -> Optional[int]:
    """The row's chain id when it is an int. A bool is not one: ``True`` is 1."""
    value = row.get("chain_id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def mainnet_chain_ids() -> frozenset[int]:
    """Chain ids that are not a testnet: the web3 network table's mainnet
    rows plus HashKey mainnet from contracts/hashkey/networks.json. Two
    tables, one reading here, so the generic refusal in startup_refusal
    knows every mainnet id the repository names."""
    found: list[int] = []
    for row in gate.NETWORKS.values():
        cid = _row_chain_id(row)
        if cid is not None and not row.get("testnet"):
            found.append(cid)
    try:
        from bot.hashkey_seal.networks import mainnet_chain_id
        found.append(int(mainnet_chain_id()))
    except Exception:
        # An unreadable HashKey file is reported by _hashkey_seal_refusal
        # when the HashKey key is set; the generic reading stays what the
        # web3 table says rather than guessing an id.
        pass
    return frozenset(found)


def testnet_chain_ids() -> frozenset[int]:
    """Chain ids the network table marks as a testnet. One reading."""
    found: list[int] = []
    for row in gate.NETWORKS.values():
        cid = _row_chain_id(row)
        if cid is not None and row.get("testnet"):
            found.append(cid)
    return frozenset(found)


def execution_refused(chain_id: object) -> Optional[str]:
    """None when this chain id may be signed or broadcast. A reason otherwise.

    Unreadable, a bool, and any id that is not a known testnet are refused.
    A bool is refused before ``int``, because ``int(True)`` is 1, which is
    Ethereum mainnet.
    """
    if isinstance(chain_id, bool) or chain_id is None:
        return "chain id is unreadable"
    if isinstance(chain_id, int):
        cid = chain_id
    else:
        text = str(chain_id).strip()
        if not text or not _integer_text(text):
            return "chain id is unreadable"
        try:
            cid = int(text)
        except (TypeError, ValueError):
            return "chain id is unreadable"
    if cid in testnet_chain_ids():
        return None
    return "mainnet signing and broadcast are refused"


def startup_refusal(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why this process must not start, or None when it may.

    An explicit mapping is the whole environment. ``{}`` is nothing set,
    not the process environment.
    """
    e: Mapping[str, str] = os.environ if env is None else env
    # mainnet_allowed reads a dict. None means "the process environment",
    # which is the same reading as ``e`` when the caller passed no mapping.
    allowed_env: Optional[dict] = None if env is None else {
        str(key): str(value) for key, value in env.items()
    }
    if gate.mainnet_allowed(allowed_env):
        return (
            "WEB3_LIVE_EXEC_ALLOW_MAINNET is set; the server does not start "
            "with mainnet execution allowed"
        )
    if _nonempty(e, "ETH_PRIVATE_KEY"):
        return (
            "ETH_PRIVATE_KEY is set; that key signs Base mainnet and the "
            "server does not hold it"
        )
    if _nonempty(e, "HYPERLIQUID_PRIVATE_KEY") and not _hyperliquid_testnet(e):
        return (
            "HYPERLIQUID_PRIVATE_KEY is set and HYPERLIQUID_TESTNET is not; "
            "the server does not sign Hyperliquid mainnet"
        )
    signing = [name for name in _TESTNET_CAPABLE_KEYS if _nonempty(e, name)]
    if not signing:
        return None
    mainnet = mainnet_chain_ids()
    for name, value in e.items():
        if not _is_chain_id_var(str(name)):
            continue
        cid = _parse_chain_id(value)
        if cid is not None and cid in mainnet:
            return (
                f"{name} is mainnet chain {cid} and a signing key is set "
                f"({signing[0]}); the server does not start"
            )
    hashkey = _hashkey_seal_refusal(e)
    if hashkey:
        return hashkey
    return None


def _hashkey_seal_refusal(env: Mapping) -> Optional[str]:
    """The HashKey seal key is for the testnet id in networks.json and no other.

    An unset chain id is not treated as a chain: the publisher asks the
    endpoint and refuses any answer other than that testnet id before it
    signs. A chain id that is set and is not that id refuses startup, so
    the process does not hold the key aimed at another chain.
    """
    if not _nonempty(env, "HASHKEY_SEAL_SIGNER_KEY"):
        return None
    raw = env.get("HASHKEY_SEAL_CHAIN_ID")
    if raw is None or str(raw).strip() == "":
        return None
    cid = _parse_chain_id(raw)
    try:
        from bot.hashkey_seal.networks import testnet_chain_id
        expected = testnet_chain_id()
    except Exception:
        return (
            "HASHKEY_SEAL_SIGNER_KEY is set and the HashKey network file "
            "could not be read; the server does not start"
        )
    if cid == expected:
        return None
    return (
        f"HASHKEY_SEAL_CHAIN_ID is not HashKey testnet {expected} and "
        "HASHKEY_SEAL_SIGNER_KEY is set; the server does not sign"
    )


def refuse_server_start(env: Optional[Mapping[str, str]] = None) -> None:
    """Mark this process as the server, then exit if startup is refused.

    Called with no arguments from the real entry points, so the decision
    is the process environment. A caller may pass a mapping to drive the
    decision without reading this box's environment; the marker is still
    set on the process, because the process is the server either way.
    """
    os.environ[SERVER_MARKER] = "1"
    reason = startup_refusal(os.environ if env is None else env)
    if reason:
        print(f"ERROR: {reason}", file=sys.stderr)
        raise SystemExit(1)


def _integer_text(text: str) -> bool:
    body = text[1:] if text[0] in "+-" else text
    return bool(body) and body.isdigit()


def _nonempty(env: Mapping, name: str) -> bool:
    return bool(str(env.get(name, "") or "").strip())


def _hyperliquid_testnet(env: Mapping) -> bool:
    raw = str(env.get("HYPERLIQUID_TESTNET", "") or "").strip().lower()
    return raw in _ENV_BOOL_TRUE


def _is_chain_id_var(name: str) -> bool:
    """A variable whose value is a chain id, not a feature flag.

    ``ONCHAIN_ENABLED=1`` contains the letters CHAIN and the digit 1.
    Reading that as Ethereum mainnet would refuse a start that is not
    signing anything. Only a name that is a chain id is read as one.
    """
    upper = name.upper()
    return upper == "CHAIN_ID" or upper.endswith("_CHAIN_ID")


def _parse_chain_id(value: object) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)

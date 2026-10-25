"""HashKey Chain endpoints. One file, read by the publisher and the guard.

``contracts/hashkey/networks.json`` is the reading. Chain ids in the
contract deploy script are checked against it by a test.
"""
from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "contracts" / "hashkey" / "networks.json"


def table() -> dict:
    """The network file. A failed read raises; it is not an empty table."""
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("network file is not an object")
    return raw


def testnet_chain_id() -> int:
    return int(table()["testnet"]["chain_id"])


def mainnet_chain_id() -> int:
    return int(table()["mainnet"]["chain_id"])


def testnet_rpc() -> str:
    return str(table()["testnet"]["rpc"])

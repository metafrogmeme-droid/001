"""Day-1 record of CoreWriter action 15 on HyperEVM testnet.

Action 15 is Borrow/Lend: operation 0 supplies, operation 1 withdraws.
The record in benchmark/hrc/corewriter_action15.json is what an eth_call
did on 2026-10-23. Settlement does not read it. KinkCapMarket does not
call CoreWriter. Seller collateral stays in the market.

An eth_call that returns empty bytes means the precompile accepted the
encoding. HyperCore can still drop the action after the EVM block. This
module does not send a transaction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

RECORD = Path(__file__).resolve().parents[2] / "benchmark" / "hrc" / "corewriter_action15.json"
COREWRITER = "0x3333333333333333333333333333333333333333"
ACTION_ID = 15


def encode_action(operation: int, token: int, wei: int) -> Optional[bytes]:
    """Version 1, action 15, abi.encode(uint8, uint64, uint64).

    ``wei`` of 0 would mean a maximal supply or withdraw. Callers that are
    only checking the encoding pass a positive amount. None when an input
    is not an integer in range.
    """
    if type(operation) is not int or operation not in (0, 1):
        return None
    if type(token) is not int or token < 0 or token >= 2**64:
        return None
    if type(wei) is not int or wei <= 0 or wei >= 2**64:
        return None
    head = bytes([1, 0, 0, ACTION_ID])
    body = operation.to_bytes(32, "big") + token.to_bytes(32, "big") + wei.to_bytes(32, "big")
    return head + body


def load_record(path: Optional[Path] = None) -> dict:
    """The committed result. A missing file is unreadable, not a failure of the action."""
    target = path or RECORD
    if not target.is_file():
        return {"readable": False, "reason": "no_record", "depended_on": False}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"readable": False, "reason": "unreadable", "depended_on": False}
    if not isinstance(payload, dict):
        return {"readable": False, "reason": "unreadable", "depended_on": False}
    payload["depended_on"] = False
    return payload

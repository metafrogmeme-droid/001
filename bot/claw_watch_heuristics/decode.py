"""Turn receipts into the dicts the scorer reads. No network.

A missing field stays missing. ``0`` is a real zero. A topic that is not
an address is left out of that log rather than coerced.
"""

from __future__ import annotations

from typing import Optional

TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
TOPIC_APPROVAL_ALL = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
TOPIC_PERMIT2_PERMIT = "0xc6a377bfc4eb120024a8ac08eef205be16b817020812c73223e81d1bdb9708ec"
TOPIC_PERMIT2_APPROVAL = "0xda9fa7c1b00402c17d0161b249b1ab8bbec047c5a52207b9c112deffd817036b"

ZERO = "0x" + "0" * 40
_HEX = frozenset("0123456789abcdef")


def address_of(value: object) -> Optional[str]:
    """A 20-byte hex address, or None. The zero address is an address."""
    if not isinstance(value, str):
        return None
    raw = value.strip().lower().removeprefix("0x")
    if len(raw) < 40:
        return None
    body = raw[-40:]
    if len(body) != 40 or any(c not in _HEX for c in body):
        return None
    return "0x" + body


def hex_int(value: object) -> Optional[int]:
    """A non-negative integer, or None when the text is not one.

    A bool is not an integer here: ``True`` would become ``1``.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text == "" or text == "0x":
        return 0
    try:
        if text.startswith("0x"):
            return int(text, 16)
        if text.isdigit():
            return int(text, 10)
    except ValueError:
        return None
    return None


def _words(data: object) -> list[int]:
    if not isinstance(data, str):
        return []
    body = data.strip().lower().removeprefix("0x")
    if body == "":
        return []
    if len(body) % 64 != 0 or any(c not in _HEX for c in body):
        return []
    return [int(body[i:i + 64], 16) for i in range(0, len(body), 64)]


def _topic_addr(topic: object) -> Optional[str]:
    if not isinstance(topic, str):
        return None
    return address_of(topic)


def decode_log(log: dict, *, permit2: str) -> dict:
    """One log. Unknown topics produce an empty contribution, not a guess."""
    topics = log.get("topics") if isinstance(log, dict) else None
    if not isinstance(topics, list) or not topics:
        return {}
    topic0 = str(topics[0]).lower()
    token = address_of(log.get("address"))
    data = log.get("data")
    if topic0 == TOPIC_TRANSFER and len(topics) in (3, 4) and token:
        frm = _topic_addr(topics[1])
        to = _topic_addr(topics[2])
        if frm is None or to is None:
            return {}
        if len(topics) == 4:
            return {"transfer": {
                "token": token, "from": frm, "to": to, "value": 1, "nft": True,
            }}
        value = hex_int(data)
        if value is None:
            return {"transfer_unreadable": True}
        return {"transfer": {
            "token": token, "from": frm, "to": to, "value": value, "nft": False,
        }}
    if topic0 == TOPIC_APPROVAL and len(topics) == 3 and token:
        owner = _topic_addr(topics[1])
        spender = _topic_addr(topics[2])
        words = _words(data)
        if owner is None or spender is None or not words:
            return {}
        return {"approval": {
            "token": token, "owner": owner, "spender": spender, "value": words[0],
        }}
    if topic0 == TOPIC_APPROVAL_ALL and len(topics) == 3 and token:
        owner = _topic_addr(topics[1])
        operator = _topic_addr(topics[2])
        words = _words(data)
        if owner is None or operator is None or not words:
            return {}
        return {"approval_for_all": {
            "token": token, "owner": owner, "operator": operator,
            "approved": words[0] == 1,
        }}
    if token == permit2 and topic0 in (TOPIC_PERMIT2_PERMIT, TOPIC_PERMIT2_APPROVAL) and len(topics) == 4:
        owner = _topic_addr(topics[1])
        tok = _topic_addr(topics[2])
        spender = _topic_addr(topics[3])
        words = _words(data)
        if owner is None or tok is None or spender is None or len(words) < 1:
            return {}
        kind = "Permit" if topic0 == TOPIC_PERMIT2_PERMIT else "Approval"
        return {"permit2": {
            "kind": kind, "owner": owner, "token": tok, "spender": spender,
            "amount": words[0],
        }}
    return {}


def decode_tx(raw: dict, *, permit2: str) -> dict:
    """One transaction. ``from`` missing is None, not the zero address."""
    tx_from = address_of(raw.get("from")) if "from" in raw and raw.get("from") is not None else None
    if "from" in raw and raw.get("from") is not None and tx_from is None:
        tx_from_status = "unreadable"
    elif tx_from is None:
        tx_from_status = "absent"
    else:
        tx_from_status = "read"
    tx_to = address_of(raw.get("to")) or ""
    data = raw.get("input") if isinstance(raw.get("input"), str) else "0x"
    selector = data[:10].lower() if isinstance(data, str) and data.startswith("0x") and len(data) >= 10 else "0x"
    block = hex_int(raw.get("block"))
    tx_hash = raw.get("hash") if isinstance(raw.get("hash"), str) else ""
    transfers: list[dict] = []
    approvals: list[dict] = []
    permit2_rows: list[dict] = []
    afa: list[dict] = []
    transfer_gap = False
    logs_raw = raw.get("logs")
    logs = logs_raw if isinstance(logs_raw, list) else []
    for log in logs:
        if not isinstance(log, dict):
            transfer_gap = True
            continue
        piece = decode_log(log, permit2=permit2)
        if piece.get("transfer_unreadable"):
            transfer_gap = True
        if "transfer" in piece:
            transfers.append(piece["transfer"])
        if "approval" in piece:
            approvals.append(piece["approval"])
        if "permit2" in piece:
            permit2_rows.append(piece["permit2"])
        if "approval_for_all" in piece:
            afa.append(piece["approval_for_all"])
    auths = []
    for row in raw.get("authorizations") or []:
        if not isinstance(row, dict):
            continue
        delegate = address_of(row.get("address") or row.get("delegate"))
        if delegate is None:
            continue
        authority = address_of(row.get("authority")) if row.get("authority") is not None else None
        auths.append({"delegate": delegate, "authority": authority})
    value = hex_int(raw.get("value"))
    return {
        "hash": tx_hash.lower() if isinstance(tx_hash, str) else "",
        "from": tx_from,
        "from_status": tx_from_status,
        "to": tx_to,
        "selector": selector,
        "block": block,
        "value": value,
        "transfers": transfers,
        "approvals": approvals,
        "permit2": permit2_rows,
        "approval_for_all": afa,
        "authorizations": auths,
        "transfer_gap": transfer_gap,
    }


def decode_window(raw: dict) -> dict:
    """A watched address and the transactions already fetched for it."""
    from bot.claw_watch_heuristics.allowlist import PERMIT2

    address = address_of(raw.get("address"))
    chain_id = raw.get("chain_id")
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        chain_id = None
    txs_raw = raw.get("transactions")
    txs_in = txs_raw if isinstance(txs_raw, list) else []
    txs = [decode_tx(tx, permit2=PERMIT2) for tx in txs_in if isinstance(tx, dict)]
    code = raw.get("code") if isinstance(raw.get("code"), str) else None
    code_status = raw.get("code_status") if raw.get("code_status") in ("read", "unreadable", "absent") else (
        "read" if isinstance(code, str) else "absent"
    )
    internal = raw.get("internal") if isinstance(raw.get("internal"), dict) else {"status": "off"}
    codes: dict[str, dict] = {}
    codes_raw = raw.get("codes")
    raw_codes = codes_raw if isinstance(codes_raw, dict) else {}
    for key, val in raw_codes.items():
        addr = address_of(key) if isinstance(key, str) else None
        if addr is None:
            continue
        if isinstance(val, str):
            codes[addr] = {"code": val.lower(), "status": "read"}
        elif isinstance(val, dict):
            status = val.get("status") if val.get("status") in ("read", "unreadable", "absent") else "unreadable"
            body = val.get("code")
            codes[addr] = {
                "code": body.lower() if isinstance(body, str) else None,
                "status": status,
            }
    return {
        "address": address,
        "chain_id": chain_id,
        "transactions": txs,
        "code": code.lower() if isinstance(code, str) else None,
        "code_status": code_status,
        "internal": internal,
        "codes": codes,
    }

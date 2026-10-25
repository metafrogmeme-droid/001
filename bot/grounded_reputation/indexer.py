"""Read registry logs. Nothing is signed and no key is read.

``eth_getLogs`` is the only RPC method used. A failed read is None, not an
empty log list. Mainnet chain ids are refused unless the read switch is on.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from bot.grounded_reputation.addrs import as_int, norm_addr, norm_word
from bot.grounded_reputation.chains import chain_allowed, chain_row
from bot.grounded_reputation.wallet import AGENT_WALLET_KEY, is_clear_transfer
from bot.hrc.keccak import keccak256

_NEW = "0x" + keccak256(
    b"NewFeedback(uint256,address,uint64,int128,uint8,string,string,string,string,string,bytes32)"
).hex()
_REVOKED = "0x" + keccak256(b"FeedbackRevoked(uint256,address,uint64)").hex()
_META = "0x" + keccak256(b"MetadataSet(uint256,string,string,bytes)").hex()
_TRANSFER = "0x" + keccak256(b"Transfer(address,address,uint256)").hex()


def fetch_logs(
    url: str,
    address: str,
    from_block: int,
    to_block: object,
    *,
    timeout: float = 20,
) -> Optional[list[object]]:
    """Logs, or None when the endpoint could not be read."""
    end: object = hex(to_block) if isinstance(to_block, int) else to_block
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "address": address,
            "fromBlock": hex(from_block),
            "toBlock": end,
        }],
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeError, TimeoutError, ValueError):
        return None
    if not isinstance(payload, dict) or "error" in payload:
        return None
    result = payload.get("result")
    if not isinstance(result, list):
        return None
    return result


def index_chain(
    chain_id: int,
    url: Optional[str],
    from_block: int,
    to_block: object = "latest",
    *,
    env: Optional[dict[str, str]] = None,
) -> dict[str, object]:
    """Pull identity and reputation logs, or report that the read did not happen."""
    if not chain_allowed(chain_id, env):
        return {"readable": False, "error": "chain_refused", "feedbacks": [], "wallet_events": []}
    row = chain_row(chain_id)
    if row is None or not isinstance(url, str) or url.strip() == "":
        return {"readable": False, "error": "rpc_unreadable", "feedbacks": [], "wallet_events": []}
    identity = fetch_logs(url, str(row["identity"]), from_block, to_block)
    reputation = fetch_logs(url, str(row["reputation"]), from_block, to_block)
    if identity is None or reputation is None:
        return {"readable": False, "error": "rpc_unreadable", "feedbacks": [], "wallet_events": []}
    parsed = apply_logs(identity, reputation, chain_id)
    parsed["readable"] = True
    parsed["error"] = None
    return parsed


def apply_logs(
    identity_logs: list[object],
    reputation_logs: list[object],
    chain_id: int,
) -> dict[str, object]:
    """Turn already-fetched logs into feedback rows and wallet events."""
    feedbacks: list[dict[str, object]] = []
    revoked: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    owners: dict[int, str] = {}
    unreadable = 0
    for raw in identity_logs:
        kind = _identity(raw)
        if kind is None:
            unreadable += 1
            continue
        if kind["kind"] == "owner":
            agent_id = kind.get("agent_id")
            owner = kind.get("owner")
            if isinstance(agent_id, int) and isinstance(owner, str):
                owners[agent_id] = owner
        elif kind["kind"] in ("set", "clear"):
            events.append(kind)
    for raw in reputation_logs:
        item = _feedback(raw, chain_id)
        if item is None:
            unreadable += 1
            continue
        if item.get("ignored") is True:
            continue
        if item.get("revoked") is True:
            revoked.append(item)
        else:
            feedbacks.append(item)
    revoked_keys = {(row.get("agent_id"), row.get("client"), row.get("feedback_index")) for row in revoked}
    for row in feedbacks:
        key = (row.get("agent_id"), row.get("client"), row.get("feedback_index"))
        if key in revoked_keys:
            row["revoked"] = True
    return {
        "feedbacks": feedbacks,
        "wallet_events": events,
        "owners": owners,
        "unreadable_logs": unreadable,
    }


def _identity(raw: object) -> Optional[dict[str, object]]:
    if not isinstance(raw, dict):
        return None
    topics = raw.get("topics")
    if not isinstance(topics, list) or not topics:
        return None
    topic0 = norm_word(topics[0])
    block = as_int(raw.get("blockNumber"))
    log_index = as_int(raw.get("logIndex"))
    if block is None:
        return None
    if topic0 == _META:
        agent = _topic_uint(topics, 1)
        data = _bytes(raw.get("data"))
        if agent is None or data is None:
            return None
        key, value = _meta_value(data)
        if key != AGENT_WALLET_KEY:
            return {"kind": "ignore", "block": block}
        wallet = _wallet_bytes(value)
        if wallet is None:
            return None
        return {"kind": "set", "block": block, "log_index": log_index, "agent_id": agent, "wallet": wallet}
    if topic0 == _TRANSFER and len(topics) == 4:
        sender = _topic_addr(topics, 1)
        owner = _topic_addr(topics, 2)
        agent = _topic_uint(topics, 3)
        if sender is None or owner is None or agent is None:
            return None
        if is_clear_transfer(sender):
            return {
                "kind": "clear",
                "block": block,
                "log_index": log_index,
                "agent_id": agent,
                "owner": owner,
            }
        return {"kind": "owner", "block": block, "agent_id": agent, "owner": owner}
    return {"kind": "ignore", "block": block}


def _feedback(raw: object, chain_id: int) -> Optional[dict[str, object]]:
    if not isinstance(raw, dict):
        return None
    topics = raw.get("topics")
    if not isinstance(topics, list) or not topics:
        return None
    topic0 = norm_word(topics[0])
    block = as_int(raw.get("blockNumber"))
    if block is None:
        return None
    if topic0 == _REVOKED and len(topics) >= 4:
        agent = _topic_uint(topics, 1)
        client = _topic_addr(topics, 2)
        index = _topic_uint(topics, 3)
        if agent is None or client is None or index is None:
            return None
        return {
            "chain_id": chain_id,
            "agent_id": agent,
            "client": client,
            "feedback_index": index,
            "block": block,
            "revoked": True,
        }
    if topic0 != _NEW or len(topics) < 3:
        return {"chain_id": chain_id, "ignored": True, "block": block, "revoked": False}
    agent = _topic_uint(topics, 1)
    client = _topic_addr(topics, 2)
    data = _bytes(raw.get("data"))
    if agent is None or client is None or data is None or len(data) < 8 * 32:
        return None
    index = int.from_bytes(data[0:32], "big")
    value = int.from_bytes(data[32:64], "big", signed=True)
    decimals = int.from_bytes(data[64:96], "big")
    uri = _dyn_string(data, int.from_bytes(data[192:224], "big"))
    digest = "0x" + data[224:256].hex()
    if uri is None:
        return None
    return {
        "chain_id": chain_id,
        "agent_id": agent,
        "client": client,
        "feedback_index": index,
        "value": value,
        "value_decimals": decimals,
        "feedback_uri": uri,
        "feedback_hash": digest,
        "block": block,
        "block_time": None,
        "tx_hash": norm_word(raw.get("transactionHash")),
        "revoked": False,
    }


def _meta_value(data: bytes) -> tuple[Optional[str], Optional[bytes]]:
    if len(data) < 64:
        return None, None
    key_at = int.from_bytes(data[0:32], "big")
    val_at = int.from_bytes(data[32:64], "big")
    return _dyn_string(data, key_at), _dyn_bytes(data, val_at)


def _dyn_string(data: bytes, offset: int) -> Optional[str]:
    raw = _dyn_bytes(data, offset)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return None


def _dyn_bytes(data: bytes, offset: int) -> Optional[bytes]:
    if offset < 0 or offset + 32 > len(data):
        return None
    length = int.from_bytes(data[offset:offset + 32], "big")
    start = offset + 32
    end = start + length
    if length < 0 or end > len(data):
        return None
    return data[start:end]


def _wallet_bytes(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    if len(value) == 20:
        return norm_addr("0x" + value.hex())
    if len(value) == 32 and value[:12] == b"\x00" * 12:
        return norm_addr("0x" + value[-20:].hex())
    try:
        text = value.decode("utf-8").strip()
    except UnicodeError:
        return None
    return norm_addr(text)


def _bytes(data: object) -> Optional[bytes]:
    if isinstance(data, bytes):
        return data
    if not isinstance(data, str):
        return None
    text = data[2:] if data.startswith(("0x", "0X")) else data
    if text == "":
        return b""
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def _topic_addr(topics: list[object], index: int) -> Optional[str]:
    if index >= len(topics):
        return None
    word = norm_word(topics[index])
    if word is None:
        return None
    return norm_addr("0x" + word[-40:])


def _topic_uint(topics: list[object], index: int) -> Optional[int]:
    if index >= len(topics):
        return None
    word = norm_word(topics[index])
    if word is None:
        return None
    return int(word, 16)

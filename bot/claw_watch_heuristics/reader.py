"""JSON-RPC reads for one address. Tests pass a transport. Nothing here runs at import.

A batch is one call. A missing result is unreadable, not an empty log.
The first poll reads the head and the code and does not replay history.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional

from bot.claw_watch_heuristics.allowlist import PERMIT2
from bot.claw_watch_heuristics.chains import chain_allowed
from bot.claw_watch_heuristics.decode import (
    TOPIC_APPROVAL,
    TOPIC_APPROVAL_ALL,
    TOPIC_PERMIT2_APPROVAL,
    TOPIC_PERMIT2_PERMIT,
    TOPIC_TRANSFER,
    address_of,
    hex_int,
)

_SENDER_CAP = 40


def _url_ok(url: str) -> bool:
    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text.startswith("http://") and not text.startswith("https://"):
        return False
    if any(ch.isspace() for ch in text):
        return False
    low = text.lower()
    if "apikey=" in low or "api_key=" in low:
        return False
    return True


def _http_post(url: str, body: list) -> list:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        parsed = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(parsed, list):
        raise RuntimeError("batch")
    return parsed


def _pad(address: str) -> str:
    return "0x" + "0" * 24 + address[2:]


def _planned(first: bool, internal: bool) -> int:
    """Calls a poll will spend before it starts. A batch counts as one."""
    calls = 1 if first else 2
    if internal and not first:
        calls += 1
    return calls


def planned_calls(first: bool, internal: bool) -> int:
    return _planned(first, internal)


def _logs_query(address: str, from_block: int) -> list[tuple[str, list]]:
    pad = _pad(address)
    start = hex(from_block)
    filt = {"fromBlock": start, "toBlock": "latest"}
    return [
        ("eth_getLogs", [{**filt, "topics": [TOPIC_TRANSFER, pad]}]),
        ("eth_getLogs", [{**filt, "topics": [TOPIC_TRANSFER, None, pad]}]),
        ("eth_getLogs", [{**filt, "topics": [TOPIC_APPROVAL, pad]}]),
        ("eth_getLogs", [{**filt, "topics": [TOPIC_APPROVAL_ALL, pad]}]),
        ("eth_getLogs", [{
            **filt, "address": PERMIT2, "topics": [TOPIC_PERMIT2_PERMIT, pad],
        }]),
        ("eth_getLogs", [{
            **filt, "address": PERMIT2, "topics": [TOPIC_PERMIT2_APPROVAL, pad],
        }]),
    ]


def _invoke(url: str, calls: list[tuple[str, list]], transport) -> tuple[str, list]:
    body = [
        {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
        for i, (method, params) in enumerate(calls)
    ]
    try:
        if transport is None:
            raw = _http_post(url, body)
        else:
            raw = transport(calls)
    except Exception as exc:
        return (type(exc).__name__, [])
    if not isinstance(raw, list) or len(raw) != len(calls):
        return ("unreadable", [])
    return ("ok", raw)


def _result(row: object) -> tuple[str, object]:
    if not isinstance(row, dict):
        return ("unreadable", None)
    if row.get("error") is not None:
        return ("unreadable", None)
    if "result" not in row or row.get("result") is None:
        return ("unreadable", None)
    return ("read", row.get("result"))


def fetch_window(url: str, address: str, chain_id: int, from_block: Optional[int],
                 transport=None, *, internal: bool = False) -> dict:
    """One poll. ``calls`` is how many transports ran, for the weekly budget.

    ``from_block`` None is the first poll: head and code only, history not replayed.
    """
    base = {"calls": 0, "transactions": [], "history": "not_replayed" if from_block is None else "forward"}
    if not isinstance(address, str) or address_of(address) is None:
        base.update({"status": "refused", "reason": "address"})
        return base
    address = address_of(address) or address
    if isinstance(chain_id, bool) or not isinstance(chain_id, int) or not chain_allowed(chain_id):
        base.update({"status": "refused", "reason": "chain"})
        return base
    if not _url_ok(url):
        reason = "unconfigured" if not str(url or "").strip() else "refused"
        base.update({"status": reason, "reason": reason})
        return base
    head_calls: list[tuple[str, list]] = [
        ("eth_chainId", []),
        ("eth_blockNumber", []),
        ("eth_getCode", [address, "latest"]),
    ]
    if from_block is not None:
        head_calls.extend(_logs_query(address, from_block))
    status, rows = _invoke(url, head_calls, transport)
    base["calls"] = 1
    if status != "ok":
        base.update({"status": "unreadable", "error_class": status if status != "unreadable" else "Response"})
        return base
    chain_status, chain_raw = _result(rows[0])
    head_status, head_raw = _result(rows[1])
    code_status, code_raw = _result(rows[2])
    if chain_status != "read" or head_status != "read":
        base.update({"status": "unreadable", "error_class": "Response"})
        return base
    got = hex_int(chain_raw)
    head = hex_int(head_raw)
    if got is None or head is None:
        base.update({"status": "unreadable", "error_class": "Response"})
        return base
    if got != chain_id:
        base.update({"status": "chain_mismatch", "observed_chain_id": got})
        return base
    code = code_raw if isinstance(code_raw, str) else None
    window = {
        **base,
        "status": "read",
        "head": head,
        "cursor_block": head,
        "code": code,
        "code_status": "read" if code_status == "read" and isinstance(code, str) else "unreadable",
        "chain_id": chain_id,
        "address": address,
    }
    if from_block is None:
        return window
    logs: list[dict] = []
    for row in rows[3:]:
        log_status, log_raw = _result(row)
        if log_status != "read":
            window.update({"status": "unreadable", "error_class": "Response", "transactions": []})
            return window
        if not isinstance(log_raw, list):
            window.update({"status": "unreadable", "error_class": "Response", "transactions": []})
            return window
        for item in log_raw:
            if isinstance(item, dict):
                logs.append(item)
    by_hash: dict[str, dict] = {}
    for log in logs:
        tx_hash = log.get("transactionHash")
        if not isinstance(tx_hash, str) or not tx_hash:
            continue
        tx = by_hash.setdefault(tx_hash, {
            "hash": tx_hash,
            "block": log.get("blockNumber"),
            "logs": [],
        })
        tx["logs"].append({
            "address": log.get("address"),
            "topics": log.get("topics"),
            "data": log.get("data"),
        })
    hashes = list(by_hash)
    fetched = hashes[:_SENDER_CAP]
    unread = hashes[_SENDER_CAP:]
    if fetched:
        sender_calls = [("eth_getTransactionByHash", [tx_hash]) for tx_hash in fetched]
        sender_status, sender_rows = _invoke(url, sender_calls, transport)
        window["calls"] = 2
        if sender_status != "ok":
            for tx_hash in fetched:
                by_hash[tx_hash]["from"] = ""
        else:
            for tx_hash, row in zip(fetched, sender_rows):
                row_status, tx_raw = _result(row)
                if row_status != "read" or not isinstance(tx_raw, dict):
                    by_hash[tx_hash]["from"] = ""
                    continue
                sender = tx_raw.get("from")
                by_hash[tx_hash]["from"] = sender if isinstance(sender, str) else ""
                to = tx_raw.get("to")
                if isinstance(to, str):
                    by_hash[tx_hash]["to"] = to
                data = tx_raw.get("input")
                if isinstance(data, str):
                    by_hash[tx_hash]["input"] = data
                if "value" in tx_raw:
                    by_hash[tx_hash]["value"] = tx_raw.get("value")
                auths = tx_raw.get("authorizationList")
                if isinstance(auths, list):
                    by_hash[tx_hash]["authorizations"] = [
                        {"address": item.get("address"), "authority": item.get("authority")}
                        for item in auths if isinstance(item, dict)
                    ]
    else:
        window["calls"] = 1
    for tx_hash in unread:
        by_hash[tx_hash]["from"] = ""
    window["transactions"] = list(by_hash.values())
    window["history"] = "forward"
    # ``internal`` is filled by the caller. This reader does not open that path.
    if internal:
        window["internal_planned"] = True
    return window

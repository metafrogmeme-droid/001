"""Optional internal-transaction read. Off unless the flag and a key are set.

The key is read from the environment and is not put on a result or a log
line. A fork is not an Etherscan chain. A refused chain does not build a URL.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional

from bot.claw_watch_heuristics.chains import FORK_CHAIN_IDS, internal_chain
from bot.claw_watch_heuristics.decode import address_of, hex_int
from bot.claw_watch_heuristics.flag import etherscan_key, internal_enabled


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", "replace")
    return ""


def _off(reason: str) -> dict:
    return {"status": "off", "reason": reason, "transfers": None}


def read_internal(address: str, chain_id: object, start_block: int, env: Optional[dict] = None,
                  transport=None) -> dict:
    """``status`` is ``off``, ``read``, or ``unreadable``. Off is not an empty list."""
    if not internal_enabled(env):
        return _off("disabled")
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return _off("chain")
    if chain_id in FORK_CHAIN_IDS:
        return _off("fork")
    if not internal_chain(chain_id):
        return _off("chain")
    if address_of(address) is None:
        return {"status": "unreadable", "error_class": "Address", "transfers": None}
    key = etherscan_key(env)
    # The key stays in this frame. It is not returned and not interpolated into a log.
    url = (
        "https://api.etherscan.io/v2/api"
        f"?chainid={chain_id}&module=account&action=txlistinternal"
        f"&address={address_of(address)}&startblock={start_block}&endblock=latest&sort=asc"
        f"&apikey={key}"
    )
    try:
        if transport is None:
            raw = _http_get(url)
        else:
            raw = transport(url)
    except Exception as exc:
        return {"status": "unreadable", "error_class": type(exc).__name__, "transfers": None}
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {"status": "unreadable", "error_class": "ValueError", "transfers": None}
    return _parse(doc)


def _parse(doc: object) -> dict:
    if not isinstance(doc, dict):
        return {"status": "unreadable", "error_class": "Response", "transfers": None}
    message_raw = doc.get("message")
    message = message_raw if isinstance(message_raw, str) else ""
    result = doc.get("result")
    status = str(doc.get("status") if doc.get("status") is not None else "")
    if status == "1" and isinstance(result, list):
        rows, gap = _rows(result)
        return {"status": "read", "transfers": rows, "transfer_gap": gap}
    if "No transactions found" in message:
        return {"status": "read", "transfers": [], "transfer_gap": False}
    return {"status": "unreadable", "error_class": "Response", "transfers": None}


def _rows(result: list) -> tuple[list[dict], bool]:
    rows = []
    gap = False
    for item in result:
        if not isinstance(item, dict):
            gap = True
            continue
        value = hex_int(item.get("value"))
        src = address_of(item.get("from"))
        dest = address_of(item.get("to"))
        if src is None or dest is None or value is None:
            gap = True
            continue
        block = hex_int(item.get("blockNumber"))
        tx_hash = item.get("hash") if isinstance(item.get("hash"), str) else ""
        rows.append({
            "from": src,
            "to": dest,
            "value": value,
            "block": block,
            "tx_hash": tx_hash,
        })
    return rows, gap

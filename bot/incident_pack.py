"""Incident pack, the Panic Button's read-only follow-up.

One address and one time window. Public chain data only: transactions,
token and NFT transfers, approval and Permit2 logs, and the current
7702 delegation. The pack states those facts and any public name tags.
It does not say what they mean, and it does not send anything.

Chains are local (31337) and the testnets named in
``web3_exec_gate.NETWORKS``. A mainnet name is refused before a read.
The flag ``INCIDENT_PACK_ENABLED`` is off unless it is ``1`` or ``true``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from bot.approval_hygiene import INVALID_ADDRESS, normalize_address, parse_iso, short_address
from bot.panic_button import SECRET_REPLY, refuses_secret
from bot.web.mainnet_guard import mainnet_chain_ids
from bot.web.web3_exec_gate import NETWORKS

FLAG = "INCIDENT_PACK_ENABLED"
SCHEMA = "claw-incident-pack/0.1"
LOCAL_ID = 31337
WINDOW_MAX = 7 * 24 * 3600
_TEMPLATES = Path(__file__).resolve().parent / "incident_pack_templates"
_AMS = ZoneInfo("Europe/Amsterdam")
_UA = "runeclaw-incident-pack/0.1"
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

# Blockscout read APIs for the same testnet rows NETWORKS already names.
# Chain id and RPC come from that table. These hosts are only the log API.
_BLOCKSCOUT = {
    "sepolia": "https://eth-sepolia.blockscout.com",
    "base-sepolia": "https://base-sepolia.blockscout.com",
    "arbitrum-sepolia": "https://arbitrum-sepolia.blockscout.com",
}

TOPIC = {
    "erc20_or_721_approval": "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
    "approval_for_all": "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31",
}
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
_APPROVE = "0x095ea7b3"
_SELECTORS = {
    _APPROVE: "approve",
    "0x39509351": "increaseAllowance",
    "0xa22cb465": "setApprovalForAll",
    "0xa9059cbb": "transfer",
    "0x23b872dd": "transferFrom",
    "0xd505accf": "permit",
}
_EMPTY_MESSAGES = frozenset({
    "", "OK", "No transactions found", "No records found", "No internal transactions found",
})


class IncidentSourceUnreadable(Exception):
    """A public read did not return a body this pack can use."""


class IncidentChainRefused(Exception):
    """The chain is not local and not one of the configured testnets."""


class IncidentRequestRefused(Exception):
    """The address, the window, or the chain name was refused."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``. Anything else, including unset, is off."""
    src = os.environ if env is None else env
    raw = str(src.get(FLAG) or "").strip().lower()
    return raw == "1" or raw == "true"


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _chain_table() -> dict[str, dict[str, Any]]:
    """Local plus the three testnets. A mainnet id in that table is a refusal."""
    banned = mainnet_chain_ids()
    table: dict[str, dict[str, Any]] = {
        "local": {
            "bs": "http://127.0.0.1:4000",
            "rpc": "http://127.0.0.1:8545",
            "id": LOCAL_ID,
            "explorer": "http://127.0.0.1:4000",
        },
    }
    for name, bs in _BLOCKSCOUT.items():
        row = NETWORKS[name]
        cid = row.get("chain_id")
        rpc = row.get("public_rpc")
        if row.get("testnet") is not True or isinstance(cid, bool) or not isinstance(cid, int):
            raise IncidentChainRefused("table")
        if cid in banned or cid == LOCAL_ID:
            raise IncidentChainRefused("table")
        if not isinstance(rpc, str) or _host(rpc) in _LOOPBACK or "sepolia" not in bs:
            raise IncidentChainRefused("table")
        table[name] = {"bs": bs, "rpc": rpc, "id": cid, "explorer": bs}
    return table


CHAINS = _chain_table()


def resolve_chain(name: str) -> dict[str, Any]:
    """The chain row, or a refusal. Mainnet names are not a default."""
    key = str(name or "").strip().lower()
    spec = CHAINS.get(key)
    if spec is None:
        raise IncidentChainRefused("chain")
    cid = spec["id"]
    if isinstance(cid, bool) or cid in mainnet_chain_ids():
        raise IncidentChainRefused("chain")
    for field in ("bs", "rpc", "explorer"):
        host = _host(str(spec[field]))
        if cid == LOCAL_ID and host not in _LOOPBACK:
            raise IncidentChainRefused("chain")
        if cid != LOCAL_ID and host in _LOOPBACK:
            raise IncidentChainRefused("chain")
    return spec


def _pad(addr: str) -> str:
    return "0x" + ("0" * 24) + addr.lower()[2:]


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def _iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ams(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, _AMS).strftime("%Y-%m-%d %H:%M:%S %Z")


def decode_calldata(data: str) -> dict[str, Any]:
    """Name a few known selectors. Anything else stays ``unknown``."""
    raw = data or "0x"
    selector = raw[:10].lower() if raw.startswith("0x") and len(raw) >= 10 else ""
    fn = _SELECTORS.get(selector, "unknown")
    signals: list[dict[str, str]] = []
    if fn in ("approve", "increaseAllowance") and len(raw) >= 10 + 128:
        word = raw[10 + 64:10 + 128]
        try:
            amount = int(word, 16)
        except ValueError:
            amount = None
        if amount is not None and amount >= 2 ** 255:
            signals.append({"code": "UNLIMITED_AMOUNT"})
    return {"fn": fn, "selector": selector, "signals": signals}


def _get_json(url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IncidentSourceUnreadable() from exc
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IncidentSourceUnreadable() from exc
    if not isinstance(parsed, dict):
        raise IncidentSourceUnreadable()
    return parsed


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IncidentSourceUnreadable() from exc
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IncidentSourceUnreadable() from exc
    if not isinstance(parsed, dict):
        raise IncidentSourceUnreadable()
    return parsed


class Source:
    """Public reads for one allowed chain. Tests pass a stand-in instead."""

    def __init__(self, chain: str) -> None:
        self.c = resolve_chain(chain)

    def api(self, **params: Any) -> list[Any]:
        payload = _get_json(self.c["bs"] + "/api", params)
        result = payload.get("result")
        if isinstance(result, list):
            return result
        message = payload.get("message")
        if not isinstance(message, str) or message not in _EMPTY_MESSAGES:
            raise IncidentSourceUnreadable()
        if result in (None, [], ""):
            return []
        raise IncidentSourceUnreadable()

    def block_at(self, ts: int, closest: str) -> int:
        payload = _get_json(self.c["bs"] + "/api", {
            "module": "block", "action": "getblocknobytime", "timestamp": ts, "closest": closest,
        })
        res = payload.get("result")
        if isinstance(res, dict):
            res = res.get("blockNumber")
        if isinstance(res, str) and res.isdigit():
            return int(res)
        if isinstance(res, int) and not isinstance(res, bool):
            return res
        raise IncidentSourceUnreadable()

    def tx_v2(self, tx_hash: str) -> dict[str, Any]:
        payload = _get_json(f"{self.c['bs']}/api/v2/transactions/{tx_hash}")
        if not isinstance(payload, dict):
            raise IncidentSourceUnreadable()
        return payload

    def addr_v2(self, addr: str) -> dict[str, Any]:
        payload = _get_json(f"{self.c['bs']}/api/v2/addresses/{addr}")
        if not isinstance(payload, dict):
            raise IncidentSourceUnreadable()
        return payload

    def get_code(self, addr: str) -> str:
        payload = _post_json(self.c["rpc"], {
            "jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [addr, "latest"],
        })
        result = payload.get("result")
        if not isinstance(result, str) or not result.startswith("0x"):
            raise IncidentSourceUnreadable()
        return result


def _public_label(src: Any, addr: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Blockscout name tags. A failed read stays unread, not an empty tag list."""
    addr = addr.lower()
    cached = cache.get(addr)
    if cached is not None:
        return cached
    unread: dict[str, Any] = {"blockscout_tags": None, "tags_read": False}
    try:
        body = src.addr_v2(addr)
    except Exception:
        cache[addr] = unread
        return unread
    if not isinstance(body, dict):
        cache[addr] = unread
        return unread
    raw_tags = ((body.get("metadata") or {}) if isinstance(body.get("metadata"), dict) else {}).get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if not isinstance(tag, dict):
                continue
            if tag.get("tagType") not in ("name", "generic", "protocol"):
                continue
            name = tag.get("name")
            if isinstance(name, str) and name.strip():
                tags.append(name.strip())
    read: dict[str, Any] = {"blockscout_tags": tags, "tags_read": True}
    cache[addr] = read
    return read


def _collect(src: Any, victim: str, start: int, end: int) -> dict[str, Any]:
    b0, b1 = src.block_at(start, "after"), src.block_at(end, "before")
    common = {"module": "account", "address": victim, "startblock": b0, "endblock": b1, "sort": "asc"}
    txs = src.api(action="txlist", **common) or []
    erc20 = src.api(action="tokentx", **common) or []
    nft = src.api(action="tokennfttx", **common) or []
    logs: list[dict[str, Any]] = []
    for name, topic0 in TOPIC.items():
        rows = src.api(
            module="logs", action="getLogs", fromBlock=b0, toBlock=b1,
            topic0=topic0, topic1=_pad(victim), topic0_1_opr="and",
        ) or []
        for row in rows:
            if isinstance(row, dict):
                row["_kind"] = name
                logs.append(row)
    permit = src.api(
        module="logs", action="getLogs", address=PERMIT2, fromBlock=b0, toBlock=b1, topic1=_pad(victim),
    ) or []
    for row in permit:
        if isinstance(row, dict):
            row["_kind"] = "permit2_event"
            logs.append(row)
    auths: dict[str, Any] = {}
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        kind = str(tx.get("type", ""))
        if kind in ("4", "0x4") or tx.get("authorizationList"):
            tx_hash = tx.get("hash")
            if isinstance(tx_hash, str):
                body = src.tx_v2(tx_hash) or {}
                auths[tx_hash] = body.get("authorization_list") if isinstance(body, dict) else None
    code = src.get_code(victim)
    return {
        "victim": victim, "window": [start, end], "blocks": [b0, b1],
        "txs": txs, "erc20": erc20, "nft": nft, "logs": logs, "auth_lists": auths, "code_now": code,
    }


def _events(src: Any, raw: dict[str, Any]) -> list[dict[str, Any]]:
    victim = raw["victim"]
    events: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    for tx in raw["txs"]:
        if not isinstance(tx, dict):
            continue
        decoded = decode_calldata(str(tx.get("input") or "0x"))
        sender = str(tx.get("from") or "").lower()
        events.append({
            "ts": int(tx["timeStamp"]),
            "block": int(tx["blockNumber"]),
            "tx": tx["hash"],
            "kind": "tx_sent" if sender == victim else "tx_received_or_touching",
            "from": sender,
            "to": str(tx.get("to") or "").lower(),
            "value_wei": tx.get("value"),
            "status": "fail" if tx.get("isError") == "1" else "ok",
            "decoded": {k: v for k, v in decoded.items() if k != "signals"},
            "signals": [s["code"] for s in decoded.get("signals", [])],
        })
    for tx in raw["erc20"]:
        if not isinstance(tx, dict):
            continue
        outbound = str(tx.get("from") or "").lower() == victim
        counterparty = str(tx.get("to") if outbound else tx.get("from") or "").lower()
        events.append({
            "ts": int(tx["timeStamp"]),
            "block": int(tx["blockNumber"]),
            "tx": tx["hash"],
            "kind": "token_out" if outbound else "token_in",
            "token": str(tx.get("contractAddress") or "").lower(),
            "symbol": tx.get("tokenSymbol"),
            "decimals": tx.get("tokenDecimal"),
            "amount_raw": tx.get("value"),
            "counterparty": counterparty,
            "counterparty_public": _public_label(src, counterparty, cache) if outbound else None,
        })
    for tx in raw["nft"]:
        if not isinstance(tx, dict):
            continue
        outbound = str(tx.get("from") or "").lower() == victim
        events.append({
            "ts": int(tx["timeStamp"]),
            "block": int(tx["blockNumber"]),
            "tx": tx["hash"],
            "kind": "nft_out" if outbound else "nft_in",
            "token": str(tx.get("contractAddress") or "").lower(),
            "symbol": tx.get("tokenSymbol"),
            "token_id": tx.get("tokenID"),
            "counterparty": str(tx.get("to") if outbound else tx.get("from") or "").lower(),
        })
    for row in raw["logs"]:
        topics = row.get("topics") or []
        event: dict[str, Any] = {
            "ts": int(row["timeStamp"], 16),
            "block": int(row["blockNumber"], 16),
            "tx": row["transactionHash"],
            "kind": row["_kind"],
            "emitter": str(row.get("address") or "").lower(),
        }
        if row["_kind"] == "erc20_or_721_approval" and len(topics) >= 3:
            data = row.get("data") or "0x"
            if isinstance(data, str) and data not in ("0x", "0x0"):
                amount = int(data, 16)
            elif len(topics) > 3:
                amount = int(topics[3], 16)
            else:
                amount = 0
            spender = _topic_addr(topics[2])
            event.update(
                spender=spender, amount_raw=str(amount), unlimited=amount >= 2 ** 255,
                spender_public=_public_label(src, spender, cache),
            )
        elif row["_kind"] == "approval_for_all" and len(topics) >= 3:
            operator = _topic_addr(topics[2])
            data = row.get("data") or "0x0"
            event.update(
                operator=operator, approved=int(data, 16) == 1,
                operator_public=_public_label(src, operator, cache),
            )
        elif row["_kind"] == "permit2_event" and len(topics) >= 4:
            spender = _topic_addr(topics[3])
            event.update(
                token=_topic_addr(topics[2]), spender=spender,
                spender_public=_public_label(src, spender, cache),
            )
        events.append(event)
    for tx_hash, auth_list in raw["auth_lists"].items():
        for auth in auth_list or []:
            if not isinstance(auth, dict):
                continue
            delegate = str(auth.get("address_hash") or auth.get("address") or "").lower()
            events.append({
                "ts": None, "tx": tx_hash, "kind": "eip7702_authorization",
                "delegate": delegate, "chain_id": auth.get("chain_id"),
                "authority": str(auth.get("authority") or "").lower(),
                "delegate_public": _public_label(src, delegate, cache) if delegate else None,
            })
    stamp = {e["tx"]: (e.get("ts"), e.get("block")) for e in events if e.get("ts")}
    for event in events:
        if event.get("ts") is None and event.get("tx") in stamp:
            event["ts"], event["block"] = stamp[event["tx"]]
    events.sort(key=lambda e: (e.get("ts") or 0, e.get("block") or 0, e.get("kind") or ""))
    return events


def _label_line(event: dict[str, Any]) -> Optional[str]:
    pub = (
        event.get("counterparty_public") or event.get("spender_public")
        or event.get("operator_public") or event.get("delegate_public") or {}
    )
    if not isinstance(pub, dict):
        return None
    who = event.get("counterparty") or event.get("spender") or event.get("operator") or event.get("delegate")
    if not who:
        return None
    if pub.get("tags_read") is False:
        return f"Public tags for {who} could not be read."
    tags = pub.get("blockscout_tags") or []
    if not tags:
        return None
    shown = "; ".join(str(tag) for tag in tags)
    return f"Public labels for {who}: {shown} (source: Blockscout public tags)."


def findings(raw: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    """Facts. A public tag is quoted and attributed. No verdict."""
    lines: list[str] = []
    code = str(raw.get("code_now") or "0x").lower()
    if code.startswith("0xef0100") and len(code) == 48:
        lines.append(
            f"Address currently has an EIP-7702 delegation to 0x{code[8:]} (from eth_getCode, latest block)."
        )
    elif code not in ("0x", ""):
        lines.append("Address currently has contract code.")
    outbound = [e for e in events if e.get("kind") in ("token_out", "nft_out")]
    if outbound:
        counts: dict[str, int] = {}
        for event in outbound:
            who = str(event.get("counterparty") or "")
            counts[who] = counts.get(who, 0) + 1
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        shown = ", ".join(f"{addr} (x{n})" for addr, n in top)
        lines.append(
            f"{len(outbound)} outbound token/NFT transfers to {len(counts)} recipient(s); most frequent: {shown}"
        )
    sent = {e.get("tx") for e in events if e.get("kind") == "tx_sent"}
    pulled = [e for e in outbound if e.get("tx") not in sent]
    if pulled:
        lines.append(
            f"{len(pulled)} outbound transfers happened in transactions NOT sent by this address "
            "(consistent with a third party using an approval, permit or delegation)."
        )
    for event in events:
        line = _label_line(event)
        if line and line not in lines:
            lines.append(line)
    approvals = [
        e for e in events if e.get("kind") in ("erc20_or_721_approval", "approval_for_all", "permit2_event")
    ]
    if approvals:
        lines.append(
            f"{len(approvals)} approval-type events emitted for this owner in the window "
            "(spenders listed in the timeline)."
        )
    return lines


def merkle(leaves: list[bytes]) -> str:
    if not leaves:
        return "0x" + hashlib.sha256(b"").hexdigest()
    level = [hashlib.sha256(leaf).digest() for leaf in leaves]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level), 2)]
    return "0x" + level[0].hex()


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def seal(pack: dict[str, Any]) -> dict[str, Any]:
    body = {k: pack[k] for k in pack if k != "seal"}
    leaves = [_canon(event) for event in pack["events"]]
    pack["seal"] = {
        "algo": "sha256 over canonical JSON (sorted keys, no whitespace) of everything except 'seal'",
        "sha256": hashlib.sha256(_canon(body)).hexdigest(),
        "events_merkle_root_sha256": merkle(leaves),
        "n_events": len(leaves),
        "note": "Recompute both to check the pack was not altered after it was built.",
    }
    return pack


def verify(pack: dict[str, Any]) -> bool:
    body = {k: pack[k] for k in pack if k != "seal"}
    seal_row = pack.get("seal")
    if not isinstance(seal_row, dict):
        return False
    digest = hashlib.sha256(_canon(body)).hexdigest()
    root = merkle([_canon(event) for event in pack.get("events") or []])
    return digest == seal_row.get("sha256") and root == seal_row.get("events_merkle_root_sha256")


def _amount_text(event: dict[str, Any]) -> str:
    raw = event.get("amount_raw")
    decimals = event.get("decimals")
    if raw is None:
        return "?"
    if decimals is None or decimals == "":
        return str(raw)
    try:
        scale = int(decimals)
        whole = int(raw)
    except (TypeError, ValueError):
        return str(raw)
    if scale < 0 or scale > 36:
        return str(raw)
    text = f"{whole / 10 ** scale:,.6f}".rstrip("0").rstrip(".")
    return text


def timeline_md(pack: dict[str, Any], explorer: str) -> str:
    lines = [
        f"# Incident timeline - {pack['victim']} ({pack['chain']})",
        "",
        "Public on-chain data only. Facts and public labels. Nothing here is a verdict. Nothing was sent.",
        "",
        f"- Window: {_iso(pack['window'][0])} -> {_iso(pack['window'][1])} "
        f"({_ams(pack['window'][0])} -> {_ams(pack['window'][1])})",
        f"- Blocks: {pack['blocks'][0]} - {pack['blocks'][1]}",
        f"- Generated: {pack['generated_utc']}",
        f"- Seal sha256: `{pack['seal']['sha256']}`",
        f"- Events Merkle root: `{pack['seal']['events_merkle_root_sha256']}`",
        "",
        "## Key facts",
        "",
    ]
    lines.extend(f"- {line}" for line in pack["findings"])
    if not pack["findings"]:
        lines.append("- (none)")
    lines.extend([
        "",
        "## Events",
        "",
        "| # | Time (UTC) | Time (Amsterdam) | Kind | Detail | Tx |",
        "|---|---|---|---|---|---|",
    ])
    for index, event in enumerate(pack["events"], 1):
        kind = event.get("kind")
        if kind in ("token_out", "token_in"):
            way = "to" if kind == "token_out" else "from"
            detail = f"{_amount_text(event)} {event.get('symbol')} {way} {event.get('counterparty')}"
        elif kind in ("nft_out", "nft_in"):
            way = "to" if kind == "nft_out" else "from"
            detail = f"{event.get('symbol')} #{event.get('token_id')} {way} {event.get('counterparty')}"
        elif kind == "erc20_or_721_approval":
            amount = "UNLIMITED" if event.get("unlimited") else event.get("amount_raw")
            detail = f"Approval on {event.get('emitter')} to {event.get('spender')} amount {amount}"
        elif kind == "approval_for_all":
            detail = f"ApprovalForAll on {event.get('emitter')} to {event.get('operator')} = {event.get('approved')}"
        elif kind == "permit2_event":
            detail = f"Permit2 event: token {event.get('token')} spender {event.get('spender')}"
        elif kind == "eip7702_authorization":
            detail = f"7702 delegation to {event.get('delegate')} (chainId {event.get('chain_id')})"
        else:
            decoded = event.get("decoded") or {}
            signals = event.get("signals") or []
            signal = (" signals: " + ",".join(signals)) if signals else ""
            detail = (
                f"{event.get('from')} -> {event.get('to')} "
                f"fn={decoded.get('fn')}{signal} status={event.get('status')}"
            )
        ts = event.get("ts")
        tx_hash = str(event.get("tx") or "")
        link = f"[{tx_hash[:10]}...]({explorer}/tx/{tx_hash})" if tx_hash else "?"
        lines.append(
            f"| {index} | {_iso(ts) if ts else '?'} | {_ams(ts) if ts else '?'} | {kind} | {detail} | {link} |"
        )
    return "\n".join(lines) + "\n"


def render_notes(pack: dict[str, Any]) -> dict[str, str]:
    """The checklist and the placeholder. No other template is shipped."""
    recipients = sorted({
        f"- {e['counterparty']}" for e in pack["events"] if e.get("kind") in ("token_out", "nft_out")
    })
    ctx = {
        "VICTIM": pack["victim"],
        "CHAIN": pack["chain"],
        "WINDOW_UTC": f"{_iso(pack['window'][0])} - {_iso(pack['window'][1])}",
        "WINDOW_AMS": f"{_ams(pack['window'][0])} - {_ams(pack['window'][1])}",
        "SEAL": pack["seal"]["sha256"],
        "FINDINGS": "\n".join(f"- {line}" for line in pack["findings"]) or "- (none)",
        "RECIPIENTS": "\n".join(recipients) or "- (none)",
    }
    notes: dict[str, str] = {}
    for path in sorted(_TEMPLATES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for key, value in ctx.items():
            text = text.replace("{{" + key + "}}", value)
        notes[path.name] = text
    return notes


def _check_window(start: object, end: object) -> tuple[int, int]:
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise IncidentRequestRefused("window")
    if end <= start or (end - start) > WINDOW_MAX:
        raise IncidentRequestRefused("window")
    return start, end


def make_pack(
    victim: str,
    start: int,
    end: int,
    chain: str = "local",
    outdir: Optional[str] = None,
    src: Any = None,
) -> dict[str, Any]:
    """Build the pack. ``src`` is the public reader. Tests pass a stand-in."""
    spec = resolve_chain(chain)
    address = normalize_address(victim)
    if address is None:
        raise IncidentRequestRefused("address")
    start_i, end_i = _check_window(start, end)
    reader = src if src is not None else Source(chain)
    raw = _collect(reader, address, start_i, end_i)
    events = _events(reader, raw)
    pack: dict[str, Any] = {
        "schema": SCHEMA,
        "victim": address,
        "chain": chain,
        "chain_id": spec["id"],
        "window": [start_i, end_i],
        "blocks": raw["blocks"],
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [
            spec["bs"] + "/api (txlist, tokentx, tokennfttx, getLogs, v2 tx/address)",
            spec["rpc"] + " (eth_getCode)",
        ],
        "code_now": raw["code_now"],
        "findings": findings(raw, events),
        "events": events,
        "disclaimer": (
            "Public on-chain data only. Facts and public labels. "
            "Nothing here is a verdict. Nothing was sent."
        ),
    }
    seal(pack)
    if outdir:
        folder = Path(outdir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pack.json").write_text(json.dumps(pack, indent=1, sort_keys=True), encoding="utf-8")
        (folder / "timeline.md").write_text(timeline_md(pack, spec["explorer"]), encoding="utf-8")
        for name, text in render_notes(pack).items():
            (folder / name).write_text(text, encoding="utf-8")
    return pack


def _parse_window(start: str, end: str) -> tuple[int, int]:
    try:
        start_dt = parse_iso(start)
        end_dt = parse_iso(end)
    except (TypeError, ValueError) as exc:
        raise IncidentRequestRefused("window") from exc
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _text(raw: Any) -> str:
    return raw if isinstance(raw, str) else ""


def run_body(body: Any, *, src: Any = None) -> dict[str, Any]:
    """One request in, one JSON object out. Nothing is sent."""
    if not isinstance(body, dict):
        return {"ok": False, "readable": False, "error": "IncidentRequestRefused"}
    address = _text(body.get("address"))
    start = _text(body.get("start"))
    end = _text(body.get("end"))
    chain = _text(body.get("chain"))
    # Each field on its own. Joining them with the window puts digits next
    # to a word list, and the secret check then no longer sees a seed.
    if any(refuses_secret(part) for part in (address, start, end, chain)):
        return {"ok": False, "readable": False, "refused": "secret", "message": SECRET_REPLY}
    if not chain.strip():
        return {"ok": False, "readable": False, "refused": "chain", "error": "IncidentChainRefused"}
    try:
        start_i, end_i = _parse_window(start, end)
        pack = make_pack(address, start_i, end_i, chain.strip().lower(), None, src)
    except IncidentChainRefused:
        return {"ok": False, "readable": False, "refused": "chain", "error": "IncidentChainRefused"}
    except IncidentRequestRefused as exc:
        if exc.code == "address":
            return {
                "ok": False, "readable": False, "refused": "address",
                "message": INVALID_ADDRESS, "error": "IncidentRequestRefused",
            }
        return {"ok": False, "readable": False, "refused": exc.code, "error": "IncidentRequestRefused"}
    except IncidentSourceUnreadable:
        return {"ok": False, "readable": False, "error": "IncidentSourceUnreadable"}
    except Exception as exc:
        return {"ok": False, "readable": False, "error": type(exc).__name__}
    spec = resolve_chain(pack["chain"])
    notes = render_notes(pack)
    return {
        "ok": True,
        "readable": True,
        "verify": verify(pack),
        "chain": pack["chain"],
        "chain_id": pack["chain_id"],
        "events": len(pack["events"]),
        "seal": pack["seal"]["sha256"],
        "findings": pack["findings"],
        "timeline_md": timeline_md(pack, spec["explorer"]),
        "checklist_md": notes.get("70_self_checklist.md", ""),
        "later_md": notes.get("00_later.md", ""),
        "pack": pack,
    }


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def command_text(args: Optional[list[str]], *, src: Any = None) -> str:
    """The reply body. A short argument list does not read a chain.

    The caller adds the admin-preview line. This function does not.
    """
    parts = [str(part) for part in (args or [])]
    if len(parts) < 4:
        return (
            "Usage: /incidentpack 0xADDRESS 2026-01-01T00:00:00Z 2026-01-01T06:00:00Z "
            "local|sepolia|base-sepolia|arbitrum-sepolia"
        )
    result = run_body(
        {"address": parts[0], "start": parts[1], "end": parts[2], "chain": parts[3]},
        src=src,
    )
    if result.get("ok") is not True:
        return _html(str(result.get("message") or result.get("error") or "IncidentRequestRefused"))
    head = [f"Incident pack ({result['chain']}, chain {result['chain_id']})"]
    head.append(f"Address {short_address(result['pack']['victim'])}")
    head.append(f"Events {result['events']}. Seal {result['seal']}. Check {result['verify']}.")
    head.append("")
    head.append("Facts:")
    head.extend(f"- {line}" for line in result["findings"])
    if not result["findings"]:
        head.append("- (none)")
    head.append("")
    head.append(result["checklist_md"].rstrip())
    head.append("")
    head.append(result["later_md"].rstrip())
    head.append("")
    head.append(result["timeline_md"].rstrip())
    head.append("")
    head.append("Nothing was sent.")
    return _html("\n".join(head))


def _stdin_body() -> Any:
    try:
        body = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {"error": "bad request"}
    return body


def main() -> int:
    json.dump(run_body(_stdin_body()), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

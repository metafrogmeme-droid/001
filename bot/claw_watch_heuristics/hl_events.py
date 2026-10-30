"""Map a Hyperliquid testnet info read into session events.

The only host is the testnet info URL. A different host is refused
before any socket opens. This module does not sign and does not read
a key. ``read_account`` does nothing while ``CLAW_WATCH_HEURISTICS_ENABLED``
is off: the lists are None, not empty.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable, Optional

from bot.claw_watch_heuristics.flag import flag_on

TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"
_MAX_BODY = 8 * 1024 * 1024

OUTBOUND = frozenset({
    "withdraw", "send", "spotTransfer", "internalTransfer", "subAccountTransfer", "vaultDeposit",
})
_INBOUND_CHECK = frozenset({"send", "spotTransfer", "internalTransfer"})
_CLOSE_DIRS = frozenset({"Long > Short", "Short > Long", "Sell"})

Fetch = Callable[[dict], object]


class RefusedHost(Exception):
    """The URL is not the testnet info host. Nothing was requested."""

    pass


class PayloadTooLarge(Exception):
    """The response was larger than the cap. The body is not kept."""

    pass


def info(payload: dict, url: str = TESTNET_INFO, timeout: float = 20.0) -> object:
    """One POST to the testnet info host. No key header."""
    if url != TESTNET_INFO:
        raise RefusedHost()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_BODY + 1)
    if len(raw) > _MAX_BODY:
        raise PayloadTooLarge()
    return json.loads(raw.decode("utf-8"))


def _rows(value: object) -> Optional[list]:
    """A list the API returned. None means the field was not a list.

    JSON null is an empty list: the info API uses null for no rows.
    Any other shape is unreadable and stays None.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return None


def _num(raw: object) -> Optional[float]:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _ledger_amount(delta: dict) -> Optional[float]:
    for key in ("usdc", "usdcValue", "amount"):
        if key not in delta:
            continue
        amount = _num(delta.get(key))
        if amount is not None:
            return abs(amount)
    return None


def read_account(user: str, start_ms: int, end_ms: int, fetch_fn: Optional[Fetch] = None,
                 env: Optional[dict] = None) -> dict:
    """Agents, fills, and ledger rows. While the flag is off, no call is made."""
    account = user.lower()
    if not flag_on(env):
        return {
            "user": account,
            "status": "off",
            "agents": None,
            "fills": None,
            "ledger": None,
        }
    call = fetch_fn or info
    agents = _rows(call({"type": "extraAgents", "user": account}))
    fills = _rows(call({
        "type": "userFillsByTime", "user": account, "startTime": start_ms, "endTime": end_ms,
    }))
    ledger = _rows(call({
        "type": "userNonFundingLedgerUpdates", "user": account,
        "startTime": start_ms, "endTime": end_ms,
    }))
    if agents is None or fills is None or ledger is None:
        return {
            "user": account,
            "status": "unreadable",
            "agents": None,
            "fills": None,
            "ledger": None,
        }
    return {"user": account, "status": "read", "agents": agents, "fills": fills, "ledger": ledger}


def to_events(raw: dict, known_agents: Optional[set] = None, now_ms: Optional[int] = None,
              self_close_oids: Optional[set] = None) -> list[dict]:
    """Session events from one completed info read.

    An agent already in ``known_agents`` is not a new key. A fill is a
    close when its ``dir`` starts with ``Close`` or is a flip or a sell.
    An inbound send is left out. A withdrawal the caller lists in
    ``self_close_oids`` is not what this maps; that set marks close oids.
    """
    status = raw.get("status")
    if status in ("off", "unreadable"):
        raise RuntimeError(str(status))
    user = str(raw.get("user", "")).lower()
    events: list[dict] = []
    known = {str(addr).lower() for addr in (known_agents or set())}
    stamped = 0 if now_ms is None else now_ms // 1000
    agents = raw.get("agents") or []
    if isinstance(agents, list):
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            addr_raw = agent.get("address")
            if not isinstance(addr_raw, str) or not addr_raw:
                continue
            addr = addr_raw.lower()
            if addr in known:
                continue
            events.append({
                "type": "api_key_created",
                "ts": stamped,
                "key_id": addr,
                "scopes": ["trade"],
                "source": "hl_extraAgents",
            })
    fills = raw.get("fills") or []
    if isinstance(fills, list):
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            direction = fill.get("dir") or ""
            if not isinstance(direction, str):
                continue
            if not (direction.startswith("Close") or direction in _CLOSE_DIRS):
                continue
            when = fill.get("time")
            if isinstance(when, bool) or not isinstance(when, (int, float)):
                continue
            row: dict = {
                "type": "order",
                "ts": int(when) // 1000,
                "reduce_only": True,
                "oid": fill.get("oid"),
                "coin": fill.get("coin"),
            }
            px = _num(fill.get("px"))
            sz = _num(fill.get("sz"))
            if px is not None and sz is not None:
                row["notional"] = px * sz
            if self_close_oids and fill.get("oid") in self_close_oids:
                row["initiator"] = "self"
            events.append(row)
    ledger = raw.get("ledger") or []
    if isinstance(ledger, list):
        for entry in ledger:
            if not isinstance(entry, dict):
                continue
            delta = entry.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            kind = delta.get("type")
            if kind not in OUTBOUND:
                continue
            source = delta.get("user") or user
            if not isinstance(source, str):
                source = user
            if kind in _INBOUND_CHECK and source.lower() != user:
                continue
            fallback = "bridge" if kind == "withdraw" else ""
            picked = delta.get("destination") or delta.get("vault") or fallback
            dest_raw = picked if isinstance(picked, str) else ""
            dest = dest_raw.lower() or "unknown"
            amount = _ledger_amount(delta)
            if amount is None:
                continue
            when = entry.get("time")
            if isinstance(when, bool) or not isinstance(when, (int, float)):
                continue
            events.append({
                "type": "withdrawal" if kind == "withdraw" else "transfer",
                "ts": int(when) // 1000,
                "key_id": "main",
                "to": dest,
                "amount": amount,
                "hl_type": kind,
            })
    return sorted(events, key=lambda row: row["ts"])


def baseline_from_events(events: list[dict]) -> dict:
    """What a later day is scored against: recipients, outflow sizes, close pace."""
    outs = [event for event in events if event.get("type") in ("withdrawal", "transfer")]
    closes = sorted({
        (event["ts"], event.get("oid"))
        for event in events
        if event.get("type") == "order" and event.get("reduce_only")
    })
    seen: list = []
    uniq: list[int] = []
    for ts, oid in closes:
        if oid not in seen[-500:]:
            uniq.append(ts)
            seen.append(oid)
    busiest = 0
    start = 0
    for index in range(len(uniq)):
        while uniq[index] - uniq[start] >= 300:
            start += 1
        span = index - start + 1
        if span > busiest:
            busiest = span
    last = None
    if outs:
        last = max(event["ts"] for event in outs)
    return {
        "max_closes_5m": busiest,
        "recipients": sorted({event["to"] for event in outs}),
        "outflows": [event["amount"] for event in outs],
        "last_outflow_ts": last,
    }

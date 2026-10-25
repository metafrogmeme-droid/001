"""Snapshot a Hyperliquid account and diff it. No network unless ``fetch`` does it.

The first snapshot is a baseline: info-level notes on what is already listed.
Later snapshots report changes. A value that was not read is left out. It is
not treated as zero.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Optional

from bot.claw_watch_hl.client import post_info

# maxBuilderFee is in tenths of a basis point. 0.1% = 10 bp = 100 tenths.
PERP_BUILDER_CAP = 100
AGENT_EXPIRY_WARN_MS = 7 * 24 * 3600 * 1000
AGENT_MAX_MS = 180 * 24 * 3600 * 1000
LEDGER_LOOKBACK_MS = 7 * 24 * 3600 * 1000
# Past this, a baseline lists a count instead of one line per agent.
AGENT_LIST_CAP = 12
SUDDEN = Decimal("0.5")

OUTBOUND_TYPES = {
    "withdraw", "send", "spotTransfer", "internalTransfer", "subAccountTransfer",
    "vaultDeposit", "cStakingTransfer", "deployGasAuction",
}

Fetch = Callable[[dict], object]


def _alert(code: str, level: str, text: str, **kw) -> dict:
    return {"code": code, "level": level, "text": text, **kw}


def _dec(value) -> Optional[Decimal]:
    """A readable number, or None when the field was absent or not a number.

    ``0`` is a reading. ``None`` and ``""`` are not.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except InvalidOperation:
            return None
    return None


def _bp(fee) -> str:
    n = _dec(fee)
    if n is None:
        return "?"
    return format(n / Decimal(10), "f")


def _at_cap(fee) -> bool:
    n = _dec(fee)
    return n is not None and n >= PERP_BUILDER_CAP


def _expiry_ms(value):
    """Milliseconds, None when the field says there is no expiry, or ``unread``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "unread"
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return "unread"


def _soon(vu, now: int) -> bool:
    return isinstance(vu, int) and 0 < vu - now < AGENT_EXPIRY_WARN_MS


def _unusual(vu, now: int) -> bool:
    return isinstance(vu, int) and vu - now > AGENT_MAX_MS + 86_400_000


def snapshot(user: str, now_ms: Optional[int] = None, fetch: Optional[Fetch] = None,
             env: Optional[Mapping[str, str]] = None, ledger_lookback_ms: int = LEDGER_LOOKBACK_MS) -> dict:
    """One account, as the public info API returned it. ``fetch`` is one POST."""
    user = user.lower()
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    def _fetch(payload: dict):
        if fetch is not None:
            return fetch(payload)
        return post_info(payload, env=env)

    agents_raw = _fetch({"type": "extraAgents", "user": user}) or []
    builders_raw = _fetch({"type": "approvedBuilders", "user": user}) or []
    if not isinstance(agents_raw, list):
        raise ValueError("extraAgents")
    if not isinstance(builders_raw, list):
        raise ValueError("approvedBuilders")
    agents: dict = {}
    for row in agents_raw:
        if not isinstance(row, dict):
            continue
        addr = row.get("address")
        if not isinstance(addr, str) or not addr:
            continue
        agents[addr.lower()] = {"name": row.get("name", ""), "validUntil": row.get("validUntil")}
    builders_list = []
    for raw in builders_raw:
        if isinstance(raw, str) and raw:
            builders_list.append(raw.lower())
    maxfee = {
        b: _fetch({"type": "maxBuilderFee", "user": user, "builder": b})
        for b in builders_list
    }
    role_raw = _fetch({"type": "userRole", "user": user}) or {}
    role = role_raw.get("role") if isinstance(role_raw, dict) else None
    subs_raw = _fetch({"type": "subAccounts", "user": user}) or []
    subs = []
    if isinstance(subs_raw, list):
        for row in subs_raw:
            if not isinstance(row, dict):
                continue
            sub = row.get("subAccountUser") or ""
            if isinstance(sub, str) and sub:
                subs.append(sub.lower())
    ref_raw = _fetch({"type": "referral", "user": user}) or {}
    referred = None
    if isinstance(ref_raw, dict):
        rb = ref_raw.get("referredBy") or {}
        if isinstance(rb, dict):
            referrer = rb.get("referrer") or ""
            if isinstance(referrer, str) and referrer:
                referred = referrer.lower()
    ch_raw = _fetch({"type": "clearinghouseState", "user": user})
    account_value = None
    withdrawable = None
    margin_used = None
    positions: Optional[dict] = None
    if isinstance(ch_raw, dict):
        margin = ch_raw.get("marginSummary")
        if isinstance(margin, dict):
            account_value = margin.get("accountValue") if "accountValue" in margin else None
            margin_used = margin.get("totalMarginUsed") if "totalMarginUsed" in margin else None
        elif margin is None and "marginSummary" not in ch_raw:
            account_value = None
            margin_used = None
        withdrawable = ch_raw.get("withdrawable") if "withdrawable" in ch_raw else None
        positions = {}
        rows = ch_raw.get("assetPositions")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pos = row.get("position")
                if not isinstance(pos, dict):
                    continue
                coin = pos.get("coin")
                if not isinstance(coin, str) or not coin:
                    continue
                positions[coin] = {
                    "szi": pos.get("szi") if "szi" in pos else None,
                    "marginUsed": pos.get("marginUsed") if "marginUsed" in pos else None,
                }
        elif rows is not None:
            positions = None
    ledger_raw = _fetch({
        "type": "userNonFundingLedgerUpdates",
        "user": user,
        "startTime": now_ms - ledger_lookback_ms,
    }) or []
    ledger = []
    if isinstance(ledger_raw, list):
        for row in ledger_raw:
            if not isinstance(row, dict) or not row.get("hash"):
                continue
            delta = row.get("delta")
            ledger.append({
                "time": row.get("time"),
                "hash": row.get("hash"),
                "delta": delta if isinstance(delta, dict) else {},
            })
    return {
        "user": user,
        "taken_ms": now_ms,
        "agents": agents,
        "builders": {b: maxfee.get(b) for b in builders_list},
        "role": role,
        "subAccounts": sorted(subs),
        "referredBy": referred,
        "accountValue": account_value,
        "withdrawable": withdrawable,
        "marginUsed": margin_used,
        "positions": positions,
        "ledger": ledger,
    }


def _standing_wanted(old: Optional[dict], addr: str, agent: dict, now: int) -> bool:
    """True when a standing agent note is new, not a repeat of the last poll."""
    if old is None:
        return True
    prev = (old.get("agents") or {}).get(addr)
    if prev is None:
        return True
    if prev.get("validUntil") != agent.get("validUntil"):
        return True
    vu = _expiry_ms(agent.get("validUntil"))
    old_now = old.get("taken_ms")
    if isinstance(vu, int) and isinstance(old_now, int):
        if _soon(vu, now) and not _soon(vu, old_now):
            return True
    return False


def _agent_standing(addr: str, agent: dict, now: int) -> list[dict]:
    vu = _expiry_ms(agent.get("validUntil"))
    if vu == "unread":
        return [_alert(
            "HL_AGENT_EXPIRY_UNREAD", "info",
            f"Agent {addr} expiry could not be read", agent=addr,
        )]
    out = []
    if vu is None:
        out.append(_alert(
            "HL_AGENT_NO_EXPIRY", "attention",
            f"Agent {addr} has no expiry in the public data", agent=addr,
        ))
        return out
    if _unusual(vu, now):
        out.append(_alert(
            "HL_AGENT_EXPIRY_UNUSUAL", "attention",
            f"Agent {addr} valid > 180 days", agent=addr,
        ))
    if _soon(vu, now):
        out.append(_alert(
            "HL_AGENT_EXPIRES_SOON", "info",
            f"Agent {addr} expires within 7 days", agent=addr, validUntil=vu,
        ))
    return out


def _known_destinations(snap: dict) -> set[str]:
    known = set()
    user = snap.get("user")
    if isinstance(user, str) and user:
        known.add(user.lower())
    for raw in snap.get("subAccounts") or []:
        if isinstance(raw, str) and raw:
            known.add(raw.lower())
    return known


def _position_alerts(old: dict, new: dict) -> list[dict]:
    """Sudden size changes. A missing book is unread, not a flat book."""
    if "positions" not in old or "positions" not in new:
        return []
    before = old.get("positions")
    after = new.get("positions")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    out = []
    for coin, row in after.items():
        if not isinstance(row, dict):
            continue
        ns = _dec(row.get("szi"))
        if coin not in before:
            if ns is not None and ns != 0:
                out.append(_alert(
                    "HL_POSITION_CHANGED", "attention",
                    f"Position {coin} is open at size {row.get('szi')}", coin=coin,
                ))
            continue
        prev = before.get(coin)
        if not isinstance(prev, dict):
            continue
        osz = _dec(prev.get("szi"))
        if ns is None or osz is None or ns == osz:
            continue
        if osz == 0 or ns == 0 or abs(ns - osz) >= abs(osz) * SUDDEN:
            out.append(_alert(
                "HL_POSITION_CHANGED", "attention",
                f"Position {coin} size changed from {prev.get('szi')} to {row.get('szi')}",
                coin=coin,
            ))
    for coin, prev in before.items():
        if coin in after or not isinstance(prev, dict):
            continue
        osz = _dec(prev.get("szi"))
        if osz is not None and osz != 0:
            out.append(_alert(
                "HL_POSITION_CHANGED", "attention",
                f"Position {coin} is no longer listed (size was {prev.get('szi')})",
                coin=coin,
            ))
    return out


def _margin_alert(old: dict, new: dict) -> list[dict]:
    if "marginUsed" not in old or "marginUsed" not in new:
        return []
    om, nm = _dec(old.get("marginUsed")), _dec(new.get("marginUsed"))
    if om is None or nm is None or om == nm:
        return []
    if om == 0 or abs(nm - om) >= abs(om) * SUDDEN:
        return [_alert(
            "HL_MARGIN_CHANGED", "attention",
            f"Margin in use changed from {old.get('marginUsed')} to {new.get('marginUsed')}",
        )]
    return []


def diff(old: Optional[dict], new: dict) -> list[dict]:
    """Change alerts. ``old is None`` is the baseline, info-level on exposure."""
    out: list[dict] = []
    now = int(new["taken_ms"])
    o_agents = (old or {}).get("agents") or {}
    agents = new.get("agents") or {}
    summarize = old is None and len(agents) > AGENT_LIST_CAP
    standing_budget = AGENT_LIST_CAP
    if summarize:
        out.append(_alert(
            "HL_API_WALLETS_PRESENT", "info",
            f"{len(agents)} API wallets are listed. "
            f"Individual rows are limited to {AGENT_LIST_CAP}.",
            count=len(agents),
        ))
    listed_new = 0
    hidden_new = 0
    for addr, agent in agents.items():
        is_new = addr not in o_agents
        if is_new and not summarize:
            if old is not None and listed_new >= AGENT_LIST_CAP:
                hidden_new += 1
            else:
                listed_new += 1
                name = agent.get("name")
                out.append(_alert(
                    "HL_NEW_API_WALLET" if old else "HL_API_WALLET_PRESENT",
                    "attention" if old else "info",
                    f"API/agent wallet {addr} ('{name}') can place and cancel trades for this account"
                    " (cannot withdraw). If you did not approve it, revoke it in Hyperliquid settings.",
                    agent=addr, name=name, validUntil=agent.get("validUntil"),
                ))
        if not _standing_wanted(old, addr, agent, now):
            continue
        notes = _agent_standing(addr, agent, now)
        if not notes:
            continue
        if standing_budget <= 0:
            continue
        standing_budget -= len(notes)
        out.extend(notes)
    if hidden_new:
        out.append(_alert(
            "HL_AGENT_LIST_TRUNCATED", "info",
            f"{hidden_new} further API wallet rows were not listed one by one.",
            count=hidden_new,
        ))
    if old:
        for addr in o_agents.keys() - agents.keys():
            out.append(_alert(
                "HL_API_WALLET_REMOVED", "info",
                f"Agent {addr} no longer listed (revoked, expired or replaced)",
                agent=addr,
            ))
        for addr in o_agents.keys() & agents.keys():
            if o_agents[addr].get("validUntil") != agents[addr].get("validUntil"):
                out.append(_alert(
                    "HL_AGENT_EXPIRY_CHANGED", "attention",
                    f"Agent {addr} expiry changed",
                    agent=addr,
                    before=o_agents[addr].get("validUntil"),
                    after=agents[addr].get("validUntil"),
                ))
    o_b = (old or {}).get("builders") or {}
    builders = new.get("builders") or {}
    for b, fee in builders.items():
        if b not in o_b:
            out.append(_alert(
                "HL_NEW_BUILDER_APPROVAL" if old else "HL_BUILDER_APPROVAL_PRESENT",
                "attention" if (old or _at_cap(fee)) else "info",
                f"Builder {b} may charge up to {_bp(fee)} bp per fill on orders it routes"
                " (main-wallet signature was required).",
                builder=b, max_fee_tenths_bp=fee,
            ))
        elif fee != o_b[b]:
            raised = False
            nf, of = _dec(fee), _dec(o_b[b])
            if nf is not None and of is not None and nf > of:
                raised = True
            out.append(_alert(
                "HL_BUILDER_FEE_CHANGED", "attention" if raised else "info",
                f"Builder {b} max fee changed {o_b[b]} -> {fee} (tenths of bp)",
                builder=b,
            ))
        if _at_cap(fee):
            prev = o_b.get(b) if b in o_b else None
            if not _at_cap(prev):
                out.append(_alert(
                    "HL_BUILDER_FEE_AT_PERP_CAP", "attention",
                    f"Builder {b} approved at or above the 0.1% perp cap",
                    builder=b,
                ))
    if old:
        for b in o_b.keys() - builders.keys():
            out.append(_alert(
                "HL_BUILDER_REVOKED", "info",
                f"Builder {b} approval removed", builder=b,
            ))
        if old.get("role") != new.get("role"):
            out.append(_alert(
                "HL_ROLE_CHANGED", "attention",
                f"Account role changed {old.get('role')} -> {new.get('role')}",
            ))
        fresh_subs = set(new.get("subAccounts") or []) - set(old.get("subAccounts") or [])
        if fresh_subs:
            out.append(_alert(
                "HL_NEW_SUBACCOUNT", "info",
                "New sub-account(s)", sub=sorted(fresh_subs),
            ))
        if old.get("referredBy") != new.get("referredBy"):
            out.append(_alert(
                "HL_REFERRER_CHANGED", "info",
                "Referrer changed",
                before=old.get("referredBy"), after=new.get("referredBy"),
            ))
        ov, nv = _dec(old.get("accountValue")), _dec(new.get("accountValue"))
        if ov is not None and nv is not None and ov > 0 and nv < ov * SUDDEN:
            out.append(_alert(
                "HL_ACCOUNT_VALUE_DROP_50PCT", "attention",
                f"Perp account value fell from {ov} to {nv} since last poll",
            ))
        out.extend(_position_alerts(old, new))
        out.extend(_margin_alert(old, new))
    elif isinstance(new.get("positions"), dict) and new.get("positions"):
        out.append(_alert(
            "HL_POSITIONS_PRESENT", "info",
            f"{len(new['positions'])} open position(s) on this account.",
            count=len(new["positions"]),
        ))
    seen = {e.get("hash") for e in (old or {}).get("ledger") or []}
    known = _known_destinations(new)
    if old is not None:
        for entry in new.get("ledger") or []:
            delta = entry.get("delta") or {}
            kind = delta.get("type")
            if entry.get("hash") in seen or kind not in OUTBOUND_TYPES:
                continue
            dest_raw = delta.get("destination") or delta.get("vault") or delta.get("user") or ""
            dest = dest_raw.lower() if isinstance(dest_raw, str) else ""
            if dest and dest in known:
                continue
            amount = delta.get("usdc")
            if amount is None:
                amount = delta.get("amount")
            amount_s = "" if amount is None else str(amount)
            token = delta.get("token") or ""
            where = dest or "L1/bridge"
            out.append(_alert(
                "HL_FUNDS_MOVED_OUT", "attention",
                f"Ledger: {kind} {amount_s} {token} -> {where}",
                hash=entry.get("hash"), time=entry.get("time"), delta=delta,
            ))
    return out

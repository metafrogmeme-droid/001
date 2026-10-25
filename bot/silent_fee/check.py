"""One address in, one reading out. No key, no signature, no order.

Documented info calls run whenever the main flag is on. ``extraAgents``,
``userDetails`` and explorer ``txDetails`` run only when the explorer flag
is also on. A fill that cannot be tied to a builder stays unattributed.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from bot.silent_fee.address import (
    NOT_AN_ADDRESS,
    SECRET_REPLY,
    WINDOW_REPLY,
    looks_like_secret,
    parse_address,
)
from bot.silent_fee.advisory import load_advisories, match_advisories
from bot.silent_fee.client import explorer_url, info_url, urllib_transport
from bot.silent_fee.flag import explorer_on, flag_on, mainnet_read_on
from bot.silent_fee.units import _as_f, bp_of, headroom, per_10k_usdc, percent_of
from bot.silent_fee.units import _text as _dec_text

_WINDOWS = (30, 90)
_DAY_MS = 86_400_000
_PAGE = 2000
_MAX_PAGES = 4
_MAX_ACCOUNTS = 8
_MAX_HASHES = 15
_BUDGET_S = 45
_SUB_KEYS = ("subAccountUser", "user", "address")
_REVOKE = "https://app.hyperliquid.xyz/builderCodes"
_FOOTER = "Information, not advice."


def _base(available: bool) -> dict:
    return {
        "available": available,
        "signs": False,
        "ai_written": False,
        "footer": _FOOTER,
        "revoke_at": _REVOKE,
    }


def _call(transport, url: str, body: dict, deadline: float):
    """``(payload, None)`` or ``(None, class name)``. The message is dropped."""
    if time.monotonic() > deadline:
        return None, "deadline"
    try:
        return transport(url, body), None
    except Exception as exc:
        return None, type(exc).__name__


def _decimal(raw) -> Optional[Decimal]:
    if isinstance(raw, bool) or raw is None or isinstance(raw, float):
        return None
    if isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text or len(text) > 40:
            return None
        try:
            value = Decimal(text)
        except Exception:
            return None
    else:
        return None
    if not value.is_finite():
        return None
    return value


def _builder_fee(row: dict) -> tuple[str, Optional[Decimal]]:
    """``absent`` is the documented zero. A bad token is unreadable."""
    if "builderFee" not in row or row.get("builderFee") is None:
        return "absent", Decimal(0)
    amount = _decimal(row.get("builderFee"))
    if amount is None or amount < 0:
        return "bad", None
    return "present", amount


def _date(ts) -> Optional[str]:
    if isinstance(ts, bool) or not isinstance(ts, int):
        return None
    seconds = ts // 1000 if ts > 10**12 else ts
    if seconds < 0 or seconds > 10**11:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d")


def _page_fills(transport, url: str, user: str, start: int, end: int, deadline: float):
    rows: list[dict] = []
    seen: set[tuple] = set()
    cursor = end
    complete = True
    reason = None
    for _ in range(_MAX_PAGES):
        payload, err = _call(transport, url, {
            "type": "userFillsByTime",
            "user": user,
            "startTime": start,
            "endTime": cursor,
            "aggregateByTime": False,
        }, deadline)
        if err is not None or not isinstance(payload, list):
            return rows, False, err or "unreadable"
        fresh = 0
        times: list[int] = []
        for row in payload:
            if not isinstance(row, dict):
                complete = False
                reason = "unreadable"
                continue
            tid = row.get("tid")
            key: tuple
            if isinstance(tid, int) and not isinstance(tid, bool):
                key = ("tid", tid)
            else:
                key = (
                    "row", row.get("hash"), row.get("time"), row.get("coin"),
                    row.get("sz"), row.get("px"), row.get("side"),
                )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            fresh += 1
            stamp = row.get("time")
            if isinstance(stamp, int) and not isinstance(stamp, bool):
                times.append(stamp)
        if len(payload) < _PAGE:
            return rows, complete, reason
        if not times:
            return rows, False, "unreadable"
        oldest = min(times)
        nxt = oldest - 1
        if nxt <= start or nxt >= cursor:
            return rows, complete, reason
        cursor = nxt
        if fresh == 0:
            return rows, False, "unreadable"
    return rows, False, "page_cap"


def _sum_fees(rows: list[dict]) -> dict:
    total = Decimal(0)
    fills = 0
    bad = 0
    kept: list[dict] = []
    for row in rows:
        kind, amount = _builder_fee(row)
        if kind == "bad":
            bad += 1
            continue
        if kind == "present" and amount is not None and amount > 0:
            fills += 1
            total += amount
            kept.append(row)
    return {
        "builder_fee_usdc": _dec_text(total),
        "builder_fee_fills": fills,
        "builder_fee_unreadable_rows": bad,
        "builder_fee_rows": kept,
        "_total": total,
    }


def _sub_addresses(payload, master: str):
    if payload is None:
        return [], None
    if not isinstance(payload, list):
        return None, "unreadable"
    found: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            return None, "unreadable"
        picked = None
        for key in _SUB_KEYS:
            if key not in item or item[key] is None:
                continue
            if not isinstance(item[key], str):
                return None, "unreadable"
            picked = parse_address(item[key])
            if picked is None:
                return None, "unreadable"
            break
        if picked is None:
            return None, "unreadable"
        if picked != master and picked not in found:
            found.append(picked)
    return found, None


def _role(payload, queried: str) -> dict:
    if not isinstance(payload, dict):
        return {"read": False, "reason": "unreadable", "role": None, "master": queried}
    role = payload.get("role")
    if not isinstance(role, str) or not role.strip():
        return {"read": False, "reason": "unreadable", "role": None, "master": queried}
    master = queried
    if role == "agent":
        data = payload.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        parsed = parse_address(user) if isinstance(user, str) else None
        if parsed is None:
            return {
                "read": True, "role": "agent", "master": None,
                "reason": "master_unreadable",
            }
        master = parsed
    return {"read": True, "role": role, "master": master, "reason": None}


def _referral(payload, table) -> dict:
    if not isinstance(payload, dict):
        return {"read": False, "reason": "unreadable"}
    referred = payload.get("referredBy")
    code = None
    referrer = None
    if referred is not None:
        if not isinstance(referred, dict):
            return {"read": False, "reason": "unreadable"}
        raw_code = referred.get("code")
        raw_who = referred.get("referrer")
        if raw_code is not None and not isinstance(raw_code, str):
            return {"read": False, "reason": "unreadable"}
        if isinstance(raw_code, str) and raw_code.strip():
            code = raw_code.strip()
        if raw_who is not None:
            if not isinstance(raw_who, str):
                return {"read": False, "reason": "unreadable"}
            referrer = parse_address(raw_who)
            if referrer is None:
                return {"read": False, "reason": "unreadable"}
    state = payload.get("referrerState")
    own_code = None
    if isinstance(state, dict):
        data = state.get("data")
        raw_own = data.get("code") if isinstance(data, dict) else None
        if isinstance(raw_own, str) and raw_own.strip():
            own_code = raw_own.strip()
    hits = match_advisories(table, builder=referrer, code=code or own_code)
    return {
        "read": True,
        "code": code,
        "referrer": referrer,
        "own_code": own_code,
        "advisories": hits,
    }


def _user_fees(payload) -> dict:
    if not isinstance(payload, dict):
        return {"read": False, "reason": "unreadable"}
    out: dict = {"read": True, "user_cross_rate": None, "active_referral_discount": None}
    for key, dest in (
        ("userCrossRate", "user_cross_rate"),
        ("activeReferralDiscount", "active_referral_discount"),
    ):
        if key not in payload or payload[key] is None:
            continue
        amount = _decimal(payload[key])
        if amount is None:
            return {"read": False, "reason": "unreadable"}
        out[dest] = _dec_text(amount)
    return out


def _agents(payload) -> dict:
    if not isinstance(payload, list):
        return {"read": False, "reason": "unreadable"}
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            return {"read": False, "reason": "unreadable"}
        raw_addr = item.get("address", item.get("agentAddress"))
        raw_name = item.get("name", item.get("agentName"))
        row: dict = {}
        if raw_addr is not None:
            if not isinstance(raw_addr, str):
                return {"read": False, "reason": "unreadable"}
            parsed = parse_address(raw_addr)
            if parsed is None:
                return {"read": False, "reason": "unreadable"}
            row["address"] = parsed
        if isinstance(raw_name, str) and raw_name.strip():
            row["name"] = raw_name.strip()[:64]
        if not row:
            return {"read": False, "reason": "unreadable"}
        rows.append(row)
    return {"read": True, "agents": rows}


def _hash_of(row: dict) -> Optional[str]:
    raw = row.get("hash")
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    if len(text) != 66 or not text.startswith("0x"):
        return None
    if any(ch not in "0123456789abcdef" for ch in text[2:]):
        return None
    return text


def _builder_from_tx(payload) -> tuple[Optional[str], Optional[int]]:
    if not isinstance(payload, dict):
        return None, None
    tx = payload.get("tx") if isinstance(payload.get("tx"), dict) else payload
    action = tx.get("action") if isinstance(tx, dict) else None
    if not isinstance(action, dict):
        return None, None
    builder = action.get("builder")
    if not isinstance(builder, dict):
        return None, None
    raw_b = builder.get("b")
    addr = parse_address(raw_b) if isinstance(raw_b, str) else None
    fee = _as_f(builder.get("f"))
    return addr, fee


def _approval_date(payload, builder: str) -> Optional[str]:
    found: list[int] = []

    def walk(node, depth: int) -> None:
        if depth > 8:
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if not isinstance(node, dict):
            return
        action = node.get("action") if isinstance(node.get("action"), dict) else None
        kind = action.get("type") if isinstance(action, dict) else node.get("type")
        src = action if isinstance(action, dict) and action.get("type") == "approveBuilderFee" else node
        if kind == "approveBuilderFee" and isinstance(src, dict):
            who = src.get("builder")
            parsed = parse_address(who) if isinstance(who, str) else None
            stamp = node.get("time")
            if parsed == builder and isinstance(stamp, int) and not isinstance(stamp, bool):
                found.append(stamp)
        for child in node.values():
            walk(child, depth + 1)

    walk(payload, 0)
    if not found:
        return None
    return _date(min(found))


def _attribute(transport, rows: list[dict], deadline: float) -> dict:
    groups: dict[str, dict] = {}
    unattributed = Decimal(0)
    no_hash = 0
    order: list[str] = []
    for row in rows:
        kind, amount = _builder_fee(row)
        if kind != "present" or amount is None or amount <= 0:
            continue
        digest = _hash_of(row)
        if digest is None:
            unattributed += amount
            no_hash += 1
            continue
        slot = groups.get(digest)
        if slot is None:
            slot = {"paid": Decimal(0), "n": 0, "times": []}
            groups[digest] = slot
            order.append(digest)
        slot["paid"] += amount
        slot["n"] += 1
        stamp = row.get("time")
        if isinstance(stamp, int) and not isinstance(stamp, bool):
            slot["times"].append(stamp)
    cache: dict[str, tuple[Optional[str], Optional[int], Optional[str]]] = {}
    asked = 0
    capped = False
    for digest in order:
        if asked >= _MAX_HASHES or time.monotonic() > deadline:
            capped = True
            break
        asked += 1
        payload, err = _call(transport, explorer_url(), {"type": "txDetails", "hash": digest}, deadline)
        if err is not None:
            cache[digest] = (None, None, err)
            continue
        addr, fee = _builder_from_tx(payload)
        if addr is None:
            cache[digest] = (None, None, "unattributed")
            continue
        cache[digest] = (addr, fee, None)
    by_builder: dict[str, dict] = {}
    for digest in order:
        slot = groups[digest]
        got = cache.get(digest)
        addr = None if got is None else got[0]
        if got is None or addr is None:
            unattributed += slot["paid"]
            continue
        fee = got[1]
        bucket = by_builder.get(addr)
        if bucket is None:
            bucket = {"paid": Decimal(0), "n": 0, "times": [], "fs": []}
            by_builder[addr] = bucket
        bucket["paid"] += slot["paid"]
        bucket["n"] += slot["n"]
        bucket["times"].extend(slot["times"])
        if fee is not None:
            bucket["fs"].append(fee)
    status = "read"
    if capped or no_hash or unattributed > 0:
        status = "partial"
    if not order and no_hash == 0 and unattributed == 0:
        status = "read"
    return {
        "status": status,
        "unattributed": unattributed,
        "by_builder": by_builder,
        "capped": capped,
    }


def _cap_fields(fee) -> dict:
    parsed = _as_f(fee)
    if parsed is None:
        return {
            "max_builder_fee": None,
            "cap_read": False,
            "cap_bp": None,
            "cap_percent": None,
            "cap_per_10k_usdc": None,
        }
    return {
        "max_builder_fee": parsed,
        "cap_read": True,
        "cap_bp": _dec_text(bp_of(parsed)),
        "cap_percent": _dec_text(percent_of(parsed)),
        "cap_per_10k_usdc": _dec_text(per_10k_usdc(parsed)),
    }


def _paid_row(builder: str, bucket: dict, caps: dict, approval) -> dict:
    charged = max(bucket["fs"]) if bucket["fs"] else None
    known = caps.get(builder, {}).get("cap_read") is True
    cap = caps.get(builder, {}).get("max_builder_fee") if known else None
    gap = headroom(cap, charged) if known else None
    times = bucket["times"]
    charged_bp = _dec_text(bp_of(charged)) if charged is not None else None
    charged_percent = _dec_text(percent_of(charged)) if charged is not None else None
    row = {
        "builder": builder,
        "paid_usdc": _dec_text(bucket["paid"]),
        "n_fills": bucket["n"],
        "charged_f": sorted(set(bucket["fs"])),
        "charged_bp": gap["charged_bp"] if gap else charged_bp,
        "charged_percent": gap["charged_percent"] if gap else charged_percent,
        "approved_cap_bp": None if gap is None else gap["cap_bp"],
        "headroom_bp": None if gap is None else gap["headroom_bp"],
        "headroom_per_10k_usdc": None if gap is None else gap["headroom_per_10k_usdc"],
        "first_seen": _date(min(times)) if times else None,
        "last_seen": _date(max(times)) if times else None,
        "approval_signed_at": approval,
    }
    if bucket["fs"] and (min(bucket["fs"]) != max(bucket["fs"])):
        row["charged_f_min"] = min(bucket["fs"])
        row["charged_f_max"] = max(bucket["fs"])
    return row


def _headline(accounts: list[dict]) -> dict:
    best = None
    best_gap = None
    for account in accounts:
        for row in account.get("builders_paid") or []:
            if row.get("headroom_bp") is None:
                continue
            numeric = _decimal(row.get("headroom_bp"))
            if numeric is None:
                continue
            if best_gap is None or numeric < best_gap:
                best_gap = numeric
                best = row
    if best is None:
        return {
            "signal": "unread",
            "reason": "charged_rate_not_read",
            "builder": None,
            "headroom_bp": None,
            "headroom_per_10k_usdc": None,
            "approved_cap_bp": None,
            "charged_bp": None,
        }
    return {
        "signal": "headroom",
        "reason": None,
        "builder": best["builder"],
        "headroom_bp": best["headroom_bp"],
        "headroom_per_10k_usdc": best["headroom_per_10k_usdc"],
        "approved_cap_bp": best["approved_cap_bp"],
        "charged_bp": best["charged_bp"],
    }


def _one_account(transport, url: str, user: str, start: int, end: int, table, explore: bool, deadline: float) -> dict:
    account: dict = {"address": user}
    approved, err = _call(transport, url, {"type": "approvedBuilders", "user": user}, deadline)
    builders: list[dict] = []
    caps: dict[str, dict] = {}
    if err is not None or not isinstance(approved, list):
        account["approved_builders_read"] = False
        account["approved_builders"] = []
    else:
        account["approved_builders_read"] = True
        for item in approved:
            if not isinstance(item, str):
                account["approved_builders_read"] = False
                break
            parsed = parse_address(item)
            if parsed is None:
                account["approved_builders_read"] = False
                break
            fee_payload, fee_err = _call(
                transport, url,
                {"type": "maxBuilderFee", "user": user, "builder": parsed},
                deadline,
            )
            fields = _cap_fields(fee_payload if fee_err is None else None)
            if fee_err is not None:
                fields = _cap_fields(None)
            row = {"builder": parsed, **fields}
            builders.append(row)
            caps[parsed] = fields
        account["approved_builders"] = builders if account["approved_builders_read"] else []
    referral_payload, referral_err = _call(transport, url, {"type": "referral", "user": user}, deadline)
    account["referral"] = (
        {"read": False, "reason": referral_err or "unreadable"}
        if referral_err is not None else _referral(referral_payload, table)
    )
    fees_payload, fees_err = _call(transport, url, {"type": "userFees", "user": user}, deadline)
    account["user_fees"] = (
        {"read": False, "reason": fees_err or "unreadable"}
        if fees_err is not None else _user_fees(fees_payload)
    )
    rows, complete, _reason = _page_fills(transport, url, user, start, end, deadline)
    summed = _sum_fees(rows)
    if summed["builder_fee_unreadable_rows"]:
        complete = False
    account["builder_fee_usdc"] = summed["builder_fee_usdc"]
    account["builder_fee_fills"] = summed["builder_fee_fills"]
    account["builder_fee_unreadable_rows"] = summed["builder_fee_unreadable_rows"]
    account["builder_fee_complete"] = complete
    if not explore:
        account["attribution"] = "off"
        account["unattributed_usdc"] = summed["builder_fee_usdc"]
        account["builders_paid"] = []
        account["extra_agents"] = {"read": False, "reason": "undocumented_read_off"}
        return account
    attributed = _attribute(transport, summed["builder_fee_rows"], deadline)
    details, details_err = _call(transport, explorer_url(), {"type": "userDetails", "user": user}, deadline)
    agents_payload, agents_err = _call(transport, url, {"type": "extraAgents", "user": user}, deadline)
    account["extra_agents"] = (
        {"read": False, "reason": agents_err or "unreadable"}
        if agents_err is not None else _agents(agents_payload)
    )
    paid = []
    for builder, bucket in sorted(attributed["by_builder"].items()):
        if details_err is not None or details is None:
            signed = "unknown"
        else:
            signed = _approval_date(details, builder) or "unknown"
        paid.append(_paid_row(builder, bucket, caps, signed))
    account["builders_paid"] = paid
    account["unattributed_usdc"] = _dec_text(attributed["unattributed"])
    account["attribution"] = attributed["status"] if complete else "partial"
    return account


def _notices(accounts: list[dict], table) -> list[dict]:
    if table is None:
        return []
    first: dict[str, Optional[str]] = {}
    for account in accounts:
        for row in account.get("builders_paid") or []:
            builder = row.get("builder")
            if isinstance(builder, str) and builder not in first:
                first[builder] = row.get("first_seen")
    seen: set = set()
    notices: list[dict] = []

    def add(addr: Optional[str], code: Optional[str], when) -> None:
        hits = match_advisories(table, builder=addr, code=code)
        if not hits:
            return
        for hit in hits:
            key = (hit["id"], addr or "", code or "", tuple(hit["matched_on"]))
            if key in seen:
                continue
            seen.add(key)
            notices.append({
                "address": addr or "",
                "code": code or "",
                "hit": hit,
                "first_seen": when if "builder" in hit["matched_on"] else None,
            })

    for account in accounts:
        for row in account.get("approved_builders") or []:
            add(row.get("builder"), None, first.get(row.get("builder")))
        for row in account.get("builders_paid") or []:
            add(row.get("builder"), None, row.get("first_seen"))
        referral_raw = account.get("referral")
        referral = referral_raw if isinstance(referral_raw, dict) else {}
        if referral.get("read") is True:
            add(referral.get("referrer"), referral.get("code"), None)
            if referral.get("own_code") and referral.get("own_code") != referral.get("code"):
                add(None, referral.get("own_code"), None)
    return notices


def read_account(
    address: str,
    *,
    days: int = 30,
    transport=None,
    env=None,
    now_ms: Optional[int] = None,
) -> dict:
    """The reading for one address. Off unless the main flag is on.

    ``transport(url, body)`` replaces the network. Tests pass one. Production
    uses the stdlib POST and nothing else.
    """
    if not flag_on(env):
        return _base(False)
    if not isinstance(address, str) or looks_like_secret(address):
        out = _base(True)
        out.update({"refused": "secret", "message": SECRET_REPLY})
        return out
    parsed = parse_address(address)
    if parsed is None:
        out = _base(True)
        out.update({"refused": "address", "message": NOT_AN_ADDRESS})
        return out
    if days not in _WINDOWS:
        out = _base(True)
        out.update({"refused": "window", "message": WINDOW_REPLY})
        return out
    network = "mainnet" if mainnet_read_on(env) else "testnet"
    url = info_url(network)
    talk = transport if transport is not None else urllib_transport
    now = now_ms if isinstance(now_ms, int) and not isinstance(now_ms, bool) else int(time.time() * 1000)
    start = now - days * _DAY_MS
    deadline = time.monotonic() + _BUDGET_S
    table = load_advisories()
    role_payload, role_err = _call(talk, url, {"type": "userRole", "user": parsed}, deadline)
    role = (
        {"read": False, "reason": role_err or "unreadable", "role": None, "master": parsed}
        if role_err is not None else _role(role_payload, parsed)
    )
    result = _base(True)
    result.update({
        "network": network,
        "info_url": url,
        "window_days": days,
        "queried": parsed,
        "role": {k: v for k, v in role.items() if k != "reason" or v},
        "explorer": explorer_on(env),
        "advisory_table": (
            {"read": False, "ids": []} if table is None
            else {"read": True, "ids": [row["id"] for row in table]}
        ),
    })
    if role.get("reason") == "master_unreadable":
        result["sub_accounts"] = {"read": False, "reason": "master_unreadable", "addresses": []}
        result["accounts"] = []
        result["notices"] = []
        result["headline"] = _headline([])
        return result
    master_raw = role.get("master")
    master = master_raw if isinstance(master_raw, str) and master_raw else parsed
    sub_payload, sub_err = _call(talk, url, {"type": "subAccounts", "user": master}, deadline)
    if sub_err is not None:
        subs, sub_reason = None, sub_err
    else:
        subs, sub_reason = _sub_addresses(sub_payload, master)
    if subs is None:
        result["sub_accounts"] = {"read": False, "reason": sub_reason or "unreadable", "addresses": []}
        targets = [master]
    else:
        result["sub_accounts"] = {"read": True, "addresses": subs}
        targets = [master, *subs][:_MAX_ACCOUNTS]
        if len([master, *subs]) > _MAX_ACCOUNTS:
            result["sub_accounts"]["truncated"] = True
    explore = explorer_on(env)
    accounts = [
        _one_account(talk, url, user, start, now, table, explore, deadline)
        for user in targets
    ]
    result["accounts"] = accounts
    result["notices"] = _notices(accounts, table)
    result["headline"] = _headline(accounts)
    return result

"""The per-user watch: command answers and one poll.

Scoring does not open a socket. A poll does, and only for a testnet or a
local fork, and only while the weekly budget has room. The chat id is kept
in memory for delivery and stored only as an HMAC.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from bot.approval_hygiene import MAX_ADDRESSES, normalize_address
from bot.claw_watch_heuristics.alert import ALERT_CAP, alerts_of, render_alert
from bot.claw_watch_heuristics.chains import chain_allowed, chain_label
from bot.claw_watch_heuristics.flag import (
    CALLS_PER_WALLET_WEEK,
    STORE,
    configured_chain_id,
    flag_on,
    internal_enabled,
    min_poll_sec,
    rpc_url,
    salt_of,
)
from bot.claw_watch_heuristics.internal import read_internal
from bot.claw_watch_heuristics.reader import fetch_window, planned_calls
from bot.claw_watch_heuristics.score import DISCLAIMER, consider
from bot.claw_watch_heuristics.store import (
    budget_charge,
    budget_spent,
    cursor_of,
    has_seen,
    load_memory,
    mark_polled,
    mark_seen,
    poll_block,
    save_memory,
    set_cursor,
    watch_add,
    watch_all,
    watch_list,
    watch_remove,
)
from bot.daily_duel_store import player_key
from bot.utils.paths import env_state_path, state_path

_CHATS: dict[str, int] = {}


def remember_chat(chat_key: str, chat_id: object) -> None:
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        return
    _CHATS[chat_key] = chat_id


def chat_for(chat_id: object, env: Optional[dict] = None) -> Optional[str]:
    salt = salt_of(env)
    if salt is None or chat_id is None or isinstance(chat_id, bool):
        return None
    return player_key(str(chat_id), salt)


def store_path(env: Optional[dict] = None) -> str:
    """Absolute path. A relative override is anchored to the repo, not the cwd."""
    if env is None:
        # The name is a literal because the durable-path scan only recognises
        # that form. It is the same variable as STORE.
        return str(env_state_path("CLAW_WATCH_HEURISTICS_DB", "data/claw_watch_heuristics.db"))
    raw = env.get(STORE)
    if not isinstance(raw, str) or not raw.strip():
        return str(state_path("data/claw_watch_heuristics.db"))
    return str(state_path(raw.strip()))


def drain_addresses(chat_id: object) -> dict:
    """Addresses from Claw Watch, when that package is installed. This does not write it.

    ImportError is ``absent`` (the package is not on this tree). Any other
    failure is ``unreadable``, which is not an empty list.
    """
    try:
        from bot.drain_watch.service import salt_of as drain_salt
        from bot.drain_watch.service import store_path as drain_path
        from bot.drain_watch.store import DrainWatchStore
    except ImportError:
        return {"status": "absent", "addresses": None}
    except Exception:
        return {"status": "unreadable", "addresses": None}
    try:
        salt = drain_salt()
        if not salt or chat_id is None:
            return {"status": "absent", "addresses": None}
        key = player_key(str(chat_id), salt)
        rows = DrainWatchStore(drain_path()).addresses(key)
    except Exception:
        return {"status": "unreadable", "addresses": None}
    if rows is None:
        return {"status": "unreadable", "addresses": None}
    found = [row for row in rows if isinstance(row, str)]
    return {"status": "read", "addresses": found}


def _seen_key(finding: dict) -> str:
    tx_hash = finding.get("tx_hash") if isinstance(finding.get("tx_hash"), str) else ""
    stage = finding.get("stage") if isinstance(finding.get("stage"), str) else ""
    if tx_hash:
        return f"{tx_hash}:{stage}"
    for sig in finding.get("signals") or []:
        if not isinstance(sig, dict):
            continue
        ev_raw = sig.get("evidence")
        ev = ev_raw if isinstance(ev_raw, dict) else {}
        delegate = ev.get("delegate")
        if isinstance(delegate, str) and delegate:
            return f"code:{delegate}"
    return f"row:{stage}:{finding.get('score')}"


def describe(path: str, chat_key: Optional[str], chat_id: object, env: Optional[dict] = None) -> str:
    """Status text. A budget of 0 is spent-nothing. None is a failed read."""
    lines = [DISCLAIMER]
    chain_id = configured_chain_id(env)
    if chain_id is None or not chain_allowed(chain_id):
        lines.append("The chain id could not be read, or it is not a testnet or local fork.")
    else:
        label = chain_label(chain_id) or str(chain_id)
        lines.append(f"Chain: {label}.")
    lines.append("Internal transactions: on." if internal_enabled(env) else "Internal transactions: off.")
    if chat_key is None:
        lines.append("No salt is set, so this chat is not stored.")
        return "\n".join(lines)
    rows = watch_list(path, chat_key)
    if rows is None:
        lines.append("The watch list could not be read.")
    elif not rows:
        lines.append("No address is stored for this chat.")
    else:
        now = int(time.time())
        for address, row_chain in rows:
            spent = budget_spent(path, address, row_chain, now)
            if spent is None:
                lines.append(f"{address}: budget could not be read.")
            else:
                lines.append(f"{address}: budget used {spent} of {CALLS_PER_WALLET_WEEK} this week.")
    extra = drain_addresses(chat_id)
    if extra["status"] == "absent":
        lines.append("Claw Watch address list: absent.")
    elif extra["status"] == "unreadable":
        lines.append("Claw Watch address list: could not be read.")
    else:
        count = len(extra["addresses"] or [])
        lines.append(f"Claw Watch address list: {count}.")
    return "\n".join(lines)


def add_address(path: str, chat_key: Optional[str], address: str, env: Optional[dict] = None,
                now: Optional[int] = None) -> str:
    if chat_key is None:
        return "No salt is set, so this chat is not stored."
    chain_id = configured_chain_id(env)
    if chain_id is None or not chain_allowed(chain_id):
        return "This chain is not a testnet or local fork this watch reads."
    normal = normalize_address(address)
    if normal is None:
        return "That is not a wallet address. Send one address: 0x followed by 40 hex characters."
    stamp = int(time.time()) if now is None else now
    result = watch_add(path, chat_key, normal, chain_id, stamp, MAX_ADDRESSES)
    if result == "unreadable":
        return "The watch list could not be written."
    if result == "duplicate":
        return "That address is already stored."
    if result == "full":
        return f"This chat already stores {MAX_ADDRESSES} addresses, which is the maximum."
    return f"Stored {normal}. This chat can store {MAX_ADDRESSES}."


def remove_address(path: str, chat_key: Optional[str], address: str) -> str:
    if chat_key is None:
        return "No salt is set, so this chat is not stored."
    normal = normalize_address(address)
    if normal is None:
        return "That is not a wallet address. Send one address: 0x followed by 40 hex characters."
    result = watch_remove(path, chat_key, normal)
    if result is None:
        return "The watch list could not be written."
    if result is False:
        return "That address was not stored."
    return f"Removed {normal}."


async def poll_address(path: str, chat_key: str, address: str, chain_id: int,
                       env: Optional[dict], now: int, send, transport=None,
                       internal_transport=None) -> dict:
    """One address. ``send`` is an async callable and returns True when Telegram accepted it."""
    if not flag_on(env):
        return {"status": "disabled"}
    state, cursor = cursor_of(path, address, chain_id)
    if state == "unreadable":
        return {"status": "unreadable"}
    first = state == "missing"
    want_internal = internal_enabled(env) and not first and isinstance(cursor, int)
    planned = planned_calls(first, want_internal)
    blocked = poll_block(path, address, chain_id, now, min_poll_sec(env), planned)
    if blocked is not None:
        return {"status": blocked}
    fetched = await asyncio.to_thread(
        fetch_window, rpc_url(env), address, chain_id,
        None if first else cursor, transport, internal=want_internal,
    )
    calls = fetched.get("calls") if isinstance(fetched.get("calls"), int) else None
    if calls is None:
        return {"status": "unreadable"}
    if calls > 0 and not budget_charge(path, address, chain_id, now, calls):
        return {"status": "unreadable", "charged": calls}
    if calls > 0:
        mark_polled(path, address, chain_id, now)
    if fetched.get("status") != "read":
        return {"status": fetched.get("status") or "unreadable", "charged": calls}
    window = {
        "address": address,
        "chain_id": chain_id,
        "transactions": fetched.get("transactions") or [],
        "code": fetched.get("code"),
        "code_status": fetched.get("code_status") or "unreadable",
        "internal": {"status": "off", "reason": "disabled"},
    }
    if want_internal and isinstance(cursor, int):
        internal = await asyncio.to_thread(
            read_internal, address, chain_id, cursor, env, internal_transport,
        )
        window["internal"] = internal
        if internal.get("status") != "off":
            if not budget_charge(path, address, chain_id, now, 1):
                return {"status": "unreadable", "charged": calls + 1}
            calls += 1
    memory = load_memory(path, address, chain_id)
    result = consider(window, env, memory)
    if result.get("scored") is not True:
        return {"status": "not_scored", "charged": calls, "findings": None}
    fresh = []
    for finding in result.get("findings") or []:
        key = _seen_key(finding)
        seen = has_seen(path, address, chain_id, key)
        if seen is None:
            return {"status": "unreadable", "charged": calls}
        if seen:
            continue
        finding = dict(finding)
        finding["_seen_key"] = key
        fresh.append(finding)
    alertable = alerts_of(fresh)
    chat_id = _CHATS.get(chat_key)
    sent_keys: list[str] = []
    held = False
    if alertable and chat_id is None:
        held = True
    else:
        for finding in alertable[:ALERT_CAP]:
            try:
                ok = await send(chat_id, render_alert(finding))
            except Exception:
                ok = False
            if ok is not True:
                held = True
                break
            sent_keys.append(finding["_seen_key"])
        if len(alertable) > ALERT_CAP:
            held = True
    for key in sent_keys:
        mark_seen(path, address, chain_id, key)
    if not held:
        for finding in fresh:
            if finding.get("signal_strength") not in ("medium", "high"):
                mark_seen(path, address, chain_id, finding["_seen_key"])
        head = fetched.get("cursor_block")
        if isinstance(head, int) and not isinstance(head, bool):
            set_cursor(path, address, chain_id, head)
        if result.get("memory_status") == "read" and isinstance(result.get("memory"), dict):
            save_memory(path, address, chain_id, result["memory"])
    return {
        "status": "read",
        "charged": calls,
        "alerts": len(sent_keys),
        "held": held,
        "history": fetched.get("history"),
    }


def _targets(path: str, env: Optional[dict]) -> Optional[list[tuple[str, str, int]]]:
    rows = watch_all(path)
    if rows is None:
        return None
    chain_id = configured_chain_id(env)
    out = [(key, addr, cid) for key, addr, cid in rows if chain_allowed(cid)]
    seen = {(addr, cid) for _key, addr, cid in out}
    if chain_id is None or not chain_allowed(chain_id):
        return out
    for chat_key, _addr, _cid in rows:
        chat_id = _CHATS.get(chat_key)
        extra = drain_addresses(chat_id)
        if extra["status"] != "read":
            continue
        for addr in extra["addresses"] or []:
            normal = normalize_address(addr)
            if normal is None or (normal, chain_id) in seen:
                continue
            seen.add((normal, chain_id))
            out.append((chat_key, normal, chain_id))
    return out


async def poll_all(env: Optional[dict] = None, *, transport=None, internal_transport=None,
                   send=None, now: Optional[int] = None) -> dict:
    """Every stored address. Returns counts. A failed book is not zero addresses."""
    path = store_path(env)
    stamp = int(time.time()) if now is None else now
    rows = _targets(path, env)
    if rows is None:
        return {"status": "unreadable", "polled": None}
    if send is None:
        async def send(_chat_id, _text):
            return False
    polled = 0
    for chat_key, address, chain_id in rows:
        if not chain_allowed(chain_id):
            continue
        await poll_address(
            path, chat_key, address, chain_id, env, stamp, send, transport, internal_transport,
        )
        polled += 1
    return {"status": "read", "polled": polled}

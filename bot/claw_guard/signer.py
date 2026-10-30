"""The Guard signs the trade-only key. The bot sends an intent.

Nothing else reads the key. A block is not signed. A confirm on a
risk-increasing order is not signed until an admin approves that exact
order and the check passes again. A cancel, a reduce-only close, and the
kill switch are signed without waiting when they are not a block.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Mapping, Optional

from bot.claw_guard.confirm import (
    consume,
    grant,
    new_session,
    remember,
    timed_out,
)
from bot.claw_guard.evaluate import evaluate
from bot.claw_guard.flag import flag_on
from bot.claw_guard.keys import trade_key
from bot.claw_guard.network import default_endpoint, endpoint_refusal
from bot.claw_guard.payload import canonical, expired, expires_after_ok, payload_hash, signed_payload
from bot.claw_guard.verdicts import blocked

Poster = Callable[[Mapping[str, Any], str], None]


def submit_intent(
    action: Mapping[str, Any],
    account: Mapping[str, Any],
    *,
    now: float,
    caller_is_admin: bool,
    env: Optional[Mapping[str, str]] = None,
    origin: str = "user",
    session: Optional[dict[str, Any]] = None,
    approval: Optional[Mapping[str, Any]] = None,
    poster: Optional[Poster] = None,
) -> dict[str, Any]:
    """Evaluate, then sign only when this key is allowed to."""
    if not flag_on(env) and caller_is_admin is not True:
        return _refused(now)
    book = session if session is not None else new_session()
    decision = evaluate(action, account, now=now, origin=origin)
    result = dict(decision)
    result["signed"] = False
    result["sent"] = False
    result["waited"] = False
    result["refused"] = False
    result["preview"] = flag_on(env) is not True
    if decision.get("decision") is None:
        return result
    if decision.get("decision") == "BLOCK":
        return result

    identity = str(decision.get("order_hash") or "")
    if decision.get("decision") == "CONFIRM" and _waits(action, origin):
        result["waited"] = True
        if timed_out(book, identity, now):
            return _timeout(result, now)
        if approval is None:
            remember(book, identity, now)
            return result
        if caller_is_admin is not True:
            return result
        if approval.get("order_hash") != identity:
            return result
        if not consume(book, identity, approval):
            return result
        # The approval was for this order. The check runs again on the
        # account the caller just passed.
        again = evaluate(action, account, now=now, origin=origin)
        if again.get("order_hash") != identity or again.get("decision") == "BLOCK":
            result = dict(again)
            result["signed"] = False
            result["sent"] = False
            result["waited"] = False
            result["refused"] = False
            return result
        decision = again
        result = dict(again)
        result["signed"] = False
        result["sent"] = False
        result["waited"] = False
        result["refused"] = False

    if caller_is_admin is not True:
        return result
    endpoint = action.get("endpoint") or default_endpoint(action.get("venue"))
    chain_id = action.get("chain_id")
    if action.get("venue") == "polymarket" and action.get("chain_id") is None:
        chain_id = 80002
    if action.get("venue") != "polymarket" and action.get("chain_id") is None:
        chain_id = 421614
    refusal = endpoint_refusal(endpoint, chain_id, action.get("chain") or action.get("hyperliquidChain"))
    if refusal:
        result["decision"] = "BLOCK"
        result["text"] = blocked(refusal)
        result["rules"] = list(result.get("rules") or []) + [{
            "id": "NET", "level": "block", "text": blocked(refusal),
        }]
        return result
    key = trade_key(env, action.get("venue"))
    if key == "":
        return result
    payload = signed_payload(action, now, str(endpoint))
    if not expires_after_ok(payload, now):
        return result
    signature = _sign(payload, key)
    result["signed"] = True
    result["payload"] = payload
    result["payload_hash"] = payload_hash(payload)
    result["signature"] = signature
    if poster is not None:
        poster(payload, signature)
        result["sent"] = True
    return result


def bot_submit(
    action: Mapping[str, Any],
    account: Mapping[str, Any],
    *,
    now: float,
    caller_is_admin: bool,
    guard_up: bool,
    env: Optional[Mapping[str, str]] = None,
    origin: str = "user",
    session: Optional[dict[str, Any]] = None,
    approval: Optional[Mapping[str, Any]] = None,
    poster: Optional[Poster] = None,
) -> dict[str, Any]:
    """The bot's side. A down Guard sends nothing, including a kill switch.

    The kill switch runs inside the Guard (`submit_intent`), so it still
    flattens when the bot cannot reach a separate process.
    """
    if guard_up is not True:
        return {
            "decision": None,
            "rules": [],
            "text": "",
            "signed": False,
            "sent": False,
            "waited": False,
            "refused": False,
            "not_sent": True,
            "order_hash": "",
            "ts": now,
            "out_of_scope": None,
        }
    return submit_intent(
        action, account, now=now, caller_is_admin=caller_is_admin,
        env=env, origin=origin, session=session, approval=approval, poster=poster,
    )


def reject_resubmit(payload: Mapping[str, Any], now: float) -> bool:
    """A signed payload past ``expiresAfter`` is not sent again."""
    return expired(payload, now)


def approve(session: dict[str, Any], order_hash: str, now: float, *, admin: bool) -> Optional[dict[str, Any]]:
    return grant(session, order_hash, now, admin=admin)


def _waits(action: Mapping[str, Any], origin: str) -> bool:
    if origin == "kill_switch":
        return False
    kind = str(action.get("type") or "")
    if kind in {"cancel", "cancelByCloid", "cancelByOid"}:
        return False
    if action.get("reduce_only") is True and kind in {"order", "modify", "trigger", "trailingStop"}:
        return False
    orders = action.get("orders")
    if isinstance(orders, list) and orders and all(
        isinstance(row, Mapping) and row.get("reduce_only") is True for row in orders
    ):
        return False
    return True


def _timeout(result: dict[str, Any], now: float) -> dict[str, Any]:
    text = blocked("no OK received within 60 s.")
    result["decision"] = "BLOCK"
    result["text"] = text
    result["rules"] = list(result.get("rules") or []) + [{"id": "TIMEOUT", "level": "block", "text": text}]
    result["signed"] = False
    result["sent"] = False
    result["waited"] = False
    result["ts"] = now
    return result


def _refused(now: float) -> dict[str, Any]:
    return {
        "decision": None,
        "rules": [],
        "order_hash": "",
        "ts": now,
        "text": "",
        "out_of_scope": None,
        "signed": False,
        "sent": False,
        "waited": False,
        "refused": True,
        "preview": False,
    }


def _sign(payload: Mapping[str, Any], key: str) -> str:
    """Sign the payload. The key is the HMAC secret and is not returned."""
    raw = canonical(payload).encode("utf-8")
    return hmac.new(key.encode("utf-8"), raw, hashlib.sha256).hexdigest()

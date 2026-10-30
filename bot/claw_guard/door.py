"""The bot's door. An intent comes in. The order function does not sign it.

``origin`` is set by the caller. A field on the intent is not an origin,
and a client tag is not the kill switch.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from bot.claw_guard.service import check_body
from bot.claw_guard.signer import approve, bot_submit, reject_resubmit
from bot.claw_guard.verdicts import blocked

_SESSION: dict[str, Any] = {"pending": {}}


def accept_intent(
    intent: Mapping[str, Any],
    account: Optional[Mapping[str, Any]],
    *,
    user_id: str = "",
    caller_is_admin: bool = False,
    origin: str = "user",
    now: float = 0.0,
    env: Optional[Mapping[str, str]] = None,
    session: Optional[dict[str, Any]] = None,
    approval_at: Optional[float] = None,
) -> str:
    """The sentence the bot shows. The signature does not leave this process."""
    del user_id
    try:
        text = _run(
            intent, account, caller_is_admin=caller_is_admin, origin=origin,
            now=now, env=env, session=session, approval_at=approval_at,
        )
    except Exception as exc:
        return blocked(f"the check could not be finished ({type(exc).__name__}).")
    return text


def _run(
    intent: Mapping[str, Any],
    account: Optional[Mapping[str, Any]],
    *,
    caller_is_admin: bool,
    origin: str,
    now: float,
    env: Optional[Mapping[str, str]],
    session: Optional[dict[str, Any]],
    approval_at: Optional[float],
) -> str:
    if not isinstance(intent, Mapping):
        return blocked("that order could not be read.")
    if str(intent.get("type") or "") == "resting":
        check_body({"op": "resting", "now": now})
        return ""
    if account is None:
        return blocked("that account could not be read.")
    action = {key: value for key, value in intent.items() if key not in {"origin", "client_tag", "cloid"}}
    resubmit = action.pop("resubmit", None)
    if isinstance(resubmit, Mapping) and reject_resubmit(resubmit, now):
        return blocked("this order has expired.")
    book = session if session is not None else _SESSION
    approval = None
    if approval_at is not None:
        from bot.claw_guard.payload import order_hash
        approval = approve(book, order_hash(action), float(approval_at), admin=caller_is_admin)
    result = bot_submit(
        action, account, now=now, caller_is_admin=caller_is_admin,
        guard_up=True, env=env, origin=origin, session=book, approval=approval,
    )
    payload = result.get("payload")
    if isinstance(payload, dict) and reject_resubmit(payload, now):
        return blocked("this order has expired.")
    if result.get("refused") is True:
        return blocked("this check is not open.")
    if result.get("not_sent") is True:
        return ""
    if result.get("decision") is None:
        return ""
    text = result.get("text")
    return text if isinstance(text, str) else ""

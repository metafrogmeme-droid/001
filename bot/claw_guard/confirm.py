"""Confirm wait. One approval, one order, sixty seconds.

An unanswered confirm becomes a block. Approving runs the check again
against the account the caller passes, which is the current state. The
approval matches that order's hash and is spent on use. A cancel, a
reduce-only close, and the kill switch do not wait.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

CONFIRM_TIMEOUT_S = 60.0


def new_session() -> dict[str, Any]:
    return {"pending": {}}


def remember(session: dict[str, Any], order_hash: str, now: float) -> None:
    pending = session.setdefault("pending", {})
    row = pending.get(order_hash)
    if isinstance(row, dict) and row.get("status") == "open":
        return
    pending[order_hash] = {"ts": now, "status": "open", "used": False}


def timed_out(session: Mapping[str, Any], order_hash: str, now: float) -> bool:
    row = _row(session, order_hash)
    if row is None or row.get("status") != "open":
        return False
    ts = row.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False
    return float(now) - float(ts) >= CONFIRM_TIMEOUT_S


def grant(
    session: dict[str, Any],
    order_hash: str,
    now: float,
    *,
    admin: bool,
) -> Optional[dict[str, Any]]:
    """A single-use approval. None when the caller is not an admin, the
    confirm is missing, or the 60 s have already passed."""
    if admin is not True:
        return None
    row = _row(session, order_hash)
    if row is None or row.get("status") != "open" or row.get("used") is True:
        return None
    ts = row.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    if float(now) - float(ts) >= CONFIRM_TIMEOUT_S:
        row["status"] = "blocked"
        return None
    approval: dict[str, Any] = {"order_hash": order_hash, "ts": now, "used": False}
    row["approval"] = approval
    return approval


def consume(session: dict[str, Any], order_hash: str, approval: Mapping[str, Any]) -> bool:
    """Spend the approval. False if it does not match this order or was used."""
    row = _row(session, order_hash)
    if row is None:
        return False
    stored = row.get("approval")
    if not isinstance(stored, dict):
        return False
    if stored.get("used") is True or approval.get("used") is True:
        return False
    if stored.get("order_hash") != order_hash or approval.get("order_hash") != order_hash:
        return False
    stored["used"] = True
    row["status"] = "spent"
    row["used"] = True
    return True


def _row(session: Mapping[str, Any], order_hash: str) -> Optional[dict[str, Any]]:
    pending = session.get("pending")
    if not isinstance(pending, dict):
        return None
    row = pending.get(order_hash)
    return row if isinstance(row, dict) else None

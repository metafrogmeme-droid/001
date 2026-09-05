"""Free-tier chat quota — N questions/day per user, then an upgrade prompt.

Free (basic-tier) web users get a small number of AI chat questions per UTC day
on the operator-funded model (xAI Grok). Paid tiers (pro/elite) and the admin are
exempt. This is the spend fence around the operator's prepaid Grok budget: at $20
of grok-4.3 (~$1.25/$2.50 per MTok ≈ 16M in / 8M out), an uncapped free chat would
drain it in a day — the per-user daily cap bounds it instead.

State is a tiny JSON file ({uid: {"day": "YYYY-MM-DD", "n": int}}), written
atomically. Counting is per UTC day and resets automatically when the day rolls.
Deliberately simple and dependency-free — this is a soft product limit, not a
security control, so an approximate count that never crashes chat is the goal.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from bot.utils.paths import env_state_path
from typing import Optional

from bot.utils.atomic_write import atomic_write_json

# Free users get this many AI questions per UTC day. Operator-overridable so the
# limit can be tuned to the funded budget without a code change.
DEFAULT_FREE_DAILY_LIMIT = 5

# Tiers that are NEVER quota-limited (paid + operator).
_EXEMPT_TIERS = frozenset({"pro", "elite", "admin", "premium"})

# Anchored to the repo root, not the cwd — see bot/utils/paths.py. A quota
# store that moves with the working directory silently resets every free
# user's counter to zero, which reads as 'nobody has used their quota'.
_STORE_PATH = env_state_path("FREE_CHAT_QUOTA_PATH", "data/free_chat_quota.json")
_LOCK = threading.Lock()


def free_daily_limit() -> int:
    """The per-day free question limit (env FREE_CHAT_DAILY_LIMIT, default 5)."""
    try:
        n = int(os.getenv("FREE_CHAT_DAILY_LIMIT", str(DEFAULT_FREE_DAILY_LIMIT)))
        return n if n > 0 else DEFAULT_FREE_DAILY_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_FREE_DAILY_LIMIT


def quota_enabled() -> bool:
    """Whether the free-chat quota is active. The cap exists ONLY to protect the
    operator's prepaid Grok budget — so it turns on only when Grok is actually the
    funded free-chat model (XAI_API_KEY set), or when the operator forces it on
    (FREE_CHAT_QUOTA_ENABLED). With no funded budget there is nothing to protect:
    free chat falls back to the genuinely-free Groq/Gemini tiers, uncapped."""
    if str(os.getenv("FREE_CHAT_QUOTA_ENABLED", "")).strip().lower() in (
            "1", "true", "yes", "on"):
        return True
    if str(os.getenv("FREE_CHAT_QUOTA_ENABLED", "")).strip().lower() in (
            "0", "false", "no", "off"):
        return False                             # explicit off wins over key presence
    return bool(str(os.getenv("XAI_API_KEY", "")).strip())


def is_quota_exempt(tier: Optional[str]) -> bool:
    """Paid tiers and admin are never limited."""
    return str(tier or "").strip().lower() in _EXEMPT_TIERS


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def seconds_until_reset() -> int:
    """Seconds until the free-question counter rolls over (next UTC midnight).

    The count is per UTC day (see ``_today``), so the reset moment is always
    00:00 UTC of the following day. Used to tell a capped user *when* their free
    questions return, so the wall reads as a wait, not a dead end."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((tomorrow - now).total_seconds()))


def _load() -> dict:
    try:
        if _STORE_PATH.exists():
            with open(_STORE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    try:
        atomic_write_json(_STORE_PATH, data, indent=None)
    except Exception:
        pass                                    # never let a write error break chat


def _entry_used(data: dict, uid: str, day: str) -> int:
    e = data.get(uid)
    if isinstance(e, dict) and e.get("day") == day:
        try:
            return max(0, int(e.get("n", 0)))
        except (TypeError, ValueError):
            return 0
    return 0                                     # missing / stale day → 0 used today


def status(uid: str, tier: Optional[str] = None) -> dict:
    """Peek the caller's quota WITHOUT consuming. Returns
    ``{exempt, limit, used, remaining}``. Exempt users report a huge remaining."""
    if not quota_enabled() or is_quota_exempt(tier):
        return {"exempt": True, "limit": None, "used": 0, "remaining": None,
                "reset_in_seconds": None}
    limit = free_daily_limit()
    with _LOCK:
        used = _entry_used(_load(), str(uid), _today())
    return {"exempt": False, "limit": limit, "used": used,
            "remaining": max(0, limit - used),
            "reset_in_seconds": seconds_until_reset()}


def unmetered() -> dict:
    """The allow-and-do-not-count result, in `consume()`'s shape.

    For a caller whose tier could not be READ. `is_quota_exempt(None)` is
    False — an absent tier is not an exempt tier — so passing None to
    `consume()` would meter an unclassifiable caller against the free cap,
    which is the defect this exists to avoid. Naming the case beats inventing
    a tier string that no user actually holds.
    """
    return {"allowed": True, "exempt": True, "limit": None,
            "used": 0, "remaining": None, "reset_in_seconds": None,
            "unmetered": True}


def refund(uid: str, tier: Optional[str] = None) -> None:
    """Give back one free question that bought nothing.

    `consume()` increments and persists BEFORE the caller knows whether a
    model answered, and several paths return without one: the FAQ
    short-circuit hands back a canned string with no LLM call at all, and the
    budget-exhausted, deadline and all-providers-failed branches return an
    apology. A free user asking "what is RUNECLAW" therefore lost one of five
    daily questions to a static paragraph, and a user who got "the AI is
    unavailable" was charged for it and then shown "Upgrade to keep chatting".

    This module's own docstring says the quota exists to protect the prepaid
    Grok spend. None of those paths spend any.

    Floored at zero and a no-op for an exempt or unmetered caller, so a
    double refund can never MINT questions.
    """
    if not quota_enabled() or is_quota_exempt(tier):
        return
    day = _today()
    key = str(uid)
    with _LOCK:
        data = _load()
        used = _entry_used(data, key, day)
        if used <= 0:
            return
        data[key] = {"day": day, "n": used - 1}
        _save(data)


def consume(uid: str, tier: Optional[str] = None) -> dict:
    """Try to spend one free question. Returns
    ``{allowed, exempt, limit, used, remaining}``. When not allowed (limit hit),
    nothing is incremented and ``allowed`` is False — the caller shows the upgrade
    prompt instead of calling the LLM. Exempt callers are always allowed."""
    if not quota_enabled():                      # no funded budget → never limit
        return {"allowed": True, "exempt": True, "limit": None,
                "used": 0, "remaining": None, "reset_in_seconds": None}
    if is_quota_exempt(tier):
        return {"allowed": True, "exempt": True, "limit": None,
                "used": 0, "remaining": None, "reset_in_seconds": None}
    limit = free_daily_limit()
    day = _today()
    key = str(uid)
    with _LOCK:
        data = _load()
        used = _entry_used(data, key, day)
        if used >= limit:
            return {"allowed": False, "exempt": False, "limit": limit,
                    "used": used, "remaining": 0,
                    "reset_in_seconds": seconds_until_reset()}
        data[key] = {"day": day, "n": used + 1}
        _save(data)
        return {"allowed": True, "exempt": False, "limit": limit,
                "used": used + 1, "remaining": max(0, limit - (used + 1)),
                "reset_in_seconds": seconds_until_reset()}

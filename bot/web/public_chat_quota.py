"""The public preview's per-visitor daily question count.

Anonymous website chat had a per-minute limit (six messages per address, in
the website's own memory) and nothing per day, and every one of its turns was
charged to the same chat budget signed-in users answer from. This is the
per-visitor half of the fix; `cost.public_chat_budget_bound` is the
budget half, and the one that holds whatever this count misses.

WHAT A VISITOR IS. The website derives an opaque key from the caller's
address (`app/lib/visitor_key.js`: an HMAC under a key it draws at start-up)
and sends that, never the address. Both doors into the public chat send it:
the website's chat route and the MCP `ask_runeclaw` tool. So one address is
one count across both.

WHERE THE COUNT LIVES, AND WHAT RESETS IT. In this process's memory, per UTC
day. A bot restart starts every visitor's count again, and so does a website
restart, because the website draws a new key and every address maps to a new
visitor. Neither is written anywhere, which is also why no address or key
reaches a file or a log line from here.

WHAT IS COUNTED. A question the model answered. `consume()` counts before the
call and the gateway gives it back with `refund()` when no model answered
(a built-in answer, a refusal, a failure), as the signed-in free quota does
(`chat_quota.refund`).

ABSENT IS NOT A VISITOR. A turn that arrives with no readable key (a website
older than this module, or a caller of the gateway that is not the website)
is allowed and NOT counted, and the result says so (`unmetered`). Counting
every such turn against one shared key would refuse every visitor of an older
website at once. The budget slice still bounds what those turns can spend.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("runeclaw.public_chat_quota")

#: What a visitor key looks like: the website sends hex. Anything else is
#: not a key this module counts against.
_KEY_SHAPE = re.compile(r"^[0-9a-f]{16,64}$")

#: How many visitors one day may hold. Past it a NEW visitor is allowed and
#: not counted (said so in the result), rather than evicting somebody's
#: count, which would hand that visitor a fresh day.
MAX_VISITORS = 50_000

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"day": "", "counts": {}}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def daily_limit() -> int:
    """`PUBLIC_CHAT_DAILY_LIMIT`, as the config read it."""
    from bot.config import CONFIG
    return int(CONFIG.llm.public_chat_daily_limit)


def visitor_key(raw: object) -> Optional[str]:
    """The visitor key in a request body, or None when there is none."""
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    return key if _KEY_SHAPE.match(key) else None


def _counts_for_today() -> dict[str, int]:
    day = _today()
    if _STATE["day"] != day:
        _STATE["day"] = day
        _STATE["counts"] = {}
    counts: dict[str, int] = _STATE["counts"]
    return counts


def _unmetered(limit: int, why: str) -> dict:
    return {"allowed": True, "limit": limit, "used": None, "remaining": None,
            "unmetered": why}


def consume(key: Optional[str]) -> dict:
    """Count one question for this visitor, or refuse it.

    ``{"allowed", "limit", "used", "remaining"}``; ``allowed`` False when the
    visitor already had ``limit`` today, and then nothing is counted.
    ``unmetered`` names why a question was allowed without counting.
    """
    from bot.web.chat_quota import seconds_until_reset
    limit = daily_limit()
    if key is None:
        return _unmetered(limit, "no_visitor_key")
    with _LOCK:
        counts = _counts_for_today()
        used = counts.get(key, 0)
        if used >= limit:
            return {"allowed": False, "limit": limit, "used": used,
                    "remaining": 0, "reset_in_seconds": seconds_until_reset()}
        if key not in counts and len(counts) >= MAX_VISITORS:
            log.warning("public preview visitor table is full (%d today) — "
                        "a new visitor is allowed and not counted", len(counts))
            return _unmetered(limit, "table_full")
        counts[key] = used + 1
        return {"allowed": True, "limit": limit, "used": used + 1,
                "remaining": max(0, limit - used - 1)}


def refund(key: Optional[str]) -> None:
    """Give back one question that bought no model answer. Floored at zero,
    and a no-op for a key with nothing counted today, so it cannot mint."""
    if key is None:
        return
    with _LOCK:
        counts = _counts_for_today()
        used = counts.get(key, 0)
        if used <= 1:
            counts.pop(key, None)
        else:
            counts[key] = used - 1


def limit_notice(q: dict, lang: str = "en") -> str:
    """What a visitor over the limit reads, in their language. It names the
    PUBLIC PREVIEW's per-visitor limit: the bot's budget is not what ran out,
    and signing in is a different allowance."""
    from bot.utils.i18n import t
    from bot.web.chat_quota import reset_phrase
    return t("chat_public_daily_limit", lang, limit=q.get("limit") or daily_limit(),
             reset=reset_phrase(q.get("reset_in_seconds"), lang))


def _snapshot() -> dict:
    """For the test harness: the state as it stands."""
    with _LOCK:
        return {"day": _STATE["day"], "counts": dict(_STATE["counts"])}


def _restore(saved: Optional[dict]) -> None:
    """For the test harness: put a snapshot back (None: a fresh start)."""
    with _LOCK:
        _STATE["day"] = (saved or {}).get("day", "")
        _STATE["counts"] = dict((saved or {}).get("counts", {}))

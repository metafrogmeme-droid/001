"""The seam to Claw Watch.

The import sits inside the function. A missing package is "not present",
and startup does not depend on it. When the package is present, addresses
a chat stored there are included in this watch's poll. An unread list is
not an empty one. This module does not edit that package.
"""

from __future__ import annotations

from typing import NamedTuple, Optional


class ClawWatchRead(NamedTuple):
    """``present`` is whether the package imported.

    ``addresses`` is the list that was read, or None when the package is
    there and the read did not succeed. None is unread, not an empty watch.
    """

    present: bool
    addresses: Optional[list[str]]


def read_claw_watch(chat_id: str) -> ClawWatchRead:
    """Addresses Claw Watch stored for this chat, or unread, or not present."""
    if not chat_id:
        return ClawWatchRead(False, None)
    try:
        from bot.drain_watch.service import get_service
    except ImportError:
        return ClawWatchRead(False, None)
    except Exception:
        return ClawWatchRead(True, None)
    try:
        svc = get_service()
    except Exception:
        return ClawWatchRead(True, None)
    if svc is None:
        return ClawWatchRead(True, None)
    try:
        key = svc.watch_key(str(chat_id))
    except Exception:
        return ClawWatchRead(True, None)
    if not key:
        return ClawWatchRead(True, None)
    try:
        rows = svc.store.addresses(key)
    except Exception:
        return ClawWatchRead(True, None)
    out: list[str] = []
    for raw in rows or []:
        if isinstance(raw, str) and raw:
            out.append(raw.lower())
    return ClawWatchRead(True, out)

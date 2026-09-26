"""Once per period, across restarts: a claim written to disk before the act.

THE DEFECT THIS EXISTS FOR. Three once-a-day messages remembered that they
had been sent in MEMORY only (`ProactiveMonitor._digest_sent`), and one
remembered nothing at all. A redeploy inside the evening-wrap hour re-sent the
morning brief and the evening wrap, and the weekly parity digest the same way;
`/daily_report` posted the day to the public channels on EVERY call.

``claim_period(path, key, period)`` records that ``key`` happened in
``period`` (a UTC day, an ISO week) and answers which of four things is true:

  ``claimed``     it had not happened this period; the claim is on disk now.
  ``already``     the file says it already happened this period.
  ``unreadable``  the file is there and will not read. It is NOT "not yet":
                  it may hold this very claim, and reading it as empty is the
                  double-send the file exists to stop. Nothing is written over
                  it (``bot/utils/json_store.py``).
  ``unwritten``   the claim could not be saved. The act may or may not be
                  wise; the caller says, because a public post and an
                  operator digest cost different things when repeated.

It is a claim BEFORE the act, not a record after it: a restart between the
two loses one message, which is the direction that cannot be taken back less.
"""

from __future__ import annotations

from typing import Any

from bot.utils.atomic_write import StrPath
from bot.utils.json_store import StoreUnreadable, update_json_store

CLAIMED = "claimed"
ALREADY = "already"
UNREADABLE = "unreadable"
UNWRITTEN = "unwritten"


def claim_period(path: StrPath, key: str, period: str) -> tuple[str, str]:
    """``(outcome, detail)``: claim ``key`` for ``period`` in the file at
    ``path``. ``detail`` names why a file could not be read or written (an
    exception's CLASS, never its text) and is empty otherwise."""

    def _change(data: dict) -> Any:
        if data.get(key) == period:
            return False
        data[key] = period
        return None

    try:
        _, written = update_json_store(path, _change)
    except StoreUnreadable as exc:
        return UNREADABLE, exc.detail
    except OSError as exc:
        return UNWRITTEN, type(exc).__name__
    return (CLAIMED if written else ALREADY), ""

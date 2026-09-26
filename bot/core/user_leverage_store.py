"""NB3 — tiny per-user leverage-preference store (non-secret, JSON-backed).

BYOK live users can pin their own standard leverage (only ever applied as a
reduce vs the operator default — see bot.core.leverage.resolve_user_leverage).
This is deliberately SEPARATE from the encrypted credential store: it holds no
secret, just a small integer per user, so it never touches the key material.

A MISSING file is a fresh start and every user has no preference. A file that
is there and will NOT READ is not that, and this module used to read it as
that twice over: ``get`` answered None (the operator default, which is the
LOOSEST a reduce-only preference can resolve to), and ``set_pref`` saved the
empty map it had read plus one entry, erasing every other user's preference.
Driven: ``{"111": 2, "222": 3, "333": 2}``, one failed read, ``set_pref("444",
5)`` -> a file holding ``{"444": 5}``.

So every function raises :class:`StoreUnreadable` for an unreadable file and
writes nothing over it (``bot/utils/json_store.py``); the caller says so. The
engine reads it at executor bind as :data:`UNREAD`, which the executor re-reads
at order time and, while it still cannot, sizes at the tightest leverage a
preference can hold -- never the operator default.

A write that did not land is a third fact, and says so: ``set_pref`` answers
None (as it does for unusable input) and ``clear`` raises the OSError, because
"nothing to clear" is a false answer about a preference that is still on disk.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from bot.utils.json_store import StoreUnreadable, load_json_store, update_json_store

log = logging.getLogger(__name__)
_LOCK = threading.Lock()


class _Unread:
    """A preference that could not be read. Never a number: handed to
    ``resolve_user_leverage`` it would read as "no preference", the loosest
    answer, which is the defect this marker exists to keep out."""

    def __repr__(self) -> str:
        return "<leverage preference unread>"


#: What the engine binds on an executor when the store could not be read.
UNREAD = _Unread()

#: The tightest a preference can be (``set_pref`` refuses anything below it),
#: so the leverage an unread preference is sized at: a reduce-only setting
#: nobody could read is read as the largest reduction a user could have made.
TIGHTEST_PREF = 1

#: What a person is told when the file will not read. Not "saved", not
#: "cleared": nothing was written, and the sentence says why and what their
#: orders are sized at meanwhile. It names no command, so any surface can say it.
UNREAD_SENTENCE = (
    "⚠️ The stored leverage preferences could not be read, so yours "
    "was not changed: saving over that file would erase every other person's. "
    f"Your live orders are sized at {TIGHTEST_PREF}x until it reads.")


def _path() -> str:
    base = os.environ.get("RUNECLAW_STATE_DIR", "data")
    return os.path.join(base, "user_leverage.json")


def _prefs() -> dict:
    """The whole map ({} for a fresh start), or StoreUnreadable."""
    try:
        return load_json_store(_path())
    except StoreUnreadable as exc:
        log.warning("user_leverage: %s — no preference is read as absent and "
                    "nothing is written over the file until it reads", exc)
        raise


def get(user_id) -> Optional[int]:
    """The stored preference for a user, or None (→ use the operator default).

    Raises :class:`StoreUnreadable` when the file is there and cannot be read:
    "no preference" is a claim about the file, and nobody read it."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    v = _prefs().get(uid)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def set_pref(user_id, value) -> Optional[int]:
    """Persist a leverage preference (int >= 1). Returns the stored int, or None
    if the input is unusable or the write failed. Raises
    :class:`StoreUnreadable` (and writes nothing) for a file that cannot be
    read, because saving over it would erase every other user's preference."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n < TIGHTEST_PREF:
        return None

    def _set(d: dict) -> None:
        d[uid] = n

    with _LOCK:
        try:
            update_json_store(_path(), _set, indent=None)
        except StoreUnreadable as exc:
            log.warning("user_leverage: %s — %s's preference was NOT saved",
                        exc, uid)
            raise
        except OSError as exc:
            log.warning("user_leverage write failed: %s", exc)
            return None
    return n


def clear(user_id) -> bool:
    """Remove a user's preference (→ back to the operator default).

    True when it was removed, False when there was none. Raises
    :class:`StoreUnreadable` for a file that cannot be read and the OSError of
    a write that did not land: the preference is still on disk in both, and
    "there was nothing to clear" would say otherwise."""
    uid = str(user_id or "").strip()
    if not uid:
        return False

    def _drop(d: dict) -> bool:
        if uid not in d:
            return False
        del d[uid]
        return True

    with _LOCK:
        try:
            _data, written = update_json_store(_path(), _drop, indent=None)
        except StoreUnreadable as exc:
            log.warning("user_leverage: %s — %s's preference was NOT cleared",
                        exc, uid)
            raise
    return written

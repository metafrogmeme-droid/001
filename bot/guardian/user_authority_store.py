"""Per-user Authority Envelope store — binds one compiled envelope per user.

This is the missing piece that lets the web live gate (`bot/web/web_live_gate`)
see an ENFORCE-mode envelope for a web user: a user authors their envelope in
plain words, it is compiled + clamped by `authority.compile_envelope`, bound
here, and read back by the gate's `envelope_enforcing` precondition and by the
per-user executor at trade time.

Simple JSON persistence keyed by user id (Telegram or ``web:<id>``), one
envelope per user. Mode transitions are tighten-first: a fresh envelope starts
in ``shadow`` (observe) and only reaches ``enforce`` by an explicit, separate
call — never as a side effect of authoring. Revocation is preserved (a revoked
envelope authorises nothing).

A FILE THAT WILL NOT READ IS NOT AN EMPTY STORE. The loader used to catch
every read failure into ``{}``, and every writer then saved that map whole.
Driven: a file binding 111 (revoked) and 222, one failed read, one ``bind``
for 333, and the file held 333 alone. 111's REVOKE was gone for good, which is
the kill-switch undone by an unrelated user's write. Every read and every write
raises :class:`StoreUnreadable` now and nothing is written over the file
(``bot/utils/json_store.py``); each call tries the read again. Every reader
already fails closed on a raise (no envelope enforcing, nothing authorized),
and the gateway's writers say the store could not be read.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from bot.guardian.authority import VALID_MODES, revoke as _revoke_env

from bot.utils.json_store import (
    UNREADABLE,
    StoreUnreadable,
    read_json_store,
    update_json_store,
)
from bot.utils.paths import env_state_path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = str(env_state_path(
    "USER_AUTHORITY_STORE_PATH", "data/user_authority.json"))

#: What a person is told when the store will not read. It names no command,
#: so any surface can say it.
UNREAD_SENTENCE = (
    "The stored authority envelopes could not be read, so nothing was "
    "changed: saving over that file would erase every other person's "
    "envelope, revocations included. Nothing is authorized until it reads.")


def _envelopes_of(data: dict) -> dict:
    """The bound envelopes a file holds: a row that is not an envelope is not
    bound to anybody, and is left in the FILE for whoever wrote it."""
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


class UserAuthorityStore:
    """Thread-safe, JSON-backed per-user envelope binding."""

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._envelopes: dict[str, dict] = {}
        #: True once the file has been read or found absent. False while it
        #: cannot be read: memory then is a reading of nothing.
        self._loaded = False
        self._unread_detail = ""
        #: Changes whose write did not land, oldest first. Memory is the file
        #: plus these, and the next write that lands carries them too.
        self._pending: list[Callable[[dict], bool]] = []
        self._load()

    def _load(self) -> bool:
        r = read_json_store(self._path)
        if r.state == UNREADABLE:
            self._unread_detail = r.detail
            logger.warning("authority store %s could not be read (%s) — no "
                           "envelope is read as absent and nothing is written "
                           "over it until it reads", self._path, r.detail)
            return False
        envelopes = _envelopes_of(r.data or {})
        for change in self._pending:
            change(envelopes)
        self._envelopes = envelopes
        self._loaded = True
        return True

    def _readable(self) -> None:
        """Raise :class:`StoreUnreadable` unless memory holds a reading of the
        file, reading it now if an earlier read failed."""
        if not self._loaded and not self._load():
            raise StoreUnreadable(self._path, self._unread_detail)

    def _save(self, change: Callable[[dict], bool]) -> bool:
        """Apply ONE change to the file, never write memory over it. True only
        when the write LANDED and the change changed something.

        The file is read again and ``change`` edits what it holds, so a write
        cannot erase a person this process does not hold. ``change`` answers
        False when there was nothing to change. A file that has become
        unreadable raises :class:`StoreUnreadable`, writing nothing.

        A write that did not land applies the change to memory, keeps it as
        pending and answers False; the next write that lands carries it. It
        used to swallow the OSError and every writer returned True regardless
        — so the human kill-switch answered ``revoked`` over a write that
        failed, the running process stayed revoked, and the next restart read
        the file and came back ENFORCING. A revoke that disappears on restart
        is the one failure a kill-switch must never report as success, and a
        revoke this process holds must not disappear from it either: memory
        adopts what a write READ, so without the pending list the next
        person's bind would un-revoke this one here.
        """
        with self._lock:
            self._readable()
            pending = list(self._pending)
            asked = {"changed": False}

            def _apply(d: dict) -> bool:
                moved = False
                for earlier in pending:
                    if earlier(d) is not False:
                        moved = True
                asked["changed"] = change(d) is not False
                return moved or asked["changed"]

            try:
                data, written = update_json_store(self._path, _apply,
                                                  separators=(",", ":"))
            except StoreUnreadable as exc:
                self._loaded = False
                self._unread_detail = exc.detail
                raise
            except OSError as exc:
                logger.warning("authority store write failed (%s): the change "
                               "is held in memory only and will not survive a "
                               "restart", type(exc).__name__)
                if change(self._envelopes) is not False:
                    self._pending.append(change)
                return False
            self._pending = []
            self._envelopes = _envelopes_of(data)
            return bool(written and asked["changed"])

    # ── reads ─────────────────────────────────────────────────────────

    def get(self, user_id) -> Optional[dict]:
        """The user's bound envelope, or None. Raises :class:`StoreUnreadable`
        for a file that will not read: None is "nothing bound", and nobody
        read the file to say so."""
        with self._lock:
            self._readable()
            env = self._envelopes.get(str(user_id))
            return dict(env) if env else None

    def mode(self, user_id) -> str:
        """The bound envelope's mode ('off' when none/revoked)."""
        env = self.get(user_id)
        if not env or env.get("revoked"):
            return "off"
        m = str(env.get("mode", "off")).lower()
        return m if m in VALID_MODES else "off"

    def is_enforcing(self, user_id) -> bool:
        """True only when a non-revoked envelope is bound in enforce mode."""
        return self.mode(user_id) == "enforce"

    # ── writes ────────────────────────────────────────────────────────

    # Every writer below returns True only when the change is ON DISK. On a
    # failed write the change is KEPT in memory — the running process honours
    # a revoke it could not persist rather than un-revoking itself — and the
    # writer returns False. A caller that sees False while the change it asked
    # for is in memory (`get`) is looking at "held in memory only", and must
    # say so: the change comes back undone on the next restart. A store that
    # cannot be read raises StoreUnreadable from every writer, changing
    # nothing anywhere.

    def bind(self, user_id, envelope: dict) -> bool:
        """Bind (replace) the user's compiled envelope. True when it landed on
        disk; False for a malformed envelope or a write that did not land."""
        if not isinstance(envelope, dict) or not envelope.get("envelope_id"):
            return False
        uid, env = str(user_id), dict(envelope)

        def _bind(d: dict) -> bool:
            d[uid] = dict(env)
            return True

        return self._save(_bind)

    def set_mode(self, user_id, mode: str) -> bool:
        """Flip the bound envelope's mode (off/shadow/enforce). No envelope →
        False; a write that did not land → False (the mode is set in memory)."""
        mode = str(mode).lower()
        if mode not in VALID_MODES:
            return False
        uid = str(user_id)

        def _mode(d: dict) -> bool:
            env = d.get(uid)
            if not isinstance(env, dict) or not env:
                return False
            env["mode"] = mode
            return True

        return self._save(_mode)

    def revoke(self, user_id) -> bool:
        """Human kill-switch: revoke (keeps the record, authorises nothing).
        No envelope → False; a write that did not land → False, with the
        revoke KEPT in memory so this process stays revoked."""
        uid = str(user_id)

        def _revoke(d: dict) -> bool:
            env = d.get(uid)
            if not isinstance(env, dict) or not env:
                return False
            d[uid] = _revoke_env(env)
            return True

        return self._save(_revoke)

    def clear(self, user_id) -> bool:
        """Remove the binding entirely. Nothing bound → False; a write that did
        not land → False, with the binding gone from memory only."""
        uid = str(user_id)

        def _clear(d: dict) -> bool:
            if uid not in d:
                return False
            del d[uid]
            return True

        return self._save(_clear)


_STORE: Optional[UserAuthorityStore] = None
_STORE_LOCK = threading.Lock()


def get_user_authority_store() -> UserAuthorityStore:
    """Process-wide singleton."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = UserAuthorityStore()
        return _STORE

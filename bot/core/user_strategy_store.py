"""Per-user strategy preference store (non-secret, JSON-backed).

"Your bot, your strategy": a user pins ONE of the engine's strategy presets
(the same catalogue /public/strategies serves) and their confirms are then
gated by that preset's rules — a tighten-only veto that can refuse a trade
for them but never invent one. This file mirrors user_leverage_store.py:
it holds no secret, just a preset key per user, so it never touches key
material and never takes a command down.

A MISSING file is a fresh start: nobody has a selection. A file that is there
and will NOT READ is not that. This module used to say "a missing/corrupt
preferences file returns None and the caller treats the user as having no
selection", and a corrupt one was then erased: every writer saved the empty
map it had read plus one entry. Driven: 111 on a preset and 222 on a
long-only community gate, one failed read, ``set_pref("444", ...)`` -> a
file holding 444 alone, and ``get_entry("222")`` -> None, so 222's
tighten-only veto was gone for good.

Every function raises :class:`StoreUnreadable` for an unreadable file now and
writes nothing over it (``bot/utils/json_store.py``). The confirm path reads
that as a refusal, the same fail-CLOSED answer an ARMED preference that cannot
be evaluated already gets (see bot/core/strategy_gate.py): an unreadable file
may hold a selection, and reading it as "none" chooses for the user.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

log = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _path() -> str:
    base = os.environ.get("RUNECLAW_STATE_DIR", "data")
    return os.path.join(base, "user_strategy.json")


import re
from datetime import datetime, timezone

from bot.utils.json_store import StoreUnreadable, load_json_store, update_json_store

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: What a person is told when the file will not read. Not "none selected" and
#: not "nothing to clear": nobody read the file to say so. It names no command,
#: so any surface can say it.
UNREAD_SENTENCE = (
    "⚠️ The stored strategy selections could not be read, so yours "
    "could not be shown or changed: saving over that file would erase every "
    "other person's. Confirms are refused until it reads (fail closed).")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update(uid: str, change) -> bool:
    """Read-modify-write one user's row. True when it landed on disk.

    Raises :class:`StoreUnreadable`, writing nothing, for a file that cannot
    be read; a write that did not land is logged and answers False."""
    with _LOCK:
        try:
            _data, written = update_json_store(_path(), change, indent=None)
        except StoreUnreadable as exc:
            log.warning("user_strategy: %s — %s's selection was NOT changed",
                        exc, uid)
            raise
        except OSError as exc:
            log.warning("user_strategy write failed: %s", exc)
            return False
    return written


def _selections() -> dict:
    """The whole map ({} for a fresh start), or StoreUnreadable."""
    try:
        return load_json_store(_path())
    except StoreUnreadable as exc:
        log.warning("user_strategy: %s — no selection is read as absent and "
                    "nothing is written over the file until it reads", exc)
        raise


def get(user_id) -> Optional[str]:
    """The stored ENGINE preset key, or None. A community snapshot is not a
    preset key, so this returns None for it — callers that understand both
    shapes use get_entry()."""
    v = get_entry(user_id)
    if isinstance(v, str):
        return v
    return None


def get_entry(user_id):
    """The raw stored selection: a preset key (str) for engine presets, or a
    community snapshot dict {kind, slug, label, gates, armed_at}.

    Raises :class:`StoreUnreadable` when the file is there and cannot be read:
    None is "no selection", and nobody read the file to say so."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    v = _selections().get(uid)
    if isinstance(v, dict) and v.get("kind") == "community" and v.get("slug"):
        return v
    return str(v) if v else None


def set_pref(user_id, preset_key, valid_keys) -> Optional[str]:
    """Persist a strategy selection. `preset_key` must already be canonical
    (alias resolution is the caller's job) and must be in `valid_keys` —
    a selection that names no real preset is refused, never stored.
    Returns the stored key, or None (bad input, or a write that did not land).
    Raises :class:`StoreUnreadable` for a file that cannot be read."""
    uid = str(user_id or "").strip()
    key = str(preset_key or "").strip().lower()
    if not uid or not key or key not in set(valid_keys or []):
        return None

    def _set(d: dict) -> None:
        d[uid] = key

    if not _update(uid, _set):
        return None
    return key


def set_custom(user_id, slug, name, gates, source_rules=None) -> Optional[dict]:
    """Pin a COMMUNITY strategy by storing a validated SNAPSHOT of its
    signal-checkable gates.

    The bot cannot read the website's database, so what is stored here is a
    copy taken at arming time — not a live link. That is a fact about the
    system, so the snapshot carries `armed_at` and the surfaces SAY it is a
    snapshot: editing the strategy on the web does not silently re-arm the
    bot; the user re-arms. Gates are validated here (the caller's projection
    is not trusted blindly) — anything unrecognised is dropped rather than
    stored, so a malformed payload can never widen what the gate enforces.
    """
    uid = str(user_id or "").strip()
    sl = str(slug or "").strip().lower()
    if not uid or not sl or not _SLUG_RE.match(sl):
        return None
    g = {}
    src = gates if isinstance(gates, dict) else {}
    try:
        thr = src.get("confidence_threshold")
        if thr is not None and 0 < float(thr) <= 1:
            g["confidence_threshold"] = round(float(thr), 4)
    except (TypeError, ValueError):
        pass
    for key in ("symbols", "blocked_symbols"):
        v = src.get(key)
        if isinstance(v, (list, tuple)) and v:
            syms = [str(x).strip().upper()[:12] for x in v if str(x).strip()][:20]
            if syms:
                g[key] = syms
    if src.get("direction") in ("long_only", "short_only"):
        g["direction"] = src["direction"]
    rf = src.get("regime_filter")
    if rf and str(rf).lower() != "any":
        g["regime_filter"] = str(rf)[:24]
    entry = {
        "kind": "community", "slug": sl,
        "label": str(name or sl)[:80],
        "gates": g,
        "armed_at": _now_iso(),
    }
    def _set(d: dict) -> None:
        d[uid] = entry

    if not _update(uid, _set):
        return None
    return entry


def clear(user_id) -> bool:
    """Remove a user's selection (→ ungated confirms again).

    True when it was removed, False when there was none. Raises
    :class:`StoreUnreadable` for a file that cannot be read and the OSError of
    a write that did not land: the selection is still on disk in both, and
    "there was nothing to clear" would say otherwise. It used to write with a
    bare ``open(..., "w")``, so a full disk mid-write left a truncated file
    and every selection in it unreadable."""
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
            log.warning("user_strategy: %s — %s's selection was NOT cleared",
                        exc, uid)
            raise
    return written

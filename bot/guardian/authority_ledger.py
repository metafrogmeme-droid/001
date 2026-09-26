"""Rolling notional-spend ledger for the Authority Envelope's daily cap.

The Authority Envelope (``authority.py``) carries a ``max_notional_daily_usd``
ceiling, but ``authorize`` is pure and holds no state — the caller must supply how
much has already been spent in the rolling window. This module is that state: a
small, persisted, per-authority accumulator of notional over a rolling 24h window.

Design mirrors the risk engine's state file (atomic tmp→replace write). Two
deliberate safety choices:

* **Idempotent by ref.** ``record(key, amount, now, ref=…)`` ignores a duplicate
  ``ref`` (e.g. the same trade id re-evaluated), so a spend is never
  double-counted by a re-run of the risk gate.
* **Conservative for a cap.** Recording on *approval* (before fill confirmation) can
  only ever over-count, which makes the daily cap TIGHTER — it errs toward denying,
  never toward letting more value out. That is the correct bias for a spend
  ceiling, and the rolling window self-heals as old entries age out.

Pure core (``prune``/``window_sum``) is separated from I/O so the accounting is
trivially testable without a filesystem.

**An unreadable ledger is not an empty one.** This module used to say "a corrupt
ledger must fail-safe to EMPTY", and empty is $0 spent, the one answer a daily
cap reads as "the whole allowance is left". Driven: every user's spend read 0.0
after one failed read, the next ``record`` wrote that one user's entry over the
file and erased everybody else's day, and after a restart an already-recorded
ref was recorded again. ``spent``, ``remaining`` and ``record`` raise
:class:`StoreUnreadable` now and nothing is written over the file; every caller
already reads a raising ledger as a spend nobody could read, which a daily cap
refuses by name. Every call tries the read again, so a transient failure
recovers on its own. A write that did not land raises its ``OSError`` after the
entry is kept in memory: the day is still counted in this process (the
over-count bias above), and the caller learns the spend was not persisted.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from bot.utils.json_store import (
    UNREADABLE,
    StoreUnreadable,
    read_json_store,
    update_json_store,
)

DEFAULT_WINDOW_S = 86_400   # 24h rolling window


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _is_ledger(data: dict) -> str:
    """``""`` for a ledger file, a reason for a JSON dict that is not one.

    An absent ``book`` is an empty ledger. A ``book`` that is not a map of
    lists of entries is somebody else's file, and writing an empty book over
    it would erase it just as surely as a failed parse."""
    book = data.get("book", {})
    if not isinstance(book, dict):
        return "book is not a JSON dict"
    for entries in book.values():
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            return "a book entry is not a list of entries"
    return ""


def _ident(entry: dict) -> tuple:
    """What makes two rows one row, for the merge a write makes."""
    return (entry.get("ts"), entry.get("amount"), entry.get("ref"))


def prune(entries: list[dict], now_ts: float, window_s: float = DEFAULT_WINDOW_S) -> list[dict]:
    """Return only the entries within ``[now-window, now]``. Pure. ``ts`` is a
    numeric epoch in the SAME unit the caller uses for ``now_ts`` (seconds)."""
    lo = now_ts - window_s
    out = []
    for e in entries or []:
        ts = _num(e.get("ts"))
        if ts is not None and lo <= ts <= now_ts:
            out.append(e)
    return out


def window_sum(entries: list[dict], now_ts: float, window_s: float = DEFAULT_WINDOW_S) -> float:
    """Sum of in-window entry amounts (non-negative). Pure."""
    total = 0.0
    for e in prune(entries, now_ts, window_s):
        amt = _num(e.get("amount"))
        if amt is not None and amt > 0:
            total += amt
    return round(total, 6)


class AuthoritySpendLedger:
    """Per-authority rolling-window notional accumulator, persisted atomically."""

    def __init__(self, state_file: Optional[str] = None,
                 window_s: float = DEFAULT_WINDOW_S) -> None:
        self._path = state_file
        self._window_s = window_s
        self._lock = threading.RLock()
        # key -> list[{ts, amount, ref}]
        self._book: dict[str, list[dict]] = {}
        self._refs: dict[str, set] = {}      # key -> set of recorded refs (dedup)
        #: True once the file has been read or found absent. False while it
        #: cannot be read: memory then is a reading of nothing.
        self._loaded = False
        self._unread_detail = ""
        self._load()

    # -- persistence -----------------------------------------------------

    def _adopt(self, raw: dict) -> None:
        book = raw.get("book") or {}
        self._book = {str(k): list(v) for k, v in book.items()}
        self._refs = {
            k: {str(e.get("ref")) for e in v if e.get("ref") is not None}
            for k, v in self._book.items()
        }

    def _load(self) -> None:
        """Read the file into memory. An unreadable file leaves memory as it
        was and ``_loaded`` False, so the next call tries again."""
        if not self._path:
            self._loaded = True
            return
        r = read_json_store(self._path, check=_is_ledger)
        if r.state == UNREADABLE:
            self._unread_detail = r.detail
            return
        if r.data is not None:
            self._adopt(r.data)
        self._loaded = True

    def _readable(self) -> None:
        """Raise :class:`StoreUnreadable` unless memory holds a reading of the
        file, reading it now if an earlier read failed."""
        if not self._loaded:
            self._load()
        if not self._loaded:
            raise StoreUnreadable(self._path or "", self._unread_detail)

    def _save(self, key: str, now_ts: float) -> None:
        """Merge memory into the FILE, never over it, and adopt the result.

        Read-modify-write: the file is read again and every row in memory it
        does not already hold is added, so a write cannot erase a person this
        process does not hold, and a write that failed earlier is repaired by
        the next one. Raises :class:`StoreUnreadable` (nothing written) or the
        write's ``OSError``."""
        if not self._path:
            return
        mem = self._book

        def _merge(data: dict) -> None:
            book = data.setdefault("book", {})
            for k, entries in mem.items():
                held = book.setdefault(k, [])
                seen = {_ident(e) for e in held}
                for e in entries:
                    if _ident(e) not in seen:
                        held.append(e)
                        seen.add(_ident(e))
            book[key] = prune(book.get(key, []), now_ts, self._window_s)

        data, _written = update_json_store(self._path, _merge, check=_is_ledger)
        self._adopt(data)

    # -- public API ------------------------------------------------------

    def spent(self, key: str, now_ts: float) -> float:
        """In-window notional already recorded under ``key`` as of ``now_ts``.

        Raises :class:`StoreUnreadable` when the ledger file cannot be read:
        $0 is a measurement, and nobody measured it."""
        with self._lock:
            self._readable()
            return window_sum(self._book.get(str(key), []), now_ts, self._window_s)

    def record(self, key: str, amount: Any, now_ts: float,
               ref: Optional[str] = None) -> bool:
        """Record ``amount`` of notional under ``key`` at ``now_ts``. Idempotent by
        ``ref`` (a duplicate ref is ignored). Prunes old entries opportunistically.
        Returns True if a new entry was added, False if it was a duplicate/invalid.

        Raises :class:`StoreUnreadable`, recording nothing, when the ledger file
        cannot be read: the duplicate check reads the file's refs, and the
        write would erase every other person's day. A write that did not land
        raises its ``OSError`` with the entry kept in memory."""
        amt = _num(amount)
        if amt is None or amt <= 0:
            return False
        k = str(key)
        with self._lock:
            self._readable()
            refs = self._refs.setdefault(k, set())
            if ref is not None and str(ref) in refs:
                return False
            book = self._book.setdefault(k, [])
            book.append({"ts": float(now_ts), "amount": round(amt, 6),
                         "ref": (str(ref) if ref is not None else None)})
            if ref is not None:
                refs.add(str(ref))
            # opportunistic prune keeps the file bounded
            kept = prune(book, now_ts, self._window_s)
            if len(kept) != len(book):
                self._book[k] = kept
                self._refs[k] = {str(e.get("ref")) for e in kept if e.get("ref") is not None}
            self._save(k, now_ts)
            return True

    def remaining(self, key: str, daily_cap: Any, now_ts: float) -> Optional[float]:
        """``daily_cap - spent(key)``, floored at 0, or None if no cap given.

        Raises :class:`StoreUnreadable` through ``spent`` for an unreadable
        ledger: the whole cap is not what is left of a day nobody read."""
        cap = _num(daily_cap)
        if cap is None:
            return None
        return max(0.0, round(cap - self.spent(key, now_ts), 6))


_USER_LEDGER: Optional[AuthoritySpendLedger] = None
_USER_LEDGER_LOCK = threading.Lock()


def user_spend_ledger() -> AuthoritySpendLedger:
    """The ONE per-user ledger every authority reader shares.

    Keyed by the user id the envelope is bound under. The web-live gate records
    into it, the testnet signer records into it, the risk sentry, the yield
    preview and the meme preflight read it. Two ledgers for one envelope would
    be two answers about how much of its daily cap is used, and the second
    would always read a quieter day than the first."""
    global _USER_LEDGER
    with _USER_LEDGER_LOCK:
        if _USER_LEDGER is None:
            from bot.utils.paths import env_state_path
            _USER_LEDGER = AuthoritySpendLedger(state_file=str(env_state_path(
                "WEB_LIVE_LEDGER_PATH", "data/web_live_ledger.json")))
        return _USER_LEDGER

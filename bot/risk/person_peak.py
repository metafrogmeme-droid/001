"""One equity high-water mark per PERSON, shared across their venues.

The last of Phase 3's person-level caps (``docs/MULTI_VENUE_RISK_SPLIT.md``),
and the one that is not an aggregation. Max-open-positions and daily loss are
functions of what the books say right now, so summing a fresh read answers
them. A drawdown is measured against a PEAK, and a peak is state.

WHY IT CANNOT LIVE IN THE RISK ENGINE. There is one engine per (user, venue)
after Phase 2, so each would keep its own copy of "this person's peak" and they
would diverge the moment the venues' equity moved apart. The divergence is
invisible: every engine reports a plausible drawdown off a peak only it
believes in, and the operator sees whichever engine happened to answer.
CLAUDE.md already records the cost of a mishandled high-water mark — an
operator reading ~0% from a gate that was refusing trades at 9%.

So the peak has exactly one owner, keyed by user, persisted, and every engine
that person trades reads the same number.

THE CLAMP IS NOT DEFENSIVENESS, IT IS THE DOCUMENTED FAILURE. A peak restored
from a corrupted or transient-too-high reading pins drawdown near 100% for
ever, so the breaker re-trips on the very next evaluation and a manual reset
never sticks — the "still halted after reset" report. The same bounds the
engine's own restore uses apply here: a peak must be a finite positive number
below 1e12, and anything else is treated as NO peak, to be re-seeded from the
next live reading. Fail-closed here means "re-measure", not "assume the worst",
because the drawdown gate still evaluates against fresh equity either way.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from bot.utils.json_store import (
    UNREADABLE,
    StoreUnreadable,
    read_json_store,
    update_json_store,
)
from bot.utils.paths import state_path

log = logging.getLogger("runeclaw.person_peak")

#: Anchored, not relative — a peak read from whichever directory the process
#: was launched from is a peak that silently resets. Same lesson as
#: ``venue_key.venue_root``.
_STATE_FILE = "data/person_equity_peak.json"

#: A peak outside these bounds is garbage, not a measurement. Mirrors the
#: engine's own ``_restore_peak`` check so the two cannot disagree about what
#: a believable peak is.
_MIN_PEAK = 0.0
_MAX_PEAK = 1e12


def _sane(value) -> Optional[float]:
    """``float(value)`` when it is a believable peak, else ``None``."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return v if _MIN_PEAK < v < _MAX_PEAK else None


def _is_peak_file(data: dict) -> str:
    """``""`` for a peak file, a reason for a JSON dict that is not one.

    An absent ``peaks`` is an empty store; one that is not a dict is somebody
    else's file, and an empty map written over it would erase it."""
    peaks = data.get("peaks", {})
    return "" if isinstance(peaks, dict) else "peaks is not a JSON dict"


class PersonPeakStore:
    """``{user_id: peak_equity_usd}``, durable, one process-wide instance.

    Every method is best-effort on the PERSISTENCE and strict on the
    ARITHMETIC. A failed write costs durability, which the next write
    repairs; a wrong peak costs a wrong drawdown on the control that decides
    how much real money is lost before the bot halts.

    A FILE THAT WILL NOT READ IS NOT AN EMPTY STORE. It used to be read as
    one: every peak was forgotten, the next reading seeded a fresh peak AT the
    current equity, so the drawdown read 0.0 (the confident all-clear this
    module exists to refuse), and the save that followed wrote that one
    person's peak over the file and erased everybody else's. Driven: a file
    holding 111 at 1000 and 222 at 5000, one failed read, then
    ``drawdown_pct("111", 800)`` -> 0.0 and a file holding 111 alone; after a
    restart ``drawdown_pct("222", 4000)`` -> 0.0 where it is 20%. Until the
    file reads, no peak is reported (``None``, which callers already read as
    "not measurable") and nothing is written over it; every call tries the
    read again, so a transient failure recovers on its own.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = str(state_path(path or _STATE_FILE))
        self._peaks: dict = {}
        #: True once the file has been read or found absent. False while it
        #: cannot be read: nothing in memory then is a reading of anything.
        self._loaded = False
        self._unread_said = False
        self._unread_detail = ""
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> bool:
        """Read the file into memory. False, and nothing changed, if it will
        not read."""
        r = read_json_store(self._path, check=_is_peak_file)
        if r.state == UNREADABLE:
            self._unread_detail = r.detail
            if not self._unread_said:
                self._unread_said = True
                log.warning("person peak store %s could not be read (%s) — no "
                            "peak is reported and nothing is written over it "
                            "until it reads", self._path, r.detail)
            return False
        peaks: dict = {}
        for user, raw in ((r.data or {}).get("peaks") or {}).items():
            v = _sane(raw)
            if v is not None:
                peaks[str(user)] = v
            else:
                # NOT silently dropped into a zero: say so, because a peak
                # that vanishes reads downstream as "no drawdown".
                log.warning("Ignoring implausible stored peak for user %s: "
                            "%r — it will re-seed from the next reading",
                            user, raw)
        self._peaks = peaks
        self._loaded = True
        self._unread_said = False
        return True

    def _read(self) -> bool:
        """True when memory holds a reading of the file (reading it now if an
        earlier read failed)."""
        return self._loaded or self._load()

    def _persist(self, drop: Optional[str] = None) -> None:
        """Write memory into the FILE it came from, never over it.

        Read-modify-write: the file is read again and every peak in memory is
        merged in with ``max`` (a peak only rises), so a write cannot erase a
        person this process does not hold and a write that failed earlier is
        repaired by the next one. ``drop`` is the one exception, for
        ``reseed``. A file that has become unreadable since it was loaded is
        left alone; a write that did not land is logged."""
        def _merge(data: dict) -> None:
            peaks = data.setdefault("peaks", {})
            if drop is not None:
                peaks.pop(drop, None)
            for user, v in self._peaks.items():
                held = _sane(peaks.get(user))
                if held is None or v > held:
                    peaks[user] = v

        try:
            update_json_store(self._path, _merge, check=_is_peak_file,
                              indent=None)
        except StoreUnreadable as exc:
            log.warning("person peak save refused: %s — nothing was written "
                        "over it", exc)
        except OSError as exc:
            log.warning("person peak save skipped: %s", exc)

    def observe(self, user_id: str, equity_usd) -> Optional[float]:
        """Record a fresh equity reading and return the peak, or ``None``.

        MONOTONE BY CONSTRUCTION: a peak only ever rises here. Lowering it on a
        smaller reading would make the drawdown zero at exactly the moment the
        account is furthest down, which is the same defect as an absent
        measurement scoring healthy — with the number moving in the direction
        that keeps trading.

        An unusable reading returns the EXISTING peak untouched rather than
        seeding a new one. Seeding from a bad reading is how the peak gets
        pinned high; ignoring it costs one observation. A store that could not
        be read answers ``None`` and seeds nothing, for the same reason: a
        peak seeded at today's equity reads as no drawdown.
        """
        v = _sane(equity_usd)
        key = str(user_id)
        with self._lock:
            if not self._read():
                return None
            current: Optional[float] = self._peaks.get(key)
            if v is None:
                return current
            if current is None or v > current:
                self._peaks[key] = v
                self._persist()
                return v
            return current

    def peak(self, user_id: str) -> Optional[float]:
        with self._lock:
            if not self._read():
                return None
            return self._peaks.get(str(user_id))

    def reseed(self, user_id: str) -> None:
        """Forget this person's peak, to be re-measured from the next reading.

        The manual-reset path. An operator resuming after a confirmed transfer
        needs the peak re-measured, not preserved — otherwise the breaker
        re-trips immediately and the reset never sticks. Raises
        :class:`StoreUnreadable` for a file that will not read: forgetting one
        peak means writing the file, and that write would erase the rest.
        """
        with self._lock:
            if not self._read():
                raise StoreUnreadable(self._path, self._unread_detail)
            if str(user_id) in self._peaks:
                self._peaks.pop(str(user_id), None)
                self._persist(drop=str(user_id))

    def drawdown_pct(self, user_id: str, equity_usd) -> Optional[float]:
        """Percent below this person's peak, or ``None`` if not measurable.

        ``None`` — never 0.0 — when there is no peak, no usable equity, or a
        store that could not be read. A zero here reads as "no drawdown",
        which is a confident all-clear assembled from nothing, on the gate
        that decides when to stop trading. The caller has to distinguish
        those two, so this refuses to collapse them.
        """
        v = _sane(equity_usd)
        if v is None:
            return None
        p = self.observe(user_id, v)
        if p is None or p <= 0:
            return None
        return max(0.0, (p - v) / p * 100.0)


_STORE: Optional[PersonPeakStore] = None
_STORE_LOCK = threading.Lock()


def get_person_peak_store() -> PersonPeakStore:
    """The process-wide store. One owner is the entire point of this module."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = PersonPeakStore()
    return _STORE

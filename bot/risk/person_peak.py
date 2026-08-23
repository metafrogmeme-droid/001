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

from bot.utils.atomic_write import atomic_write_json
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


class PersonPeakStore:
    """``{user_id: peak_equity_usd}``, durable, one process-wide instance.

    Every method is best-effort on the PERSISTENCE and strict on the
    ARITHMETIC. A failed write costs durability, which the next observation
    repairs; a wrong peak costs a wrong drawdown on the control that decides
    how much real money is lost before the bot halts.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = str(state_path(path or _STATE_FILE))
        self._peaks: dict = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        import json
        import os
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            for user, raw in (data.get("peaks") or {}).items():
                v = _sane(raw)
                if v is not None:
                    self._peaks[str(user)] = v
                else:
                    # NOT silently dropped into a zero: say so, because a peak
                    # that vanishes reads downstream as "no drawdown".
                    log.warning("Ignoring implausible stored peak for user %s: "
                                "%r — it will re-seed from the next reading",
                                user, raw)
        except Exception as exc:
            log.warning("person peak load skipped: %s", exc)

    def _save(self) -> None:
        try:
            atomic_write_json(self._path, {"peaks": dict(self._peaks)}, indent=None)
        except Exception as exc:
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
        pinned high; ignoring it costs one observation.
        """
        v = _sane(equity_usd)
        key = str(user_id)
        with self._lock:
            current: Optional[float] = self._peaks.get(key)
            if v is None:
                return current
            if current is None or v > current:
                self._peaks[key] = v
                self._save()
                return v
            return current

    def peak(self, user_id: str) -> Optional[float]:
        return self._peaks.get(str(user_id))

    def reseed(self, user_id: str) -> None:
        """Forget this person's peak, to be re-measured from the next reading.

        The manual-reset path. An operator resuming after a confirmed transfer
        needs the peak re-measured, not preserved — otherwise the breaker
        re-trips immediately and the reset never sticks.
        """
        with self._lock:
            if str(user_id) in self._peaks:
                self._peaks.pop(str(user_id), None)
                self._save()

    def drawdown_pct(self, user_id: str, equity_usd) -> Optional[float]:
        """Percent below this person's peak, or ``None`` if not measurable.

        ``None`` — never 0.0 — when there is no peak or no usable equity. A
        zero here reads as "no drawdown", which is a confident all-clear
        assembled from nothing, on the gate that decides when to stop trading.
        The caller has to distinguish those two, so this refuses to collapse
        them.
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

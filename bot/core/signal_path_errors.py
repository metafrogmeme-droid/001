"""Errors the signal path swallows, counted and said, instead of silently passed.

The analyzer and the engine's analysis path enrich a signal from many optional
sources -- a funding rate, an order-flow snapshot, a session adjustment, a
VWAP variant. Each enrichment is fail-open on purpose: a missing extra must
never block or crash the analysis. But forty of them failed open with
``except Exception: pass``, so an enrichment that broke on every call looked
exactly like one that worked: the signal still came out, without the input
the reader believed it had.

`note_signal_path_error` keeps the fail-open behaviour and ends the silence.
Each site has a name; the count per name is kept for the process; the FIRST
failure at a site is logged at WARNING (later ones at DEBUG, so a site that
fails on every symbol of a 200-symbol sweep does not drown the log); and only
the exception's CLASS is written, never its text, because a venue's error
echoes the request. `/gates` prints the counts.

It never raises. Several of these handlers exist because test stand-ins and
partial engines lack attributes; a counter that could raise would turn a
swallowed error back into a crash.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("runeclaw.signal_path")

_LOCK = threading.Lock()
_COUNTS: dict[str, int] = {}
_LAST_CLASS: dict[str, str] = {}


def note_signal_path_error(site: str, exc: BaseException) -> None:
    """Count one swallowed error at ``site``; WARNING the first time, DEBUG after."""
    try:
        cls = type(exc).__name__
        with _LOCK:
            n = _COUNTS.get(site)
            n = 1 if n is None else n + 1
            _COUNTS[site] = n
            _LAST_CLASS[site] = cls
        if n == 1:
            log.warning("signal path: %s swallowed %s (first at this site; /gates counts)",
                        site, cls)
        else:
            log.debug("signal path: %s swallowed %s (#%d)", site, cls, n)
    except Exception:  # noqa: BLE001 -- the counter must never raise into the analysis
        return


def signal_path_error_counts() -> dict[str, tuple[int, str]]:
    """``{site: (count, last exception class)}`` for every site that has failed."""
    with _LOCK:
        return {site: (n, _LAST_CLASS.get(site, "?")) for site, n in _COUNTS.items()}

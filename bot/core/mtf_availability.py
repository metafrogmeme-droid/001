"""A timeframe the venue will not serve was re-requested every tick, forever.

The multi-timeframe leg fetches four extra timeframes per symbol:

    _tf_specs = (("15m", 200), ("1h", 200), ("4h", 200), ("1d", 200))
    _tf_results = await asyncio.gather(*[self._cached_ohlcv(...ttl=180)...])

Three properties combine badly:

  * NO CATEGORY GUARD — every non-lightweight symbol gets all four.
  * ttl=180 caches SUCCESSES. A failure caches nothing.
  * fail-open per timeframe — a failure omits that leg and is logged at
    debug, so nothing surfaces.

So a symbol whose extra timeframes the venue does not serve fails all four,
caches nothing, and is asked again on the very next tick. Every tick. Each
attempt costs up to the per-call timeout, and none of it is ever visible as
an error.

2026-08-01, production: mtf overtook fetch as the dominant stage (~59-64% vs
~36-41%) on an 85-symbol universe, with 22-27 signals skipped per cycle and
the operator reporting the stall was "almost all R* stocks" — the tokenized
equities. One tick hit the 300s phase cap at 315s having finished 60/60: work
completed, cancelled at the tape.

    A CACHE THAT ONLY REMEMBERS SUCCESSES ASKS THE SAME FAILING QUESTION
    FOREVER.

WHY NOT JUST EXCLUDE THE R* SYMBOLS

That fixes the instance, not the class. `R` is a venue-specific prefix that
can change, it says nothing about WHY those symbols are slow, and the next
family with thin timeframe coverage would rediscover the whole problem. This
remembers what actually failed, whatever it is called.

WHY THIS CANNOT LATCH ON A BLIP

The same asymmetry as the venue-auth classifier: suppressing a timeframe that
was merely having a bad minute costs real analysis quality, silently. So

  * a single failure suppresses nothing — it takes CONSECUTIVE_FAILURES in a
    row, so one bad response cannot latch anything;
  * suppression EXPIRES, so a venue that starts serving a timeframe is picked
    back up without a restart;
  * any success clears the record immediately.

Fail-open throughout: when in doubt, fetch. The cost of an unnecessary fetch
is latency; the cost of a wrongly-suppressed timeframe is a worse decision on
real money.

This module decides nothing about trading. It answers one question — "is it
worth asking for this timeframe right now" — and never says a symbol is bad,
because it cannot see why a fetch failed.
"""
from __future__ import annotations

import time
from typing import Any

__all__ = ["new_state", "record_failure", "record_success", "is_suppressed",
           "filter_specs", "summarize", "render",
           "CONSECUTIVE_FAILURES", "SUPPRESS_SECONDS"]

#: Consecutive failures before a (symbol, timeframe) is suppressed. One bad
#: response is a blip; three in a row on the same pair is a pattern.
CONSECUTIVE_FAILURES = 3

#: How long a suppression lasts before the pair is probed again. Long enough
#: to matter across many ticks, short enough that restored coverage returns
#: without a restart.
SUPPRESS_SECONDS = 3600.0


def new_state() -> dict:
    """A fresh record. Plain dict so it survives being logged as JSON."""
    return {"fails": {}, "until": {}, "suppressed_total": 0}


def _key(symbol: Any, timeframe: Any) -> str:
    return f"{str(symbol or '?')}|{str(timeframe or '?')}"


def record_failure(state: dict, symbol: str, timeframe: str,
                   *, now: float | None = None) -> bool:
    """Note one failed timeframe fetch. Returns True if it is now suppressed.

    Never raises — this is bookkeeping beside a fail-open path, and an
    exception here would sink an analysis to save a fetch.
    """
    try:
        if not isinstance(state, dict):
            return False
        t = float(now if now is not None else time.monotonic())
        k = _key(symbol, timeframe)
        fails = state.setdefault("fails", {})
        n = int(fails.get(k, 0)) + 1
        fails[k] = n
        if n >= CONSECUTIVE_FAILURES:
            until = state.setdefault("until", {})
            if k not in until or until[k] <= t:
                state["suppressed_total"] = int(
                    state.get("suppressed_total", 0)) + 1
            until[k] = t + SUPPRESS_SECONDS
            return True
        return False
    except Exception:
        return False


def record_success(state: dict, symbol: str, timeframe: str) -> None:
    """Clear a pair's record. A success is proof the pair is servable now."""
    try:
        if not isinstance(state, dict):
            return
        k = _key(symbol, timeframe)
        state.get("fails", {}).pop(k, None)
        state.get("until", {}).pop(k, None)
    except Exception:
        pass


def is_suppressed(state: dict, symbol: str, timeframe: str,
                  *, now: float | None = None) -> bool:
    """True only while a live suppression covers this pair.

    Expiry is checked on READ rather than swept, so a pair whose window has
    passed is probed again the next time it is asked for, with no background
    task and no restart.
    """
    try:
        if not isinstance(state, dict):
            return False
        t = float(now if now is not None else time.monotonic())
        exp = (state.get("until") or {}).get(_key(symbol, timeframe))
        if exp is None:
            return False
        if float(exp) <= t:
            # Window passed: forget it entirely so the next probe starts from
            # a clean count rather than one failure away from re-suppression.
            state.get("until", {}).pop(_key(symbol, timeframe), None)
            state.get("fails", {}).pop(_key(symbol, timeframe), None)
            return False
        return True
    except Exception:
        return False


def filter_specs(state: dict, symbol: str, specs,
                 *, now: float | None = None) -> list:
    """The timeframe specs still worth requesting for ``symbol``.

    Returns a list of the same (timeframe, limit) pairs, minus any currently
    suppressed. On any internal error it returns the specs UNCHANGED — the
    failure mode of this module must be "fetched something we could have
    skipped", never "silently stopped fetching".
    """
    try:
        return [s for s in specs
                if not is_suppressed(state, symbol, s[0], now=now)]
    except Exception:
        # `specs` itself, NOT list(specs): the fallback ran the very
        # iteration that had just failed, so the recovery path could raise
        # the same error it was recovering from. Returning the original
        # object cannot fail and is exactly what the caller passed in.
        return specs


def summarize(state: dict, *, now: float | None = None) -> dict:
    """Counts of what is currently suppressed. {} when nothing is."""
    try:
        t = float(now if now is not None else time.monotonic())
        until = dict((state or {}).get("until", {}) or {})
        live = {k: v for k, v in until.items() if float(v) > t}
        if not live:
            return {}
        by_symbol: dict[str, int] = {}
        for k in live:
            sym = k.split("|", 1)[0]
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
        return {
            "pairs": len(live),
            "symbols": len(by_symbol),
            "top": sorted(by_symbol.items(), key=lambda kv: (-kv[1], kv[0]))[:8],
        }
    except Exception:
        return {}


def render(state: dict, *, now: float | None = None) -> str:
    """One operator-facing line, or "" when nothing is suppressed.

    Suppression that nobody can see is the same defect as the silent
    fail-open it replaces: an analysis quietly missing a timeframe, with no
    way to find out. It reports WHAT is skipped and never why — this cannot
    see why a fetch failed, and a sentence supplying a reason would be the
    verdict-from-a-heuristic this codebase keeps deleting.
    """
    s = summarize(state, now=now)
    if not s:
        return ""
    names = ", ".join(f"{sym} ×{n}" for sym, n in s["top"][:5])
    return (f"mtf: {s['pairs']} timeframe(s) across {s['symbols']} symbol(s) "
            f"temporarily not requested after {CONSECUTIVE_FAILURES} "
            f"consecutive failures each; re-probed within "
            f"{int(SUPPRESS_SECONDS / 60)}min. Most: {names}")

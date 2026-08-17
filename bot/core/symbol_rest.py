"""How long a symbol rests after its analysis times out, and how to say so.

THE INCIDENT, 2026-08-17. `/status` reported:

    Slowest tick phase: ⚠ analyze 292s peak of 300s (97%)

Three symbols — MCD, AMD and HOOD, all tokenized US equities — were timing out
at ANALYSIS_TIMEOUT_SEC (90s) each. 3 x 90 = 270 of those 292 seconds, every
sweep, indefinitely. The engine already NAMED them (`_last_analysis_timeout`),
which was the previous fix and a good one: a quiet tick hiding a hanging
dependency is how this stayed invisible for a day. But naming is not resting.
Nothing ever skipped them, so the same three symbols burned 92% of the analyze
phase on every pass while the other sixty-odd shared what was left.

WHY THE ARITHMETIC LIVES IN ITS OWN MODULE. The arming site is inside a nested
`_one()` coroutine, in an `except asyncio.TimeoutError`, inside a
`asyncio.gather` over a batch. Nothing can drive that from a unit test without
standing up an entire scan. The escalation rule and the operator sentence are
the parts that carry the judgement, so they are pure functions here and the
engine keeps only the wiring.

THE HALF THAT IS NOT PERFORMANCE. A rested symbol was NOT analysed and found
nothing — it was not looked at. The engine's progress counter increments for a
timeout ("counts finished work of ANY outcome"), so without care a resting
symbol is published as scanned, and "Read 62 of 67 symbols" starts meaning two
different things in the same sentence. Absence presented as a measurement, in
the surface that exists to report coverage. `rest_note` is how a reader is told
the difference; it returns "" when nothing rested, because a coverage line that
always mentions resting teaches people to ignore it.
"""

from __future__ import annotations

import math


def rest_seconds(strikes: int, base_sec: float, cap_sec: float) -> float:
    """Seconds to rest a symbol after its ``strikes``-th consecutive timeout.

    Doubling, capped: 15m, 30m, 1h, 2h, 4h. A symbol that hung once on a bad
    exchange minute is retried inside the hour; one that is reliably
    unanalysable stops being asked several times an hour forever.

    Returns 0.0 when resting is disabled (``base_sec <= 0``) or when the strike
    count is not a real strike — the caller must treat 0.0 as "do not arm",
    never as "rest for no time", which would clear on the next comparison and
    silently restore the old behaviour.
    """
    try:
        strikes = int(strikes)
        base = float(base_sec)
        cap = float(cap_sec)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    # NaN and inf must be refused EXPLICITLY. Every comparison against NaN is
    # False, so `base <= 0` does not catch it and the arithmetic below happily
    # returns NaN — a value the caller would then treat as a duration. It
    # happens to be survivable downstream (`if _rest > 0` is also False for
    # NaN) but that is luck, not design: the function's contract is that its
    # return is a real number of seconds or 0.0 meaning do-not-arm. Caught by
    # test_rest_seconds_is_total_... on its first run.
    if not (math.isfinite(base) and math.isfinite(cap)):
        return 0.0
    if base <= 0 or strikes < 1:
        return 0.0
    if cap <= 0:
        return base
    # 2 ** (strikes - 1) overflows for a large strike count, and a counter that
    # only ever increments is exactly where that arrives. Clamp the exponent
    # rather than let the guard raise inside a timeout handler.
    shift = min(max(strikes - 1, 0), 32)
    return float(min(base * (2 ** shift), cap))


def rest_note(rested: int, total: int) -> str:
    """The operator-facing sentence for a pass where symbols were resting.

    Empty when nothing rested. A coverage line that always carries a resting
    clause is one people stop reading, and the whole point is that it appears
    exactly when the count it qualifies means something different than usual.

    Deliberately says "not analysed this pass" rather than "skipped": skipped
    reads as a decision about the symbol's merit, and this is a decision about
    the bot's own capacity to read it.
    """
    try:
        rested = int(rested)
        total = int(total)
    except (TypeError, ValueError, OverflowError):
        return ""
    if rested <= 0:
        return ""
    if total > 0 and rested >= total:
        return (f"⚠️ all {rested} symbols are resting after repeated analysis "
                f"timeouts — nothing was analysed this pass")
    return (f"{rested} resting after repeated analysis timeouts "
            f"(not analysed this pass)")


def coverage_sentence(analysed: int, rested: int, total: int) -> str:
    """One line that cannot be read as claiming more coverage than it has.

    "Read 62 of 67 symbols" is true and, once symbols can rest, incomplete:
    it does not say whether the missing five were unreadable or never asked.
    Those are different facts and the operator acts differently on each.
    """
    try:
        analysed = int(analysed)
        rested = int(rested)
        total = int(total)
    except (TypeError, ValueError, OverflowError):
        return ""
    if total <= 0:
        return ""
    base = f"Analysed {analysed} of {total} symbols"
    note = rest_note(rested, total)
    return f"{base} — {note}" if note else base

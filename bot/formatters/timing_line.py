"""Entry timing on the card a person taps from: has the turn been confirmed?

Live `/parity` (9 October): the stop exits are the losses, 72 of them, and
every other exit class is net positive. In the frozen replays 28-46% of
stop-outs come within two bars, after a median favourable move of about 0.2R
(`docs/FROZEN_BENCHMARK.md`, the stop-out study).

The bot already computes whether the sub-degree turn is confirmed for every
idea it analyses (`entry_timing.turn_reading`), and gates only autonomous
entries on it, in TREND_DOWN. Every live order is a tap, so the reading belongs
on the card instead: information for the person deciding, not a rule. It is
not a default either: the frozen-benchmark re-measure split by window
(`docs/FROZEN_BENCHMARK.md`, discovery data).

THREE VALUES. Confirmed, not confirmed yet, and not read (too little history or
a failed check) are different facts with different sentences. An idea the
engine never analysed (a hand-typed ticket) has no reading at all, and its card
says nothing about timing.
"""
from __future__ import annotations

from typing import Any, Optional


def entry_timing_line(engine: Any, idea: Any) -> Optional[str]:
    """One card line for ``idea``'s turn reading, or None without one.

    Never raises: a card is not refused for a line it could not build.
    """
    try:
        from bot.core.entry_timing import TURN_CONFIRMED, TURN_NOT_CONFIRMED, TURN_UNREAD

        reading = (getattr(engine, "_pending_turn", None) or {}).get(getattr(idea, "id", None))
        if not isinstance(reading, tuple) or len(reading) != 3:
            return None
        state, reason, timeframe = reading
        where = f"Entry timing ({timeframe}, closed bars)" if timeframe else "Entry timing (closed bars)"
        if state == TURN_CONFIRMED:
            return f"⏱ {where}: turn confirmed ({reason})"
        if state == TURN_NOT_CONFIRMED:
            return f"⏱ {where}: turn not confirmed yet ({reason})"
        if state == TURN_UNREAD:
            return f"⏱ {where}: not read ({reason})"
        return None
    except Exception:  # noqa: BLE001 -- the card stands without this line
        return None

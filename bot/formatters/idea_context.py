"""The lines a card that offers an idea carries for the person deciding.

Every live order is a tap, so whatever could change the decision belongs on the
card with the Confirm button: the person's own live record in the idea's asset
class (`class_record_line`) and whether the turn is confirmed on the idea's
closed bars (`entry_timing_line`). This is the one reading every card that
offers an idea takes, so no two of them can disagree about what is said beside
a Confirm button, or forget a line another card carries.

Plain text: the caller escapes for its parse mode. A line with nothing to say
is left out; the list can be empty. Never raises.
"""
from __future__ import annotations

from typing import Any, Iterable


def idea_context_lines(engine: Any, user_id: Any, idea: Any) -> list[str]:
    """The context lines for a card offering ``idea`` to ``user_id``."""
    try:
        from bot.formatters.class_record import class_record_line
        from bot.formatters.timing_line import entry_timing_line

        lines = (class_record_line(engine, user_id, getattr(idea, "asset", "") or ""),
                 entry_timing_line(engine, idea))
        return [line for line in lines if line]
    except Exception:  # noqa: BLE001 -- a card stands without its context
        return []


def class_lines_for(engine: Any, user_id: Any, symbols: Iterable[str]) -> list[str]:
    """One class-record line per asset class among ``symbols``, in first-seen
    order: for a card that offers several ideas at once (a scan), where the
    record is the class's and repeating it per row would say one thing many
    times."""
    try:
        from bot.core.market_scanner import category_for_symbol
        from bot.formatters.class_record import class_record_line

        seen: set[str] = set()
        out: list[str] = []
        for sym in symbols:
            cat = category_for_symbol(str(sym or ""))
            if cat in seen:
                continue
            seen.add(cat)
            line = class_record_line(engine, user_id, sym)
            if line:
                out.append(line)
        return out
    except Exception:  # noqa: BLE001 -- a card stands without its context
        return []

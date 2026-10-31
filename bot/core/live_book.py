"""What a live executor's book still holds, read one way for every caller.

`/golive OFF` asks it before turning live off, and the engine asks it to
decide whether a live book is winding down: live is off, and a position it
opened is still on the venue (`RuneClawEngine._winding_down_executors`).
"""
from __future__ import annotations

from typing import Optional

#: The row states that mean "the venue may hold something for this row".
HELD_STATUSES = ("open", "pending_fill", "closing")


def live_book_open(executor) -> Optional[list[str]]:
    """The live positions and resting entries on ``executor``'s book, as symbols.

    `[]` when there is no executor or nothing is held; None when the book
    cannot be read. Both callers treat None as "may hold a position": turning
    live off over one, or leaving one unmanaged, is the outcome each must not
    produce.
    """
    if executor is None:
        return []
    try:
        rows = list(getattr(executor, "_positions", {}).values())
    except Exception:
        return None
    out = []
    for p in rows:
        if getattr(p, "status", None) in HELD_STATUSES:
            out.append(str(getattr(p, "symbol", "?")))
    return out

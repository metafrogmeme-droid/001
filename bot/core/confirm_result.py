"""What a `confirm_trade` answer says was placed, and where.

`engine.confirm_trade` answers with a sentence, and two doors decide from that
sentence whether to tell the person "✅ executed": the Confirm button
(`callback_handler`) and the scan card's confirm (`scan_skill`). Each carried a
private prefix list, and both lists were missing the refusals `confirm_trade`
writes without the word "REJECTED": the chosen-strategy refusal (🛡), the
duplicate skip (⏭️), "Paper trading is disabled" (⛔), and the practice fill's
cooldown (⏸) and failure. Each of those read as "✅ Trade executed", and the
Confirm button then posted the idea to the public channels as a TRADE OPENED.

`placed_nothing` is the one reading, and
`tests/test_a_refusal_is_never_announced_as_a_trade.py` walks every literal
answer `confirm_trade` can give and requires it to be read correctly, so a
refusal added tomorrow fails a test rather than a person's chat.

`held_on_operator_book` answers the other question the public post needs, by
MEASURING rather than predicting: the live executor keys a new position by the
idea's id, so the trade landed on the operator's book when that book holds it.
"""
from __future__ import annotations

from typing import Any

# `confirm_trade`'s own answers that place nothing, by how they begin. The
# executor's answers are the executor's vocabulary
# (`live_executor.execution_indicates_failure`) and are asked there.
REFUSAL_PREFIXES = (
    "Trade not found",
    "Trade REJECTED",
    "Trade HALTED",
    "Execution denied",
    "\U0001f6e1",                                   # the chosen strategy refused
    "\u23ed",                                       # duplicate suppressed
    "\u26d4",                                       # paper trading is disabled
    "\u23f8 [PAPER]",                               # practice cooldown
    "\u26a0\ufe0f [PAPER] Simulated fill failed",   # practice fill raised
)


def placed_nothing(result: Any) -> bool:
    """True when `confirm_trade`'s answer means no position resulted.

    A non-string answer is not a reading of a placement, so it is read as
    nothing placed (`execution_indicates_failure` answers that before the
    prefixes are asked): announcing a trade off an answer nobody can read is
    the direction that cannot be taken back.
    """
    from bot.core.live_executor import execution_indicates_failure

    return execution_indicates_failure(result) or result.startswith(REFUSAL_PREFIXES)


def held_on_operator_book(engine: Any, trade_id: str) -> bool:
    """True when the operator's live executor holds `trade_id` open or resting.

    This is what the public channel may announce: the agent's own book. A
    person's own account, a practice fill and every refusal leave it without
    the id, whatever the answer's wording.
    """
    executor = getattr(engine, "live_executor", None)
    positions = getattr(executor, "_positions", None)
    if not isinstance(positions, dict):
        return False
    pos = positions.get(trade_id)
    return pos is not None and getattr(pos, "status", None) in ("open", "pending_fill")

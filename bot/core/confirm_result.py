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

`left_resting` answers the third: a limit order placed to rest is neither a
refusal nor a trade, so the door does not call it "executed". The public post
is not the door's to make: the engine announces the agent's open when the
row on the operator's book is open (`RuneClawEngine._announce_agent_open`).
"""
from __future__ import annotations

from typing import Any

# `confirm_trade`'s own answers that place nothing, by how they begin, each
# with the words `/whynot` says for it. The words are a column on this table
# and not a second list: a refusal added here is named there too. The
# executor's answers are the executor's vocabulary
# (`live_executor.execution_indicates_failure`) and are asked there.
REFUSAL_WORDS: dict[str, str] = {
    "Trade not found": "the idea had expired or was not found",
    "Trade REJECTED": "the re-check at confirm rejected it",
    "Trade HALTED": "trading was halted",
    "Execution denied": "execution was denied",
    "\U0001f6e1": "the chosen strategy refused it",
    "\u23ed": "a position or order on this symbol already existed",
    # Two answers begin so: "Paper trading is disabled" on a live-only bot,
    # and an unattended confirm while `/golive OFF` stands. Both are this.
    "\u26d4": "the engine was not live, so nothing could be placed",
    "\u23f8 [PAPER]": "the practice book was cooling down",
    "\u26a0\ufe0f [PAPER] Simulated fill failed": "the practice fill failed",
}
REFUSAL_PREFIXES = tuple(REFUSAL_WORDS)

#: The words for a refusal that is the executor's own answer.
EXECUTOR_REFUSAL_WORDS = "the order was not placed (the executor refused or failed)"


def placed_nothing(result: Any) -> bool:
    """True when `confirm_trade`'s answer means no position resulted.

    A non-string answer is not a reading of a placement, so it is read as
    nothing placed (`execution_indicates_failure` answers that before the
    prefixes are asked): announcing a trade off an answer nobody can read is
    the direction that cannot be taken back.

    THIS IS NOT THE ONLY QUESTION. An answer `outcome_unverified` reads as the
    third outcome is neither a placement nor a refusal, and a door that asks
    only this function announces it as one of the two; every door asks
    `outcome_unverified` FIRST (a test walks them).
    """
    from bot.core.live_executor import execution_indicates_failure

    return execution_indicates_failure(result) or result.startswith(REFUSAL_PREFIXES)


def refusal_words(result: Any) -> str:
    """What a refusal is called where a person reads why nothing was placed.

    Words from the table, never the answer itself: the answer can carry a
    figure about the account it was refused on, and the record it feeds is
    read on other surfaces. Only meaningful when `placed_nothing` is True.
    """
    text = result if isinstance(result, str) else ""
    for prefix, words in REFUSAL_WORDS.items():
        if text.startswith(prefix):
            return words
    return EXECUTOR_REFUSAL_WORDS


def outcome_unverified(result: Any) -> bool:
    """True when the executor's answer says the venue confirmed the entry
    neither way: the send raised in a way that leaves it unknown whether the
    order was taken, and the order lists could not be read after it.

    Not a placement (nothing is on the book, the public channel has nothing to
    announce) and not a refusal (an announcement that nothing was placed is a
    claim about the venue nobody could make). The executor keeps the
    submission on record and reconciles it by client id; the doors say that,
    and only that.
    """
    from bot.core.live_executor import execution_outcome_unverified

    return execution_outcome_unverified(result)


def left_resting(result: Any) -> bool:
    """True when the answer is a limit order placed and resting, not filled.

    Something was placed, so `placed_nothing` is False, and nothing is held:
    the door says the order is resting rather than "executed", and the
    public announcement of the open waits for the fill
    (`RuneClawEngine._announce_agent_open`).
    """
    from bot.core.live_executor import execution_left_resting

    return execution_left_resting(result)

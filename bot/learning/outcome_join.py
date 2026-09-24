"""One decision row per closed trade, joined to the outcome that closed it.

The learning store is append-only, so a trade's confidence and its result
live in two records: the DECISION row written when the trade was confirmed
(``pnl_result`` still None) and the OUTCOME row written when it closed.
Both carry ``paper_trade_id``, and every learner that asks "did the trades
the engine was confident about win" has to join them. Two did, each with its
own copy of the join -- the confidence calibrator and the voter-weight
learner -- and both copies joined EVERY decision row carrying the id.

THAT COUNTED ONE TRADE TWICE. `_confirm_trade_inner` pops the pending idea
only after a successful execution (its C-05 comment says so), and it logs a
decision row either way: ``EXECUTION_FAILED`` when the venue refused, under
the same trade id. The idea stays confirmable, a retry that succeeds logs
``TRADE_ACCEPTED_LIVE`` under the SAME id, and when the position closes both
rows join the one outcome -- two samples, one trade, the same confidence
counted twice against the learners' evidence floors. A trade that never
opened is not a sample of anything, which is what the accept path's own
comment says about that row: "a trade that never opened must not train the
calibrator as one that did". It said so and nothing read it.

So a decision row joins only when its word does not name a trade that never
opened, and each trade id yields ONE row. Both learners ask this module, so
they cannot disagree about which rows a trade has.
"""
from __future__ import annotations

from typing import Any, Iterable, List, NamedTuple, Optional, Tuple

#: Decision words the engine writes for a trade that did not open. Read by
#: `opened`; the guard walks every ``log_decision`` call in the engine and
#: fails on a word `opened` cannot place, so a new word is classified the day
#: it is written rather than joining by default.
NOT_OPENED_DECISIONS = frozenset({"EXECUTION_FAILED", "TRADE_REJECTED_FAIL_CLOSED"})

#: The accept path's words start with this; a paper fill and a live one are
#: the same fact to a learner -- a position existed and closed.
_OPENED_PREFIX = "TRADE_ACCEPTED"


def opened(decision: Any) -> Optional[bool]:
    """Whether a decision word names a trade that opened.

    ``None`` is a word this build does not know -- a row an older build wrote,
    or one that carries no word at all -- and it joins as it always has:
    refusing it would drop history on a guess about vocabulary, and the words
    the engine writes TODAY are all placed (the guard says so).
    """
    word = str(decision or "").strip()
    if word in NOT_OPENED_DECISIONS:
        return False
    if word.startswith(_OPENED_PREFIX):
        return True
    return None


class Joined(NamedTuple):
    #: ``(decision_row, won)``, one per trade id.
    rows: List[Tuple[Any, bool]]
    #: Decision rows for a closed trade's id whose word says the trade never
    #: opened -- the failed attempt before a successful retry.
    not_opened: int


def join_outcomes(decisions: Iterable[Any]) -> Joined:
    """Each closed trade's decision row, with whether it won. Never raises on
    a row missing a field; a row with no id or no outcome does not join."""
    decisions = list(decisions)
    outcome: dict = {}
    for d in decisions:
        tid = getattr(d, "paper_trade_id", "") or ""
        pnl = getattr(d, "pnl_result", None)
        if tid and pnl is not None:
            outcome[tid] = float(pnl) > 0.0
    chosen: dict = {}
    not_opened = 0
    for d in decisions:
        tid = getattr(d, "paper_trade_id", "") or ""
        if not tid or tid not in outcome:
            continue
        # The outcome row itself: it carries the id and the result and is not
        # a decision.
        if getattr(d, "pnl_result", None) is not None:
            continue
        if opened(getattr(d, "decision", "")) is False:
            not_opened += 1
            continue
        chosen[tid] = d
    return Joined([(d, outcome[tid]) for tid, d in chosen.items()], not_opened)

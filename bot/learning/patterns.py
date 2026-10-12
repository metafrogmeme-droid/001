"""RUNECLAW AI Learning — closes in the learning store, grouped by condition.

WHAT THIS WAS. `detect_patterns` named the learning store's groups after
patterns it never looked for. Any trend-regime group that won more than 55% of
five or more closes became TREND_CONTINUATION; any range or chop group that
won under 40% became BREAKOUT_FAILURE, though nothing checked that one of
those trades was a breakout; and each carried a "confidence" that was the win
rate capped at 0.85 (or one minus it). The /patterns card printed that
confidence, the win rate with no interval, and an average in dollars over
every account's closes.

WHAT IT IS. A group is the closes whose row recorded one condition: the
market regime, a macro state other than NORMAL, or low volatility. Each
carries its wins, losses and flat closes, counted by the one rule
(`win_rate.outcome_of`: a recorded 0.0 is a flat close, not a loss), and the
95% Wilson interval on its win rate (`readiness.wilson_interval`). A group is
what happened under that condition. It is not a pattern that predicts it, and
the record names it by the condition, never by a story about it.

Every group with `MIN_GROUP_CLOSES` scored closes is reported, not only the
ones past a threshold: choosing the groups whose rate looks remarkable and
printing their rate is how a 5-trade coincidence reads as a finding.

NO RETURN FIGURE. The store's outcome rows carry P&L in dollars, with no stop
and no size, from every account's closes (`_on_live_position_closed` writes
per-user live closes here unattributed). An average of those is not an R and
not a percent, so none is computed.

Patterns are observations, NOT trade commands. No record may override the
risk engine.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from bot.utils.win_rate import OUTCOME_FLAT, OUTCOME_LOSS, OUTCOME_UNSCORED, OUTCOME_WIN, outcome_of

from .models import DecisionMemory, PatternRecord
from .readiness import wilson_interval
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.patterns")

# Minimum sample size to consider a group "confirmed" vs "experimental"
MIN_CONFIRMED_SAMPLES = 20

#: Scored closes a group needs before it is reported at all. One floor for
#: every dimension: the macro groups had a floor of three of their own.
MIN_GROUP_CLOSES = 5

#: How many of the newest decision rows are read. The log holds a row for
#: every idea the risk gate rejects, so most of these are not closes; the
#: reading says how many were.
DECISION_WINDOW = 1000

#: The dimensions a group is cut on, in the order they are reported.
GROUP_DIMENSIONS = ("regime", "macro_state", "volatility")

#: The value a row with nothing recorded on a dimension is grouped under.
NOT_RECORDED = "not recorded"


@dataclass(frozen=True)
class OutcomeGroups:
    """What was read, and the groups it holds.

    ``rows_read`` is the decision rows the read returned (at most
    ``window``), ``closes`` those carrying a result that could be scored and
    ``unscored`` those carrying one that could not (NaN, inf), which leave
    every rate rather than counting as losses.
    """
    window: int
    rows_read: int
    closes: int
    unscored: int
    groups: list[PatternRecord] = field(default_factory=list)


def _group(rows: list[DecisionMemory], dimension: str, value: str,
           ) -> Optional[PatternRecord]:
    """The record for one group, or None below the floor."""
    outcomes = [outcome_of(r.pnl_result) for r in rows]
    wins = outcomes.count(OUTCOME_WIN)
    losses = outcomes.count(OUTCOME_LOSS)
    flat = outcomes.count(OUTCOME_FLAT)
    scored = wins + losses + flat
    if scored < MIN_GROUP_CLOSES:
        return None
    return PatternRecord(
        pattern_type=dimension,
        market_regime=value,
        sample_size=scored,
        is_experimental=scored < MIN_CONFIRMED_SAMPLES,
        description=f"{wins} won, {losses} lost, {flat} flat of {scored} "
                    f"closes with {dimension.replace('_', ' ')} {value}",
        historical_win_rate=wins / scored,
        wins=wins, losses=losses, flat=flat,
        win_rate_interval=wilson_interval(wins, scored),
        may_override_risk=False,  # ALWAYS False
    )


class PatternLearner:
    """Group the learning store's closes by the condition each recorded.

    Rules:
    - Groups are observations, not automatic trade commands.
    - Every group carries its counts and the interval on its rate.
    - Low sample-size groups are marked experimental.
    - No group may override the risk engine.
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def outcome_groups(
        self,
        decisions: Optional[list[DecisionMemory]] = None,
    ) -> OutcomeGroups:
        """The newest `DECISION_WINDOW` decision rows' closes, grouped.

        Raises when the decision log is there and cannot be read, so that a
        failed read never renders as a log with no closes in it.
        """
        if decisions is None:
            decisions = self._store.get_decisions(limit=DECISION_WINDOW, strict=True)

        completed = [d for d in decisions if d.pnl_result is not None]
        unscored = sum(1 for d in completed
                       if outcome_of(d.pnl_result) == OUTCOME_UNSCORED)

        by_dimension: dict[str, dict[str, list[DecisionMemory]]] = {
            dim: defaultdict(list) for dim in GROUP_DIMENSIONS}
        for d in completed:
            by_dimension["regime"][d.market_regime or NOT_RECORDED].append(d)
            # NORMAL is the absence of a macro condition, and an empty state
            # was never recorded; neither is a group of its own.
            if d.macro_state not in ("", "NORMAL"):
                by_dimension["macro_state"][d.macro_state].append(d)
            if d.volatility_state == "low":
                by_dimension["volatility"]["low"].append(d)

        groups: list[PatternRecord] = []
        for dim in GROUP_DIMENSIONS:
            found = [g for value, rows in by_dimension[dim].items()
                     if (g := _group(rows, dim, value)) is not None]
            found.sort(key=lambda g: g.sample_size, reverse=True)
            groups.extend(found)

        return OutcomeGroups(window=DECISION_WINDOW, rows_read=len(decisions),
                             closes=len(completed) - unscored,
                             unscored=unscored, groups=groups)

"""
The pre-trade critique: heuristic concerns asked before every confirm.

Before a trade executes, this module lists HEURISTIC concerns about the idea:
a high confidence score, a marginal R:R, a book already holding several
positions, a macro REDUCE state and a stop inside 1% of entry. Each is a rule
of thumb. None has been backtested against outcomes, and the backtest executes
every approved idea without asking them, yet four of them HALT a trade, one a
person has already confirmed included. `scripts/critique_replay.py` replays the
concerns over the frozen benchmark's approved ideas, so what the halt would
remove is a measured figure (docs/lessons/ENGINEERING_LOG.md records the run).

The sentences say what each rule is and no more. "High probability of stop
hunt" was a probability nothing measured, and "model may be overfitting" blamed
the model for a score the rule-based confluence carries most of at shipped
weights. A concern names the reading and the bar it crossed.

Fail-open by design: if critique cannot be computed, trade proceeds
with a logged warning (unlike risk engine which is fail-closed).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bot.core.signal_confidence import displayed_confidence, score_number


@dataclass
class CritiqueResult:
    """Structured output from the pre-trade critique."""
    verdict: str  # "HALT" | "WARN" | "PASS"
    bear_case: str  # 1-2 sentence summary of the concerns
    concerns: list[str] = field(default_factory=list)
    confidence_adjustment: float = 0.0  # negative = reduce confidence
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    #: The rule behind each concern, in the same order: what a replay counts
    #: without parsing the sentences.
    rules: list[str] = field(default_factory=list)


class TradeCritique:
    """Pre-trade review gate: rule-based heuristic concerns (no LLM call).

    Analyzes a TradeIdea and its risk check results for reasons the trade
    might fail. The reasons are heuristics, not measurements.
    """

    # Thresholds. Each is a heuristic bar, not one fitted to outcomes.
    HIGH_CONFIDENCE_WARN = 0.90  # a confidence score above this is a concern
    LOW_RR_WARN = 1.5            # R:R close to minimum
    HOT_BOOK_POSITIONS = 4       # this many open positions is a concern
    TIGHT_STOP_PCT = 1.0         # a stop nearer than this % of entry
    MAX_CONCERNS_FOR_HALT = 4    # 4+ concerns = HALT

    def evaluate(self, idea, risk_check, portfolio_snapshot, macro_context=None) -> CritiqueResult:
        """List the heuristic concerns about a trade idea."""
        concerns: list[str] = []
        rules: list[str] = []
        confidence_adj = 0.0

        # 1. High confidence score
        #
        # THE MEASURED BLEND, NOT THE FIELD. `HIGH_CONFIDENCE_WARN` is 0.90 on
        # the RAW scale -- the scale every confidence floor in this repo is
        # defined on -- and `idea.confidence` is whatever the calibration curve
        # left on the field. Driven both ways: a hand-typed ticket
        # (`build_manual_idea` stamps 1.0) tripped this concern EVERY time at
        # shipped defaults, under a sentence blaming "the model" for a number
        # the operator typed, costing -0.05 and one of the four concerns that
        # HALT a trade; and with a curve applied, a measured blend of 0.95 came
        # back 0.31 on the field and was NOT flagged -- the exact idea this
        # check exists to catch.
        #
        # A stamp is not a high score, because nothing measured it, and an
        # unreadable confidence is not one either. Both abstain.
        #
        # The score blends the thesis's figure with the rule-based confluence,
        # and the confluence carries the larger weight while no calibration
        # curve is applied (`_blend_weights`), so the sentence names the score
        # and the bar, never "the model".
        _conf_read = displayed_confidence(idea)
        if _conf_read.above(self.HIGH_CONFIDENCE_WARN):
            concerns.append(
                f"Confidence {_conf_read.text()} is above the critique's "
                f"{score_number(self.HIGH_CONFIDENCE_WARN)} bar (heuristic)")
            rules.append("high_confidence")
            confidence_adj -= 0.05

        # 2. Marginal R:R
        if idea.risk_reward_ratio < self.LOW_RR_WARN:
            concerns.append(
                f"R:R {idea.risk_reward_ratio:.2f} is under the critique's "
                f"{self.LOW_RR_WARN} bar (heuristic)")
            rules.append("marginal_rr")
            confidence_adj -= 0.03

        # 3. Concentration risk (same direction as existing positions)
        # PortfolioState.open_positions is an int (count), not a list.
        # Use it for the open-book check; direction/asset checks require
        # the actual positions list from the portfolio object.
        open_count = 0
        if hasattr(portfolio_snapshot, 'open_positions'):
            val = portfolio_snapshot.open_positions
            if isinstance(val, int):
                open_count = val
            elif hasattr(val, '__len__'):
                open_count = len(val)

        # 4. Same-asset double-down
        # Cannot check per-asset or per-direction without position objects;
        # skip these checks when only a count is available.
        # (The risk engine enforces max-positions and concentration limits.)

        # 5. Open-book check (many open positions)
        if open_count >= self.HOT_BOOK_POSITIONS:
            concerns.append(
                f"{open_count} open positions on this book, at or over the "
                f"critique's {self.HOT_BOOK_POSITIONS} (heuristic)")
            rules.append("hot_book")
            confidence_adj -= 0.03

        # 6. Macro headwind
        if macro_context is not None:
            risk_state = getattr(macro_context, 'risk_state', None)
            if risk_state == "REDUCE":
                concerns.append(
                    "The macro provider reads REDUCE (heuristic: event risk)")
                rules.append("macro_reduce")
                confidence_adj -= 0.05

        # 7. Tight stop (SL very close to entry)
        if idea.entry_price > 0 and idea.stop_loss > 0:
            sl_distance_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
            if sl_distance_pct < self.TIGHT_STOP_PCT:
                concerns.append(
                    f"Stop loss {sl_distance_pct:.1f}% from entry, inside the "
                    f"critique's {self.TIGHT_STOP_PCT:.0f}% bar (heuristic; no "
                    "stop-out rate was measured)")
                rules.append("tight_stop")
                confidence_adj -= 0.05

        # Build verdict
        n = len(concerns)
        if n >= self.MAX_CONCERNS_FOR_HALT:
            verdict = "HALT"
            bear_case = (f"{n} heuristic concerns, at the critique's halt count "
                         f"of {self.MAX_CONCERNS_FOR_HALT}; none of these rules "
                         "has been backtested")
        elif n >= 2:
            verdict = "WARN"
            bear_case = f"{n} heuristic concerns; the trade may proceed"
        elif n == 1:
            verdict = "WARN"
            bear_case = concerns[0]
        else:
            verdict = "PASS"
            bear_case = "No heuristic concern fired"

        return CritiqueResult(
            verdict=verdict,
            bear_case=bear_case,
            concerns=concerns,
            confidence_adjustment=confidence_adj,
            rules=rules,
        )

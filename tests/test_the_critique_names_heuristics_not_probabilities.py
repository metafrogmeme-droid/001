"""The pre-trade critique names its concerns as heuristics, not probabilities.

`TradeCritique` can HALT a trade a person already confirmed on four concerns,
and every one of them is a rule of thumb nobody has backtested. Two of its
sentences said more than that: a stop under 1% from entry was "high
probability of stop hunt", a probability nothing measured, and a confidence
score above 0.90 was the MODEL "overfitting to recent pattern", for a score the
rule-based confluence carries most of at shipped weights. The HALT itself is
unchanged here: whether it should stop a confirmed trade is the operator's
call, made on `scripts/critique_replay.py`'s measurement, not on these words.

Driven: each rule fires on its own fixture, and the verdict on both sides of
the halt count.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from bot.core.critique import TradeCritique
from bot.utils.models import Direction, TradeIdea


def _idea(*, blend=0.70, entry=100.0, sl=95.0, tp=110.0, **kw) -> TradeIdea:
    idea = TradeIdea(id="T-1", asset="SOL/USDT", direction=Direction.LONG,
                     entry_price=entry, stop_loss=sl, take_profit=tp,
                     confidence=blend, reasoning="drive", **kw)
    idea.blended_confidence_raw = blend
    return idea


def _run(idea, open_count=0, macro=None):
    return TradeCritique().evaluate(idea, None, NS(open_positions=open_count), macro)


# One fixture per rule: (rule, idea kwargs, open count, macro state).
RULES = [
    ("high_confidence", dict(blend=0.95), 0, None),
    ("marginal_rr", dict(tp=106.0), 0, None),          # R:R 1.2
    ("hot_book", {}, 4, None),
    ("macro_reduce", {}, 0, "REDUCE"),
    ("tight_stop", dict(sl=99.5, tp=101.0), 0, None),  # 0.5% stop, R:R 2.0
]


def _fire(rule):
    _, kw, n, macro = next(r for r in RULES if r[0] == rule)
    return _run(_idea(**kw), n, NS(risk_state=macro) if macro else None)


class TestEachRuleIsNamedAndBounded:

    @pytest.mark.parametrize("rule", [r[0] for r in RULES])
    def test_the_rule_fires_alone_and_says_it_is_a_heuristic(self, rule):
        res = _fire(rule)
        assert res.rules == [rule], res.concerns
        assert len(res.concerns) == 1
        assert "heuristic" in res.concerns[0]

    @pytest.mark.parametrize("rule", [r[0] for r in RULES])
    def test_no_sentence_claims_a_probability_or_blames_the_model(self, rule):
        text = " ".join(_fire(rule).concerns).lower()
        for word in ("probability", "likely", "the model", "overfit", "stop hunt"):
            assert word not in text, (rule, text)

    def test_a_clean_idea_raises_nothing(self):
        res = _run(_idea())
        assert res.verdict == "PASS" and res.concerns == [] and res.rules == []

    def test_the_confidence_concern_names_the_score_and_its_bar(self):
        (line,) = _fire("high_confidence").concerns
        assert "score 95 (uncalibrated)" in line and "90 bar" in line
        assert "%" not in line

    def test_the_tight_stop_names_the_distance_and_the_bar(self):
        (line,) = _fire("tight_stop").concerns
        assert "0.5% from entry" in line and "1% bar" in line

    def test_the_book_rule_sits_on_its_bar(self):
        assert _run(_idea(), 3).rules == []
        assert _run(_idea(), TradeCritique.HOT_BOOK_POSITIONS).rules == ["hot_book"]


class TestTheHaltIsUnchanged:
    """The wording moved and the gate did not: three concerns warn, four halt,
    with the same confidence adjustments as before."""

    def test_three_concerns_warn(self):
        res = _run(_idea(blend=0.95, tp=106.0), 4)
        assert res.rules == ["high_confidence", "marginal_rr", "hot_book"]
        assert res.verdict == "WARN"
        assert res.confidence_adjustment == pytest.approx(-0.11)

    def test_four_concerns_halt_and_say_none_was_backtested(self):
        res = _run(_idea(blend=0.95, sl=99.5, tp=100.6), 4)
        assert len(res.rules) == 4
        assert res.verdict == "HALT"
        assert res.confidence_adjustment == pytest.approx(-0.16)
        assert "4 heuristic concerns" in res.bear_case
        assert "backtested" in res.bear_case

    def test_the_halt_count_is_the_classs(self):
        assert TradeCritique.MAX_CONCERNS_FOR_HALT == 4

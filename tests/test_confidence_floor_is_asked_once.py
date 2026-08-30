"""A benchmarked flag that could not reach the thing it gates.

`PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED` exists so swing (0.50), position
(0.45) and intraday (0.55) setups are not killed by the flat global 0.60. Its
comment in bot/config.py is unambiguous about the harm:

    the risk engine re-gates on the flat global value downstream -- for
    swing/intraday/position (floors below the global default) that flat
    re-gate silently rejects trades the analyzer already approved at its own
    tuned threshold. Frozen-benchmark A/B'd.

It had ONE reader — risk_engine.py, at trade-confirmation time. Three gates in
engine.py ran first and all read the flat global unconditionally:

    4146  the autonomous tick's presentation filter
    5873  the post-critique re-check
    6452  force_scan

An operator could set the flag, restart, see no error, and change nothing that
came from a scan: the ideas it exists to save were discarded three gates
earlier. Same shape as a flag on the wrong dataclass — documented, measured,
and structurally unable to reach its subject.

The last test here is the one that would have caught it, and it is a
reachability check rather than a behavioural one on purpose: the defect was
never in what any single gate DID, it was in how many gates there were.
"""

import io
import tokenize
from pathlib import Path

import pytest

from bot.config import CONFIG
from bot.risk.confidence_floor import clears_confidence_floor, min_confidence_for


class _Idea:
    def __init__(self, confidence=0.55, strategy_type="swing"):
        self.confidence = confidence
        self.strategy_type = strategy_type


@pytest.fixture
def per_strategy(request):
    """Flip the frozen-dataclass flag and put it back."""
    prev = CONFIG.risk.per_strategy_confidence_floor_enabled
    object.__setattr__(CONFIG.risk, "per_strategy_confidence_floor_enabled",
                       request.param)
    yield request.param
    object.__setattr__(CONFIG.risk, "per_strategy_confidence_floor_enabled", prev)


# ── the floor itself ─────────────────────────────────────────────────────

class TestTheFloor:
    @pytest.mark.parametrize("per_strategy", [False], indirect=True)
    def test_off_means_the_flat_global_for_every_type(self, per_strategy):
        for st in ("scalp", "intraday", "swing", "position"):
            assert min_confidence_for(_Idea(strategy_type=st)) == CONFIG.risk.min_confidence

    @pytest.mark.parametrize("per_strategy", [True], indirect=True)
    def test_on_means_the_per_type_floor(self, per_strategy):
        got = {st: min_confidence_for(_Idea(strategy_type=st))
               for st in ("scalp", "intraday", "swing", "position")}
        assert got == {"scalp": 0.65, "intraday": 0.55, "swing": 0.50, "position": 0.45}, got

    @pytest.mark.parametrize("per_strategy", [True], indirect=True)
    def test_the_flag_actually_changes_which_ideas_survive(self, per_strategy):
        # The whole point, stated as an outcome rather than a number: a swing
        # setup at 0.52 clears its tuned floor and used to be thrown away by a
        # global it was never meant to answer to.
        swing = _Idea(confidence=0.52, strategy_type="swing")
        assert clears_confidence_floor(swing)
        object.__setattr__(CONFIG.risk, "per_strategy_confidence_floor_enabled", False)
        assert not clears_confidence_floor(swing), (
            "with the flag OFF this idea must still be rejected — otherwise "
            "the flag is not what decides")

    @pytest.mark.parametrize("per_strategy", [True], indirect=True)
    def test_scalp_is_TIGHTENED_not_loosened(self, per_strategy):
        # The flag is not "lower every floor". Scalp's 0.65 is stricter than
        # the global 0.60, and a scalp at 0.62 must now be refused.
        assert not clears_confidence_floor(_Idea(confidence=0.62, strategy_type="scalp"))


class TestItFailsTowardsTheTighterGate:
    @pytest.mark.parametrize("per_strategy", [True], indirect=True)
    def test_an_unknown_strategy_falls_back_rather_than_guessing(self, per_strategy):
        floor = min_confidence_for(_Idea(strategy_type="nonsense"))
        assert floor >= 0.45 and floor <= CONFIG.risk.min_confidence

    @pytest.mark.parametrize("per_strategy", [True], indirect=True)
    def test_a_missing_strategy_type_does_not_raise(self, per_strategy):
        class Bare:
            confidence = 0.9
        assert isinstance(min_confidence_for(Bare()), float)

    def test_a_missing_confidence_does_NOT_clear(self):
        # An unmeasured setup is not a passing one.
        class Bare:
            strategy_type = "swing"
        assert not clears_confidence_floor(Bare())

    def test_a_confidence_of_exactly_zero_is_COMPARED_not_treated_as_absent(self):
        # 0.0 is a real, measured reading of a worthless setup. `is None`, not
        # falsiness — the rule this repo repeats most often.
        assert not clears_confidence_floor(_Idea(confidence=0.0))
        low = _Idea(confidence=0.0)
        assert min_confidence_for(low) > 0.0

    def test_a_non_numeric_confidence_does_not_clear(self):
        assert not clears_confidence_floor(_Idea(confidence="high"))


# ── the reachability check that would have caught it ─────────────────────

def _code_only(path):
    """Source with comments and strings removed — CLAUDE.md's own advice, and
    necessary here because the comments beside these gates NAME the expression
    they replaced."""
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(io.BytesIO(fh.read()).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                out.append(tok.string)
    return " ".join(out)


class TestTheFloorIsAskedInExactlyOnePlace:
    def test_no_gate_compares_against_the_flat_global_directly(self):
        """The defect, as a property.

        Not "gate 4146 behaves correctly" — the bug was that four independent
        gates each decided the question and only one knew about the flag. Any
        NEW bare comparison reintroduces it, silently, because a gate that
        agrees with itself looks perfectly healthy.
        """
        code = _code_only(Path("bot/core/engine.py"))
        bad = []
        for op in ("<", ">=", ">", "<="):
            needle = f"confidence {op} CONFIG . risk . min_confidence"
            if needle in code:
                bad.append(needle)
        assert not bad, (
            f"a confidence gate compares against the flat global directly: {bad}. "
            "Use clears_confidence_floor(idea) — otherwise "
            "PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED cannot affect it.")

    def test_the_engine_actually_calls_the_helper(self):
        # The other half: removing the comparisons and calling nothing would
        # also pass the test above.
        code = _code_only(Path("bot/core/engine.py"))
        assert code.count("clears_confidence_floor ( idea )") >= 3, (
            "the engine no longer consults the shared floor at three gates")

    def test_the_risk_engine_uses_the_same_helper(self):
        code = _code_only(Path("bot/risk/risk_engine.py"))
        assert "min_confidence_for ( idea )" in code
        assert "per_strategy_confidence_floor_enabled" not in code, (
            "the risk engine reads the flag directly again — that is how it "
            "came to be the only reader")

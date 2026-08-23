"""READY must mean evidence, not a non-zero sample or an opened file.

This card was shown live on 2026-08-23, recommending a live-money flag:

    Resolved outcomes: 6
    ✅ voter_weights: READY
       OOS hold rate 67% on 17 unseen trades (bar 60%)
    ✅ setup_expectancy: READY
       setup-expectancy: 42 setups, 61 trades, 0 setup(s) at/above
       10-trade threshold
    • voter_weights is validated but not applied — consider
      VOTER_WEIGHT_LEARNING_ENABLED=true

Three separate things are wrong with it.

1. THE BAR HAD NO SAMPLE FLOOR. `if oos.get("n_test", 0) and hold_rate >= 0.6`
   — the only requirement beside the bar was that n_test be non-zero, so one
   held voter on one unseen trade reported READY at a confident 100%. The
   bar's own comment says "0.5 = coin flip; demand clearly better", and at
   n=17 a 67% reading cannot distinguish itself from one.

2. THE PERCENTAGE IS NOT WHAT IT READS AS. `hold_rate` is documented in
   validate_oos as "the fraction of learned VOTERS whose adjustment direction
   held" — rendered as "67% on 17 unseen trades", which invites reading it as
   a trade-level win rate. With three voters, 67% is two of them.

3. `is_ready()` RETURNED `self._loaded`. setup_expectancy reported READY and
   "applied and validated ✓" whenever a file opened, directly above its own
   line saying zero setups met the threshold. Harmless to trades — the lookup
   returns identity below min_samples — and squarely misleading to the person
   reading it.

The card is where the decision gets made. A green tick over an absent
measurement is worse here than anywhere else in the repo, because it is
pointed at the risk settings.
"""

import pytest

from bot.learning.readiness import (_VW_HOLD_RATE_BAR, _VW_MIN_TEST_TRADES,
                                    _VW_MIN_VOTERS, assess_readiness)
from bot.learning.setup_expectancy import SetupExpectancy


class _Learner:
    """A VoterWeightLearner stand-in returning a planted OOS report."""

    min_samples = 20

    def __init__(self, report):
        self._report = report

    def load_samples(self, store=None):
        return [object()] * max(self.min_samples, self._report.get("n_test", 0) * 2)

    def validate_oos(self, samples):
        return self._report


def _voter_state(monkeypatch, *, hold_rate, n_test, n_voters):
    import bot.learning.voter_weights as vw
    report = {"n_train": 40, "n_test": n_test, "hold_rate": hold_rate,
              "voters": {f"v{i}": {} for i in range(n_voters)}}
    monkeypatch.setattr(vw, "VoterWeightLearner", lambda *a, **k: _Learner(report))
    return assess_readiness()["components"]["voter_weights"]


class TestTheBarNeedsASample:
    def test_the_live_case_is_no_longer_READY(self, monkeypatch):
        # 67% on 17 trades across 3 voters — the exact card that recommended
        # enabling the flag.
        comp = _voter_state(monkeypatch, hold_rate=0.67, n_test=17, n_voters=3)
        assert comp["state"] != "READY", (
            "17 unseen trades still clears the bar — the reading cannot "
            "distinguish itself from a coin flip at that sample size")
        assert comp["state"] == "VALIDATING"

    def test_one_perfect_trade_is_not_READY(self, monkeypatch):
        # The degenerate case the old `n_test > 0` admitted: 100%, n=1.
        comp = _voter_state(monkeypatch, hold_rate=1.0, n_test=1, n_voters=5)
        assert comp["state"] == "VALIDATING"
        assert "not enough to judge" in comp["note"]

    def test_enough_trades_AND_enough_voters_does_clear(self, monkeypatch):
        comp = _voter_state(monkeypatch, hold_rate=0.72,
                            n_test=_VW_MIN_TEST_TRADES, n_voters=_VW_MIN_VOTERS)
        assert comp["state"] == "READY", comp.get("note")

    def test_enough_trades_but_too_few_voters_does_not(self, monkeypatch):
        # 100% of one voter is 100%. It is also one voter.
        comp = _voter_state(monkeypatch, hold_rate=1.0,
                            n_test=_VW_MIN_TEST_TRADES, n_voters=1)
        assert comp["state"] == "VALIDATING"

    def test_a_big_sample_below_the_bar_still_fails_the_bar(self, monkeypatch):
        # The floor is additional to the bar, not a replacement for it.
        comp = _voter_state(monkeypatch, hold_rate=_VW_HOLD_RATE_BAR - 0.05,
                            n_test=200, n_voters=8)
        assert comp["state"] == "VALIDATING"
        assert "do not generalize" in comp["note"]


class TestTheNoteSaysWhatTheNumberMeasures:
    def test_it_names_voters_as_the_unit_and_counts_them(self, monkeypatch):
        comp = _voter_state(monkeypatch, hold_rate=0.75,
                            n_test=_VW_MIN_TEST_TRADES, n_voters=4)
        note = comp["note"]
        assert "voter" in note, (
            "the percentage still reads as a trade-level rate — 2 of 3 and "
            "27 of 40 are the same percentage and not the same evidence")
        assert "4 voter" in note
        assert f"{_VW_MIN_TEST_TRADES} unseen trade" in note

    def test_the_voter_count_is_on_the_payload_too(self, monkeypatch):
        comp = _voter_state(monkeypatch, hold_rate=0.75, n_test=60, n_voters=6)
        assert comp["oos_n_voters"] == 6


class TestSetupExpectancyReportsLearnedNotLoaded:
    def test_loaded_with_no_qualifying_setup_is_NOT_ready(self):
        se = SetupExpectancy(min_samples=10)
        se._loaded = True
        se._table = {"a": (0.5, 3), "b": (0.4, 9)}      # both under 10
        assert se.learned_setups() == 0
        assert not se.is_ready(), (
            "READY on zero qualifying setups — the badge said 'applied and "
            "validated' directly above '0 setup(s) at/above threshold'")

    def test_one_qualifying_setup_is_ready(self):
        se = SetupExpectancy(min_samples=10)
        se._loaded = True
        se._table = {"a": (0.5, 12), "b": (0.4, 2)}
        assert se.learned_setups() == 1
        assert se.is_ready()

    def test_not_loaded_is_never_ready(self):
        se = SetupExpectancy(min_samples=10)
        se._loaded = False
        se._table = {"a": (0.5, 99)}
        assert not se.is_ready()
        assert se.learned_setups() == 0

    def test_the_summary_and_the_verdict_cannot_disagree(self):
        # The contradiction that made the card unreadable: summary saying zero,
        # verdict saying READY. Whatever `learned_setups` counts, both read it.
        se = SetupExpectancy(min_samples=10)
        se._loaded = True
        se._table = {"a": (0.5, 3)}
        assert "0 setup(s) at/above" in se.summary()
        assert not se.is_ready()


class TestTheDenominatorsAreNotPresentedAsOne:
    def test_the_raw_decision_pool_is_reported_under_its_own_name(self):
        out = assess_readiness()
        assert "decisions_on_record" in out, (
            "one component's sample count is still the only top-level number, "
            "and it was labelled a shared denominator while three components "
            "reported 6, 17 and 61")

    def test_each_component_still_carries_its_own_count(self):
        comps = assess_readiness()["components"]
        for name, comp in comps.items():
            if comp.get("state") in (None, "ERROR"):
                continue
            assert "samples" in comp or "note" in comp, (
                f"{name} reports a state with no sample basis to read it against")

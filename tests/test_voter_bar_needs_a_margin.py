"""A bar cleared by two points is cleared by noise.

The card shown live on 2026-09-07, recommending a live-money flag:

    ✅ voter_weights: READY
       62% of 34 voter(s) held direction on 46 unseen trade(s) (bar 60%)
       apply flag: VOTER_WEIGHT_LEARNING_ENABLED
    Recommended:
    • voter_weights is validated but not applied —
      consider VOTER_WEIGHT_LEARNING_ENABLED=true

62% of 34 is 21 of 34. A fair coin reaches 21-or-better on 34 tosses about
one time in ten, so the reading is entirely consistent with the learned
adjustments carrying no information at all — and the bar's own comment in
`readiness.py` says it exists to "demand clearly better than a coin flip".
The point estimate clearing 60% is not that demand being met; it is the
smallest observation that satisfies the letter of it.

The two sample floors added previously (`_VW_MIN_TEST_TRADES`,
`_VW_MIN_VOTERS`) bound the SAMPLE and are silent about the MARGIN, which is
why this card passed them both: 46 trades and 34 voters is a real sample, and
21 of 34 is still a coin flip. The floor that was missing is on the interval:
the whole 95% range has to sit above chance, not its top end.

There is a second defect underneath it, and it is the one this repo keeps
finding: **the rate and the count were fractions of different populations**.
`hold_rate` is `holds / judged`, where a voter is judged only if some unseen
trade agreed with it and its adjustment is not ~1.0. The card printed
`len(voters)` beside it — every learned voter, agreed-with or not. "62% of
34 voter(s)" was a numerator's percentage over a denominator that never fed
it, so no reader could recover the 21 that the significance of the claim
turns on. `n_judged` and `n_holds` travel with the rate now.
"""

import pytest

from bot.learning.readiness import (
    _VW_CHANCE,
    _VW_HOLD_RATE_BAR,
    _VW_MIN_TEST_TRADES,
    _VW_MIN_VOTERS,
    assess_readiness,
    render_report,
    wilson_lower_bound,
)


class _Learner:
    """A VoterWeightLearner stand-in returning a planted OOS report."""

    min_samples = 20

    def __init__(self, report):
        self._report = report

    def load_samples(self, store=None):
        return [object()] * max(self.min_samples, self._report.get("n_test", 0) * 2)

    def validate_oos(self, samples):
        return self._report


def _assess(monkeypatch, *, holds, judged, n_test, n_learned=None):
    """Plant `holds of judged` and return the whole assessment.

    Counts, not a rate: the rate is derived from them here exactly as
    `validate_oos` derives it, so the fixture cannot express a report the
    producer could not have written.
    """
    import bot.learning.voter_weights as vw
    report = {"n_train": 40, "n_test": n_test,
              "n_judged": judged, "n_holds": holds,
              "hold_rate": round(holds / judged, 4) if judged else 0.0,
              "voters": {f"v{i}": {} for i in
                         range(judged if n_learned is None else n_learned)}}
    monkeypatch.setattr(vw, "VoterWeightLearner", lambda *a, **k: _Learner(report))
    return assess_readiness()


def _voter(monkeypatch, **kw):
    return _assess(monkeypatch, **kw)["components"]["voter_weights"]


class TestTheBoundIsArithmetic:
    def test_the_live_card_does_not_clear_chance(self):
        # 21 of 34 = 62%, the exact reading that recommended the flag.
        assert wilson_lower_bound(21, 34) == pytest.approx(0.4504, abs=5e-4)
        assert wilson_lower_bound(21, 34) < _VW_CHANCE

    def test_a_real_margin_on_the_same_sample_does(self):
        # 27 of 34 = 79% on the identical sample size — same denominator,
        # different evidence. The floor is on the margin, not the sample.
        assert wilson_lower_bound(27, 34) == pytest.approx(0.6320, abs=5e-4)
        assert wilson_lower_bound(27, 34) > _VW_CHANCE

    def test_an_empty_sample_is_zero_not_the_point_estimate(self):
        # 0/0 has no rate. Returning one — or a NaN that compares False
        # against every bar — is the fabrication this whole area exists to
        # stop. `0 / 0` would raise; the guard is what makes it total.
        assert wilson_lower_bound(0, 0) == 0.0
        assert wilson_lower_bound(5, 0) == 0.0
        assert wilson_lower_bound(0, -3) == 0.0

    def test_perfection_is_still_bounded_by_its_sample(self):
        # 3 of 3 is 100% and is not evidence: the bound is 44%, under chance.
        # This is why `_VW_MIN_VOTERS = 3` cannot by itself reach READY.
        assert wilson_lower_bound(3, 3) < _VW_CHANCE
        assert wilson_lower_bound(_VW_MIN_VOTERS, _VW_MIN_VOTERS) < _VW_CHANCE
        # ...and never reaches 1.0 however long the run.
        assert wilson_lower_bound(200, 200) < 1.0

    def test_more_of_the_same_rate_tightens_the_bound(self):
        # The property that makes it a significance floor rather than a
        # second bar: 4 of 5 and 80 of 100 are one percentage and not one
        # weight of evidence.
        bounds = [wilson_lower_bound(int(0.8 * n), n) for n in (5, 10, 25, 100)]
        assert bounds == sorted(bounds)
        assert bounds[0] < _VW_CHANCE < bounds[-1]

    def test_more_holds_on_one_sample_raises_the_bound(self):
        bounds = [wilson_lower_bound(k, 20) for k in range(21)]
        assert bounds == sorted(bounds)

    def test_it_stays_between_zero_and_the_rate_it_bounds(self):
        # A LOWER bound above its own point estimate is not a bound. This
        # sweep is what pins the two clamps in the function: the inputs are
        # read off a report dict, so garbage (a count exceeding its own
        # denominator) must be bounded rather than propagated.
        for n in range(1, 40):
            for k in range(0, n + 1):
                lo = wilson_lower_bound(k, n)
                assert 0.0 <= lo <= k / n, (k, n, lo)
        assert 0.0 <= wilson_lower_bound(99, 10) <= 1.0
        assert wilson_lower_bound(-5, 10) == 0.0


class TestTheCardStopsCallingNoiseEvidence:
    def test_the_live_reading_is_no_longer_READY(self, monkeypatch):
        comp = _voter(monkeypatch, holds=21, judged=34, n_test=46)
        assert comp["state"] == "VALIDATING", (
            "62% of 34 still reads as validated — a coin flip clears that "
            "margin about one time in ten, on the card that recommends "
            "turning the flag on")

    def test_the_note_says_which_of_the_two_it_failed(self, monkeypatch):
        note = _voter(monkeypatch, holds=21, judged=34, n_test=46)["note"]
        assert "clears" in note and "bar" in note, (
            "the note reads as a plain miss of the 60% bar, which it is not — "
            "the operator would go looking for a rate that already exists")
        assert "45%" in note, "the bound that blocked it is not on the card"

    def test_the_recommendation_is_withdrawn_with_it(self, monkeypatch):
        # The line that actually moves money: this card's "consider
        # VOTER_WEIGHT_LEARNING_ENABLED=true" is the whole reason the state
        # matters, and a fix that reached the state and not the advice would
        # have changed nothing a reader acts on.
        out = _assess(monkeypatch, holds=21, judged=34, n_test=46)
        assert not any("VOTER_WEIGHT_LEARNING_ENABLED=true" in r
                       for r in out["recommendations"]), out["recommendations"]

    def test_a_margin_that_survives_still_reaches_READY(self, monkeypatch):
        out = _assess(monkeypatch, holds=27, judged=34, n_test=46)
        comp = out["components"]["voter_weights"]
        assert comp["state"] == "READY", comp.get("note")
        assert any("VOTER_WEIGHT_LEARNING_ENABLED=true" in r
                   for r in out["recommendations"])

    def test_READY_shows_the_bound_it_cleared(self, monkeypatch):
        note = _voter(monkeypatch, holds=27, judged=34, n_test=46)["note"]
        assert "63%" in note, (
            "READY prints the point estimate alone, so the reader cannot tell "
            "a 79% that clears from a 62% that does not")

    def test_a_miss_of_the_bar_is_still_reported_as_a_miss(self, monkeypatch):
        # The two VALIDATING notes are different diagnoses and must not
        # collapse: this one is below the bar outright.
        note = _voter(monkeypatch, holds=17, judged=34, n_test=46)["note"]
        assert "do not generalize" in note
        assert "95% lower bound" not in note

    def test_the_sample_floors_are_diagnosed_before_the_margin(self, monkeypatch):
        # Both apply at 2 of 3; "get more trades" is the actionable one and
        # an interval on three voters is not worth quoting.
        note = _voter(monkeypatch, holds=2, judged=3, n_test=17)["note"]
        assert "not enough to judge" in note
        assert "coin flip reaches" not in note


class TestTheRateAndTheCountAreOnePopulation:
    def test_the_denominator_printed_is_the_rate_s_own(self, monkeypatch):
        # 34 learned voters, 12 of which any unseen trade agreed with. The
        # card said "62% of 34"; the 62% is 8 of 12.
        comp = _voter(monkeypatch, holds=8, judged=12, n_test=46, n_learned=34)
        assert comp["oos_n_voters"] == 12
        assert "12 voter" in comp["note"]
        assert "34 voter" not in comp["note"]

    def test_the_learned_population_is_still_on_the_record_under_its_own_name(
            self, monkeypatch):
        comp = _voter(monkeypatch, holds=8, judged=12, n_test=46, n_learned=34)
        assert comp["oos_n_learned"] == 34, (
            "dropping it instead of naming it loses the fact that 22 learned "
            "voters were never tested by an unseen trade")

    def test_the_bound_is_on_the_payload_for_callers_that_are_not_the_card(
            self, monkeypatch):
        comp = _voter(monkeypatch, holds=21, judged=34, n_test=46)
        assert comp["oos_hold_rate_lower"] == pytest.approx(0.4504, abs=5e-4)
        assert comp["oos_hold_rate"] == pytest.approx(0.6176, abs=5e-4)

    def test_a_report_with_no_counts_reads_as_no_evidence_not_as_pass(
            self, monkeypatch):
        # An older weights run, or any producer that does not carry the
        # counts: absent is not a measurement, so it must not clear the bar
        # by defaulting the denominator to something forgiving.
        import bot.learning.voter_weights as vw
        report = {"n_train": 40, "n_test": 200, "hold_rate": 0.99,
                  "voters": {f"v{i}": {} for i in range(50)}}
        monkeypatch.setattr(vw, "VoterWeightLearner",
                            lambda *a, **k: _Learner(report))
        comp = assess_readiness()["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING"
        assert comp["oos_n_voters"] == 0


class TestTheProducerCarriesTheCounts:
    """The assessor can only report what `validate_oos` puts on the record."""

    @staticmethod
    def _samples(n=120, split=0.7):
        # THREE POPULATIONS, ONE PER OUTCOME THE COUNTS HAVE TO TELL APART.
        # `validate_oos` fits on the first `split` chronologically:
        #   good    — agrees with the outcome throughout: learned, judged, holds
        #   flipper — agrees in training and anti-correlates after: learned,
        #             judged, does NOT hold
        #   quiet   — votes with the direction only in training, so no unseen
        #             trade agrees with it: learned, never judged
        # So 3 learned, 2 judged, 1 held — a hold_rate of 0.5, which is the
        # part that matters: a fixture where every judged voter holds cannot
        # distinguish `n_holds` from `n_judged`, and an earlier one did not.
        cut = int(n * split)
        out = []
        for i in range(n):
            won, train = (i % 3) != 0, i < cut
            aligned = 1.0 if won else -1.0
            out.append(([("good", aligned, 1.0),
                         ("flipper", aligned if train else -aligned, 1.0),
                         ("quiet", 1.0 if train else -1.0, 1.0)], "LONG", won))
        return out

    def test_the_fixture_separates_the_three_outcomes(self):
        from bot.learning.voter_weights import VoterWeightLearner
        report = VoterWeightLearner().validate_oos(self._samples())
        assert report["n_judged"] == 2 and report["n_holds"] == 1, report
        assert 0.0 < report["hold_rate"] < 1.0, (
            "every judged voter holds, so nothing here can tell a numerator "
            "from its denominator")

    def test_hold_rate_is_holds_over_judged_and_both_are_reported(self):
        from bot.learning.voter_weights import VoterWeightLearner
        report = VoterWeightLearner().validate_oos(self._samples())
        assert "n_judged" in report and "n_holds" in report, (
            "the rate travels without its own numerator and denominator, so "
            "no reader can judge how much sample is behind it")
        judged, holds = report["n_judged"], report["n_holds"]
        assert 0 <= holds <= judged <= len(report["voters"])
        expected = round(holds / judged, 4) if judged else 0.0
        assert report["hold_rate"] == pytest.approx(expected, abs=5e-5)

    def test_the_judged_population_can_be_smaller_than_the_learned_one(self):
        from bot.learning.voter_weights import VoterWeightLearner
        report = VoterWeightLearner().validate_oos(self._samples())
        assert report["n_judged"] < len(report["voters"]), (
            "this fixture has a learned voter no unseen trade agrees with; if "
            "the two counts match here the distinction is not being made")

    def test_a_report_that_never_fitted_carries_zeroes_not_absences(self):
        from bot.learning.voter_weights import VoterWeightLearner
        report = VoterWeightLearner().validate_oos([])
        assert report["hold_rate"] == 0.0
        assert report.get("n_judged", 0) == 0 and report.get("n_holds", 0) == 0


class TestItReachesTheSurfaceAnyoneReads:
    def test_the_rendered_card_carries_the_bound(self, monkeypatch):
        out = _assess(monkeypatch, holds=21, judged=34, n_test=46)
        card = render_report(out)
        assert "voter_weights</b>: VALIDATING" in card
        assert "45%" in card, (
            "the assessor knows the margin failed and the card does not say "
            "so — a fix that lands in the assessor and not the renderer has "
            "not landed")
        assert "consider VOTER_WEIGHT_LEARNING_ENABLED=true" not in card

    def test_the_bar_and_the_floors_are_still_all_stated(self, monkeypatch):
        # Nothing above removed a requirement: a card that clears the margin
        # must still have cleared both sample floors.
        comp = _voter(monkeypatch, holds=27, judged=34,
                      n_test=_VW_MIN_TEST_TRADES - 1)
        assert comp["state"] == "VALIDATING"
        assert f"{_VW_HOLD_RATE_BAR:.0%}" in comp["note"]

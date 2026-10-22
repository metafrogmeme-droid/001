"""Learned voter weights are READY when they pick better trades, not only
when the voters keep their signs.

The card shown live on 2026-10-22, recommending a live-money flag:

    ✅ voter_weights: READY
       69% of 35 voter(s) held direction on 65 unseen trade(s)
       (bar 60%, 95% lower bound 52%)
    • voter_weights is validated but not applied —
      consider VOTER_WEIGHT_LEARNING_ENABLED=true

Two things under it made 52% read stronger than the evidence was:

1. EVERY VOTER WAS JUDGED ON THE SAME 65 TRADES. The Wilson bound treats 35
   voters as 35 independent trials; they are 35 readings of one sample of
   trades, so one lucky run moves many of them together. No interval over
   voters can fix that. What applying the weights changes is how trades are
   SCORED, so the test now scores the unseen trades as the analyzer would,
   with and without the learned weights, and asks whether the weighted score
   ranks winners above losers better (AUC), resampling TRADES.

2. A VOTER WAS JUDGED ON ONE TRADE. Training moves a voter only after 8
   agreeing trades; judging counted a voter that agreed with one unseen
   trade as a whole yes or no, which on one trade is a coin flip. A voter is
   now judged on as many unseen trades as it was trained on, and the ones
   with fewer are counted apart (`n_thin`) and named on the card.

Probed on synthetic histories before this was written: a predictive history
clears the trade test on every seed tried, pure noise never does, and the
voter test alone let a noise history through at 5 of 8.
"""

import random

import pytest

import bot.learning.voter_weights as vw
from bot.learning.readiness import (
    _VW_MIN_EACH_OUTCOME,
    assess_readiness,
    render_report,
)
from bot.learning.voter_weights import (
    VoterWeightLearner,
    _auc,
    trade_ranking,
)


def _history(n, seed, good=0, bad=0, noise=8):
    """`n` closed trades in time order. `good` voters agree with the outcome
    68% of the time, `bad` voters 32%, `noise` voters vote at random; every
    hand-tuned weight is 1.0, so only the learned multipliers differ."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        won = rng.random() < 0.5
        votes = []
        for k in range(good):
            votes.append((f"g{k}", 1.0 if (won == (rng.random() < 0.68)) else -1.0, 1.0))
        for k in range(bad):
            votes.append((f"b{k}", 1.0 if (won == (rng.random() < 0.32)) else -1.0, 1.0))
        for k in range(noise):
            votes.append((f"n{k}", rng.choice([1.0, -1.0, 0.0]), 1.0))
        out.append((votes, "LONG", won))
    return out


def _predictive(seed):
    return _history(300, seed, good=8, bad=4, noise=6)


def _noise(seed):
    return _history(300, seed, noise=8)


class TestTheTradeLevelTest:
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_weights_learned_from_real_signal_rank_unseen_trades_better(self, seed):
        t = VoterWeightLearner().validate_oos(_predictive(seed))["trades"]
        assert t["diff"] > 0 and t["lower"] > 0, t
        assert t["auc_weighted"] > t["auc_unweighted"]

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_weights_learned_from_noise_never_clear_it(self, seed):
        t = VoterWeightLearner().validate_oos(_noise(seed))["trades"]
        assert t["lower"] is not None and t["lower"] <= 0, (
            "pure noise cleared the trade test: whatever the weights moved "
            f"toward, they did not pick better trades ({t})")

    def test_identity_weights_are_the_hand_tuned_ones(self):
        # No learned movement is no improvement: the difference is exactly
        # zero and so is its bound, which does not clear `> 0`.
        test = _predictive(1)[210:]
        t = trade_ranking(test, lambda _name: 1.0)
        assert (t["diff"], t["lower"]) == (0.0, 0.0)
        assert t["auc_weighted"] == t["auc_unweighted"]

    def test_weights_that_reward_the_wrong_voters_rank_worse(self):
        test = _predictive(2)[210:]
        def wrong(name):
            return 1.5 if name.startswith("b") else (0.5 if name.startswith("g") else 1.0)
        t = trade_ranking(test, wrong)
        assert t["diff"] < 0 and t["lower"] < 0, t

    def test_it_scores_trades_the_way_the_analyzer_does(self):
        # The analyzer normalises by the TOTAL weight, neutral voters
        # included: two agreeing voters out of four weighted 1.0 score 0.5,
        # and up-weighting one agreeing voter to 1.5 scores 2.5/4.5.
        votes = [("a", 1.0, 1.0), ("b", 1.0, 1.0), ("c", 0.0, 1.0), ("d", 0.0, 1.0)]
        assert vw._agreement(votes, 1.0, lambda n: 1.0) == pytest.approx(0.5)
        assert vw._agreement(votes, 1.0, lambda n: 1.5 if n == "a" else 1.0) == pytest.approx(2.5 / 4.5)
        assert vw._agreement(votes, -1.0, lambda n: 1.0) == pytest.approx(-0.5), "a SHORT reads the sign the other way"
        assert vw._agreement([("a", 1.0, 0.0)], 1.0, lambda n: 1.0) is None, "no weight is no score"

    def test_an_unrankable_set_is_unknown_not_zero(self):
        all_won = [([("a", 1.0, 1.0)], "LONG", True)] * 30
        t = trade_ranking(all_won, lambda n: 1.2)
        assert (t["auc_weighted"], t["auc_unweighted"], t["diff"], t["lower"]) == (None,) * 4
        assert (t["n_win"], t["n_loss"]) == (30, 0)
        weightless = [([("a", 1.0, 0.0)], "LONG", True), ([("a", -1.0, 0.0)], "LONG", False)]
        t = trade_ranking(weightless + [([("a", 1.0, 1.0)], "FLAT", True)], lambda n: 1.0)
        assert (t["n_scored"], t["n_unscored"]) == (0, 3), "an unscoreable trade is counted, not scored at zero"
        directionless = [([("a", 1.0, 1.0)], "FLAT", won) for won in (True, False)] * 10
        t = trade_ranking(directionless, lambda n: 1.2)
        assert (t["n_scored"], t["n_unscored"], t["auc_weighted"]) == (0, 20, None), (
            "a trade with no direction agrees with nothing; scored at 0 it would join the ranking")

    def test_auc_is_the_chance_a_winner_outscores_a_loser(self):
        assert _auc([(0.9, True), (0.1, False)]) == 1.0
        assert _auc([(0.1, True), (0.9, False)]) == 0.0
        assert _auc([(0.5, True), (0.5, False), (0.5, True)]) == 0.5, "ties count half"
        assert _auc([(0.2, True), (0.4, False), (0.6, True)]) == 0.5
        assert _auc([(0.3, True)]) is None and _auc([]) is None

    def test_one_record_gives_one_report(self):
        test = _predictive(1)[210:]
        mult = VoterWeightLearner().fit(_predictive(1)[:210]).multiplier
        assert trade_ranking(test, mult) == trade_ranking(test, mult), (
            "the interval moved between two reads of the same record")

    def test_the_bound_is_below_the_point_and_comes_from_resampling(self):
        t = VoterWeightLearner().validate_oos(_predictive(1))["trades"]
        assert t["lower"] < t["diff"], "a 95% lower bound at or above its point estimate is not a bound"
        assert t["resamples"] >= 900, t["resamples"]


class TestAVoterIsJudgedOnEnoughTrades:
    @staticmethod
    def _history():
        # "rare" agrees with every winner in training and with only three of
        # the unseen trades, all of them winners: judged on those three it
        # would "hold". "gone" agrees with training's winners and with no
        # unseen trade. "flat" agrees with every trade, so its win rate IS the
        # base rate and training leaves it unmoved. "common" agrees with the
        # outcome throughout.
        out = []
        for i in range(200):
            won = (i % 3) != 0
            rare = 1.0 if (i < 140 and won) or i in (142, 143, 145) else 0.0
            gone = 1.0 if (i < 140 and won) else 0.0
            out.append(([("common", 1.0 if won else -1.0, 1.0), ("rare", rare, 1.0),
                         ("gone", gone, 1.0), ("flat", 1.0, 1.0),
                         ("x", 1.0 if i % 2 else -1.0, 1.0)], "LONG", won))
        return out

    def test_a_voter_seen_on_three_unseen_trades_is_counted_apart(self):
        r = VoterWeightLearner().validate_oos(self._history())
        assert r["min_oos_agree"] == 8
        assert r["voters"]["rare"]["oos_agree_n"] == 3
        assert r["voters"]["rare"]["judged"] is False
        assert r["voters"]["rare"]["oos_edge"] > 0 and r["voters"]["rare"]["mult"] > 1, (
            "the fixture must be one where judging the thin voter would count it as holding")
        assert r["voters"]["rare"]["direction_holds"] is False, "an unjudged voter does not hold"
        assert r["voters"]["common"]["judged"] is True
        assert r["n_thin"] == 1, "only the voter seen on 1..7 unseen trades is thin"

    def test_a_voter_with_no_unseen_trade_is_neither_judged_nor_thin(self):
        r = VoterWeightLearner().validate_oos(self._history())
        assert r["voters"]["gone"]["mult"] > 1 and r["voters"]["gone"]["oos_agree_n"] == 0
        assert r["voters"]["gone"]["judged"] is False
        assert r["n_thin"] == 1, "'agreed with too few to judge' does not cover a voter that agreed with none"

    def test_a_voter_training_left_alone_is_not_judged_however_often_it_agreed(self):
        r = VoterWeightLearner().validate_oos(self._history())
        flat = r["voters"]["flat"]
        assert flat["mult"] == 1.0 and flat["oos_agree_n"] == 60
        assert flat["judged"] is False and flat["direction_holds"] is False

    def test_the_floor_is_the_training_floor_and_can_be_lowered_explicitly(self):
        judged_at_8 = VoterWeightLearner().validate_oos(self._history())["n_judged"]
        judged_at_1 = VoterWeightLearner().validate_oos(self._history(), min_oos_agree=1)["n_judged"]
        assert judged_at_1 == judged_at_8 + 1, "lowering the floor admits exactly the thin voter"
        assert VoterWeightLearner(min_voter_samples=5).validate_oos(self._history())["min_oos_agree"] == 5


class _Learner:
    min_samples = 20

    def __init__(self, report):
        self._report = report

    def load_samples(self, store=None):
        return [object()] * 200

    def validate_oos(self, samples):
        return self._report


def _planted(monkeypatch, *, holds, judged, n_test, trades, n_thin=0):
    report = {"n_train": 150, "n_test": n_test, "n_judged": judged, "n_holds": holds,
              "hold_rate": round(holds / judged, 4) if judged else 0.0,
              "min_oos_agree": 8, "n_thin": n_thin, "trades": trades,
              "voters": {f"v{i}": {} for i in range(judged + n_thin)}}
    monkeypatch.setattr(vw, "VoterWeightLearner", lambda *a, **k: _Learner(report))
    return assess_readiness()


def _trades(n_win, n_loss, auc_w, auc_u, lower):
    return {"n_scored": n_win + n_loss, "n_unscored": 0, "n_win": n_win, "n_loss": n_loss,
            "auc_weighted": auc_w, "auc_unweighted": auc_u,
            "diff": round(auc_w - auc_u, 4), "lower": lower, "resamples": 1000}


class TestTheGate:
    LIVE = dict(holds=24, judged=35, n_test=65)

    def test_the_live_card_with_no_trade_improvement_is_not_READY(self, monkeypatch):
        out = _planted(monkeypatch, **self.LIVE, trades=_trades(30, 35, 0.55, 0.56, -0.07))
        comp = out["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING", comp["note"]
        assert "do not yet rank winners above losers better" in comp["note"]
        assert "AUC 0.55 with the learned weights vs 0.56 without (-0.01, 95% lower bound -0.07)" in comp["note"]
        assert not any("VOTER_WEIGHT_LEARNING_ENABLED=true" in r for r in out["recommendations"])

    def test_an_improvement_inside_the_noise_is_not_READY(self, monkeypatch):
        # Better on the point estimate, and a bound that reaches zero.
        out = _planted(monkeypatch, **self.LIVE, trades=_trades(30, 35, 0.60, 0.56, 0.0))
        comp = out["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING", comp["note"]

    def test_the_same_voters_with_a_real_improvement_are_READY(self, monkeypatch):
        out = _planted(monkeypatch, **self.LIVE, trades=_trades(30, 35, 0.66, 0.56, 0.03))
        comp = out["components"]["voter_weights"]
        assert comp["state"] == "READY", comp["note"]
        assert ("rank winners above losers better: AUC 0.66 with the learned weights vs 0.56 "
                "without (+0.10, 95% lower bound +0.03)") in comp["note"]
        assert any("VOTER_WEIGHT_LEARNING_ENABLED=true" in r for r in out["recommendations"])

    def test_a_trade_test_that_never_ran_is_unknown_not_zero_trades(self, monkeypatch):
        comp = _planted(monkeypatch, **self.LIVE, trades=None)["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING"
        assert "was not run" in comp["note"]
        assert "0 winner" not in comp["note"], "an absent test was printed as a count"

    def test_a_trade_record_without_its_counts_is_unread_not_zero(self, monkeypatch):
        damaged = {k: v for k, v in _trades(30, 35, 0.70, 0.55, 0.06).items() if k != "n_loss"}
        comp = _planted(monkeypatch, **self.LIVE, trades=damaged)["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING", comp["note"]
        assert "counts could not be read" in comp["note"]
        assert "None" not in comp["note"] and "0 loser" not in comp["note"]

    def test_too_few_losers_to_compare_is_said_with_the_counts(self, monkeypatch):
        few = _trades(59, _VW_MIN_EACH_OUTCOME - 4, 0.70, 0.55, 0.05)
        comp = _planted(monkeypatch, **self.LIVE, trades=few)["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING"
        assert f"59 winner(s) and {_VW_MIN_EACH_OUTCOME - 4} loser(s)" in comp["note"]

    @pytest.mark.parametrize("n_win,n_loss,state", [
        (10, 55, "READY"), (55, 10, "READY"), (9, 56, "VALIDATING"), (56, 9, "VALIDATING")])
    def test_ten_of_each_outcome_is_the_floor(self, monkeypatch, n_win, n_loss, state):
        assert _VW_MIN_EACH_OUTCOME == 10
        t = _trades(n_win, n_loss, 0.70, 0.55, 0.05)
        assert _planted(monkeypatch, **self.LIVE, trades=t)["components"]["voter_weights"]["state"] == state

    def test_enough_trades_and_no_interval_is_not_called_too_few(self, monkeypatch):
        t = dict(_trades(30, 35, 0.70, 0.55, 0.05), lower=None, resamples=0)
        comp = _planted(monkeypatch, **self.LIVE, trades=t)["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING", comp["note"]
        assert "could not be computed" in comp["note"] and "need >=" not in comp["note"]
        assert "AUC 0.70 with the learned weights vs 0.55 without (+0.15)" in comp["note"]

    def test_a_voter_test_that_fails_still_fails_whatever_the_trades_say(self, monkeypatch):
        comp = _planted(monkeypatch, holds=21, judged=34, n_test=65,
                        trades=_trades(30, 35, 0.70, 0.55, 0.06))["components"]["voter_weights"]
        assert comp["state"] == "VALIDATING", "the two tests are both required, not either"

    def test_the_card_names_the_floor_and_the_thin_voters(self, monkeypatch):
        out = _planted(monkeypatch, **self.LIVE, n_thin=6, trades=_trades(30, 35, 0.66, 0.56, 0.03))
        card = render_report(out)
        assert "35 voter(s) judged on >= 8 unseen trades each" in card
        assert "6 more agreed with too few to judge" in card
        assert "AUC 0.66" in card


class TestEndToEnd:
    """The real learner and the real gate, on whole histories."""

    def _comp(self, monkeypatch, samples):
        monkeypatch.setattr(VoterWeightLearner, "load_samples", lambda self, store=None: samples)
        return assess_readiness()["components"]["voter_weights"]

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_noise_is_never_READY(self, monkeypatch, seed):
        assert self._comp(monkeypatch, _noise(seed))["state"] == "VALIDATING"

    def test_a_history_with_real_signal_reaches_READY(self, monkeypatch):
        comp = self._comp(monkeypatch, _predictive(1))
        assert comp["state"] == "READY", comp["note"]
        assert comp["oos_trades"]["lower"] > 0

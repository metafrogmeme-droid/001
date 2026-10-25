"""
Voter-weight learning — learn which confluence voters actually predict outcomes.

RUNECLAW scores ~35 confluence voters with HAND-TUNED weights. This module learns,
from the bot's OWN completed trades, how well each voter's vote agreed with
winning trades, and turns that into a bounded per-voter weight **multiplier**
(centred on 1.0) so good voters count for a little more and poor ones a little
less.

Phase B is instrument-first: `_score_confluence` now emits a named per-voter
breakdown (`(name, vote, weight)`), and decision records persist it
(`DecisionMemory.confluence_votes`) joined to outcomes by `paper_trade_id`. This
module is the LEARNER that consumes that data. **Application** of the multipliers
to the live confluence sum is a separate, flag-gated follow-up (B2); on its own
this module changes no decision.

Design / safety (mirrors confidence_calibration.py):
  - **Bounded** multiplier in ``[min_mult, max_mult]`` (default [0.5, 1.5]) — a
    voter's influence can only shift modestly, never flip or dominate.
  - **Shrinkage** toward 1.0 by per-voter sample count: a voter seen on few
    trades barely moves.
  - **Identity** (1.0) below ``min_samples`` total or ``min_voter_samples`` for a
    given voter, and for any unseen voter. It can only refine with evidence.
  - Pure aggregation + math. No third-party deps.
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Any, Optional

from bot.learning.outcome_join import SAMPLE_READING, join_outcomes, reading_of
from bot.utils.atomic_write import atomic_write_json

log = logging.getLogger("runeclaw.voter_weights")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_FILE = os.path.join(_STATE_DIR, "learning", "voter_weights.json")

_DEFAULT_MIN_SAMPLES = 20        # total completed trades before any learning
_DEFAULT_MIN_VOTER = 8           # agreeing trades for a voter before it moves
_DEFAULT_GAIN = 1.0              # edge -> multiplier scaling
_DEFAULT_SHRINKAGE = 10.0
_MIN_MULT = 0.5
_MAX_MULT = 1.5
_EPS = 1e-9


#: The trade-level test resamples unseen TRADES, with replacement, this many
#: times. Resampling trades rather than voters is the point: every voter is
#: judged on the same unseen trades, so their holds are not independent
#: trials, and an interval over voters is narrower than the evidence.
_RANK_RESAMPLES = 1000
#: A fixed seed, so one record gives one report: an interval that moved on
#: every /readiness would read as evidence changing when nothing had.
_RANK_SEED = 20261022


def _agreement(votes, ds: float, multiplier) -> Optional[float]:
    """How strongly the confluence agreed with a trade's direction, in [-1, 1].

    The analyzer's own normalisation (`_score_confluence`: the weighted vote
    sum over the total weight), with each voter's weight scaled by
    ``multiplier(name)``. None when the weights sum to nothing: a trade the
    confluence could not score is not a trade it scored at zero.
    """
    total = 0.0
    signed = 0.0
    for name, vote, weight in votes:
        w = float(weight) * float(multiplier(name))
        total += w
        signed += float(vote) * w
    if total <= _EPS:
        return None
    return ds * signed / total


def _auc(rows) -> Optional[float]:
    """The chance a winner's score beats a loser's (ties count half), from
    ``(score, won)`` rows; None when either side is empty, because a ranking
    with no losers ranks nothing."""
    n_w = sum(1 for _, won in rows if won)
    n_l = len(rows) - n_w
    if n_w == 0 or n_l == 0:
        return None
    order = sorted(range(len(rows)), key=lambda i: rows[i][0])
    ranks = [0.0] * len(rows)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and rows[order[j + 1]][0] == rows[order[i]][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    r_w = sum(ranks[i] for i, (_, won) in enumerate(rows) if won)
    return (r_w - n_w * (n_w + 1) / 2.0) / (n_w * n_l)


def trade_ranking(test, multiplier, resamples: int = _RANK_RESAMPLES,
                  seed: int = _RANK_SEED) -> dict:
    """Do the learned weights rank unseen winners above unseen losers better
    than the hand-tuned ones? The question turning the flag on answers.

    Each unseen trade is scored twice, as the analyzer would score it with
    and without the multipliers, and each scoring is summarised by its AUC
    over the trades' outcomes. ``lower`` is the 2.5th percentile of the AUC
    difference over trades resampled with replacement (the two scorings
    resampled together, so the comparison stays paired). None wherever there
    was nothing to rank.
    """
    rows = []
    unscored = 0
    for votes, direction, won in test:
        ds = _dir_sign(direction)
        weighted = _agreement(votes, ds, multiplier)
        plain = _agreement(votes, ds, lambda _name: 1.0)
        if not ds or weighted is None or plain is None:
            unscored += 1
            continue
        rows.append((weighted, plain, bool(won)))
    n_win = sum(1 for r in rows if r[2])
    out: dict[str, Any] = {"n_scored": len(rows), "n_unscored": unscored, "n_win": n_win,
           "n_loss": len(rows) - n_win, "auc_weighted": None,
           "auc_unweighted": None, "diff": None, "lower": None, "resamples": 0}
    a_w = _auc([(r[0], r[2]) for r in rows])
    a_u = _auc([(r[1], r[2]) for r in rows])
    if a_w is None or a_u is None:
        return out
    out.update(auc_weighted=round(a_w, 4), auc_unweighted=round(a_u, 4),
               diff=round(a_w - a_u, 4))
    rng = random.Random(seed)
    diffs = []
    n = len(rows)
    for _ in range(max(0, int(resamples))):
        pick = [rows[rng.randrange(n)] for _ in range(n)]
        b_w = _auc([(r[0], r[2]) for r in pick])
        b_u = _auc([(r[1], r[2]) for r in pick])
        if b_w is None or b_u is None:
            continue
        diffs.append(b_w - b_u)
    if diffs:
        diffs.sort()
        out["lower"] = round(diffs[int(0.025 * len(diffs))], 4)
        out["resamples"] = len(diffs)
    return out


def _dir_sign(direction) -> float:
    d = str(direction or "").strip().upper()
    if d in ("LONG", "BUY", "+1", "1"):
        return 1.0
    if d in ("SHORT", "SELL", "-1"):
        return -1.0
    return 0.0


class VoterWeightLearner:
    """Learns bounded per-voter weight multipliers from completed trades."""

    def __init__(self, min_samples: int = _DEFAULT_MIN_SAMPLES,
                 min_voter_samples: int = _DEFAULT_MIN_VOTER,
                 gain: float = _DEFAULT_GAIN, shrinkage: float = _DEFAULT_SHRINKAGE) -> None:
        self.min_samples = max(1, int(min_samples))
        self.min_voter_samples = max(1, int(min_voter_samples))
        self.gain = max(0.0, float(gain))
        self.shrinkage = max(0.0, float(shrinkage))
        self._mult: dict[str, float] = {}
        self._n_samples = 0
        self.sample_reading: Optional[int] = SAMPLE_READING

    # -- fitting ---------------------------------------------------------------

    def fit(self, samples) -> "VoterWeightLearner":
        """Fit from ``(votes, direction, won)`` tuples, where ``votes`` is a list
        of ``(name, vote, weight)``. A voter "agrees" with the trade when its vote
        sign matches the trade direction; we learn the win rate among agreements.
        """
        clean = [s for s in samples if s and s[0]]
        self._n_samples = len(clean)
        self._mult = {}
        if self._n_samples < self.min_samples:
            return self

        base_wins = sum(1 for _, _, won in clean if won)
        base_rate = base_wins / self._n_samples if self._n_samples else 0.5

        # Per voter: count agreeing trades and how many of those won.
        agree_n: dict[str, int] = {}
        agree_w: dict[str, int] = {}
        for votes, direction, won in clean:
            ds = _dir_sign(direction)
            if ds == 0.0:
                continue
            seen = set()
            for name, vote, _weight in votes:
                if name in seen:
                    continue          # one count per voter per trade
                if vote * ds > _EPS:  # voter agreed with the trade direction
                    seen.add(name)
                    agree_n[name] = agree_n.get(name, 0) + 1
                    if won:
                        agree_w[name] = agree_w.get(name, 0) + 1

        for name, n in agree_n.items():
            if n < self.min_voter_samples:
                continue
            win_rate = agree_w.get(name, 0) / n
            edge = win_rate - base_rate          # [-1, 1]
            shrink = n / (n + self.shrinkage) if (n + self.shrinkage) else 0.0
            mult = 1.0 + edge * self.gain * shrink
            self._mult[name] = max(_MIN_MULT, min(_MAX_MULT, mult))
        return self

    @staticmethod
    def samples_from_decisions(decisions):
        """Join decision records (carrying ``confluence_votes`` + direction +
        ``paper_trade_id``) to outcome records (``pnl_result`` by
        ``paper_trade_id``) into ``(votes, direction, won)`` tuples.

        The join is `outcome_join.join_outcomes`, shared with the calibrator:
        one decision row per closed trade, so a failed attempt and the
        successful retry that opened the position are one sample, not two."""
        from bot.core.equity_bucket import without_equity_bucket
        samples = []
        for d, won in join_outcomes(without_equity_bucket(decisions)).rows:
            votes = getattr(d, "confluence_votes", None)
            if not votes:
                continue
            samples.append((votes, getattr(d, "direction", ""), won))
        return samples

    def load_samples(self, store=None):
        if store is None:
            from bot.learning.store import LearningStore
            store = LearningStore()
        return self.samples_from_decisions(store.get_decisions(limit=100000))

    # -- applying --------------------------------------------------------------

    def is_ready(self) -> bool:
        return bool(self._mult) and self._n_samples >= self.min_samples

    def multiplier(self, name: str) -> float:
        """Bounded weight multiplier for a voter. 1.0 when not ready or unseen."""
        if not self.is_ready():
            return 1.0
        return self._mult.get(name, 1.0)

    # -- validation ------------------------------------------------------------

    def validate_oos(self, samples, split: float = 0.7,
                     min_oos_agree: Optional[int] = None) -> dict:
        """Time-ordered out-of-sample validation (audit fix #19).

        Fits on the FIRST ``split`` fraction of samples (they arrive in store
        order = chronological) and evaluates on the rest: for each learned
        voter, does agreeing-with-an-upweighted-voter (mult>1) still win more
        often than base rate on unseen trades — and vice versa for downweighted
        ones? Returns a report dict:

            {"n_train", "n_test", "base_rate_test", "voters":
                {name: {"mult", "oos_agree_n", "oos_win_rate", "oos_edge",
                        "direction_holds"}},
             "hold_rate"}

        ``direction_holds`` is True when the sign of the OOS edge matches the
        sign of the learned adjustment. ``hold_rate`` is the fraction of
        JUDGED voters whose adjustment direction held OOS.

        A VOTER IS JUDGED ON AS MANY UNSEEN TRADES AS IT WAS TRAINED ON. Fitting
        moves a voter only after ``min_voter_samples`` agreeing trades, and the
        judging used to count one: a voter that agreed with a single unseen
        trade was a whole yes or no in ``hold_rate``, which on one trade is a
        coin flip. ``min_oos_agree`` (default ``min_voter_samples``) is that
        floor; voters with fewer are counted in ``n_thin`` and judge nothing.

        ``trades`` is the trade-level test (`trade_ranking`): whether the
        learned weights rank the unseen trades' winners above their losers
        better than the hand-tuned ones. That is what applying them changes.
        """
        clean = [s for s in samples if s and s[0]]
        n = len(clean)
        cut = max(1, int(n * split))
        train, test = clean[:cut], clean[cut:]
        floor = self.min_voter_samples if min_oos_agree is None else max(1, int(min_oos_agree))
        report = {"n_train": len(train), "n_test": len(test),
                  "base_rate_test": 0.0, "voters": {}, "hold_rate": 0.0,
                  "min_oos_agree": floor, "n_thin": 0, "trades": None}
        if not test or len(train) < self.min_samples:
            return report

        fitted = VoterWeightLearner(self.min_samples, self.min_voter_samples,
                                    self.gain, self.shrinkage).fit(train)
        if not fitted.is_ready():
            return report

        base_rate = sum(1 for _, _, won in test if won) / len(test)
        report["base_rate_test"] = round(base_rate, 4)

        agree_n: dict[str, int] = {}
        agree_w: dict[str, int] = {}
        for votes, direction, won in test:
            ds = _dir_sign(direction)
            if ds == 0.0:
                continue
            seen = set()
            for name, vote, _weight in votes:
                if name in seen or name not in fitted._mult:
                    continue
                if vote * ds > _EPS:
                    seen.add(name)
                    agree_n[name] = agree_n.get(name, 0) + 1
                    if won:
                        agree_w[name] = agree_w.get(name, 0) + 1

        holds = 0
        judged = 0
        thin = 0
        for name, mult in fitted._mult.items():
            an = agree_n.get(name, 0)
            wr = (agree_w.get(name, 0) / an) if an else 0.0
            edge = wr - base_rate
            adj = mult - 1.0
            moved = abs(adj) > 1e-6
            is_judged = moved and an >= floor
            direction_holds = is_judged and (edge > 0) == (adj > 0)
            if is_judged:
                judged += 1
                holds += 1 if direction_holds else 0
            elif moved and an > 0:
                thin += 1
            report["voters"][name] = {
                "mult": round(mult, 3), "oos_agree_n": an,
                "oos_win_rate": round(wr, 4), "oos_edge": round(edge, 4),
                "judged": is_judged, "direction_holds": direction_holds,
            }
        # THE DENOMINATOR TRAVELS WITH THE RATE. `hold_rate` is holds/JUDGED,
        # but the only count the report exposed was `voters`, which holds an
        # entry for every learned voter including the ones no unseen trade
        # agreed with (`an == 0`) and the ones whose adjustment was ~1.0. The
        # readiness card read `len(voters)` and printed "62% of 34 voters" —
        # a fraction and a population that are not the same population. The
        # bar that gates VOTER_WEIGHT_LEARNING_ENABLED is computed from these
        # two, so both belong on the record.
        report["n_judged"] = judged
        report["n_holds"] = holds
        report["n_thin"] = thin
        report["hold_rate"] = round(holds / judged, 4) if judged else 0.0
        report["trades"] = trade_ranking(test, fitted.multiplier)
        return report

    # -- persistence -----------------------------------------------------------

    def to_dict(self) -> dict:
        from datetime import datetime, UTC
        return {"min_samples": self.min_samples, "min_voter_samples": self.min_voter_samples,
                "gain": self.gain, "shrinkage": self.shrinkage,
                "mult": self._mult, "n_samples": self._n_samples,
                # Versioning metadata (audit fix #19): a weights file without a
                # schema version + fit timestamp cannot be audited or expired.
                "schema_version": 2,
                "sample_reading": self.sample_reading,
                "fitted_at": datetime.now(UTC).isoformat()}

    def load_dict(self, d: dict) -> "VoterWeightLearner":
        self.sample_reading = reading_of(d)
        self.min_samples = int(d.get("min_samples", self.min_samples))
        self.min_voter_samples = int(d.get("min_voter_samples", self.min_voter_samples))
        self.gain = float(d.get("gain", self.gain))
        self.shrinkage = float(d.get("shrinkage", self.shrinkage))
        self._mult = {str(k): float(v) for k, v in (d.get("mult") or {}).items()}
        self._n_samples = int(d.get("n_samples", 0))
        return self

    def save(self, path: str = _FILE) -> None:
        atomic_write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: str = _FILE) -> Optional["VoterWeightLearner"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p) as f:
                return cls().load_dict(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("voter_weights load failed: %s", exc)
            return None

    def summary(self) -> str:
        if not self.is_ready():
            return (f"voter-weights: NOT READY ({self._n_samples}/{self.min_samples} "
                    "trades) — all voter weights used as-is")
        moved = {k: round(v, 2) for k, v in sorted(self._mult.items()) if abs(v - 1.0) > 0.01}
        return (f"voter-weights: learned on {self._n_samples} trades | "
                f"adjusted: {moved or 'none'}")


_INSTANCE: Optional["VoterWeightLearner"] = None


def get_voter_learner(reload: bool = False) -> Optional["VoterWeightLearner"]:
    """Process-wide singleton, lazily loaded from disk. Returns None if no learner
    has been fitted yet (caller treats that as 'use hand-tuned weights')."""
    global _INSTANCE
    if _INSTANCE is None or reload:
        _INSTANCE = VoterWeightLearner.load()
    return _INSTANCE


def refit_and_save(store=None, path: str = _FILE) -> "VoterWeightLearner":
    """Fit a learner from the learning store and persist it. Safe on a fresh bot:
    too little history stays identity (all multipliers 1.0)."""
    learner = VoterWeightLearner()
    learner.fit(learner.load_samples(store))
    try:
        learner.save(path)
    except OSError as exc:
        log.warning("voter_weights save failed: %s", exc)
    log.info("Refit %s", learner.summary())
    global _INSTANCE
    _INSTANCE = learner          # apply immediately to the live singleton
    return learner

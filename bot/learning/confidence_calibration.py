"""
Confidence calibration — turn raw model confidence into a realized win-rate.

RUNECLAW blends LLM + confluence into a ``blended_confidence`` in [0,1] that gates
every trade (and, for admins, auto-trade at >= 0.85). But that number is the
*model's opinion*, not a measured probability: a raw 0.85 has never been checked
against how often 0.85-confidence trades actually won. This module fits a
**reliability curve** from the bot's OWN closed-trade history
(``DecisionMemory.confidence`` vs ``pnl_result > 0``) so that a calibrated 0.85
means ~85% historical win rate.

Design / safety:
  - **Monotonic** (isotonic, Pool-Adjacent-Violators): a higher raw confidence can
    never map to a lower calibrated win rate, so trade ordering is preserved and
    noise can't invert it.
  - **Shrinkage toward identity** for thin bins: a bucket with few samples stays
    near the raw value instead of overfitting a handful of outcomes.
  - **Fail-safe identity:** below ``min_samples`` total (or when unfitted),
    ``calibrate(x) == x`` exactly. The curve can only refine a confidence once
    there is enough evidence; it never fabricates one.
  - Pure storage + math. It places no trades. Two flags apply the curve, and
    both are ON by default: `CONFIDENCE_CALIBRATION_ENABLED` moves every idea's
    confidence through it before the entry floor, and
    `AUTO_CONFIRM_USE_CALIBRATED` tests the auto-confirm bar against it. Below
    ``min_samples`` measured closes it is identity, so neither changes anything
    until a curve is fitted. With the first flag off the analyzer logs the
    would-be value and applies nothing (shadow).

    THIS USED TO SAY THE FIRST FLAG WAS DEFAULT OFF AND THE CURVE SHADOW-ONLY,
    and that is what a reader consults to decide whether a fitted curve can
    refuse a trade. It can: the entry floors (0.60 by default, 0.65 for a
    scalp) were tuned on the analyzer's own blend, and once a curve is fitted
    they read a WIN RATE instead. docs/CONFIDENCE_CALIBRATION.md has the
    measurement.

This module has no third-party deps (no numpy/sklearn) — PAV is a few lines.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import NamedTuple, Optional

from bot.learning.outcome_join import SAMPLE_READING, join_outcomes, reading_of
from bot.utils.atomic_write import atomic_write_json

log = logging.getLogger("runeclaw.calibration")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_CAL_FILE = os.path.join(_STATE_DIR, "learning", "confidence_calibration.json")

# Tunables (deliberately conservative).
_DEFAULT_BINS = 10
_DEFAULT_MIN_SAMPLES = 30      # below this, calibration is identity
_DEFAULT_SHRINKAGE = 5.0       # pseudo-count pulling thin bins toward raw confidence


def _pav(values: list[float], weights: list[float]) -> list[float]:
    """Pool Adjacent Violators — isotonic (non-decreasing) regression.

    Returns a non-decreasing sequence minimizing the weighted squared error to
    ``values``. O(n). Standard algorithm; weights are per-point sample counts.
    """
    n = len(values)
    if n == 0:
        return []
    # Each block: [sum(weight*value), sum(weight), count]
    blocks: list[list[float]] = []
    for v, w in zip(values, weights):
        blocks.append([v * w, w, 1])
        # Merge while the previous block's mean exceeds this one's (violation).
        while len(blocks) >= 2:
            a, b = blocks[-2], blocks[-1]
            mean_a = a[0] / a[1] if a[1] else 0.0
            mean_b = b[0] / b[1] if b[1] else 0.0
            if mean_a <= mean_b:
                break
            blocks[-2] = [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
            blocks.pop()
    # Expand block means back to per-point.
    out: list[float] = []
    for s, w, count in blocks:
        mean = s / w if w else 0.0
        out.extend([mean] * int(count))
    return out


def pre_calibration_confidence(record) -> Optional[float]:
    """The analyzer's own blend before calibration (``blended_confidence_raw``,
    #35) on a decision row or an idea, or None when it is not there.

    It is the field the curve is FITTED on, so it is also the one the curve is
    applied to: the auto-confirm bar asks this rather than ``confidence``,
    which the curve may already have moved. The field's unset value is 0.0,
    so the question is whether the figure is PRESENT, not what number stands
    in for it; a bool is a flag, not a confidence."""
    raw = getattr(record, "blended_confidence_raw", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw) if raw > 0.0 else None


class CalibrationRows(NamedTuple):
    """What `ConfidenceCalibrator.rows_from_decisions` kept and left out."""
    samples: list
    #: Decision rows for a closed trade whose word says it never opened.
    not_opened: int
    #: Rows whose confidence was not a measurement of their own trade.
    not_measured: int
    #: Rows recorded before the basis was, carrying no analyzer figure.
    unattributed: int


class ConfidenceCalibrator:
    """Fits and applies a monotonic confidence→win-rate reliability curve."""

    def __init__(self, bins: int = _DEFAULT_BINS, min_samples: int = _DEFAULT_MIN_SAMPLES,
                 shrinkage: float = _DEFAULT_SHRINKAGE) -> None:
        self.bins = max(2, int(bins))
        self.min_samples = max(1, int(min_samples))
        self.shrinkage = max(0.0, float(shrinkage))
        # Fitted curve: bin centers (x) -> calibrated win-rate (y), monotonic.
        self._x: list[float] = []
        self._y: list[float] = []
        self._n_samples: int = 0
        # A fit made in this process is counted under the current rule; one
        # loaded from disk says which rule it was counted under.
        self.sample_reading: Optional[int] = SAMPLE_READING

    # -- fitting ---------------------------------------------------------------

    def fit(self, samples: list[tuple[float, bool]]) -> "ConfidenceCalibrator":
        """Fit from ``(confidence, won)`` pairs. Confidence clamped to [0,1];
        ``won`` is truthy for a profitable trade. Insufficient data -> identity."""
        clean = [(min(1.0, max(0.0, float(c))), 1.0 if w else 0.0)
                 for c, w in samples if c is not None]
        self._n_samples = len(clean)
        if self._n_samples < self.min_samples:
            self._x, self._y = [], []
            return self

        # Bin by confidence; accumulate wins + counts per bin.
        win = [0.0] * self.bins
        cnt = [0.0] * self.bins
        for c, w in clean:
            idx = min(self.bins - 1, int(c * self.bins))
            win[idx] += w
            cnt[idx] += 1.0

        centers: list[float] = []
        rates: list[float] = []
        weights: list[float] = []
        for i in range(self.bins):
            if cnt[i] <= 0:
                continue
            center = (i + 0.5) / self.bins
            # Shrink the empirical rate toward the bin's raw confidence (center)
            # with `shrinkage` pseudo-observations, so thin bins ~ identity.
            rate = (win[i] + self.shrinkage * center) / (cnt[i] + self.shrinkage)
            centers.append(center)
            rates.append(rate)
            weights.append(cnt[i])

        if not centers:
            self._x, self._y = [], []
            return self

        self._x = centers
        self._y = [min(1.0, max(0.0, v)) for v in _pav(rates, weights)]
        return self

    @staticmethod
    def rows_from_decisions(decisions) -> "CalibrationRows":
        """The ``(confidence, won)`` samples, and what was left out and why.

        The confidence-bearing DECISION record and the realized OUTCOME record
        are separate append-only rows linked by ``paper_trade_id``;
        `outcome_join.join_outcomes` is that join, shared with the voter-weight
        learner, and it yields ONE decision row per closed trade (a failed
        attempt before a successful retry is not a second sample).

        A SAMPLE IS A CONFIDENCE MEASURED ABOUT ITS OWN TRADE, and two kinds of
        row are not. A manual ticket's confidence is the 1.0 stamp
        `build_manual_idea` writes, and a drift re-offer's was measured about
        another trade's levels; both reached this join and were fitted as
        measurements, the stamp in the top bin, the one the auto-confirm
        threshold is read against. A row records its ``confidence_basis`` now,
        and one that names a reason is left out.

        A ROW WRITTEN BEFORE THAT FIELD EXISTED SAYS NOTHING, and it is counted
        only when it carries ``blended_confidence_raw``: only the analyzer
        writes that field, and it is the field the calibrator is APPLIED to
        (#35), so its presence marks the analyzer's own measurement of that
        trade. A manual stamp and a re-offer never carry it. An old row without
        it cannot be told from a stamp, so it is left out and counted rather
        than guessed at. A measured row without it (a producer other than the
        analyzer) keeps #35's fallback to ``confidence``.
        """
        from bot.risk.quality_ladder import MEASURED_BASIS
        joined = join_outcomes(decisions)
        out: list[tuple[float, bool]] = []
        not_measured = unattributed = 0
        for d, won in joined.rows:
            basis = str(getattr(d, "confidence_basis", "") or "")
            raw = pre_calibration_confidence(d)
            if basis and basis != MEASURED_BASIS:
                not_measured += 1
                continue
            if not basis and raw is None:
                unattributed += 1
                continue
            conf = raw if raw is not None else getattr(d, "confidence", None)
            if conf is None or float(conf) <= 0.0:
                continue
            out.append((float(conf), won))
        return CalibrationRows(out, joined.not_opened, not_measured,
                               unattributed)

    @staticmethod
    def samples_from_decisions(decisions) -> list[tuple[float, bool]]:
        """Extract ``(confidence, won)`` for completed trades -- the samples
        `rows_from_decisions` keeps."""
        return ConfidenceCalibrator.rows_from_decisions(decisions).samples

    # -- applying --------------------------------------------------------------

    def is_ready(self) -> bool:
        return bool(self._x) and self._n_samples >= self.min_samples

    def calibrate(self, confidence: float) -> float:
        """Map a raw confidence to its calibrated win-rate. Identity when not
        ready. Linear interpolation between bin centers; flat beyond the ends."""
        x = min(1.0, max(0.0, float(confidence)))
        if not self.is_ready():
            return x
        xs, ys = self._x, self._y
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if x <= xs[i]:
                x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
                if x1 == x0:
                    return y1
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return ys[-1]

    # -- persistence -----------------------------------------------------------

    def to_dict(self) -> dict:
        return {"bins": self.bins, "min_samples": self.min_samples,
                "shrinkage": self.shrinkage, "x": self._x, "y": self._y,
                "n_samples": self._n_samples,
                "sample_reading": self.sample_reading}

    def load_dict(self, d: dict) -> "ConfidenceCalibrator":
        self.sample_reading = reading_of(d)
        self.bins = int(d.get("bins", self.bins))
        self.min_samples = int(d.get("min_samples", self.min_samples))
        self.shrinkage = float(d.get("shrinkage", self.shrinkage))
        self._x = [float(v) for v in d.get("x", [])]
        self._y = [float(v) for v in d.get("y", [])]
        self._n_samples = int(d.get("n_samples", 0))
        return self

    def save(self, path: str = _CAL_FILE) -> None:
        atomic_write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: str = _CAL_FILE) -> Optional["ConfidenceCalibrator"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p) as f:
                return cls().load_dict(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("confidence_calibration load failed: %s", exc)
            return None

    def summary(self) -> str:
        if not self.is_ready():
            return (f"calibration: NOT READY ({self._n_samples}/{self.min_samples} "
                    "samples) — confidence used as-is")
        pts = ", ".join(f"{x:.2f}->{y:.2f}" for x, y in zip(self._x, self._y))
        return f"calibration: ready on {self._n_samples} samples | curve: {pts}"


def refit_and_save(store=None, path: str = _CAL_FILE,
                   min_samples: int = _DEFAULT_MIN_SAMPLES) -> "ConfidenceCalibrator":
    """Fit a calibrator from completed decisions in the learning store and persist
    it. Returns the (possibly not-ready) calibrator. Safe to call on a fresh bot:
    with too little history it stays identity. ``store`` defaults to a new
    LearningStore so this can be called standalone (e.g. from a command or job).
    """
    if store is None:
        from bot.learning.store import LearningStore
        store = LearningStore()
    decisions = store.get_decisions(limit=100000)
    samples = ConfidenceCalibrator.samples_from_decisions(decisions)
    cal = ConfidenceCalibrator(min_samples=min_samples).fit(samples)
    try:
        cal.save(path)
    except OSError as exc:
        log.warning("confidence_calibration save failed: %s", exc)
    log.info("Refit %s", cal.summary())
    return cal

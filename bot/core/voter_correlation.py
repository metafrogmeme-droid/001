"""Voter de-correlation: a pairwise Pearson matrix over per-voter vote history.

``Analyzer._score_confluence`` already caps the families it KNOWS are
redundant (mean-reversion oscillators, pattern voters, structure, aggression).
Those lists are written by hand. This module measures it instead: two voters
whose votes have moved together over the recent past are, for the purpose of a
confluence average, one piece of information counted twice (RSI and OBV on a
one-way tape, MACD and VWAP on a trend).

Everything here is a pure function or a small bounded container. Nothing in it
reads a file, the network or ``CONFIG``; the threshold is passed in.

Honesty rules (CLAUDE.md, "unreadable is never zero"):

* A correlation that cannot be measured is ``None``, never ``0.0``. A series is
  unmeasurable when it is shorter than ``min_points`` jointly-present samples,
  when it is constant over those samples (zero variance, the ratio is 0/0), or
  when a sample is not a finite number (those are dropped as absent, not read
  as 0). ``0.0`` is reserved for a measured, genuinely uncorrelated pair.
* A voter with no measured correlation is NEVER penalised.
* The scaling is reduce-only: no factor returned here exceeds ``1.0``.
* A voter that was absent on a bar is absent from that bar's history (``None``
  in the aligned series), not a neutral vote.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict, deque
from typing import Deque, Mapping, Optional, Sequence

import numpy as np

# Jointly-present samples needed before a pair is measured at all. Votes are
# coarse (mostly -1/0/+1), so a handful of bars is noise; below this the answer
# is "unmeasured", not "uncorrelated".
MIN_POINTS = 20
# Rolling window kept per (symbol, timeframe).
MAX_POINTS = 200
# Distinct (symbol, timeframe) keys held before the least recently used goes.
MAX_KEYS = 512
# At |rho| == 1 a redundant later voter keeps this fraction of its weight. It is
# not zero: a perfectly correlated voter has still confirmed the read, it has
# just not confirmed it independently.
MIN_RETAINED_FRACTION = 0.25
# A variance at or below this (after centring) is a constant series.
_VAR_EPS = 1e-12

Series = Sequence[Optional[float]]
PairKey = tuple[str, str]


def _clean(series: Series) -> np.ndarray:
    """Return a float array with every unreadable sample as NaN.

    ``None``, NaN, +/-inf, bools and non-numeric values are all "absent". They
    are dropped from a pair's joint sample; they are never coerced to 0.
    """
    out = np.full(len(series), np.nan, dtype=float)
    for i, v in enumerate(series):
        if v is None or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out[i] = f
    return out


def correlation_matrix(series: Mapping[str, Series],
                       min_points: int = MIN_POINTS) -> dict[PairKey, Optional[float]]:
    """Pairwise-complete Pearson matrix over named, aligned series.

    Returns ``{(a, b): rho | None}`` for every unordered pair, with ``a``
    before ``b`` in the mapping's iteration order. Vectorised with NumPy: the
    joint sums are matrix products over a presence mask, so ~40 voters cost a
    few matmuls rather than ~800 ``corrcoef`` calls. Only positions present
    (finite) in BOTH series of a pair count toward it; fewer than
    ``min_points`` of them, or zero variance in either over them, is ``None``.

    A single pair is a two-entry mapping; there is deliberately no second,
    scalar implementation of the same quantity to drift from this one.

    All series must share one length (they are columns of the same snapshot
    list). If they do not, every pair is ``None``: a misaligned history is
    unreadable and is not guessed at.
    """
    names = list(series)
    out: dict[PairKey, Optional[float]] = {}
    n = len(names)
    if n < 2:
        return out
    lengths = {len(series[k]) for k in names}
    if len(lengths) != 1:
        for i in range(n):
            for j in range(i + 1, n):
                out[(names[i], names[j])] = None
        return out

    data = np.vstack([_clean(series[k]) for k in names])          # n x T
    present = ~np.isnan(data)
    # Centre each row on its own present mean so the variance test below is
    # about spread and not about the offset of the series.
    cnt_row = present.sum(axis=1)
    safe_cnt = np.where(cnt_row > 0, cnt_row, 1)
    row_mean = np.where(present, data, 0.0).sum(axis=1) / safe_cnt
    x = np.where(present, data - row_mean[:, None], 0.0)
    m = present.astype(float)

    cnt = m @ m.T                      # joint presence count
    sx = x @ m.T                       # sum of x_i where j present
    sxx = (x * x) @ m.T                # sum of x_i^2 where j present
    sxy = x @ x.T                      # sum of x_i * x_j (zero where either absent)
    sy = sx.T
    syy = sxx.T
    with np.errstate(divide="ignore", invalid="ignore"):
        cov = sxy - sx * sy / cnt
        vx = sxx - sx * sx / cnt
        vy = syy - sy * sy / cnt
        r = cov / np.sqrt(vx * vy)

    need = max(2, int(min_points))
    for i in range(n):
        for j in range(i + 1, n):
            val: Optional[float] = None
            if (cnt[i, j] >= need and vx[i, j] > _VAR_EPS and vy[i, j] > _VAR_EPS
                    and math.isfinite(float(r[i, j]))):
                val = max(-1.0, min(1.0, float(r[i, j])))
            out[(names[i], names[j])] = val
    return out


def retained_fraction(rho: Optional[float], threshold: float,
                      floor: float = MIN_RETAINED_FRACTION) -> float:
    """Fraction of a later voter's weight to keep, in ``[floor, 1.0]``.

    ``None`` (unmeasured) keeps everything. At or below ``threshold`` keeps
    everything. Between ``threshold`` and 1 it falls linearly to ``floor``.
    Reduce-only: never above 1.0.
    """
    if rho is None or not math.isfinite(rho):
        return 1.0
    mag = abs(rho)
    if not (0.0 <= threshold < 1.0) or mag <= threshold:
        return 1.0
    frac = (mag - threshold) / (1.0 - threshold)
    return max(floor, min(1.0, 1.0 - (1.0 - floor) * frac))


def redundancy_scales(order: Sequence[str],
                      current_votes: Mapping[str, float],
                      history: Mapping[str, Series],
                      threshold: float,
                      min_points: int = MIN_POINTS,
                      ) -> dict[str, float]:
    """Per-voter weight factors (each in ``[MIN_RETAINED_FRACTION, 1.0]``).

    ``order`` lists the voters in the order they cast (first = kept at full
    weight; a later voter is the one scaled back, as the family caps do).
    ``current_votes`` is this bar's vote per name; only ACTIVELY voting names
    (non-zero finite vote) are considered, matching the existing caps: a
    neutral voter double-counts nothing.

    A later voter ``j`` is scaled against the strongest redundancy with any
    earlier active voter ``i`` where (a) the pair's history correlation is
    measured and ``|rho| > threshold`` and (b) the two votes ARE the repeated
    information this bar: ``vote_i * vote_j * sign(rho) > 0`` (positive rho and
    same-sign votes, or negative rho and opposite-sign votes). A pair that
    disagrees against its history is not echoing and is left alone.

    Only voters that appear in ``history`` can be measured; the rest are
    absent from the result (factor 1.0 by omission). Returns only factors
    below 1.0, so ``{}`` means "nothing to do".
    """
    active: list[str] = []
    for name in order:
        if name in active:
            continue
        v = current_votes.get(name)
        if v is None or isinstance(v, bool):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv) and abs(fv) > 1e-9 and name in history:
            active.append(name)
    if len(active) < 2:
        return {}

    matrix = correlation_matrix({k: history[k] for k in active}, min_points)
    scales: dict[str, float] = {}
    for jdx in range(1, len(active)):
        j = active[jdx]
        best = 1.0
        for idx in range(jdx):
            i = active[idx]
            rho = matrix.get((i, j))
            if rho is None or abs(rho) <= threshold:
                continue
            agree = float(current_votes[i]) * float(current_votes[j]) * math.copysign(1.0, rho)
            if agree <= 0:
                continue
            best = min(best, retained_fraction(rho, threshold))
        if best < 1.0:
            scales[j] = best
    return scales


class VoterVoteHistory:
    """Bounded, thread-safe rolling record of each voter's vote per bar.

    One window per key (the analyzer uses ``(symbol, timeframe)``). A snapshot
    holds only the voters that voted on that bar; ``series`` aligns them into
    columns with ``None`` where a voter was absent. Recording the same
    ``bar_key`` twice replaces that bar's snapshot instead of adding a second
    sample, so re-analysing one still-open candle does not inflate the window.
    """

    def __init__(self, max_points: int = MAX_POINTS, max_keys: int = MAX_KEYS) -> None:
        self._max_points = max(2, int(max_points))
        self._max_keys = max(1, int(max_keys))
        self._lock = threading.Lock()
        self._data: "OrderedDict[object, Deque[tuple[object, dict[str, float]]]]" = OrderedDict()

    def record(self, key: object, votes: Mapping[str, float],
               bar_key: object = None) -> None:
        """Add one bar's votes. Non-finite votes are left out (absent)."""
        snap: dict[str, float] = {}
        for name, v in votes.items():
            if v is None or isinstance(v, bool):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                snap[str(name)] = fv
        if not snap:
            return
        with self._lock:
            dq = self._data.get(key)
            if dq is None:
                dq = deque(maxlen=self._max_points)
                self._data[key] = dq
                while len(self._data) > self._max_keys:
                    self._data.popitem(last=False)
            else:
                self._data.move_to_end(key)
            if bar_key is not None and dq and dq[-1][0] == bar_key:
                dq[-1] = (bar_key, snap)
            else:
                dq.append((bar_key, snap))

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def depth(self, key: object) -> int:
        """Number of bars recorded for ``key`` (0 when none)."""
        with self._lock:
            dq = self._data.get(key)
            return len(dq) if dq else 0

    def series(self, key: object) -> dict[str, list[Optional[float]]]:
        """Aligned per-voter columns for ``key`` (``{}`` when no history).

        Every column has one entry per recorded bar, oldest first, ``None``
        where that voter did not vote.
        """
        with self._lock:
            dq = self._data.get(key)
            if not dq:
                return {}
            snaps = [s for _, s in dq]
        names: list[str] = []
        seen: set[str] = set()
        for s in snaps:
            for n in s:
                if n not in seen:
                    seen.add(n)
                    names.append(n)
        return {n: [s.get(n) for s in snaps] for n in names}

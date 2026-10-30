"""Cross-sectional momentum behind a BTC trend regime gate, as pure functions.

The one candidate of the 2026-10-28 new-strategy search that cleared its
pre-registered bars (``docs/research/prereg/new_strategy_xsmom_regime/``,
``REPORT.md``): it passed all four, weakly, and the report says the pass is
mostly the BTC regime gate, not the momentum leg. This module is the PRIMARY
configuration only, for a paper forward test. It opens nothing.

The reference implementation is ``scripts/xsmom_regime_study.py``, which is
sealed by hash and is not imported here. This is a second copy of the rule,
so ``tests/test_the_xsmom_sleeve_matches_the_study.py`` runs both on the
study's shipped data and asserts the same target weights on sampled rebalance
dates, and pins every constant below to the script's own. That test is how
the two copies are kept to one reading.

The rule, decided at the close of day ``t`` from closes up to and including
``t``:

1. Regime: BTC's close above the simple mean of its last 150 closes. Off is
   all cash.
2. Eligible: a coin with at least 150 closes of its own history and every
   close of the last ``max(lookback, 30, 60) + 1`` days readable. Fewer than
   three eligible is all cash. ``k = min(4, n // 3)``.
3. Momentum is ``close(t) / close(t - 90) - 1``. The top ``k``, then any with
   momentum ``<= 0`` dropped.
4. Weights proportional to ``1 / (30-day realised vol)`` of daily close
   returns, summing to one.
5. Scaled by ``min(1, 30% / realised vol of that basket over 60 returns)``,
   annualised by ``sqrt(365)``. Long only, never above one times equity.
6. Rebalance when ``(day - 2017-08-17).days % 14 == 0`` and on any day the
   regime flips. Costs are 0.10% a side on traded weight.

A close that is missing is ``None`` here, the script's ``NaN``: never a zero
price and never a zero return.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping, Optional, Sequence

import numpy as np

#: The fixed ex-ante universe of the pre-registration, in the script's order.
UNIVERSE: tuple[str, ...] = (
    "BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "AVAX", "LTC",
    "LINK", "TRX", "BCH", "ATOM", "UNI", "ETC", "FIL", "NEAR", "XLM", "ALGO")
#: The coin whose trend gates the book.
REGIME_ASSET = "BTC"
#: Day zero of the 14-day rebalance calendar.
ANCHOR = date(2017, 8, 17)
#: Per side, of traded weight: fee plus slippage, all-in.
FEE = 0.0010
#: Closes of a coin's own history before it may be held.
MIN_HISTORY = 150
#: Annualised volatility the basket is scaled to, at most one times equity.
TARGET_VOL = 0.30
#: Daily returns in the per-coin vol and in the basket vol.
VOL_WIN = 30
PORT_VOL_WIN = 60
#: The most coins held at once.
MAX_K = 4
#: Days the vol is annualised over: crypto trades every day.
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Params:
    """The PRIMARY configuration's three knobs and its cost."""

    lookback: int = 90
    rebalance: int = 14
    regime_ma: int = 150
    fee: float = FEE

    @property
    def window(self) -> int:
        """Rows back from ``t`` whose closes must all read: momentum's prior
        close and the vol windows' first prior close."""
        return max(self.lookback, VOL_WIN, PORT_VOL_WIN)

    @property
    def bars_needed(self) -> int:
        """Closed daily bars one decision reads at least: the regime mean, the
        history floor and the window, whichever reaches furthest back."""
        return max(self.regime_ma, MIN_HISTORY, self.window + 1)


PRIMARY = Params()


@dataclass(frozen=True)
class Regime:
    """BTC against its moving average at one close."""

    on: bool
    close: float
    mean: float


@dataclass(frozen=True)
class Decision:
    """The book the rule would hold after the close of one day.

    ``weights`` names every coin it was handed, a zero for one not held.
    ``regime`` is None when BTC's window does not read, and the book is then
    cash because nothing was read, which ``reason`` says.
    """

    weights: dict[str, float]
    regime: Optional[Regime]
    eligible: tuple[str, ...]
    excluded: dict[str, str] = field(default_factory=dict)
    momentum: dict[str, float] = field(default_factory=dict)
    basket_vol: Optional[float] = None
    scale: Optional[float] = None
    reason: str = ""

    @property
    def gross(self) -> float:
        return float(sum(self.weights.values()))


def _readable(v: Optional[float]) -> bool:
    return v is not None and math.isfinite(v) and v > 0


def regime_at(btc: Sequence[Optional[float]], p: Params = PRIMARY) -> Optional[Regime]:
    """BTC's regime at the last close of ``btc``, or None when any of the last
    ``regime_ma`` closes does not read."""
    if len(btc) < p.regime_ma:
        return None
    win = list(btc[len(btc) - p.regime_ma:])
    if not all(_readable(v) for v in win):
        return None
    arr = np.asarray(win, dtype=float)
    mean = float(arr.sum() / arr.size)
    last = float(arr[-1])
    return Regime(on=bool(last > mean), close=last, mean=mean)


def is_rebalance_day(day: date, p: Params = PRIMARY) -> bool:
    """The calendar half of the rebalance rule; a regime flip is the other."""
    return (day - ANCHOR).days % p.rebalance == 0


def next_rebalance_day(after: date, p: Params = PRIMARY) -> date:
    """The first scheduled rebalance strictly after ``after``."""
    step = p.rebalance - ((after - ANCHOR).days % p.rebalance)
    return after + timedelta(days=step)


def _returns(closes: Sequence[float]) -> np.ndarray:
    arr = np.asarray(closes, dtype=float)
    out: np.ndarray = arr[1:] / arr[:-1] - 1
    return out


def decide(closes: Mapping[str, Sequence[Optional[float]]],
           p: Params = PRIMARY) -> Decision:
    """Target weights at the close of the last row, from the rows handed in.

    Every sequence in ``closes`` is aligned to the same days and ends on the
    decision day; ``None`` is a close that does not read. ``closes`` must hold
    BTC. Nothing after the last row exists here, so nothing after it is read.
    """
    cols = list(closes)
    zero = {c: 0.0 for c in cols}
    btc = closes.get(REGIME_ASSET)
    if btc is None:
        return Decision(zero, None, (), reason="BTC was not handed in")
    reg = regime_at(btc, p)
    if reg is None:
        return Decision(zero, None, (), reason=(
            f"BTC's last {p.regime_ma} closes do not all read: regime unread, cash"))
    if not reg.on:
        return Decision(zero, reg, (), reason=f"BTC is below its {p.regime_ma}-day mean: cash")
    n_rows = len(btc)
    t = n_rows - 1
    eligible: list[str] = []
    excluded: dict[str, str] = {}
    for c in cols:
        seq = closes[c]
        if len(seq) != n_rows:
            excluded[c] = "its closes are not aligned to BTC's days"
            continue
        hist = sum(1 for v in seq if _readable(v))
        if hist < MIN_HISTORY:
            excluded[c] = f"{hist} closes of history, {MIN_HISTORY} needed"
            continue
        if t < p.window or not all(_readable(v) for v in seq[t - p.window:]):
            excluded[c] = f"a close in the last {p.window + 1} days does not read"
            continue
        eligible.append(c)
    n = len(eligible)
    if n < 3:
        return Decision(zero, reg, tuple(eligible), excluded,
                        reason=f"{n} eligible coins, 3 needed: cash")
    k = min(MAX_K, n // 3)
    mom = {c: float(closes[c][t]) / float(closes[c][t - p.lookback]) - 1  # type: ignore[arg-type]
           for c in eligible}
    # Momentum descending; a tie (never seen on real prices) breaks by name.
    ranked = sorted(eligible, key=lambda c: (-mom[c], c))[:k]
    top = [c for c in ranked if mom[c] > 0]
    if not top:
        return Decision(zero, reg, tuple(eligible), excluded, mom,
                        reason=f"none of the top {k} has positive {p.lookback}-day momentum: cash")
    # Returns of each held coin over the basket window; the window's closes
    # were checked readable above, so every value here is a real price.
    rets = {c: _returns([float(v) for v in closes[c][t - PORT_VOL_WIN:]])  # type: ignore[arg-type]
            for c in top}
    vol = np.array([np.std(rets[c][-VOL_WIN:], ddof=1) * np.sqrt(DAYS_PER_YEAR) for c in top])
    if not bool((vol > 0).all()):
        return Decision(zero, reg, tuple(eligible), excluded, mom,
                        reason="a held coin's 30-day vol is not above zero: cash")
    inv = 1 / vol
    w = inv / inv.sum()
    basket = np.zeros(PORT_VOL_WIN)
    for i, c in enumerate(top):
        basket = basket + rets[c] * w[i]
    pv = float(np.std(basket, ddof=1) * np.sqrt(DAYS_PER_YEAR))
    if not pv > 0:
        return Decision(zero, reg, tuple(eligible), excluded, mom, pv,
                        reason="the basket's 60-day vol is not above zero: cash")
    scale = min(1.0, TARGET_VOL / pv)
    out = dict(zero)
    for i, c in enumerate(top):
        out[c] = float(w[i] * scale)
    return Decision(out, reg, tuple(eligible), excluded, mom, pv, scale,
                    reason=f"top {len(top)} of {n} eligible by {p.lookback}-day momentum")


def turnover(current: Mapping[str, float], target: Mapping[str, float]) -> float:
    """Total weight traded to move from ``current`` to ``target``."""
    names = set(current) | set(target)
    return float(sum(abs(target.get(c, 0.0) - current.get(c, 0.0)) for c in names))


__all__ = ["ANCHOR", "DAYS_PER_YEAR", "Decision", "FEE", "MAX_K", "MIN_HISTORY", "PORT_VOL_WIN",
           "PRIMARY", "Params", "REGIME_ASSET", "Regime", "TARGET_VOL", "UNIVERSE", "VOL_WIN",
           "decide", "is_rebalance_day", "next_rebalance_day", "regime_at", "turnover"]

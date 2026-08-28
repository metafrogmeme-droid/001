"""
Seasonality Analysis for RUNECLAW.

Detects time-of-day and day-of-week patterns in price action.
Uses historical candle timestamps to identify statistically favorable
trading windows (e.g., Asian session dips, US open volatility).

Data source: Candle timestamps already fetched via ccxt.

AN UNOBSERVED HOUR IS NOT A FLAT HOUR
-------------------------------------
Every bucket used to be seeded with ``0.0``:

    hour_avgs[h] = np.mean(vals) if vals else 0.0

so an hour or weekday with no candles behind it entered the comparison as a
measured break-even return. Three separate claims came out of that, and all
three were manufactured from absence. Driven with the MINIMUM input the
function accepts — 48 hourly candles, two calendar days, every observed hour
negative:

    days actually observed : Mon, Tue (2 of 7)
    best_day it reported   : Wednesday      <- never observed
    current_day            : Sunday -> neutral (avg 0.0)   <- never observed
    recommendation         : NEUTRAL        <- from invented zeros

`best_day` named a day the data had never seen, because a fabricated 0.0 beats
a real negative return. That is three rows of CLAUDE.md's table at once —
"unreadable is break-even", "a partial total printed as whole", and a verdict
scored from both.

So an empty bucket is ``None`` now, best/worst range over observed buckets
only, and a bias with too few samples behind it is ``UNKNOWN`` rather than
``neutral`` — "we have not seen this hour" and "this hour is flat" are
different sentences and only one of them is a measurement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# UTC hour ranges for major trading sessions
SESSIONS = {
    "ASIA": (0, 8),      # 00:00-08:00 UTC (Tokyo/Singapore)
    "EUROPE": (7, 16),   # 07:00-16:00 UTC (London)
    "US": (13, 22),      # 13:00-22:00 UTC (New York)
}

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


#: Samples needed behind a bucket before its average is called a bias. One
#: candle's "average" is that candle; calling it a bullish hour is noise with a
#: label on it. Below this the bias is UNKNOWN — not neutral.
MIN_BUCKET_SAMPLES = 3

#: The third value, everywhere. Not a synonym for "neutral": one says the data
#: has been looked at and shows no lean, the other says there is no data.
UNKNOWN = "unknown"


@dataclass
class SeasonalityResult:
    """Seasonality analysis output.

    Every historical field is Optional, and that is the correction: the caller
    must be able to tell "we looked and it is flat" from "we have never seen
    this hour". A float cannot carry that distinction and the old shape forced
    one anyway.
    """
    current_session: str          # "ASIA" | "EUROPE" | "US" | "OVERLAP" | "OFF"
    current_hour_utc: int
    current_day: str

    # Hour-of-day stats. None ⇒ no candle ever landed in that bucket.
    best_hour_utc: Optional[int]      # highest avg return AMONG OBSERVED hours
    worst_hour_utc: Optional[int]     # lowest avg return among observed hours
    current_hour_bias: str            # "bullish"|"bearish"|"neutral"|"unknown"
    current_hour_avg_return: Optional[float]

    # Day-of-week stats, same rule.
    best_day: Optional[str]
    worst_day: Optional[str]
    current_day_bias: str             # "bullish"|"bearish"|"neutral"|"unknown"
    current_day_avg_return: Optional[float]

    # Overall signal. None ⇒ neither component was measurable, so there is no
    # composite to report — never 0.0, which reads as a measured flat market.
    seasonality_score: Optional[float]
    recommendation: str           # "FAVORABLE"|"UNFAVORABLE"|"NEUTRAL"|"UNKNOWN"

    # How much was actually behind the answer. A reader weighing a claim needs
    # this, and its absence is why the old output looked equally confident on
    # two days of data and on two months.
    hours_observed: int = 0       # of 24
    days_observed: int = 0        # of 7
    samples_current_hour: int = 0
    samples_current_day: int = 0


def _bias(avg: Optional[float], samples: int) -> str:
    """``bullish`` / ``bearish`` / ``neutral`` / ``unknown`` for one bucket.

    UNKNOWN covers both ways of having nothing to say: no candle ever landed in
    this bucket, or too few did to call the average a pattern. Neither is
    ``neutral``, which is a measurement — the claim that we looked at enough of
    this hour to say it has no lean.
    """
    if avg is None or samples < MIN_BUCKET_SAMPLES:
        return UNKNOWN
    if avg > 0.02:
        return "bullish"
    if avg < -0.02:
        return "bearish"
    return "neutral"


def detect_session(hour_utc: int) -> str:
    """Classify current UTC hour into trading session."""
    sessions_active = []
    for name, (start, end) in SESSIONS.items():
        if start <= hour_utc < end:
            sessions_active.append(name)

    if len(sessions_active) >= 2:
        return "OVERLAP"
    elif len(sessions_active) == 1:
        return sessions_active[0]
    else:
        return "OFF"


def analyze_seasonality(
    candles: list[list],
    current_time: Optional[datetime] = None,
) -> Optional[SeasonalityResult]:
    """Compute seasonality patterns from historical candle data.

    Args:
        candles: list of [timestamp_ms, open, high, low, close, volume]
        current_time: override for current time (for testing)

    Returns:
        SeasonalityResult or None if insufficient data
    """
    if len(candles) < 48:  # Need at least 2 days of hourly data
        return None

    now = current_time or datetime.now(timezone.utc)
    current_hour = now.hour
    current_day_name = DAYS[now.weekday()]
    current_session = detect_session(current_hour)

    # Parse candle data and compute returns
    hourly_returns: dict[int, list[float]] = {h: [] for h in range(24)}
    daily_returns: dict[int, list[float]] = {d: [] for d in range(7)}

    for i in range(1, len(candles)):
        try:
            ts = candles[i][0]
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            else:
                continue

            open_price = candles[i][1]
            close_price = candles[i][4]
            if open_price > 0:
                ret = (close_price - open_price) / open_price * 100
                hourly_returns[dt.hour].append(ret)
                daily_returns[dt.weekday()].append(ret)
        except (IndexError, TypeError, ValueError):
            continue

    # Observed buckets only. An hour with no candles behind it is absent from
    # this dict entirely, so it cannot win `best_hour` on a fabricated zero.
    hour_avgs = {h: float(np.mean(v)) for h, v in hourly_returns.items() if v}

    best_hour = max(hour_avgs, key=hour_avgs.get) if hour_avgs else None
    worst_hour = min(hour_avgs, key=hour_avgs.get) if hour_avgs else None
    n_hour = len(hourly_returns.get(current_hour, []))
    current_hour_avg = hour_avgs.get(current_hour)   # None when unobserved

    hour_bias = _bias(current_hour_avg, n_hour)

    # Same rule for weekdays. With two days of candles this dict holds two
    # entries, so `best_day` can only ever name Monday or Tuesday — it used to
    # name Wednesday.
    day_avgs = {d: float(np.mean(v)) for d, v in daily_returns.items() if v}

    best_day_idx = max(day_avgs, key=day_avgs.get) if day_avgs else None
    worst_day_idx = min(day_avgs, key=day_avgs.get) if day_avgs else None
    n_day = len(daily_returns.get(now.weekday(), []))
    current_day_avg = day_avgs.get(now.weekday())

    day_bias = _bias(current_day_avg, n_day)

    # Composite over the components that EXIST, reweighted to what is known.
    # Treating an unmeasured half as 0.0 pulled the score toward neutral and
    # then printed NEUTRAL as a verdict — a confident answer whose confidence
    # came from the missing half.
    parts: list[tuple[float, float]] = []
    if hour_bias != UNKNOWN and current_hour_avg is not None:
        parts.append((float(np.clip(current_hour_avg / 0.1, -1, 1)), 0.6))
    if day_bias != UNKNOWN and current_day_avg is not None:
        parts.append((float(np.clip(current_day_avg / 0.1, -1, 1)), 0.4))

    if not parts:
        composite = None
        recommendation = "UNKNOWN"
    else:
        total_w = sum(w for _, w in parts)
        composite = float(sum(s * w for s, w in parts) / total_w)
        if composite > 0.2:
            recommendation = "FAVORABLE"
        elif composite < -0.2:
            recommendation = "UNFAVORABLE"
        else:
            recommendation = "NEUTRAL"

    return SeasonalityResult(
        current_session=current_session,
        current_hour_utc=current_hour,
        current_day=current_day_name,
        best_hour_utc=best_hour,
        worst_hour_utc=worst_hour,
        current_hour_bias=hour_bias,
        current_hour_avg_return=(None if current_hour_avg is None
                                 else round(current_hour_avg, 4)),
        best_day=(None if best_day_idx is None else DAYS[best_day_idx]),
        worst_day=(None if worst_day_idx is None else DAYS[worst_day_idx]),
        current_day_bias=day_bias,
        current_day_avg_return=(None if current_day_avg is None
                                else round(current_day_avg, 4)),
        seasonality_score=(None if composite is None else round(composite, 4)),
        recommendation=recommendation,
        hours_observed=len(hour_avgs),
        days_observed=len(day_avgs),
        samples_current_hour=n_hour,
        samples_current_day=n_day,
    )

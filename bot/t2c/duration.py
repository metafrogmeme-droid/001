"""Exit duration. Point delays are added to both percentiles.

The sweep is the uniform piece: p50 adds half of it, p95 adds 95% of it.
An unread sweep makes both percentiles unread. It does not become zero.
"""
from __future__ import annotations

from decimal import Decimal

from bot.t2c.numbers import as_decimal


def _point(parts: list[object]) -> Decimal | None:
    total = Decimal(0)
    for part in parts:
        number = as_decimal(part)
        if number is None or number < 0:
            return None
        total += number
    return total


def duration_percentiles(
    t_queue_days: object,
    t_withdrawability_days: object,
    t_sweep_days: object,
    t_protocol_days: object,
    restaked: bool = False,
    t_eigen_days: object = None,
) -> dict:
    point_parts: list[object] = [t_queue_days, t_withdrawability_days, t_protocol_days]
    if restaked:
        point_parts.append(t_eigen_days)
    point = _point(point_parts)
    sweep = as_decimal(t_sweep_days)
    if point is None or sweep is None or sweep < 0:
        return {"p50_days": None, "p95_days": None, "source": "not_read"}
    return {
        "p50_days": point + sweep * Decimal("0.5"),
        "p95_days": point + sweep * Decimal("0.95"),
        "source": "estimated",
    }


def smooth_samples(samples: list[object] | None) -> dict:
    """A mean over a window. An empty window is not smoothed, not zero.

    One unread sample makes the mean unread. The others are not averaged
    into a partial total.
    """
    if samples is None or len(samples) == 0:
        return {"value": None, "source": "not_smoothed"}
    numbers: list[Decimal] = []
    for sample in samples:
        number = as_decimal(sample)
        if number is None:
            return {"value": None, "source": "unreadable"}
        numbers.append(number)
    total = sum(numbers, Decimal(0))
    return {"value": total / Decimal(len(numbers)), "source": "mean"}

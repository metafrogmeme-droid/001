"""A resting limit's fill, when a run asks for one it can defend.

The historical rule is a touch: a long fills when the bar's low reaches the
limit. That is optimistic on a thin book, and it fills the orders whose price
kept going — the adverse ones. This module is that rule's replacement, and
it is off unless a run sets a knob. Off, ``decide_resting_fill`` answers
``legacy`` and the engine asks the touch itself.

On, the decision uses only the bar it is handed and the order's own fields.
A bar at or before the order was placed is not a fill: the order was not
resting while that bar printed. Nothing here reads a later bar.

**A RESTING FILL NEVER READS ITS BAR'S CLOSE.** A resting limit fills on the
touch, and the close prints after it. Whether a resting order filled is read
from the bar's low (a long) or high (a short) and the order's own fields, and
nothing else. The close is read only for two decisions taken AT the close:

* a reclaim entry, which is not a resting order and is booked at that close
  (``fill_at_close``);
* a cancel of an order this bar did not fill.

This rule was broken once. The reclaim knob kept a resting fill only when its
bar closed back through the limit, and the adverse-cancel knob cancelled an
order whose bar closed through it; both were booked, or refused, at the
limit price. That kept the fills that came back, dropped the ones that kept
going, and priced the kept ones where no order placed after the close could
fill. `tests/test_a_resting_fill_never_reads_the_close_after_its_touch.py`.

Knobs, each off at None/false:

* ``through_bp`` — the bar must trade this many basis points through the
  limit. A touch that only prints the price does not fill.
* ``queue_p`` — a qualified touch fills with this probability. The draw is a
  hash of the order and the bar, so a re-run takes the same fills.
* ``queue_sure_bp`` — a trade-through this deep fills regardless of the draw.
* ``reclaim`` — the order is not resting: it watches the level, and enters
  with a taker order at the close of a bar that traded through it
  (``through_bp`` deep) and closed back. The answer is ``fill_at_close``; the
  caller books the entry at that close, at the taker rate, and does not
  expose it to the range that printed before it. A taker order is not in a
  queue, so ``queue_p`` does not apply.
* ``cancel_adverse_bp`` — a close that far through the limit cancels an order
  this bar did not fill. An order the touch filled stays filled.
"""

from __future__ import annotations

import hashlib
from typing import Optional


def decide_resting_fill(config, order: dict, bar) -> str:
    """``legacy``, ``fill``, ``fill_at_close``, ``rest`` or ``cancel``.

    ``legacy`` means no knob is set and the caller keeps its own touch.
    ``fill`` is a resting fill at the order's own price. ``fill_at_close`` is
    a reclaim entry at this bar's close, at the taker rate, exposed only to
    the bars after this one.
    """
    through = _first(order, config, "through_bp", "limit_through_bp")
    queue = _first(order, config, "queue_p", "limit_queue_p")
    sure = _first(order, config, "queue_sure_bp", "limit_queue_sure_bp")
    reclaim = _first_bool(order, config, "reclaim", "limit_reclaim")
    cancel_bp = _first(order, config, "cancel_adverse_bp", "limit_cancel_adverse_bp")
    if through is None and queue is None and not reclaim and cancel_bp is None:
        return "legacy"
    placed = order.get("placed_ts")
    bar_ts = _bar_ts(bar)
    if placed is None or bar_ts is None or bar_ts <= float(placed):
        return "rest"
    px = order.get("px")
    idea = order.get("idea")
    direction = getattr(idea, "direction", None)
    if not isinstance(px, (int, float)) or isinstance(px, bool) or px <= 0 or direction is None:
        return "rest"
    long = _is_long(direction)
    need = through if through is not None else 0.0
    traded_through = _penetrated(bar, px, long, need)
    if reclaim:
        # Decided at the close, entered at the close: nothing earlier is priced.
        if traded_through and _reclaimed(bar, px, long):
            return "fill_at_close"
    elif traded_through:
        if queue is None:
            return "fill"
        if sure is not None and _penetrated(bar, px, long, sure):
            return "fill"
        if _uniform(order, bar) < queue:
            return "fill"
    # Not filled on this bar. Only now may its close decide a cancel.
    if cancel_bp is not None and _close_through(bar, px, long, cancel_bp):
        return "cancel"
    return "rest"


def _is_long(direction) -> bool:
    name = getattr(direction, "name", None) or getattr(direction, "value", None) or str(direction)
    return str(name).upper().endswith("LONG") or str(name).upper() == "LONG"


def _bar_ts(bar) -> Optional[float]:
    ts = getattr(bar, "timestamp", None)
    if ts is None:
        return None
    try:
        return float(ts.timestamp())
    except AttributeError:
        try:
            return float(ts)
        except (TypeError, ValueError):
            return None


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (out == out and 0 <= out < float("inf")):
        return None
    return out


def _first(order: dict, config, order_key: str, config_key: str) -> Optional[float]:
    if order_key in order and order.get(order_key) is not None:
        return _num(order.get(order_key))
    return _num(getattr(config, config_key, None)) if config is not None else None


def _first_bool(order: dict, config, order_key: str, config_key: str) -> bool:
    if order.get(order_key) is True:
        return True
    return bool(getattr(config, config_key, False)) if config is not None else False


def _penetrated(bar, px: float, long: bool, bp: float) -> bool:
    frac = bp / 10_000.0
    try:
        if long:
            return float(bar.low) <= px * (1.0 - frac)
        return float(bar.high) >= px * (1.0 + frac)
    except (TypeError, ValueError, AttributeError):
        return False


def _close_through(bar, px: float, long: bool, bp: float) -> bool:
    frac = bp / 10_000.0
    try:
        close = float(bar.close)
    except (TypeError, ValueError, AttributeError):
        return False
    if long:
        return close <= px * (1.0 - frac)
    return close >= px * (1.0 + frac)


def _reclaimed(bar, px: float, long: bool) -> bool:
    try:
        close = float(bar.close)
    except (TypeError, ValueError, AttributeError):
        return False
    return close > px if long else close < px


def _uniform(order: dict, bar) -> float:
    """A draw in ``[0, 1)`` from the order and this bar, stable across runs.

    The close is not an input. A later bar's price cannot change this bar's draw.
    """
    idea = order.get("idea")
    asset = getattr(idea, "asset", "") or ""
    blob = f"{order.get('placed_ts')}|{_bar_ts(bar)}|{order.get('px')}|{asset}"
    digest = hashlib.sha256(blob.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)

"""
RUNECLAW — funding-clock and liquidation-cascade timing (pure math).

Two timing edges perps have that spot doesn't:

FUNDING CLOCK — USDT perps settle funding on a per-symbol grid (1h, 2h,
4h or 8h; 00/08/16 UTC is the 8h fallback). Entering minutes before
settlement on the PAYING side of an extreme rate is a measurable head
start given away: the position pays immediately, and extreme funding
marks crowded positioning that tends to unwind around the settle. The
gate blocks only that narrow case — paying side + extreme rate + inside
the pre-settle window — unless FUNDING_CLOCK_GATE_MODE=cost_only, which
charges that one settlement into expectancy and does not block. Everything
else passes; missing funding data passes (fail-open). Every check writes
both modes' decisions side by side, and every block still lands in the
shadow book.

LIQUIDATION CASCADE — a bar whose range and volume both explode (forced
liquidations flushing a liquidity pool) is a terrible bar to CHASE: entry
in the flush direction fills at the extreme of a move that mean-reverts
more often than it continues once the forced flow is spent. The veto
blocks with-cascade-direction entries for a few bars after the flush;
counter-trend entries (fading the cascade) stay allowed. OHLCV-only, so
it backtests on the frozen benchmarks.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

# Fallback grid when a symbol has no measured interval. Bitget also settles
# 1h, 2h and 4h contracts; those ask bot.risk.funding_interval.
SETTLEMENT_INTERVAL_SEC = 8 * 3600.0  # Bitget USDT perps: 00/08/16 UTC


def seconds_to_settlement(now_ts: float, interval_sec: Optional[float] = None) -> float:
    """Seconds until the next funding settlement on this grid (UTC-aligned).

    ``interval_sec`` None is the 8h fallback. A non-positive interval is the
    same fallback: an unreadable grid is not "already settling".
    """
    try:
        interval = float(interval_sec) if interval_sec else SETTLEMENT_INTERVAL_SEC
    except (TypeError, ValueError):
        interval = SETTLEMENT_INTERVAL_SEC
    if interval <= 0:
        interval = SETTLEMENT_INTERVAL_SEC
    return interval - (float(now_ts) % interval)


def read_funding_rate(info) -> Optional[float]:
    """A venue funding-rate payload -> the rate, or None when unreadable.

    NULL-PRESERVING, and that is the whole point. `live_executor` read it as

        funding_rate = float(info.get("fundingRate", 0) or 0)
        if funding_rate != 0:

    so an absent field, a null, an empty string and a genuine 0.0 all became
    0.0 — and the check then skipped. In this domain 0 is not a neutral
    filler: the executor's own comment says "0% funding on metals/stocks =
    market likely closed", so an unreadable rate was impersonating a real and
    quite specific signal. `funding_clock_verdict` already takes
    `Optional[float]` and treats None as "no funding data (skip)"; this is the
    reader that can actually produce that None.
    """
    if not isinstance(info, dict):
        return None
    raw = info.get("fundingRate")
    if raw is None or raw == "":
        return None
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return None
    return rate if rate == rate else None      # NaN is not a reading


def pays_funding(direction: str, funding_rate: float) -> bool:
    """Whether this side PAYS the current funding rate.
    Positive rate: longs pay shorts. Negative: shorts pay longs."""
    if funding_rate > 0:
        return direction == "LONG"
    if funding_rate < 0:
        return direction == "SHORT"
    return False


def funding_clock_verdict(direction: str,
                          funding_rate: Optional[float],
                          now_ts: float,
                          window_sec: float = 1800.0,
                          extreme_rate: float = 0.0005,
                          interval_sec: Optional[float] = None) -> tuple[bool, str]:
    """(blocked, reason) for the funding-clock gate.

    Blocks ONLY when all three hold: inside the pre-settlement window,
    |rate| at/above the extreme threshold, and the trade is on the paying
    side. Missing funding data never blocks (fail-open). ``interval_sec``
    None keeps the 8h grid.
    """
    if funding_rate is None:
        return False, "no funding data (skip)"
    try:
        rate = float(funding_rate)
    except (TypeError, ValueError):
        return False, "no funding data (skip)"
    if rate != rate:  # NaN is not a rate
        return False, "no funding data (skip)"
    secs = seconds_to_settlement(now_ts, interval_sec)
    if secs > window_sec:
        return False, f"settlement {secs / 60:.0f}m away"
    if abs(rate) < extreme_rate:
        return False, f"funding {rate:+.4%} below extreme threshold"
    if not pays_funding(direction, rate):
        return False, f"extreme funding {rate:+.4%} is paid TO this side"
    return True, (f"would pay extreme funding {rate:+.4%} settling in "
                  f"{secs / 60:.0f}m — re-enter after the settle")


def gross_expectancy_r(confidence, reward_r) -> Optional[float]:
    """Expected R of one idea: ``p * reward - (1 - p) * 1``.

    ``confidence`` is the idea's stated win probability. Unreadable is None,
    not a coin-flip and not zero.
    """
    try:
        p = float(confidence)
        rr = float(reward_r)
    except (TypeError, ValueError):
        return None
    if p != p or rr != rr or p < 0.0 or p > 1.0 or rr < 0.0:
        return None
    return p * rr - (1.0 - p)


def settlement_cost_r(funding_rate, stop_distance_pct) -> Optional[float]:
    """One settlement's funding, in R.

    The rate is a fraction of notional. One R is the stop as a fraction of
    price, which is the same fraction of notional. An unreadable stop is
    not a zero cost.
    """
    try:
        rate = abs(float(funding_rate))
        stop = float(stop_distance_pct)
    except (TypeError, ValueError):
        return None
    if rate != rate or stop != stop or stop <= 0.0:
        return None
    return rate / stop


def funding_clock_gate_mode(mode: Optional[str] = None) -> str:
    """``block`` or ``cost_only``. Anything else is ``block``.

    ``block`` is the shipped behaviour. ``cost_only`` does not refuse the
    entry; it records the imminent settlement as a cost against expectancy.
    """
    if mode is None:
        try:
            from bot.config import CONFIG
            mode = getattr(CONFIG.risk, "funding_clock_gate_mode", "block")
        except Exception:
            mode = "block"
    text = str(mode or "").strip().lower()
    if text in ("block", "cost_only"):
        return text
    return "block"


def funding_clock_pair(direction: str,
                       funding_rate: Optional[float],
                       now_ts: float,
                       *,
                       window_sec: float = 1800.0,
                       extreme_rate: float = 0.0005,
                       interval_sec: Optional[float] = None,
                       confidence=None,
                       reward_r=None,
                       stop_distance_pct=None) -> dict:
    """Both mode decisions for one clock check.

    ``block`` is the verdict the gate has always returned. ``cost_only``
    never blocks. When ``block`` would have refused, ``cost_only`` subtracts
    one settlement from expectancy in R; otherwise it charges nothing.
    The charge is ``unpriced`` when the R figure cannot be formed — that is
    not a charge of zero, and it is still not a block.
    """
    blocked, reason = funding_clock_verdict(
        direction, funding_rate, now_ts, window_sec, extreme_rate, interval_sec)
    gross = gross_expectancy_r(confidence, reward_r)
    try:
        grid = float(interval_sec) if interval_sec else SETTLEMENT_INTERVAL_SEC
    except (TypeError, ValueError):
        grid = SETTLEMENT_INTERVAL_SEC
    if grid <= 0:
        grid = SETTLEMENT_INTERVAL_SEC

    cost_fraction = None
    cost_r = None
    net = gross
    charge_state = "not_charged"
    cost_reason = reason
    if blocked:
        if funding_rate is None:
            cost_fraction = None
        else:
            try:
                cost_fraction = abs(float(funding_rate))
            except (TypeError, ValueError):
                cost_fraction = None
        cost_r = (settlement_cost_r(funding_rate, stop_distance_pct)
                  if cost_fraction is not None else None)
        if cost_fraction is None or gross is None or cost_r is None:
            charge_state = "unpriced"
            net = None
            cost_reason = "cost-only, funding charge unpriced (not blocked)"
        else:
            charge_state = "charged"
            net = gross - cost_r
            cost_reason = (f"cost-only, charged {cost_fraction:.4%} of notional "
                           f"into expectancy ({gross:.2f}R -> {net:.2f}R)")
    return {
        "interval_sec": grid,
        "block": {"would_block": blocked, "reason": reason},
        "cost_only": {
            "would_block": False,
            "charge_state": charge_state,
            "cost_fraction": cost_fraction,
            "cost_r": cost_r,
            "expectancy_r_before": gross,
            "expectancy_r_after": net,
            "reason": cost_reason,
        },
    }


def append_funding_clock_shadow(row: dict, path: Optional[str] = None) -> None:
    """Append one side-by-side decision. Never raises.

    The file is a comparison record, not a gate. A full disk must not turn
    a block into a pass, so callers keep this outside the verdict's try.
    """
    try:
        import json
        target = path or os.getenv("FUNDING_CLOCK_SHADOW_PATH") or os.path.join(
            "data", "funding_clock_shadow.jsonl")
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        line = json.dumps(row, separators=(",", ":"), default=str)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        return


def cascade_state(highs: Sequence[float], lows: Sequence[float],
                  closes: Sequence[float], volumes: Sequence[float],
                  atr: float,
                  range_atr_mult: float = 2.5,
                  vol_mult: float = 3.0,
                  recent_bars: int = 3,
                  baseline_bars: int = 20) -> dict:
    """Detect a liquidation-cascade bar in the last ``recent_bars`` CLOSED
    bars: range >= range_atr_mult x ATR AND volume >= vol_mult x the
    average of the preceding ``baseline_bars``. Direction is the flush
    direction (close vs open proxy: close vs prior close).

    Returns {"cascade": bool, "direction": "UP"|"DOWN"|"", "bars_ago": int}.
    Pure; degenerate input (short series, zero ATR/volume) → no cascade."""
    out = {"cascade": False, "direction": "", "bars_ago": -1}
    try:
        n = len(closes)
        if (n < baseline_bars + recent_bars + 1 or atr is None or atr <= 0
                or len(highs) != n or len(lows) != n or len(volumes) != n):
            return out
        for bars_ago in range(1, recent_bars + 1):
            i = n - bars_ago
            rng = float(highs[i]) - float(lows[i])
            base = [float(v) for v in volumes[i - baseline_bars:i]]
            avg_vol = sum(base) / len(base) if base else 0.0
            if avg_vol <= 0:
                continue
            if rng >= range_atr_mult * atr and float(volumes[i]) >= vol_mult * avg_vol:
                direction = "UP" if float(closes[i]) >= float(closes[i - 1]) else "DOWN"
                return {"cascade": True, "direction": direction,
                        "bars_ago": bars_ago}
        return out
    except Exception:
        return out


def cascade_veto(direction: str, state: dict) -> Optional[str]:
    """Veto reason when the entry CHASES a recent cascade (same direction
    as the flush), else None. Fading the cascade is never vetoed."""
    if not state or not state.get("cascade"):
        return None
    flush = state.get("direction", "")
    if (direction == "LONG" and flush == "UP") or \
            (direction == "SHORT" and flush == "DOWN"):
        return (f"chasing a liquidation cascade ({flush} flush "
                f"{state.get('bars_ago', '?')} bar(s) ago) — forced flow, "
                f"entry at the extreme")
    return None

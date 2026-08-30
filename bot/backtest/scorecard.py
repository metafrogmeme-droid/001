"""The /backtest card's numbers — pure, so every branch can be driven.

WHY THIS IS A MODULE

It was inline in `skill_registry._backtest`, which builds an f-string around a
live `BacktestEngine.run()`. Nothing could plant a result and read what the
operator would see, so three defects sat on the card unnoticed:

1. THE FUNNEL DID NOT CLOSE. The engine counts `_ideas_rejected_preset`,
   `_et_disarmed_invalidated`, `_et_disarmed_expired` and setups still armed
   when the run ends. None of them reached `BacktestResult`, so the card showed
   `Ideas 55` and `Risk Reject 26` beside `15 trades`. A reader does that
   subtraction, gets 29, and nothing on the card accounts for the other 14.

2. THE SHARPE BAR SATURATED SILENTLY. `_bar(min(sharpe, 3.0), 3.0, 8)` renders
   3.0 and 3.76 and 30.0 identically — a full bar. On synthetic data an absurd
   Sharpe is the signal you most want to see, and the bar is where it goes to
   die.

3. MAX DRAWDOWN'S BAR RAN THE OTHER WAY. On three of four rows more filled is
   better. On drawdown more filled is worse, so an excellent 0.94% rendered as
   the emptiest bar on the card — which scans as bad, or as no data.

THE FUNNEL AUDITS ITSELF

`pipeline_rows` does not just print the stages it knows about; it subtracts
them and reports the remainder. A future exit path that forgets to report
itself shows up as an explicit "unaccounted" line rather than as a silent gap.
That is the difference between a card that was wrong once and a card that
cannot be wrong in the same way again.
"""

from __future__ import annotations

# Below this many closed trades, ratio statistics are shape, not measurement.
# Matches the web record's MIN_SAMPLE so the two surfaces flag the same point.
MIN_SAMPLE = 10

_FILL = "━"   # ━
_EMPTY = "╌"  # ╌


def _finite(v):
    """`float(v)` when it is a real number, else None.

    NaN gets its own check because it survives `float()` and then poisons every
    comparison: `max(0.0, nan)` is 0.0 — the BEST drawdown — while
    `max(nan, 0.0)` is nan, which raises in `int()`. Two different wrong
    answers from an argument order, and one of them is a full healthy bar drawn
    from a number nobody could read.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f       # f != f is True only for NaN


def bar(value: float, maximum: float, width: int = 8) -> str:
    """A proportional bar. Always `width` cells; saturation is the caller's
    problem to LABEL, not this function's to hide."""
    val, mx = _finite(value), _finite(maximum)
    if val is None or mx is None or mx <= 0:
        return _EMPTY * width
    ratio = min(max(val / mx, 0.0), 1.0)
    filled = int(ratio * width)
    return _FILL * filled + _EMPTY * (width - filled)


def capped(value: float, maximum: float) -> str:
    """`"+"` when a value is past the end of its bar, else `""`.

    The bar cannot distinguish 3.0 from 3.76 from 30.0, so the number beside it
    carries the fact that the bar stopped tracking.
    """
    val, mx = _finite(value), _finite(maximum)
    if val is None or mx is None:
        return ""
    return "+" if val > mx else ""


def drawdown_bar(dd_pct: float, worst: float = 20.0, width: int = 8) -> str:
    """Drawdown, drawn so that MORE FILLED IS BETTER — like every other row.

    A 0% drawdown fills the bar; a drawdown at or past `worst` empties it. The
    previous version passed the raw percentage straight to `bar()`, so the best
    possible outcome rendered as the emptiest row on a card whose other three
    rows mean the opposite.
    """
    dd = _finite(dd_pct)
    if dd is None:
        return _EMPTY * width
    return bar(max(0.0, worst - max(0.0, dd)), worst, width)


def pipeline_rows(r) -> list[str]:
    """The funnel from signals to trades, with nothing swallowed.

    Returns display rows as ``(label, value)`` pairs. The last row is present
    ONLY when the stages do not reconcile — and then it says so plainly, in
    either direction, because a negative remainder means something is being
    double-counted and that is not better news than a positive one.
    """
    def n(name: str) -> int:
        try:
            return int(getattr(r, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    signals = n("total_signals_generated")
    conf = n("total_ideas_rejected_confidence")
    preset = n("total_ideas_rejected_preset")
    ideas = n("total_ideas_generated")
    risk = n("total_ideas_rejected_risk")
    timing = n("total_ideas_timing_unfilled")
    pending = n("total_entries_pending_at_end")
    trades = n("total_trades")

    rows: list[tuple[str, str]] = [
        ("Signals", str(signals)),
        ("  ├ conf reject", str(conf)),
    ]
    # Shown only when it fired. A permanent "0" row for a gate that is off in
    # most configurations is noise, and noise is what stops people reading a
    # funnel at all.
    if preset:
        rows.append(("  └ preset reject", str(preset)))
    rows.append(("Ideas", str(ideas)))
    rows.append(("  ├ risk reject", str(risk)))
    if timing:
        rows.append(("  ├ armed, unfilled", str(timing)))
    if pending:
        rows.append(("  └ queued at end", str(pending)))
    rows.append(("Trades", str(trades)))

    remainder = ideas - (risk + timing + pending + trades)
    if remainder > 0:
        rows.append(("UNACCOUNTED", f"{remainder} — ideas left the pipeline "
                                    "somewhere this card cannot name"))
    elif remainder < 0:
        rows.append(("OVER-COUNTED", f"{-remainder} — a stage is being counted "
                                     "twice; the funnel is not trustworthy"))
    return rows


def sample_note(total_trades: int) -> str:
    """A caveat under the ratios, or ``""`` when the sample supports them.

    Win rate already carried its trade count; Sharpe, Sortino and profit factor
    did not, and Sharpe is the number most likely to be quoted.
    """
    try:
        t = int(total_trades or 0)
    except (TypeError, ValueError):
        return "Trade count unreadable — treat every ratio above as unproven."
    if t == 0:
        return "No trades closed — the ratios above are not measurements of anything."
    if t < MIN_SAMPLE:
        return (f"{t} trades is below {MIN_SAMPLE}: the ratios above are shape, "
                "not measurement.")
    return ""

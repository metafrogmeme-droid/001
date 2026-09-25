"""
RUNECLAW — the live-performance governor's verdict (pure).

The branch that decides OFF / WARMUP / OK / REDUCE / PAUSE and the size
multiplier that goes with it, written ONCE because three readers ask it and
one of them asks a question the other two cannot answer.

`RiskEngine.live_performance_size_multiplier` applies it to real sizing, and
`RiskEngine.live_performance_state` reports it to the `/accounts` view. The
third reader is the nightly self-audit, which proposes changes to
`LIVE_PERF_REDUCE_WINRATE` and `LIVE_PERF_REDUCE_MULT` and needs to say
whether a proposed value would change anything on the window the governor is
actually scoring. That is a MEASUREMENT — run this function twice, once with
the live configuration and once with the candidate, and read the two
multipliers — and it is only a measurement while there is one definition. A
second copy of the branch in the audit would be a second answer about what
the engine does, which is the shape `CLAUDE.md` records for maps, gates and
thresholds throughout this repo.

TWO THINGS THE SIGNATURE KEEPS APART, because the engine already did and
collapsing them would change live behaviour:

**`enabled` decides the STATUS, never the multiplier.** The caller at
`risk_engine.py` tests `live_performance_governor_enabled` itself before
applying any reduction, so the multiplier is scored for a disabled governor
too and simply goes unused. `status` is what reports OFF. Folding the flag
into the multiplier would make a disabled governor return 1.0, which reads
identically to a healthy window — a config state rendered as a measurement.

**Below `min_samples` the multiplier FAILS OPEN at 1.0 and the status is
WARMUP, not OK.** A cold start is not a healthy window; it is a window with
nothing in it. The multiplier is right to be 1.0 (never penalise a cold
start) and the word must not claim the record was read.
"""

from __future__ import annotations

import math
from typing import Optional

#: The governor is switched off. Nothing here is applied to sizing.
OFF = "OFF"
#: Fewer than `min_samples` closes: fail-open, and nothing measured.
WARMUP = "WARMUP"
#: Measured, and the window is healthy.
OK = "OK"
#: Measured, underperforming — size is cut to `reduce_mult`.
REDUCE = "REDUCE"
#: Measured, losing both often and on balance — sizing stops.
PAUSE = "PAUSE"


def governor_verdict(*,
                     enabled: bool,
                     samples: int,
                     win_rate: Optional[float],
                     net: Optional[float],
                     min_samples: int,
                     pause_winrate: float,
                     reduce_winrate: float,
                     reduce_mult: float) -> tuple[float, str]:
    """``(multiplier, status)`` for one window under one configuration.

    `win_rate` is the fraction of the window's closes that were profitable and
    `net` its total — both over the SAME window `samples` counts, which is the
    governor's own (`live_perf_window`) and not any other surface's.

    Either being None is not a window worth scoring: it fails open at
    ``(1.0, WARMUP)`` rather than reading an absent measurement as a bad one.
    """
    if samples < min_samples or win_rate is None or net is None:
        return 1.0, (OFF if not enabled else WARMUP)
    # The order is the engine's and load-bearing: PAUSE is an AND and is
    # tested FIRST, so a window that qualifies for both returns 0.0 rather
    # than the reduce multiplier. REDUCE is an OR — a net-negative window is
    # in it whatever the win-rate bar says, which is exactly why a proposal
    # that moves that bar can bind on nothing.
    if win_rate <= pause_winrate and net < 0:
        mult = 0.0
    elif win_rate <= reduce_winrate or net < 0:
        mult = reduce_mult
    else:
        mult = 1.0
    if not enabled:
        return mult, OFF
    if mult <= 0:
        return mult, PAUSE
    if mult < 1.0:
        return mult, REDUCE
    return mult, OK


def _wait_words(seconds: float) -> str:
    """A wait on a card: "13.2h" or "45m", rounded UP so a card never says a
    probe is due before it is."""
    if seconds >= 3600:
        return f"{math.ceil(seconds / 360.0) / 10:.1f}h"
    return f"{max(1, math.ceil(seconds / 60.0))}m"


def probe_clause(probe_in_seconds: Optional[float], probe_hours: float,
                 open_count: Optional[int] = None) -> str:
    """What ends a PAUSE, as a clause to append to the refusal: "" never.

    A pause with no way out was the defect (a paused book opens nothing, so
    its window cannot change), and a refusal that names the pause and not the
    way out sends the operator looking for a switch. Four outcomes, because
    they have four remedies: probing is switched off; no close is on record
    to time a probe from; a position is still open, and the probe waits for
    it; or the probe is due in a stated time. A probe that is due never
    reaches this clause: `evaluate` lets it through and `trading_blocked_by`
    names no pause for it. ``open_count`` None is a caller
    that cannot count the book (a property), and the clause then says the
    probe also needs a flat book rather than guessing that it has one.
    """
    if probe_hours <= 0:
        return "; probing is off (LIVE_PERF_PROBE_HOURS is 0), so this lifts only when the settings change"
    if probe_in_seconds is None:
        return "; no close is on record to time a probe entry from"
    if open_count is not None and open_count != 0:
        return "; a probe entry waits until no position is open"
    flat = "" if open_count == 0 else " with no position open"
    return f"; a probe entry is allowed in {_wait_words(probe_in_seconds)}{flat}"


def pause_reason(samples: int, win_rate: float,
                 probe_in_seconds: Optional[float], probe_hours: float) -> str:
    """The governor's PAUSE in `RiskEngine.trading_blocked_by`'s vocabulary.

    Counts only, never a dollar figure: this string reaches the unauthenticated
    /health payload and the website's scan chip, where the public-surface rule
    allows a count and a ratio and no account money. The prefix is the token a
    card branches on (`warroom_bot.resume_gate_line`).
    """
    wins = int(round(win_rate * samples))
    return (f"live_perf_pause: {wins} of the last {samples} closes won, net negative"
            + probe_clause(probe_in_seconds, probe_hours, None))

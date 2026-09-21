"""The size a trade was given, and every step that made it that figure.

`RiskCheck.position_size_usd` is one number, and the method that produces it
applies up to fourteen multipliers and clamps on the way -- the fixed-fractional
base, the execution ceiling, regime, session, the equity-curve breaker, the
live-performance governor, the equity throttle, a declared risk preference,
the quality ladder, drawdown recovery, the macro reduction, correlation, the
Kelly ceiling and the notional cap -- and then the engine and the executor
apply six more of their own before an order is placed (a high-conviction
target, the pyramid half, the stock session, the free-margin clamp, a manual
override, the per-account bound, the weekend rule, the entry-quality cut).
Two of the fourteen said so in `checks_passed`; the fill card printed
``Size $37.50 @ 5x`` and nothing else. A figure with no basis is a claim the
reader cannot check, and "which bound bit" is the one question an operator
asks of a size they did not expect -- `SizeBounds.why` answers it for the
account's bounds, and nothing answered it for the trade.

THE TRACE RIDES ON THE IDEA, and each step records the figure AFTER it. The
idea is the one object the risk gate, the engine and the executor all hold
(the leverage cap travels the same way, `bot/core/leverage.RISK_CAP_ATTR`),
so a step added in any of the three lands on the same record; the executor
works on a shallow copy of the idea and the copy shares the list, driven.
The trace is RESET at the top of each evaluation: an idea is evaluated at
proposal time and again at confirm time, and a trace that appended across
the two would narrate the first evaluation's steps under the second's figure.

A STEP THAT CHANGED NOTHING IS NOT A STEP. `size_basis` names the LAST
recorded step as the decider, so a ceiling that never bound, recorded
anyway, would be named as having decided a figure it never touched -- the
`size_usd` two-meanings defect wearing a sentence. Callers hand
`note_size_step` the figure they started from and a no-op is dropped.

WHAT IT CANNOT SEE IS SAID. A step that changes the size and notes nothing
leaves the last recorded figure disagreeing with the figure that was placed.
`size_basis` compares the two and says *then changed by a step not on record*
rather than naming the last recorded step as the decider, and the guard
drives both money paths to prove the ordinary path has no such gap.

`bot/risk/risk_engine.py` importing this module is the direction
`bot/core/leverage.py` records as new and safe: this file imports nothing
from the project, so it cannot cycle.
"""

from __future__ import annotations

import math
from typing import Any, List, NamedTuple, Optional

TRACE_ATTR = "_size_trace"

#: A step's recorded figure and the placed figure agree within this many
#: dollars or this fraction, whichever is looser -- the rounding the cards
#: and the executor apply (``round(size_usd, 2)``) is the only divergence
#: a fully recorded path produces.
_AGREE_USD = 0.011
_AGREE_FRAC = 0.001


class SizeStep(NamedTuple):
    label: str
    usd: float


def _money(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def reset_size_trace(idea: Any) -> None:
    """Start a fresh trace on this idea. Best-effort: an object that refuses
    the attribute simply carries no trace, and `size_basis` then says so."""
    try:
        setattr(idea, TRACE_ATTR, [])
    except Exception:
        pass


def note_size_step(idea: Any, label: str, usd: Any, before: Any = None) -> None:
    """Record that ``label`` left the size at ``usd``. Never raises, never
    changes the size: a trace that could refuse an order would be a gate.

    ``before`` is the figure the step started from. A step that left it
    where it was is NOT recorded (see the module header); a step handed no
    ``before`` is always recorded, which is right for the first step and for
    a multiplier a caller has already tested against 1.0.
    """
    f = _money(usd)
    if f is None:
        return
    b = _money(before) if before is not None else None
    if b is not None and abs(b - f) < 1e-9:
        return
    try:
        trace = getattr(idea, TRACE_ATTR, None)
        if not isinstance(trace, list):
            trace = []
            setattr(idea, TRACE_ATTR, trace)
        trace.append(SizeStep(str(label), f))
    except Exception:
        pass


def size_steps(idea: Any) -> List[SizeStep]:
    """The recorded steps, oldest first; empty for an idea carrying none."""
    try:
        trace = getattr(idea, TRACE_ATTR, None)
    except Exception:
        return []
    if not isinstance(trace, list):
        return []
    return [s for s in trace if isinstance(s, SizeStep)]


def _agrees(recorded: float, placed: float) -> bool:
    gap = abs(recorded - placed)
    return gap <= max(_AGREE_USD, abs(placed) * _AGREE_FRAC)


def size_path(idea: Any) -> List[str]:
    """Every step as ``label $figure`` -- the audit record's shape."""
    return [f"{s.label} ${s.usd:,.2f}" for s in size_steps(idea)]


def size_basis(idea: Any, placed_usd: Optional[float] = None) -> str:
    """One sentence: which step decided the figure, how many steps it took,
    and -- when ``placed_usd`` is handed in and disagrees with the last
    recorded figure -- that something not on record changed it after.

    Empty for an idea with no trace: a card prints nothing rather than a
    sentence about a path nobody recorded.
    """
    steps = size_steps(idea)
    if not steps:
        return ""
    last = steps[-1]
    first = steps[0]
    if len(steps) == 1:
        sentence = f"{last.label} decided ${last.usd:,.2f}"
    else:
        sentence = (f"{last.label} decided ${last.usd:,.2f} "
                    f"({len(steps)} steps from {first.label} ${first.usd:,.2f})")
    p = _money(placed_usd) if placed_usd is not None else None
    if p is not None and not _agrees(last.usd, p):
        sentence += f"; then changed to ${p:,.2f} by a step not on record"
    return sentence

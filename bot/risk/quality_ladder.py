"""Trade QUALITY chooses a size and a leverage within the bounds -- one reading.

The bounds (`bot/core/size_bounds.py`) say how much an account may carry; the
notional cap, the governor, the throttle, the regime and the correlation
multipliers each tighten a trade for a reason of their own. None of them read
the one thing the analyzer measured about THIS trade: its confidence. It
reached sizing through Kelly's ``kelly_f * 0.5 * conf`` -- a ceiling that only
exists once twenty closes are on record -- and through `_high_conviction_margin`,
which is binary and flat (one floor, one dollar target, opt-in). So a 0.58
idea and a 0.92 idea were sized identically on every account that had not
yet accrued a Kelly estimate, and the leverage they were set at never saw
confidence at all.

THE LADDER IS A TABLE, AND EVERY RUNG TIGHTENS OR LEAVES ALONE
-------------------------------------------------------------
A rung is ``name:floor:size_mult:leverage_mult``. The top rung is the bounds
themselves (both multipliers 1.0); a lower rung shrinks the margin and caps
the leverage at a share of the standard one, REDUCE-ONLY, the rule every
multiplier in `_evaluate_locked` is written to. Growth is not a rung: a size
past the bounds is what `SIZE_BOUNDS_MAX_*` is for, typed deliberately, and a
confidence figure -- which the analyzer's own calibration can move by 0.05
per slice -- is not evidence that a larger position is safe. Every multiplier
in a table is refused above 1.0 rather than clamped, because a table whose
author wrote 1.3 wanted growth, and silently handing them 1.0 is a second
answer about what the table says.

A MANUAL TICKET'S CONFIDENCE IS A STAMP, NOT A MEASUREMENT
---------------------------------------------------------
`manual_trade.build_manual_idea` writes ``confidence=1.0`` on every hand-typed
ticket. Nothing measured that; it is the value that clears every floor. So
the reading here is three-valued: an analyzer idea is MEASURED at its
confidence, a manual ticket (``source == "manual"``) is UNMEASURED, and a
confidence the record cannot read (absent, non-numeric, NaN, outside [0, 1])
is UNMEASURED with its own reason. An unmeasured quality takes NO rung:
size multiplier 1.0, leverage untouched, and the sentence says why -- the
`user_sizing` ruling, because shrinking a position on the strength of a
stamp we chose not to trust would be a size nobody measured either.
`kelly_confidence_factor` is the same reading for Kelly, so a change to the
stamp cannot quietly start scaling manual tickets by a number that is still
not a measurement; and `_high_conviction_margin` asks it too, so a stamp
cannot clear the conviction floor.

Both halves are behind their own flag, default OFF, and SHADOW when off: the
engine audits the would-be size so the ladder's effect is measurable before
it moves money, the shape `USER_RISK_PREF_SIZING_ENABLED` already takes.

The default table is written TWICE on purpose -- here and as the config
field's own default -- because `bot/config.py` cannot import this module
(`bot.risk` reaches `bot.config` and the import would cycle). A guard pins
the two spellings equal; a second copy that a test compares is one answer.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple, Optional, Tuple

MANUAL_SOURCE = "manual"

#: ``name:floor:size_mult:leverage_mult``, top rung first. The last rung's
#: floor is 0.0 so every measured confidence lands on one; the CONFIDENCE
#: gate (``bot/risk/confidence_floor.py``) refuses below the strategy's
#: minimum before any rung is consulted, so rung C in practice covers
#: [min_confidence, 0.70).
DEFAULT_RUNGS_TEXT = "A:0.85:1.0:1.0,B:0.70:0.75:0.8,C:0.0:0.5:0.6"


class QualityReading(NamedTuple):
    """Whether this idea's confidence is a measurement, and of what."""
    measured: bool
    confidence: Optional[float]
    why: str


class Rung(NamedTuple):
    name: str
    floor: float
    size_mult: float
    leverage_mult: float


class LadderVerdict(NamedTuple):
    """What the ladder decided for one idea -- the reading, the rung, the two
    multipliers and the clause a check line or an audit record prints."""
    measured: bool
    confidence: Optional[float]
    rung: Optional[str]
    size_mult: float
    leverage_mult: float
    why: str
    table_note: str = ""   # non-empty when the configured table was refused


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def quality_reading(idea: Any) -> QualityReading:
    """Three-valued: measured at the idea's confidence, or unmeasured with why.

    ``source == "manual"`` is unmeasured whatever the confidence field holds,
    because that field is a stamp on a manual ticket (see the module header).
    Never raises.
    """
    if idea is None:
        return QualityReading(False, None, "no idea: confidence unread")
    source = str(getattr(idea, "source", "") or "").strip().lower()
    if source == MANUAL_SOURCE:
        return QualityReading(
            False, None,
            "manual ticket: confidence is a stamp, not a measurement")
    conf = _num(getattr(idea, "confidence", None))
    if conf is None:
        return QualityReading(False, None, "confidence unread")
    if conf < 0.0 or conf > 1.0:
        return QualityReading(False, None, f"confidence {conf:g} outside [0, 1]")
    return QualityReading(True, conf, f"confidence {conf:.2f}")


def parse_rungs(text: Any) -> Tuple[Tuple[Rung, ...], str]:
    """``(rungs, note)``: the table the text describes, or the defaults with a
    note saying why the text was refused. A refused table is never silently
    "close enough" -- the note travels onto the check line and the audit
    record, because a ladder an operator typed that is not the one sizing
    their trades is the /vault hint shape pointed at an env var.
    """
    raw = str(text or "").strip()
    if not raw:
        return _DEFAULT_RUNGS, ""
    rungs: list[Rung] = []
    for chunk in raw.split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) != 4 or not parts[0] or not parts[0].isalnum():
            return _DEFAULT_RUNGS, f"malformed rung {chunk.strip()!r}; defaults in use"
        floor, smult, lmult = (_num(parts[1]), _num(parts[2]), _num(parts[3]))
        if floor is None or smult is None or lmult is None:
            return _DEFAULT_RUNGS, f"unreadable number in rung {chunk.strip()!r}; defaults in use"
        if not (0.0 <= floor <= 1.0):
            return _DEFAULT_RUNGS, f"rung {parts[0]} floor {floor:g} outside [0, 1]; defaults in use"
        if not (0.0 < smult <= 1.0) or not (0.0 < lmult <= 1.0):
            # Refused, not clamped: see the module header.
            return _DEFAULT_RUNGS, (f"rung {parts[0]} multiplier outside (0, 1] -- "
                                    "the ladder is tighten-only; defaults in use")
        rungs.append(Rung(parts[0], floor, smult, lmult))
    if not rungs:
        return _DEFAULT_RUNGS, "empty table; defaults in use"
    floors = [r.floor for r in rungs]
    if any(a <= b for a, b in zip(floors, floors[1:])):
        return _DEFAULT_RUNGS, "rung floors are not strictly descending; defaults in use"
    if rungs[-1].floor != 0.0:
        return _DEFAULT_RUNGS, ("the last rung's floor must be 0.0 so every confidence "
                                "lands on a rung; defaults in use")
    if len({r.name for r in rungs}) != len(rungs):
        return _DEFAULT_RUNGS, "duplicate rung names; defaults in use"
    return tuple(rungs), ""


_DEFAULT_RUNGS: Tuple[Rung, ...] = (
    Rung("A", 0.85, 1.0, 1.0),
    Rung("B", 0.70, 0.75, 0.8),
    Rung("C", 0.0, 0.5, 0.6),
)
assert parse_rungs(DEFAULT_RUNGS_TEXT) == (_DEFAULT_RUNGS, ""), \
    "the default text must parse to the default table"
DEFAULT_RUNGS = _DEFAULT_RUNGS


def rungs_from_config(cfg: Any = None) -> Tuple[Tuple[Rung, ...], str]:
    """The configured table, read from ``CONFIG.risk.quality_ladder_rungs``
    (or the ``cfg`` handed in -- the risk engine passes its own module-level
    CONFIG so a test that plants one drives this too). Never raises."""
    try:
        if cfg is None:
            from bot.config import CONFIG
            cfg = CONFIG.risk
        text = getattr(cfg, "quality_ladder_rungs", DEFAULT_RUNGS_TEXT)
    except Exception:
        return _DEFAULT_RUNGS, "config unreadable; defaults in use"
    return parse_rungs(text)


def rung_for(reading: QualityReading,
             rungs: Tuple[Rung, ...] = DEFAULT_RUNGS) -> Optional[Rung]:
    """The first rung whose floor the measured confidence reaches (AT the floor
    counts: a rung is ``confidence >= floor``). None for an unmeasured reading."""
    if not reading.measured or reading.confidence is None:
        return None
    for r in rungs:
        if reading.confidence >= r.floor:
            return r
    return None


def ladder_verdict(idea: Any, rungs: Optional[Tuple[Rung, ...]] = None,
                   table_note: str = "") -> LadderVerdict:
    """The ladder's answer for one idea. Multipliers are 1.0 for an unmeasured
    quality -- no rung, no reduction -- and ``why`` is the clause every line
    that prints the verdict shares: ``rung B at confidence 0.72`` for a
    measured one, the reading's own reason plus ``no rung`` otherwise."""
    if rungs is None:
        rungs, table_note = rungs_from_config()
    reading = quality_reading(idea)
    if not reading.measured:
        return LadderVerdict(False, None, None, 1.0, 1.0,
                             f"{reading.why}; no rung", table_note)
    rung = rung_for(reading, rungs)
    if rung is None:   # unreachable while parse_rungs pins the last floor at 0.0
        return LadderVerdict(True, reading.confidence, None, 1.0, 1.0,
                             f"{reading.why} reaches no rung", table_note)
    return LadderVerdict(
        True, reading.confidence, rung.name, rung.size_mult, rung.leverage_mult,
        f"rung {rung.name} at {reading.why}", table_note)


def ladder_leverage(standard: int, verdict: LadderVerdict, floor: int = 1) -> int:
    """The leverage this rung allows, REDUCE-ONLY from ``standard`` and never
    under ``floor``. Identical to ``standard`` for an unmeasured quality or a
    rung whose leverage multiplier is 1.0."""
    try:
        base = max(1, int(standard))
    except (TypeError, ValueError):
        return 1
    lo = max(1, int(floor))
    if verdict.leverage_mult >= 1.0:
        return base
    reduced = int(base * verdict.leverage_mult)
    # Floor first, then never above the standard: the other order answered
    # 2 for a 1x standard under a 2x floor -- a reduce-only rule RAISING the
    # leverage, found by the fixture at exactly that boundary.
    return min(base, max(lo, reduced))


def kelly_confidence_factor(idea: Any) -> Tuple[float, str]:
    """The confidence Kelly's half-fraction is scaled by: the measured figure,
    or 1.0 (no claim) for an unmeasured one -- which is what a manual ticket
    has always received, now by reading rather than by stamp."""
    reading = quality_reading(idea)
    if reading.measured and reading.confidence is not None:
        return reading.confidence, reading.why
    return 1.0, f"{reading.why}: half-Kelly unscaled"

"""The confidence floor an idea must clear — asked in ONE place.

`PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED` exists because the flat global
`MIN_CONFIDENCE` (0.60) is wrong for three of the four strategy types. Its own
comment in bot/config.py says so:

    the risk engine re-gates on the flat global value downstream -- for
    swing/intraday/position (floors below the global default) that flat
    re-gate silently rejects trades the analyzer already approved at its own
    tuned threshold. Frozen-benchmark A/B'd; see docs/FROZEN_BENCHMARK.md.

The flag was benchmarked, documented, and had exactly ONE reader —
`risk_engine.py`, at trade-confirmation time. Three gates ran before it and all
read `CONFIG.risk.min_confidence` unconditionally:

    engine.py:4146   the autonomous tick's presentation filter
    engine.py:5873   the post-critique re-check
    engine.py:6452   force_scan

So turning the flag on saved nothing that came from a scan: the swing (0.50)
and position (0.45) ideas it exists for were already discarded upstream. It
fixed the last gate in a chain of four — the same shape as a flag on the wrong
dataclass, and just as silent, because every gate agreed with itself.

This module is the single answer to "what floor applies to this idea". Four
callers, one rule. `tests/test_confidence_floor_is_asked_once.py` fails if a
bare `CONFIG.risk.min_confidence` comparison reappears in the engine.
"""

from __future__ import annotations

from bot.config import CONFIG

#: What an idea with no strategy_type is treated as. Matches the fallback the
#: risk engine already used, so this module changes no behaviour on its own.
_DEFAULT_STRATEGY = "swing"


def min_confidence_for(idea) -> float:
    """The floor `idea` must clear, honouring the per-strategy flag.

    Never raises: a malformed idea, a missing strategy type, or a
    strategy-types table that cannot answer all fall back to the flat global,
    which is the stricter of the two for every type except scalp. Failing
    towards the tighter gate is the right direction for a control that decides
    whether real money is committed.
    """
    flat = float(CONFIG.risk.min_confidence)
    try:
        if not CONFIG.risk.per_strategy_confidence_floor_enabled:
            return flat
        strategy = getattr(idea, "strategy_type", None) or _DEFAULT_STRATEGY
        floor = CONFIG.strategy_types.get_min_confidence(str(strategy))
        return float(floor)
    except Exception:
        return flat


def _raw_confidence(idea) -> float | None:
    """Raw (uncalibrated) confidence for gate purposes.

    When calibration is enabled, idea.confidence is the calibrated value and
    the MIN_CONFIDENCE / SCALP_MIN floors are defined on the raw scale (they
    sit below the curve's first knot and cannot be faithfully translated).
    Use blended_confidence_raw when it is present; fall back to confidence so
    the function works identically when calibration is off or the field is
    absent (e.g. manual tickets, tests).
    """
    if CONFIG.analyzer.confidence_calibration_enabled:
        raw = getattr(idea, "blended_confidence_raw", None)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    conf = getattr(idea, "confidence", None)
    return None if conf is None else float(conf)


def clears_confidence_floor(idea) -> bool:
    """Does `idea` clear its floor?

    `is None` rather than falsiness, deliberately: a confidence of exactly 0.0
    is a real, measured reading of a worthless setup and must be COMPARED, not
    treated as absent. An idea with no confidence at all is a different thing
    and does not clear — an unmeasured setup is not a passing one.

    The floor (MIN_CONFIDENCE / SCALP_MIN) lives on the raw confidence scale.
    When calibration is on, idea.confidence is calibrated; comparing it against
    a raw floor produces a units mismatch. _raw_confidence() returns the right
    reading: blended_confidence_raw when calibration is active, idea.confidence
    otherwise. See Change 1 notes in docs/CALIBRATION_ROLLOUT.md.
    """
    conf = _raw_confidence(idea)
    if conf is None:
        return False
    try:
        return conf >= min_confidence_for(idea)
    except (TypeError, ValueError):
        return False

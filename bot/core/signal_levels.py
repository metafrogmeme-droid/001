"""An ATR that cannot separate a stop from an entry is not a reading.

A live SUI card printed an entry, a stop and a target that were the SAME
number at the precision the card prints, with ``R:R 4.8`` beside them. Driven
on the analyzer's own arithmetic (``sl_mult=1.5``, ``tp_mult=7.2``):

    atr=0.0117   entry $1.1710  sl $1.1535  tp $1.2552   R:R 4.8
    atr=1e-05    entry $1.1710  sl $1.1710  tp $1.1711   R:R 4.8
    atr=1e-06    entry $1.1710  sl $1.1710  tp $1.1710   R:R 4.8

**THE RATIO IS THE FIGURE STRUCTURALLY INCAPABLE OF REVEALING IT.** R:R is
reward over risk, and both are the same multiple of one ATR, so the ATR
CANCELS: the ratio reads 4.8 whether the stop is 1.7% away or a millionth of a
percent. The most reassuring number on the card is the one that cannot move
when the setup collapses -- which is why it survived being looked at.

Two things produce such an ATR and the first is ordinary.

**`round(atr, 6)` IS AN ABSOLUTE GRID ON A RELATIVE QUANTITY.** The analyzer
recorded its ATR to six DECIMAL PLACES, so an asset priced below a cent cannot
have one. Driven over a perfectly healthy 1% range:

    SUI   $1.17          true ATR 1.17e-02   recorded 0.01171
    PEPE  $0.0000112     true ATR 1.12e-07   recorded 0.0
    SHIB  $0.0000091     true ATR 9.10e-08   recorded 0.0

A recorded `0.0` then makes `stop_loss == entry`, which `TradeIdea`'s
directional-sanity validator REFUSES -- so every sub-cent asset was silently
incapable of producing a setup, and nothing said why. That is `_fmt_price`'s
own lesson (it keeps eight places below 0.0001) one quantity over: a price
distance is recorded in SIGNIFICANT digits, never in decimal places.

The second is a venue answering flat or stale bars, where the ATR is genuinely
tiny rather than unrecorded. There the levels are arithmetic on a market that
did not move, and the card must not print a ratio over them.

`0.0` KEEPS ITS EXISTING MEANING ON THE WIRE. Every reader in this tree
already documents a recorded ATR of `0.0` as its own absence -- the risk
engine falls back to a percentage stop, `atr_pct` answers None,
`atr_reading`'s docstring says so in as many words -- and the analyzer's
`indicators.get("atr", entry * 0.02)` was the ONE reader that took it as a
measured zero, because `dict.get` fires its default for an absent KEY and not
for a present zero.
"""

from __future__ import annotations

import math
from typing import Any, Optional

#: How many significant digits a recorded price distance keeps. Six is what
#: the old `round(..., 6)` gave an asset priced near $1, so nothing above a
#: cent records less than it did; below a cent it records a figure at all.
ATR_SIG_DIGITS = 6


def record_atr(value: Any) -> float:
    """The ATR as the analyzer records it: significant digits, not places.

    Answers `0.0` for anything that is not a positive finite number, which is
    the spelling every existing reader already treats as "no ATR on record".
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v) or v <= 0.0:
        return 0.0
    return float(f"{v:.{ATR_SIG_DIGITS}g}")


#: The fewest decimal places a recorded level keeps: the old `round(..., 6)`.
LEVEL_MIN_PLACES = 6


def record_level(value: Any, min_places: int = LEVEL_MIN_PLACES) -> float:
    """A price level, or a signed figure in price units, as it is recorded.

    Six decimal places or six significant digits, whichever keeps more. The
    analyzer rounded every level it computes (the VWAP and its bands, the
    EMAs, SMA50, Bollinger, Keltner and Donchian channels, the fib ladder,
    the session range, MACD) to six decimal PLACES, the ATR's defect across
    forty-two readings. On a sub-cent asset every one of them landed on a grid a
    few percent of the price wide, so both EMAs read one number, MACD and its
    histogram read exactly 0.0 (the MACD voter abstained on every such
    asset), and Bollinger %B read 0.83 for a price above the upper band.

    At or above 0.1 in magnitude the answer is byte-identical to
    `round(value, 6)`, so no reading of a larger price moves. The sign is
    kept, and a value that is not finite is handed back as `round` would.
    `min_places` is for a site that rounded to more places than six (the
    volume profile and the limit entry used eight): it keeps that many, so
    those sites move only where eight places kept fewer than six digits.
    """
    v = float(value)
    return round(v, level_places(v, min_places))


def level_places(value: float, min_places: int = LEVEL_MIN_PLACES) -> int:
    """The decimal places `record_level` keeps for this value.

    Separate so a caller that has to step a recorded level by one unit
    steps it on the grid it was recorded on, never on a second grid.
    """
    if value == 0.0 or not math.isfinite(value):
        return min_places
    places = ATR_SIG_DIGITS - 1 - math.floor(math.log10(abs(value)))
    return max(min_places, places)


def stop_under_floor(entry: float, stop: float, floor: float) -> bool:
    """Whether a stop sits closer to the entry than the floor allows.

    The risk gate's `STOP_DISTANCE` reading, and the one a producer that
    places a stop at the floor asks before handing the idea on. Two copies
    of this arithmetic disagree in the last bit, and at the floor the last
    bit is the verdict. An entry that is not a positive price is no reading,
    and a floor of zero or less reads no distance under it by arithmetic.
    """
    if entry <= 0:
        return False
    return abs(entry - stop) / entry < floor


#: Steps a recorded stop may take outward to read at the floor when the
#: entry and the stop are recorded on ONE grid. Recording moves each of the
#: two prices by at most half a unit, so one step clears the rounding, and a
#: second covers the float noise of the division. `floor_steps` adds what a
#: coarser entry grid needs.
FLOOR_STEPS = 3


def floor_steps(entry_places: int, stop_places: int) -> int:
    """How many of the stop's units it may step outward to read at the floor.

    `record_level` keeps significant digits, so a price just above a power
    of ten keeps one decimal place fewer than one just below it, and a long
    whose entry sits within 0.4% above one records its entry on a grid ten
    times coarser than its stop's. The entry's rounding is then up to half
    of ITS unit, five of the stop's, and three steps could not clear it:
    the recorded pair still read under the floor and the gate refused the
    idea. The budget is the one-grid steps plus the entry's half unit
    counted in stop units. A short's stop is the coarser of the two, so its
    budget is unchanged.
    """
    gap = max(0, int(stop_places) - int(entry_places))
    return FLOOR_STEPS + math.ceil(0.5 * 10 ** gap) if gap else FLOOR_STEPS

#: How far under the floor, as a fraction of it, a stop may read and still
#: be one the producer placed AT the floor. Far above float noise (about
#: 1e-16) and far below any stop a producer placed deliberately tighter.
FLOOR_NOISE = 1e-9


def record_idea_levels(entry: float, stop: float, take_profit: float, *,
                       is_long: bool, floor: float,
                       min_places: int = LEVEL_MIN_PLACES
                       ) -> tuple[float, float, float]:
    """An idea's entry, stop and target as they are recorded.

    Each is `record_level`. A stop the producer placed at the floor
    (MIN_STOP_DISTANCE_PCT) can then read under it: the analyzer widened
    a stop to exactly `floor * entry`, the idea recorded both prices, and
    the risk gate recomputes `|entry - stop| / entry`. Driven on the majors
    snapshot, 202 of the 430 stops the analyzer widened read under the floor
    at the gate, and 23 ideas were refused for that alone. So when the
    unrecorded pair was at the floor and the recorded pair reads under it,
    the recorded stop steps outward one recorded unit at a time, up to
    `floor_steps` of them (more when the entry is recorded on a coarser
    grid than the stop). A stop the producer placed deliberately under the floor
    is left where it is, for the gate to refuse by name.
    """
    e = record_level(entry, min_places)
    s = record_level(stop, min_places)
    t = record_level(take_profit, min_places)
    # No floor, or no positive entry, reaches the loop and stops at its first
    # question, because `stop_under_floor` reads nothing under either.
    if abs(entry - stop) >= floor * entry * (1.0 - FLOOR_NOISE):
        places = level_places(s, min_places)
        unit = 10.0 ** -places
        for _ in range(floor_steps(level_places(e, min_places), places)):
            if not stop_under_floor(e, s, floor):
                break
            s = round(s - unit if is_long else s + unit, places)
    return e, s, t


def atr_on_record(value: Any) -> Optional[float]:
    """A recorded ATR when it is a measurement, `None` when it is not.

    `None` is what `dict.get`'s default already means to the one caller that
    builds levels from it, so a recorded `0.0` and an absent key take the same
    documented percentage fallback instead of collapsing the stop onto the
    entry.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0.0:
        return None
    return v


def _price_text(p: float) -> str:
    """The product's own price formatter, asked rather than re-spelled."""
    from bot.formatters.price_text import fmt_price

    return fmt_price(p)


def levels_separate(entry: Any, stop_loss: Any, take_profit: Any) -> bool:
    """Do these three levels print as three different prices?

    A MEASUREMENT, not a guessed threshold: it asks the formatter the cards
    actually use, the way `{:.0f}` printing "0%" for a real 0.5 is settled by
    asking the format string rather than by inventing a floor beside it. A
    level the card cannot tell from the entry is not a level a venue could
    rest an order at either.
    """
    try:
        e, s, t = float(entry), float(stop_loss), float(take_profit)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(x) for x in (e, s, t)):
        return False
    et = _price_text(e)
    return _price_text(s) != et and _price_text(t) != et


def printed_rr(entry: Any, stop_loss: Any, take_profit: Any) -> Optional[float]:
    """The reward:risk a card may print, or `None` when it would be a claim.

    The ratio is scale-free, so it stays 4.8 over levels that have collapsed
    into one price. Where the card cannot show three different prices it shows
    no ratio either, and `format_rr` renders the dash that says so.
    """
    if not levels_separate(entry, stop_loss, take_profit):
        return None
    e, s, t = float(entry), float(stop_loss), float(take_profit)
    risk = abs(e - s)
    if risk <= 0.0:
        return None
    return round(abs(t - e) / risk, 2)

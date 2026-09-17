"""The POC-retest swing setup — a SEQUENCE over candles, read on two timeframes.

The rules are the operator's, written down on 2026-09-17, and their own framing
is the design constraint:

    "These are sensible starting rules, not yet validated results. I'd test the
     ATR buffer, 1-5-candle retest window, and 2R filter as parameters rather
     than assuming they are optimal."

So every threshold is a field of `PocRetestParams` with a default, never a
constant in an expression — the rule `BacktestConfig.market_is_perp` states
about perp-ness and `funding_arb` states about its flag. Nothing here claims
an edge: it reports a SETUP, and whether that setup is worth taking is a
question for the shadow record with its own sample floor and interval.

WHAT ALREADY EXISTED, AND WHY THIS IS NOT A SECOND COPY OF IT.
`compute_volume_profile` has been live since the volume-profile slice and is a
weighted confluence voter. Two things about it are wrong for this strategy and
both are about the DENOMINATOR:

  * its window is `volume_profile_lookback` (100 candles by default), and this
    strategy's window is the SWING LEG. A POC over the last hundred candles is
    a different price from a POC over the accumulation range, under the same
    word — which is `size_usd`'s two-meanings defect one noun over. `leg_poc`
    is named for its window so no reader can take one for the other.
  * `poc_magnet_signal` answers PROXIMITY ("price is within 2 ATR of the POC,
    so it may be pulled toward it"). This strategy's claim is a SEQUENCE:
    closed decisively beyond, came back inside a window, closed back on the
    same side, then broke the retest candle's extreme. A distance cannot say
    that, and a state that has not completed is not a weaker version of one
    that has — it is a different fact, which is why `retest_state` answers
    one of `STATES` and not a score.

THE TARGET IS THE ONE THING THE RULES DO NOT GIVE, and 2R needs one. The spec
names the entry and the stop exactly and then says "reject where the setup
cannot offer at least 2R potential" — potential to WHAT. Rather than invent a
multiple, the target is the LEG'S OWN EXTREME in the trade's direction (the
swing high a long's leg made), because that is the structural objective a POC
retest continues toward; it is a parameter, and when price has already passed
it the setup is refused as `no_target` rather than handed a manufactured one.
That assumption is stated here because it is an assumption.

AND THE 2R FLOOR IS ON THE NET RATIO. `net_reward_risk` charges the venue's
own maker/taker legs, and at 10x a 2R on price is well under 2R after fees —
the defect every other pre-placement surface in this repo was cured of on the
day before this module was written. Using the price ratio here would have
shipped it back in through a new strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from bot.core.liquidity_sweep import find_swing_highs, find_swing_lows
from bot.core.position_telemetry import atr_reading
from bot.core.trade_costs import net_reward_risk
from bot.core.volume_profile import compute_volume_profile


#: Every one of these is an INPUT. The operator's note says to test them
#: rather than assume they are optimal, so none of them appears as a literal
#: inside an expression anywhere below.
@dataclass(frozen=True)
class PocRetestParams:
    #: The decisive-close distance beyond the POC, AND the stop's buffer past
    #: the retest swing extreme. One number in the spec, so one field: a second
    #: field would be a second answer about the same buffer.
    atr_buffer: float = 0.25
    #: The retest must land within 1..N candles of the decisive close.
    retest_window: int = 5
    #: The floor on the NET reward:risk (after the venue's fees), not price.
    min_net_r: float = 2.0
    #: A stop wider than this many ATR is refused.
    max_stop_atr: float = 2.0
    atr_period: int = 14
    #: Fractal order for swing detection. `find_swing_*` is non-strict (>=),
    #: which `multi_timeframe`'s own comment records as the reason the strict
    #: version "misses equal highs/lows entirely" on short windows.
    swing_order: int = 5
    profile_bins: int = 50


@dataclass(frozen=True)
class SwingLeg:
    """The most recent completed swing leg, and which way it ran."""
    direction: str            # "up" (low -> high) or "down" (high -> low)
    start_index: int
    end_index: int
    start_price: float
    end_price: float

    @property
    def low(self) -> float:
        return min(self.start_price, self.end_price)

    @property
    def high(self) -> float:
        return max(self.start_price, self.end_price)


@dataclass(frozen=True)
class RetestRead:
    """What the sequence did — one of `STATES`, never a score.

    `state` is the whole answer. `entry`/`stop`/`target` are populated only on
    `confirmed`; every other state leaves them None rather than a zero, because
    a level nobody measured printed as a price is this repo's opening subject.
    """
    state: str
    why: str
    poc: Optional[float] = None
    atr: Optional[float] = None
    side: Optional[str] = None            # "long" | "short"
    breakout_index: Optional[int] = None
    retest_index: Optional[int] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    leg: Optional[SwingLeg] = None
    params: PocRetestParams = field(default_factory=PocRetestParams)


#: The whole vocabulary, listed so a reader can see it at once and a guard
#: can assert the SET rather than a sample of it. Deliberately not counted
#: in prose anywhere: the first draft of this file said "seven" in two
#: docstrings while the tuple held eight, which is the shape this module's
#: own header quotes the rule about.
STATES = (
    "atr_unread",       # ATR(14) could not be measured -> nothing downstream can be
    "no_leg",           # fewer than two swings on the higher timeframe
    "no_poc",           # the volume profile over the leg could not be read
    "no_breakout",      # price never closed atr_buffer x ATR beyond the POC
    "awaiting_retest",  # it broke out and has not come back YET, window still open
    "window_expired",   # it broke out and the window passed with no retest
    "retest_failed",    # it came back but closed on the WRONG side of the POC
    "confirmed",        # the whole sequence, with levels
)


def _arr(seq: Sequence[Any]) -> np.ndarray:
    return np.asarray(list(seq), dtype=float)


def swing_leg(highs: Sequence[float], lows: Sequence[float],
              params: Optional[PocRetestParams] = None) -> Optional[SwingLeg]:
    """The most recent completed swing leg, or None when there is not one.

    `None` is not "a flat market": it is "fewer than two swing points were
    found", which on a short window or a monotone ramp is the ordinary answer
    — `multi_timeframe._analyze_structure` reports "ranging" for exactly that
    input, and this repo already records that reporting it as a verdict is the
    defect. So the caller gets `no_leg` and no POC is computed at all.
    """
    p = params or PocRetestParams()
    hi, lo = _arr(highs), _arr(lows)
    if len(hi) != len(lo) or len(hi) < 2 * p.swing_order + 2:
        return None
    sh = find_swing_highs(hi, order=p.swing_order)
    sl = find_swing_lows(lo, order=p.swing_order)
    if not sh or not sl:
        return None
    last_h, last_l = sh[-1], sl[-1]
    if last_h[0] == last_l[0]:
        return None                      # one bar is both: no leg between them
    if last_h[0] > last_l[0]:
        return SwingLeg("up", last_l[0], last_h[0], last_l[1], last_h[1])
    return SwingLeg("down", last_h[0], last_l[0], last_h[1], last_l[1])


def leg_poc(highs: Sequence[float], lows: Sequence[float],
            closes: Sequence[float], volumes: Sequence[float],
            leg: SwingLeg,
            params: Optional[PocRetestParams] = None) -> Optional[float]:
    """The Point of Control over THE LEG'S candles, or None when unreadable.

    Named for its window. `analyzer`'s POC is computed over
    `volume_profile_lookback` candles and is a different price; a reader who
    takes one for the other gets a level that looks right and is not the one
    the structure defines.
    """
    p = params or PocRetestParams()
    a, b = leg.start_index, leg.end_index + 1
    hi, lo, cl, vo = _arr(highs), _arr(lows), _arr(closes), _arr(volumes)
    if b > min(len(hi), len(lo), len(cl), len(vo)) or b - a < 2:
        return None
    # AN UNREADABLE CANDLE MUST NOT RAISE OUT OF A THREE-VALUED READING.
    # `compute_volume_profile` bins by `int((typical - min) / (max - min) *
    # bins)`, and `int(nan)` is a ValueError — so one nan high anywhere in the
    # leg took the whole `retest_state` down rather than answering `no_poc`.
    # Driven, not reasoned: the fixture written for "a nan POC does not reach
    # the comparisons" crashed instead. Its own `price_max <= price_min` guard
    # cannot see it, because every comparison against nan is False.
    try:
        vp = compute_volume_profile(hi[a:b], lo[a:b], cl[a:b], vo[a:b],
                                    num_bins=p.profile_bins)
    except (ValueError, TypeError, ZeroDivisionError, IndexError):
        return None
    if vp is None:
        return None
    poc = float(vp.poc)
    # A nan or non-positive POC is REACHABLE (a nan low leaves `price_min` nan
    # and the profile's own guard passes), and it is the worst kind: every
    # `_decisive` comparison against it is False, so an unreadable profile
    # would report "price never cleared the buffer" — a confident negative
    # about a level nobody measured.
    if poc != poc or poc <= 0:
        return None
    return poc


def _side_for(leg: SwingLeg) -> str:
    """Up leg -> long, down leg -> short. The spec, not an inference.

    "The higher-timeframe structure forms a swing low -> swing high" is the
    long's first condition, and the short is "the inverse". So the leg decides
    the side, and a breakout the other way is not a setup with the sign
    flipped — it is no setup, which is why `retest_state` only ever looks for
    the leg's own direction.
    """
    return "long" if leg.direction == "up" else "short"


def retest_state(htf_highs: Sequence[float], htf_lows: Sequence[float],
                 htf_closes: Sequence[float], htf_volumes: Sequence[float],
                 ltf_highs: Sequence[float], ltf_lows: Sequence[float],
                 ltf_closes: Sequence[float],
                 params: Optional[PocRetestParams] = None) -> RetestRead:
    """Where the POC-retest sequence has got to, on this pair of timeframes.

    The HIGHER timeframe supplies the leg and the POC; the LOWER one supplies
    the sequence and the entry. That split is the spec ("4-hour chart: identify
    swing highs/lows and calculate the volume profile. 1-hour chart: confirm
    the POC retest and execute the entry"), and the POC price is the only thing
    that crosses between them.

    ATR is read on the ENTRY timeframe, because every distance it scales is an
    entry-timeframe distance: the decisive-close buffer is measured against a
    1h close, and the stop sits under a 1h candle's low. A 4h ATR would be
    roughly twice as wide and would quietly widen both.
    """
    p = params or PocRetestParams()

    atr = atr_reading(list(ltf_highs), list(ltf_lows), list(ltf_closes),
                      p.atr_period)
    if atr is None:
        return RetestRead("atr_unread",
                          "ATR(14) could not be measured on the entry "
                          "timeframe, so no distance below can be", params=p)
    if atr == 0.0:
        return RetestRead("atr_unread",
                          "ATR(14) is a measured zero — the entry timeframe "
                          "did not move, so every buffer would be zero wide",
                          atr=atr, params=p)

    leg = swing_leg(htf_highs, htf_lows, p)
    if leg is None:
        return RetestRead("no_leg",
                          "fewer than two swing points on the higher "
                          "timeframe — no leg to build a profile over",
                          atr=atr, params=p)

    poc = leg_poc(htf_highs, htf_lows, htf_closes, htf_volumes, leg, p)
    if poc is None:
        return RetestRead("no_poc",
                          "the volume profile over the leg could not be read",
                          atr=atr, leg=leg, params=p)

    side = _side_for(leg)
    buf = p.atr_buffer * atr
    hi, lo, cl = _arr(ltf_highs), _arr(ltf_lows), _arr(ltf_closes)
    n = len(cl)
    if n < 2 or len(hi) != n or len(lo) != n:
        return RetestRead("no_breakout",
                          "not enough entry-timeframe candles to read a "
                          "breakout", poc=poc, atr=atr, side=side, leg=leg,
                          params=p)

    # A SEQUENCE IS WALKED FORWARD, and the first draft searched backwards for
    # "the most recent decisive close" instead. That looked right and was
    # wrong: the retest candle closes back beyond the POC by construction, and
    # on any real setup it clears the buffer too — so the backwards search
    # claimed the RETEST as the breakout, found no retest after it, and
    # reported `awaiting_retest` on a completed setup. Driven, not reasoned:
    # the fixture built to produce `confirmed` produced `awaiting_retest`.
    #
    # Forward, with one more condition the backwards version could not express:
    # a breakout is the FIRST decisive close of an excursion. In a sustained
    # move every candle closes beyond the POC, and calling each one a fresh
    # breakout would restart the window on every bar — so the walk only arms
    # when price was NOT already decisively beyond.
    def _decisive(i: int) -> bool:
        # `bool(...)` because a numpy comparison answers `np.bool_`, which
        # is truthy and is NOT a `bool` -- the mypy ratchet said so, and
        # `np.bool_` leaking into a dataclass or a JSON payload is its own
        # small dishonesty about what was measured.
        return bool((cl[i] >= poc + buf) if side == "long"
                    else (cl[i] <= poc - buf))

    def _touched(i: int) -> bool:
        return bool((lo[i] <= poc) if side == "long" else (hi[i] >= poc))

    def _held(i: int) -> bool:
        return bool((cl[i] > poc) if side == "long" else (cl[i] < poc))

    armed: Optional[int] = None      # index of the decisive close in play
    last = RetestRead("no_breakout",
                      f"no close reached {p.atr_buffer:g}x ATR beyond the POC",
                      poc=poc, atr=atr, side=side, leg=leg, params=p)

    for i in range(n):
        if armed is None:
            if _decisive(i) and (i == 0 or not _decisive(i - 1)):
                armed = i
                last = RetestRead(
                    "awaiting_retest",
                    "it closed decisively beyond the POC and has not come "
                    "back yet — the window is still open",
                    poc=poc, atr=atr, side=side, breakout_index=i, leg=leg,
                    params=p)
            continue

        if i - armed > p.retest_window:
            last = RetestRead(
                "window_expired",
                f"it closed decisively beyond the POC and the "
                f"{p.retest_window}-candle window passed with no retest",
                poc=poc, atr=atr, side=side, breakout_index=armed, leg=leg,
                params=p)
            armed = i if (_decisive(i) and not _decisive(i - 1)) else None
            if armed is not None:
                last = RetestRead(
                    "awaiting_retest",
                    "it closed decisively beyond the POC and has not come "
                    "back yet — the window is still open",
                    poc=poc, atr=atr, side=side, breakout_index=armed,
                    leg=leg, params=p)
            continue

        if not _touched(i):
            continue
        if not _held(i):
            last = RetestRead(
                "retest_failed",
                "price came back to the POC and closed on the wrong side of "
                "it", poc=poc, atr=atr, side=side, breakout_index=armed,
                retest_index=i, leg=leg, params=p)
            armed = None
            continue

        # The retest candle. Entry on a break of its extreme; the stop sits
        # past the extreme the retest itself made, buffered. "Below the retest
        # swing low by 0.25x ATR" — the retest candle's own low IS the swing
        # low the retest made, and reading it off a wider window would be a
        # different stop under the same sentence.
        last = RetestRead(
            "confirmed",
            "decisive close beyond the POC, retest held, entry on a break of "
            "the retest candle's extreme",
            poc=poc, atr=atr, side=side, breakout_index=armed, retest_index=i,
            entry=(float(hi[i]) if side == "long" else float(lo[i])),
            stop=((float(lo[i]) - buf) if side == "long" else (float(hi[i]) + buf)),
            target=(leg.high if side == "long" else leg.low),
            leg=leg, params=p)
        armed = None

    return last


#: What `setup_verdict` can answer. Kept APART from `STATES` because "the
#: sequence completed" and "and it is worth taking" are different facts, and
#: folding a rejected-on-R setup into "no setup" loses the one an operator
#: most wants to see.
VERDICTS = ("ok", "no_target", "stop_too_wide", "below_min_r", "unpriceable")


@dataclass(frozen=True)
class SetupVerdict:
    verdict: str
    why: str
    net_r: Optional[float] = None
    gross_r: Optional[float] = None
    stop_atr: Optional[float] = None


def setup_verdict(read: RetestRead, *, order_type: object = None,
                  params: Optional[PocRetestParams] = None) -> SetupVerdict:
    """Whether a CONFIRMED sequence clears the spec's two rejections.

    "Reject trades where the stop is wider than 2x ATR or where the setup
    cannot offer at least 2R potential."

    The 2R is on the NET ratio. `net_reward_risk` charges the venue's own
    maker/taker legs, and at 10x a 2R on price is comfortably under 2R once
    fees are paid — the defect every other pre-placement surface in this repo
    was cured of the day before this was written. `order_type` picks the entry
    leg's liquidity: this strategy enters on a BREAK of a candle extreme, which
    is a stop/market order, so an unstated type is taker and that is the honest
    default rather than the flattering one.
    """
    p = params or read.params
    if read.state != "confirmed":
        return SetupVerdict("unpriceable",
                            f"the sequence is {read.state}, not a setup")
    if read.entry is None or read.stop is None or read.target is None \
            or read.atr is None:
        return SetupVerdict("unpriceable",
                            "a confirmed read is missing a level")

    # Price already past the structural objective: there is no target left, and
    # inventing a multiple would be a level nobody measured.
    beyond = (read.target <= read.entry) if read.side == "long" \
        else (read.target >= read.entry)
    if beyond:
        return SetupVerdict("no_target",
                            "the leg's own extreme is already behind the "
                            "entry — no structural target remains")

    stop_width = abs(read.entry - read.stop)
    stop_atr = stop_width / read.atr
    if stop_atr > p.max_stop_atr:
        return SetupVerdict("stop_too_wide",
                            f"the stop is {stop_atr:.2f}x ATR, past the "
                            f"{p.max_stop_atr:g}x limit",
                            stop_atr=stop_atr)

    costed = net_reward_risk(read.entry, read.stop, read.target,
                             order_type=order_type)
    if costed is None:
        return SetupVerdict("unpriceable",
                            "the reward:risk could not be read from these "
                            "levels", stop_atr=stop_atr)
    if costed.net is None:
        return SetupVerdict("below_min_r",
                            "the target does not clear one round trip of fees",
                            gross_r=costed.gross, stop_atr=stop_atr)
    if costed.net < p.min_net_r:
        return SetupVerdict("below_min_r",
                            f"{costed.net:.2f}R after fees, under the "
                            f"{p.min_net_r:g}R floor ({costed.gross:.2f}R on "
                            f"price alone)",
                            net_r=costed.net, gross_r=costed.gross,
                            stop_atr=stop_atr)
    return SetupVerdict("ok",
                        f"{costed.net:.2f}R after fees, stop {stop_atr:.2f}x "
                        f"ATR", net_r=costed.net, gross_r=costed.gross,
                        stop_atr=stop_atr)

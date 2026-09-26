"""The time exits a live position is subject to: one reading, three readers.

A live position can be closed on the clock by five rules, run in two places:

* the executor's TIME STOP (``LiveExecutor.check_positions``): from the
  strategy type's close hours on, a position that is not in profit after its
  round-trip fees is closed. Runs while TIME_STOP_ENABLED is on.
* four SMART EXITS (``RuneClawEngine._evaluate_live_smart_exits``), which also
  need TIME_STOP_LIVE_AUTO_CLOSE and a readable 1R:

  - no progress: from N one-hour candles (per strategy type), under 0.5R;
  - the signal type's hold limit: from it, under 1R; from twice it, whatever
    the R;
  - volume decay, for volume-spike signals: under 0.3R from 2 candles, under
    1R from 4.

  (VWAP reversion is a fifth smart exit, and it is decided by price, not by
  the clock, so it is not listed here.)

Every one of these is a CONDITION FROM AN HOUR ON, not an event at that hour:
the rules run on every tick, so "after 8h if under 1R" closes a position
that was at +1.4R at 8h and falls to +0.9R at 11h.

NOTHING ON A POSITION CARD SAID ANY OF IT. A swing trade on a momentum signal
is closed at 16h at +2R, by the hard hold limit, and the card showed its hold
time and nothing about the clock it was running against. And adoption gave a
position the bot did not open the dataclass defaults -- swing, momentum --
so a position somebody opened by hand was closed on a momentum signal nobody
assigned it: driven, an adopted position at +2R was closed at market at 16h.
The code says elsewhere that adopted positions are never force-closed because
they may be intentional, and the operator decided (2026-09-24) that a
position with no recorded strategy gets no time exits.

ONE READING. The rules read their thresholds from ``smart_exits``' own
accessors and the executor's strategy table, the two exit paths ask
``thesis_recorded`` and ``in_profit_after_fees`` here, and the cards render
``plan_for`` -- so a card cannot state a rule the code does not run, and a
rule changed in one place changes on the card. ``tests/test_a_position_card_
states_its_time_exits.py`` drives every check function against the plan.

The THIRD reader is the monitor's time-stop alert (``ProactiveMonitor.
_check_time_stops``), which asks ``due_exit`` and ``next_exit``: it used to
judge "intraday or swing" off the stop distance against four hours no exit
reads, told a holder to close by hand a position the bot closes by itself,
and read "in profit" gross.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from bot.core.position_telemetry import entered_at, price_on_record, r_denominator
from bot.core.smart_exits import (
    HOLD_HARD_LIMIT_MULT,
    HOLD_LIMIT_MIN_R,
    VOLUME_DECAY_STAGES,
    VOLUME_SIGNALS,
    progress_limit,
    signal_hold_hours,
)
from bot.core.trade_costs import round_trip_pct
from bot.utils.i18n import t

#: Origins whose strategy and signal type are the dataclass defaults unless a
#: local record of the bot's own supplied them (``thesis_source="inherited"``).
UNRECORDED_ORIGINS = frozenset({"adopted", "reclaimed"})

#: The five rules, for the reader that wants to name one.
RULES = ("time_stop", "progress", "signal_hold", "signal_hard", "volume_decay")


@dataclass(frozen=True)
class TimeExit:
    """From ``hours`` on, the rule closes the position while its condition holds.

    ``r_below`` set: while R is under it. ``fee_profit``: unless it is in
    profit after its round-trip fees. Neither: whatever the R.
    """
    rule: str
    hours: float
    r_below: Optional[float] = None
    fee_profit: bool = False

    @property
    def always(self) -> bool:
        return self.r_below is None and not self.fee_profit


def _dominates(a: TimeExit, b: TimeExit) -> bool:
    """True when ``a`` closes the position whenever ``b`` would.

    Only the comparisons that are certain: an unconditional rule covers every
    rule from its hour on, and an R rule covers a later one with a lower bar.
    "Not in profit after fees" is a bar in PRICE, and its size in R depends on
    how far the stop is, so it is never compared with an R rule.
    """
    if a.hours > b.hours:
        return False
    if a.always:
        return True
    if a.r_below is not None and b.r_below is not None:
        return a.r_below >= b.r_below
    return False


def _prune(exits: list[TimeExit]) -> tuple[TimeExit, ...]:
    """Drop the rules another one always fires ahead of: a rule no position
    can reach is not a rule the card should state."""
    # At one hour the rule that covers most comes first -- unconditional, then
    # the highest R bar -- so a greedy pass never keeps a rule a later one at
    # the same hour covers.
    ordered = sorted(exits, key=lambda e: (
        e.hours, not e.always, -(e.r_below if e.r_below is not None else -1.0)))
    kept: list[TimeExit] = []
    for e in ordered:
        if not any(_dominates(k, e) for k in kept):
            kept.append(e)
    return tuple(kept)


def rules_for(strategy_type: str, signal_type: str, *, r_readable: bool,
              time_stop_on: bool, smart_exits_on: bool,
              strategy_types: Any) -> tuple[TimeExit, ...]:
    """Every time exit that can close a position of this strategy and signal.

    Pure over the tables; ``strategy_types`` is the executor's
    ``CONFIG.strategy_types`` (passed in so the reading is testable against a
    planted table). The smart exits are listed only when they can run: both
    flags on and a 1R to measure against, which is what the engine checks.
    """
    if not time_stop_on:
        return ()
    exits = [TimeExit("time_stop",
                      float(strategy_types.get_time_close_hours(strategy_type)),
                      fee_profit=True)]
    if smart_exits_on and r_readable:
        candles, min_r = progress_limit(strategy_type)
        exits.append(TimeExit("progress", float(candles), r_below=min_r))
        hold = signal_hold_hours(signal_type)
        if hold is not None:
            exits.append(TimeExit("signal_hold", hold, r_below=HOLD_LIMIT_MIN_R))
            exits.append(TimeExit("signal_hard", hold * HOLD_HARD_LIMIT_MULT))
        if signal_type in VOLUME_SIGNALS:
            for candles_at, r in VOLUME_DECAY_STAGES:
                exits.append(TimeExit("volume_decay", float(candles_at), r_below=r))
    return _prune(exits)


def thesis_recorded(pos: Any) -> bool:
    """Whether this position's strategy and signal type are a recorded thesis.

    A position the bot opened carries the idea's own. An ADOPTED or RECLAIMED
    one was built with the dataclass defaults, which are nobody's reading, and
    has a thesis only when adoption found a local record of the bot's own to
    copy it from (``thesis_source="inherited"``). A record written before that
    marker existed says the same thing through ``sl_tp_source``: the donor that
    supplied its levels is the one that supplied its strategy.
    """
    if getattr(pos, "origin", "executed") not in UNRECORDED_ORIGINS:
        return True
    src = getattr(pos, "thesis_source", None)
    if src is not None:
        return bool(src == "inherited")
    return bool(getattr(pos, "sl_tp_source", None) == "inherited")


def r_multiple_now(pos: Any, mark: Optional[float]) -> Optional[float]:
    """The position's R at ``mark`` against the risk taken at entry, or None.

    None, never 0.0: a flat trade is a measurement every R rule acts on, and a
    position whose 1R cannot be read (no stop, no trailing initial risk) is
    not flat. The smart exits skip their R rules on None, and the card says so.
    """
    risk = r_denominator(pos)
    entry = price_on_record(getattr(pos, "entry_price", None))
    px = price_on_record(mark)
    if risk <= 0 or entry is None or px is None:
        return None
    mark = px
    pnl = (mark - entry) if getattr(pos, "direction", "") == "LONG" else (entry - mark)
    return pnl / risk


def in_profit_after_fees(direction: str, entry: float, mark: float,
                         order_type: object = None) -> bool:
    """Whether ``mark`` clears the position's own round trip in fees.

    The time stop spares exactly these; a position up by less than its fees
    is a net loser and is not spared (audit exits, 2026-07-21).
    """
    buf = entry * round_trip_pct(order_type) / 100.0
    return mark > entry + buf if direction == "LONG" else mark < entry - buf


@dataclass(frozen=True)
class TimeExitPlan:
    """What the clock can do to one position.

    ``state``: ``armed`` (the exits listed apply), ``no_thesis`` (adopted with
    no recorded strategy -- none apply), ``off`` (TIME_STOP_ENABLED is off),
    ``practice`` (a practice book, which no time exit reads), ``untracked``
    (on the exchange with no record here, so nothing runs on it).
    ``r_inert``: the smart exits would run but the 1R cannot be read.
    """
    state: str
    exits: tuple[TimeExit, ...] = ()
    r_inert: bool = False


def plan_for(pos: Any, time_stop: Any, strategy_types: Any) -> TimeExitPlan:
    """The time exits the live exit code will run on ``pos``.

    ``time_stop`` is ``CONFIG.time_stop`` and ``strategy_types`` is
    ``CONFIG.strategy_types``; both exit paths read the same two objects.
    """
    if not thesis_recorded(pos):
        return TimeExitPlan("no_thesis")
    if not time_stop.enabled:
        return TimeExitPlan("off")
    smart = bool(time_stop.live_auto_close_enabled)
    r_readable = r_denominator(pos) > 0
    exits = rules_for(
        getattr(pos, "strategy_type", "swing"),
        getattr(pos, "signal_type", "momentum_confluence"),
        r_readable=r_readable, time_stop_on=True, smart_exits_on=smart,
        strategy_types=strategy_types)
    return TimeExitPlan("armed", exits, r_inert=smart and not r_readable)


# ── Rendering ────────────────────────────────────────────────────────────────
#
# Every word is an i18n key (``tx_*``): /positions is a fourteen-language card
# and the detail and /livepositions cards read the same table, so there is one
# vocabulary for what the clock can do to a position.

def _hours(h: float) -> str:
    return f"{h:g}h"


def _duration(hours: float) -> str:
    mins = max(0, int(round(hours * 60)))
    if mins < 60:
        return f"{mins}m"
    if mins < 48 * 60:
        return f"{mins // 60}h{mins % 60:02d}m" if mins % 60 else f"{mins // 60}h"
    return f"{mins / 1440:.1f}d"


def closes_at_reading(e: TimeExit, r_now: Optional[float],
                      fee_clear: Optional[bool]) -> Optional[bool]:
    """Whether the rule's condition holds at this reading, once its hour is
    reached: True, False, or None when the reading it needs was not taken.

    The one reading of a rule's condition: the card's status and the
    monitor's alert both ask it, so the alert cannot call a rule due that the
    card calls armed.
    """
    if e.fee_profit:
        return None if fee_clear is None else not fee_clear
    if e.r_below is None:
        return True                    # whatever the R
    if r_now is None:
        return None
    return r_now < e.r_below


def _status(e: TimeExit, held_h: Optional[float], r_now: Optional[float],
            fee_clear: Optional[bool], lang: str) -> str:
    """Where the rule stands: counting down, armed and not met, or due."""
    if held_h is None:
        return ""
    if held_h < e.hours:
        return t("tx_in", lang, d=_duration(e.hours - held_h))
    closes = closes_at_reading(e, r_now, fee_clear)
    if closes:
        return t("tx_due", lang)
    if closes is None:
        return t("tx_armed", lang)
    if e.fee_profit:
        return t("tx_armed_fee", lang)
    return t("tx_armed_r", lang, r=f"{r_now:+.2f}")


def due_exit(plan: TimeExitPlan, held_h: Optional[float], r_now: Optional[float],
             fee_clear: Optional[bool]) -> Optional[TimeExit]:
    """The first rule past its hour whose condition holds at this reading: the
    one the exit code closes the position on, or None.

    A plan that is not armed carries no rules, so nothing is due on it.
    """
    if held_h is None:
        return None
    for e in plan.exits:
        if held_h >= e.hours and closes_at_reading(e, r_now, fee_clear):
            return e
    return None


def next_exit(plan: TimeExitPlan, held_h: Optional[float], r_now: Optional[float],
              fee_clear: Optional[bool]) -> Optional[TimeExit]:
    """The first rule not yet at its hour whose condition holds at this
    reading: what closes the position first if the reading does not change,
    or None (nothing would, or the plan runs nothing).

    A rule whose reading was not taken is not predicted: "unread" is not
    "would close".
    """
    if held_h is None:
        return None
    for e in plan.exits:
        if held_h < e.hours and closes_at_reading(e, r_now, fee_clear):
            return e
    return None


def warn_hour(pos: Any, strategy_types: Any) -> float:
    """From this many hours held the time-stop alert may warn: the strategy
    table's warn hours, the ones the executor's own time stop reads."""
    return float(strategy_types.get_time_warn_hours(
        getattr(pos, "strategy_type", "swing")))


def exit_phrase(e: TimeExit, held_h: Optional[float] = None,
                r_now: Optional[float] = None,
                fee_clear: Optional[bool] = None, lang: str = "en") -> str:
    """One rule as a phrase: ``after 8h if under 1R (in 3h12m)``."""
    h = _hours(e.hours)
    if e.always:
        rule = t("tx_after_any", lang, h=h)
    elif e.fee_profit:
        rule = t("tx_after_fee", lang, h=h)
    else:
        rule = t("tx_after_r", lang, h=h, r=f"{e.r_below:g}")
    status = _status(e, held_h, r_now, fee_clear, lang)
    return rule + (f" ({status})" if status else "")


#: The sentence for each state that runs no time exit.
_NO_EXIT_KEYS = {"no_thesis": "tx_no_thesis", "off": "tx_off",
                 "practice": "tx_practice", "untracked": "tx_untracked"}


def time_exit_line(plan: TimeExitPlan, *, held_h: Optional[float] = None,
                   r_now: Optional[float] = None,
                   fee_clear: Optional[bool] = None, lang: str = "en") -> str:
    """The card line, plain text (no markup, so any surface can escape it)."""
    if plan.state in _NO_EXIT_KEYS:
        return t(_NO_EXIT_KEYS[plan.state], lang)
    if plan.state != "armed":
        raise ValueError(f"unknown time-exit state {plan.state!r}")
    parts = [exit_phrase(e, held_h, r_now, fee_clear, lang) for e in plan.exits]
    line = t("tx_head", lang, rules=" · ".join(parts))
    if plan.r_inert:
        line += " — " + t("tx_r_inert", lang)
    return line


def position_time_exit_line(pos: Any, mark: Optional[float], now: Any,
                            time_stop: Any, strategy_types: Any,
                            lang: str = "en") -> str:
    """``time_exit_line`` for a live position at ``mark`` and ``now``.

    The R and the fee test are the ones the exit code takes, so "armed, at
    +1.40R" is the reading the rule acts on.
    """
    plan = plan_for(pos, time_stop, strategy_types)
    held_h, r_now, fee_clear = clock_reading(pos, mark, now)
    return time_exit_line(plan, held_h=held_h, r_now=r_now, fee_clear=fee_clear,
                          lang=lang)


def clock_reading(pos: Any, mark: Optional[float], now: Any
                  ) -> tuple[Optional[float], Optional[float], Optional[bool]]:
    """``(held_h, r_now, fee_clear)`` for a live position at ``mark`` and
    ``now``: the hours since it entered, its R, and whether the mark clears
    its round trip in fees. Each is None when it cannot be read.

    One reading for the card and the monitor's alert, so the two cannot
    disagree about how long a position has been held or whether it is in
    profit.
    """
    opened = entered_at(pos)
    held_h = ((now - opened).total_seconds() / 3600.0) if opened is not None else None
    px = price_on_record(mark)
    entry = price_on_record(getattr(pos, "entry_price", None))
    r_now = r_multiple_now(pos, px)
    fee_clear = (in_profit_after_fees(getattr(pos, "direction", ""), entry, px,
                                      getattr(pos, "order_type", None))
                 if px is not None and entry is not None else None)
    return held_h, r_now, fee_clear

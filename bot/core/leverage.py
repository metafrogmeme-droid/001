"""Leverage resolution (pure) — every rule that can only ever REDUCE.

Three things live here, and they share one property: nothing in this module
can raise leverage above what the operator configured.

- **The ceiling.** ``MAX_LEVERAGE`` is the leverage no NEW position is set
  above (operator decision 2026-09-28). `operator_standard` is the one reading
  of the standard every order starts from -- the `/leverage` override or the
  configured default, capped at the ceiling -- and every step after it (the
  two rules below, dynamic scaling, the quality ladder) only lowers, so the
  cap applied there is the whole enforcement. The executor's F-3 notional
  block then measures it on the order itself, as the backstop.
- **The per-user preference (NB3).** A BYOK live user may pin their own
  standard leverage, but only ever DOWN from the operator's standard.
- **The margin-risk cap.** `max_margin_risk_pct` bounds SL-distance ×
  leverage, and the leverage it bounds is the one the VENUE is set to. See the
  block above `RISK_CAP_ATTR` for why the sized leverage cannot stand in for
  it, and `margin_risk_verdict` for why a reduction that does not clear the cap
  is a refusal rather than a pass.

An order has two leverages since the venue standard moved to 10x: the one it
is SIZED at and the one the VENUE is set to (`order_standards`,
`venue_leverage`). Every rule above lowers the sized one; the venue follows in
proportion, so a trade keeps the notional it had at 5x and only the margin
changes.

All three are here so the executor's two set paths, its sizing path, the risk
gate and the two cards that print the standard ask one function: a second
copy of a reduce-only rule is a second answer about how much of the account a
stop can take, and a second copy of the standard is two answers about what
leverage the next order runs at -- which is how `/leverage set 20` was set on
the venue under a ``MAX_LEVERAGE=10`` that nothing read.

THE RISK GATE IMPORTING THIS IS A NEW DIRECTION, and it is recorded rather
than routed around. Everywhere else `bot.core` reads `bot.risk`; here
`bot/risk/risk_engine.py` reads this module. It cannot cycle: this file
imports nothing from the project (only `typing`), and `bot/core/__init__.py`
is empty, so `import bot.core.leverage` pulls in nothing else — driven, both
import orders resolve. `bot/utils/` is where this repo usually puts a leaf
both layers read, and moving it there would churn the path of a module three
other files already import for a dependency that cannot fire. If a cycle ever
becomes possible, the move is the fix, not a lazy import.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional


def resolve_user_leverage(pref, cap, min_lev: int = 1) -> int:
    """Clamp a user's leverage preference into ``[min_lev, cap]``.

    - ``pref`` None / non-numeric / <= 0  → ``cap`` (unchanged behaviour).
    - a valid ``pref``                    → ``max(min_lev, min(pref, cap))``.

    The result NEVER exceeds ``cap`` — the operator default is the ceiling, so a
    user can only reduce. Fail-safe: any error returns a sane clamp of ``cap``
    (or 1 if even that is unusable). Returns an int >= 1.
    """
    try:
        c = max(1, int(cap))
    except (TypeError, ValueError):
        return 1
    try:
        m = max(1, int(min_lev))
    except (TypeError, ValueError):
        m = 1
    m = min(m, c)
    if pref is None:
        return c
    try:
        p = int(float(pref))
    except (TypeError, ValueError):
        return c
    if p <= 0:
        return c
    return max(m, min(p, c))


def describe_user_leverage(pref: Optional[int], cap: int) -> str:
    """A short human line for status surfaces."""
    eff = resolve_user_leverage(pref, cap)
    if pref is None:
        return f"{eff}x (operator default)"
    if eff < int(pref):
        return f"{eff}x (your {int(pref)}x was capped at the operator {cap}x)"
    return f"{eff}x (your preference)"


# ── The margin-risk cap: one reading, three readers ──────────────────────────
#
# `max_margin_risk_pct` is a claim about the VENUE's leverage. Driven:
#
#     loss_at_stop / venue_locked_margin = L_venue × sl_dist_pct / 100
#
# The leverage the order is SIZED at cancels out of that ratio entirely — it
# decides the notional, and so the dollar loss, and moves the capped quantity
# not at all. So the risk gate's reduction has to reach the number the venue is
# SET to or it enforces nothing: sized at 2x with the venue at 5x and a 10%
# stop, the audit line read "30.0% ≤ 30.0%" while the real figure was 50% of
# the margin the venue had locked. At a 20% stop it was 100% — liquidation at
# the stop, from the control written to prevent exactly that.
#
# The reduction travels on the idea because that is where the risk gate can
# write it and both executor paths can read it. The ATTRIBUTE NAME lives here
# rather than being spelled in each module: `risk_engine` wrote it and
# `live_executor` read it, two spellings of one contract, and a rename at
# either end disconnects them silently — which is this module's own subject
# one layer down.

RISK_CAP_ATTR = "_adjusted_leverage"

MIN_LEVERAGE_DEFAULT = 2
"""The floor when the config cannot state one.

`CONFIG.exchange.min_leverage` is a dataclass field with its own default of 2,
so it is never absent on a real config and this is unreachable from production.
It exists because the two readers had each INVENTED a fallback — 1 in the
executor, 2 in the risk engine — for a field that cannot be missing: a fallback
that cannot fire and disagrees with its twin is a claim that there is a check.
One default, read from one place.
"""

MAX_LEVERAGE_DEFAULT = 10
"""The ceiling when the config cannot state one.

`CONFIG.exchange.max_leverage` is a dataclass field with its own default of
10, so like the floor it is never absent on a real config and this is
unreachable from production. A stand-in that names no ceiling gets the one the
field declares rather than none, because "no ceiling" is the LOOSE direction:
a hand-written stand-in that forgot the attribute must not loosen anything,
and `MIN_LEVERAGE_DEFAULT` above is the same reading for the floor.
"""


def leverage_ceiling(cfg: Any) -> int:
    """``MAX_LEVERAGE`` as every placement reads it, an int >= 1.

    The one read of the field on the money path: the executor's F-3 notional
    block and `operator_standard` both ask this rather than the attribute, so a
    stand-in with no ceiling, or one holding junk, answers the declared default
    in both places instead of two different fallbacks.
    """
    try:
        return max(1, int(getattr(cfg, "max_leverage", MAX_LEVERAGE_DEFAULT)))
    except (TypeError, ValueError):
        return MAX_LEVERAGE_DEFAULT


class StandardLeverage(NamedTuple):
    """The standard leverage every order starts from, and where it came from.

    NAMED, for the reason `MarginRiskVerdict` gives.

    ``leverage`` is the number the next order starts from: ``requested``
    capped at ``ceiling``. ``requested`` is what was asked for -- the
    `/leverage` override when one is set, else the configured default -- and
    ``source`` says which (``"override"`` / ``"default"``). ``capped`` is the
    one fact the cards must not hide: an operator who typed 15 and is shown
    "15x" while every order runs at 10 has been told two numbers for one thing.
    """

    leverage: int
    requested: int
    source: str
    ceiling: int

    @property
    def capped(self) -> bool:
        return self.requested > self.leverage

    def source_note(self) -> str:
        """The parenthetical the cards print beside the figure."""
        where = "runtime override" if self.source == "override" else "configured default"
        if not self.capped:
            return where
        return (f"{where} {self.requested}x, capped at the MAX_LEVERAGE "
                f"ceiling {self.ceiling}x")


def operator_standard(cfg: Any, override: Any) -> StandardLeverage:
    """The operator's standard leverage: the override, else the configured
    default, and never above the ceiling.

    ``override`` is `RUNTIME.leverage_override` (``None`` for none). The
    config places its own default under the ceiling where the fields are
    declared, so on a real config only the override can arrive above it; the
    cap is made here anyway, because this is the reading every placement
    takes and a stand-in config that breaks the config's invariant must not
    break the ceiling with it. A user's preference, dynamic scaling, the
    quality ladder and the margin-risk cap all start from ``.leverage`` and
    only lower it.
    """
    ceiling = leverage_ceiling(cfg)
    requested: Optional[int] = None
    source = "default"
    if override is not None:
        try:
            requested = int(override)
            source = "override"
        except (TypeError, ValueError):
            requested = None
    if requested is None:
        requested = int(cfg.default_leverage)
        source = "default"
    requested = max(1, requested)
    return StandardLeverage(min(requested, ceiling), requested, source, ceiling)


# ── Two standards: the leverage an order is SIZED at, and the one the VENUE is
#    set to ──────────────────────────────────────────────────────────────────
#
# THE OPERATOR'S DECISION OF 11 OCTOBER: the venue standard moves from 5x to
# 10x, and every trade keeps the notional and the dollar loss at its stop that
# it had at 5x; only the margin the venue locks changes. Every sizing figure
# in this repo is a MARGIN written at 5x -- the risk budget's base
# (budget / (stop x leverage)), MAX_POSITION_PCT, the execution ceiling,
# MICRO_MAX_POSITION_USD and the total, the Kelly ceiling, the high-conviction
# margin, a typed ticket margin, the exposure caps -- so read at 10x each one
# would have placed twice the notional wherever it bound. They are not
# rewritten: an order is SIZED as before, at the sizing standard, and placed at
# the venue leverage with the margin that notional needs there. Driven:
#
#     notional = sized_margin x sized_leverage
#     venue_margin = notional / venue_leverage
#
# so the quality ladder, a user's preference, dynamic scaling and the
# margin-risk cap, which all lower the SIZED leverage, cut the notional exactly
# as they did, and `venue_leverage` moves the venue with them in proportion.

SIZING_LEVERAGE_DEFAULT = 5
"""The leverage every sizing figure is written at, when the config states none.

`CONFIG.exchange.sizing_leverage` is a dataclass field with this default, so
like the floor and the ceiling this is unreachable from a real config."""


def sizing_leverage(cfg: Any) -> int:
    """``SIZING_LEVERAGE`` as an int >= 1."""
    try:
        return max(1, int(getattr(cfg, "sizing_leverage", SIZING_LEVERAGE_DEFAULT)))
    except (TypeError, ValueError):
        return SIZING_LEVERAGE_DEFAULT


class LeveragePair(NamedTuple):
    """An order's two leverages, or the two standards they start from.

    ``sized`` is the leverage the risk budget, every margin cap and the quality
    ladder read: notional = sized margin x ``sized``. ``venue`` is the leverage
    the venue is set to, and the margin it locks is notional / ``venue``.
    ``venue >= sized`` always, so the venue never locks more margin than the
    sizing figure."""

    sized: int
    venue: int


def order_standards(cfg: Any, override: Any) -> LeveragePair:
    """The two standards every order starts from.

    ``venue`` is `operator_standard` -- the `/leverage` override or the
    configured default, under the ceiling. ``sized`` is ``SIZING_LEVERAGE``,
    never above the venue standard: with the venue at or under 5x the two are
    one number and every order is sized and placed exactly as before this
    split existed; above it, the notional stays the 5x notional and the rest
    is margin the venue does not lock."""
    venue = operator_standard(cfg, override).leverage
    return LeveragePair(min(sizing_leverage(cfg), venue), venue)


def venue_leverage(sized: int, standards: LeveragePair) -> int:
    """The leverage the venue is set to for an order SIZED at ``sized``.

    The same share of the venue standard as ``sized`` is of the sized
    standard, rounded down: a rung that cut the sizing from 5x to 4x sets a
    10x venue to 8x. Never above the venue standard, never below ``sized``
    (the venue standard is never below the sized one), never below 1. A
    caller that also holds a user's preference lowers this further; that
    keeps it at or above ``sized``, which the preference already bounds."""
    s = max(1, int(standards.sized))
    v = max(s, int(standards.venue))
    x = max(1, int(sized))
    return max(1, min(v, (x * v) // s))


def sized_for_venue(venue: int, standards: LeveragePair) -> int:
    """The sized leverage whose `venue_leverage` is at most ``venue``.

    The way back for a cap decided at the venue (the margin-risk cap) onto the
    sized leverage the idea carries: ``venue_leverage(sized_for_venue(u)) <=
    u`` for every ``u``, so a reduction measured at the venue holds there."""
    s = max(1, int(standards.sized))
    v = max(s, int(standards.venue))
    return max(1, (max(1, int(venue)) * s) // v)


def venue_margin(sized_margin: float, sized: int, venue: int) -> float:
    """The margin the venue locks for the notional ``sized_margin`` was sized
    to: ``sized_margin x sized / venue``."""
    return float(sized_margin) * max(1, int(sized)) / max(1, int(venue))


MARGIN_RISK_PCT_AT_SIZING = 30.0
"""The margin-risk cap when ``MAX_MARGIN_RISK_PCT`` is unset, stated at the
SIZED leverage: 30% of the margin at 5x is a 6% stop, the figure the cap has
always allowed."""


def margin_risk_cap_pct(configured: Optional[float], standards: LeveragePair) -> float:
    """``MAX_MARGIN_RISK_PCT`` as the cap on the VENUE's margin.

    Set, it is read as written. Unset, it is 30% scaled to the venue, 30% x
    venue / sized: 60% at a 10x venue sized at 5x, 30% where the two are one.
    Either way the stop the cap allows is the one it allowed at 5x (6%), so a
    deployment whose environment still pins ``DEFAULT_LEVERAGE=5`` keeps 30%,
    and one that moves the venue to 10x is not refused every stop past 3%."""
    if configured is not None:
        try:
            return float(configured)
        except (TypeError, ValueError):
            pass
    s = max(1, int(standards.sized))
    v = max(s, int(standards.venue))
    return MARGIN_RISK_PCT_AT_SIZING * v / s


MARGIN_RISK_TOLERANCE_PCT = 0.5
"""Slack on the cap, applied to the TRIGGER and to the acceptance alike.

One tolerance. Asking "is this over the cap?" with 0.5pp of slack and then
"does the reduction clear it?" with none would be two answers about where the
cap is, and the second is the one that decides whether a trade is refused.
"""


def leverage_floor(cfg: Any) -> int:
    """The configured minimum leverage, as an int ≥ 1."""
    try:
        return max(1, int(getattr(cfg, "min_leverage", MIN_LEVERAGE_DEFAULT)))
    except (TypeError, ValueError):
        return MIN_LEVERAGE_DEFAULT


def set_margin_risk_cap(idea: Any, leverage: Optional[int]) -> None:
    """Record the leverage the margin-risk cap allows for this idea.

    ``None`` is *no cap to record* and writes nothing — the verdict states a
    leverage only for ``reduced``, and the other three states leave the
    standard leverage exactly where it was. Taking the Optional here rather
    than making the caller narrow it keeps that branch off the money path:
    the pairing (``reduced`` always names a leverage) is the LEAF's invariant
    and is pinned in its own table, so a reader restating it would be a second
    answer about a state this module decides.

    Best-effort otherwise: a `TradeIdea` that refuses the attribute keeps
    whatever it had, and `apply_margin_risk_cap` then reads no cap — which
    leaves the standard leverage in place, the behaviour every non-capped
    idea gets.
    """
    if leverage is None:
        return
    try:
        setattr(idea, RISK_CAP_ATTR, int(leverage))
    except Exception:
        pass


def tighten_leverage_cap(idea: Any, leverage: Optional[int]) -> None:
    """Record a leverage cap for this idea, keeping the LOWER of it and any
    cap already on record.

    The margin-risk cap and the quality ladder both write the one attribute
    `apply_margin_risk_cap` reads, and they are decided at different points
    of the risk gate; a second writer that overwrote would silently undo the
    first, in whichever order they happened to run. ``set_margin_risk_cap``
    keeps its own contract (a verdict names the leverage the cap allows) and
    the gate calls it AFTER this, on a leverage it measured from the
    ladder's -- so its figure is never above this one; a later reordering
    is what this min() is for. ``None`` writes nothing, as its sibling.
    """
    if leverage is None:
        return
    try:
        lev = max(1, int(leverage))
    except (TypeError, ValueError):
        return
    try:
        prior = getattr(idea, RISK_CAP_ATTR, None)
        prior_i = int(prior) if prior else None
    except (TypeError, ValueError):
        prior_i = None
    try:
        setattr(idea, RISK_CAP_ATTR, lev if prior_i is None else min(prior_i, lev))
    except Exception:
        pass


def apply_margin_risk_cap(target: int, idea: Any) -> int:
    """Clamp ``target`` down to this idea's margin-risk cap. REDUCE-ONLY.

    A no-op when the idea carries no cap, when the cap is unreadable, or when
    the cap is already above the target — so every path that does not go
    through the risk gate's reduction keeps exactly the leverage it had.
    """
    try:
        base = max(1, int(target))
    except (TypeError, ValueError):
        return 1
    cap = getattr(idea, RISK_CAP_ATTR, None) if idea is not None else None
    if not cap:
        return base
    try:
        return max(1, min(base, int(cap)))
    except (TypeError, ValueError):
        return base


class MarginRiskVerdict(NamedTuple):
    """What the margin-risk cap decided about one idea.

    NAMED, because positions are how readers drift — the remedy
    `tests/conftest.py`'s `_Reach` and `engine._LiveRecheck` already record.

    ``state`` is one of:
      ``ok``             within the cap at the leverage the executor will use
      ``reduced``        over it, and a reduce-only leverage clears it
      ``refused``        over it, and no permitted leverage clears it
      ``not_applicable`` nothing to measure (1x, or no entry price on record)

    ``leverage`` is the number to USE — the reduced one for ``reduced``, the
    original for ``ok`` — and is ``None`` where there is none to name.
    ``margin_risk_pct`` is the measured SL-distance × leverage at
    ``leverage``, or ``None`` when nothing was measured.
    """

    state: str
    leverage: Optional[int]
    margin_risk_pct: Optional[float]
    sentence: str


def margin_risk_verdict(*, leverage: int, entry_price: float, stop_loss: float,
                        max_margin_risk_pct: float, min_leverage: int,
                        may_reduce: bool) -> MarginRiskVerdict:
    """Measure SL-distance × leverage against the cap, and say what to do.

    ``may_reduce`` is the dynamic-leverage flag: with it off, an over-cap idea
    is REFUSED rather than resized, which is the behaviour this gate has always
    had. With it on, the reduction is attempted — and a reduction that does not
    itself clear the cap is refused too, because ``max(min_leverage, …)`` can
    floor the answer ABOVE the cap and the old sentence then printed the ``≤``
    as a LITERAL: ``SL 16.0% × 2x = 32.0% ≤ 30.0%``, filed as a passed check.
    Here the comparison is MADE, so the operator printed is the one measured.
    """
    try:
        lev = max(1, int(leverage))
    except (TypeError, ValueError):
        lev = 1
    if lev <= 1 or not entry_price or entry_price <= 0:
        return MarginRiskVerdict("not_applicable", None, None,
                                 "MARGIN_RISK: no leverage, skipped")

    sl_dist_pct = abs(entry_price - stop_loss) / entry_price * 100
    margin_risk = sl_dist_pct * lev
    cap = float(max_margin_risk_pct)
    tol = MARGIN_RISK_TOLERANCE_PCT

    if margin_risk <= cap + tol:
        return MarginRiskVerdict(
            "ok", lev, margin_risk,
            f"MARGIN_RISK: {margin_risk:.1f}% OK "
            f"(SL {sl_dist_pct:.1f}% × {lev}x) ≤ {cap:.1f}% cap")

    over = (f"MARGIN_RISK: {margin_risk:.1f}% "
            f"(SL {sl_dist_pct:.1f}% × {lev}x) exceeds {cap:.1f}% cap")
    if not may_reduce or sl_dist_pct <= 0:
        return MarginRiskVerdict("refused", None, margin_risk, over)

    floor = max(1, int(min_leverage))
    safe_lev = max(floor, int(cap / sl_dist_pct))
    new_margin = sl_dist_pct * safe_lev
    if new_margin > cap + tol:
        # The floor bit. Refusing is the same answer the flag-off arm gives,
        # and it is the only honest one: there is no permitted leverage at
        # which this stop distance fits the cap.
        return MarginRiskVerdict(
            "refused", None, new_margin,
            f"{over} — and the {floor}x floor still leaves "
            f"{new_margin:.1f}%, so no permitted leverage clears it")
    return MarginRiskVerdict(
        "reduced", safe_lev, new_margin,
        f"MARGIN_RISK: reduced leverage {lev}x→{safe_lev}x "
        f"(SL {sl_dist_pct:.1f}% × {safe_lev}x = {new_margin:.1f}% ≤ {cap:.1f}%)")

"""Leverage resolution (pure) — every rule that can only ever REDUCE.

Two things live here, and they share one property: nothing in this module can
raise leverage above what the operator configured.

- **The per-user preference (NB3).** A BYOK live user may pin their own
  standard leverage, but only ever DOWN from the operator's configured default.
- **The margin-risk cap.** `max_margin_risk_pct` bounds SL-distance ×
  leverage, and the leverage it bounds is the one the VENUE is set to. See the
  block above `RISK_CAP_ATTR` for why the sized leverage cannot stand in for
  it, and `margin_risk_verdict` for why a reduction that does not clear the cap
  is a refusal rather than a pass.

Both are here so the executor's two set paths, its sizing path and the risk
gate ask one function: a second copy of a reduce-only rule is a second answer
about how much of the account a stop can take.

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

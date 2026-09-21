"""The live margin bounds, derived from the balance the venue reported.

MICRO_MAX_POSITION_USD, MICRO_MAX_TOTAL_EXPOSURE and PER_USER_MAX_FUNDS_USD are
flat absolute dollars read once at import, so they say the same thing to a $200
account and a $20,000 one: the first is refused a position its balance supports,
the second is held to a ceiling a twentieth of its book. The SIZE was never the
problem — `_evaluate_locked` already sizes off the account
(``sizing_equity * max_position_pct``, then fixed-fractional by stop distance)
and every multiplier around it is tighten-only. It is the CEILING that knows
nothing, and it lands on that sizing at one line::

    if max_position_usd is not None and max_position_usd > 0:
        position_usd = min(position_usd, max_position_usd)

THE RESERVE WAS THE SHARPEST OF THE FOUR, because it reads like a percent and is
not one. ``MIN_RESERVE_PCT = 20.0`` was applied to ``MICRO_MAX_TOTAL_EXPOSURE``
— twenty percent of a CONSTANT — so the "capital buffer" it warns about was a
fraction of a number the operator typed and had no relationship to the money in
the account. A buffer is what is left in the account after the trade, or it is
not a buffer.

THREE BASES, AND THE UNREAD ONE NEVER WIDENS
--------------------------------------------
``flat``     the feature is off. The bounds are today's constants, byte for byte.
``unread``   the feature is on and nobody read a balance. Still today's
             constants: an unreadable balance may not be the reason a bound
             grows. It may not be the reason one SHRINKS either — a size nobody
             chose, from evidence nobody has, is `user_sizing`'s recorded
             argument one module over.
``balance``  a figure was read. Each bound is ``min(available x pct, ceiling)``.

A READ ``0.0`` IS A MEASUREMENT and takes the ``balance`` branch: fully-deployed
capital and an empty wallet are real states, and both honestly support a bound
of ``$0.00`` that refuses the next order by name. Folding that into ``unread``
would be the defect this whole repository is built around, at the one reader
where it decides whether money moves. `margin_clamp.read_money_field` is the
reading, because it tests PRESENCE rather than truthiness and already refuses a
NaN, an infinity and an empty string as money.

THE CEILINGS DEFAULT TO TODAY'S FIGURES, WHICH IS THE WHOLE SAFETY ARGUMENT
---------------------------------------------------------------------------
With the feature armed and no ceiling raised, a balance can only TIGHTEN a
bound: a $50 account gets a bound its balance supports, and a $20,000 one is
still held at the operator's $100. GROWTH needs a second number the operator
types deliberately (``SIZE_BOUNDS_MAX_POSITION_USD`` /
``SIZE_BOUNDS_MAX_TOTAL_USD``). So arming the flag alone cannot raise the size
of any position on any account, and the card prints which bound bit — the
operator sees the figure before they arm the growth half.

NO ARTIFICIAL FLOOR, and that is a refusal rather than an omission. A dust
balance honestly supports a dust bound; inventing a floor under it publishes a
cap nobody's balance supports, and the venue's own minimum notional refuses such
an order anyway. The bound says what the account can carry, and what the account
can carry is sometimes very little.

THIS MODULE REFUSES NOTHING, and that is deliberate too.
`margin_clamp.clamp_to_free_margin` already refuses an unreadable free margin
with its own two reasons ("unreadable" / "insufficient"), one layer below every
caller here. A second refusal on the same fact would be a second answer to one
question, which is the shape this repository keeps finding in maps and gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

from bot.core.margin_clamp import read_money_field

#: The bases, in the order a reader should think about them. `balance` is the
#: only one derived from an account; the other two are the operator's constants
#: under two different reasons, and a card that printed one word for both would
#: be telling an operator their bounds were measured when they were typed.
BASES = ("flat", "unread", "balance")


@dataclass(frozen=True)
class SizeBounds:
    """What one account may commit, and what produced the figures.

    ``reserve_usd`` is ``None`` on the ``flat`` and ``unread`` bases — there is
    no balance to reserve a share OF, and quoting a share of the CEILING is the
    defect this module exists to remove. The caller keeps whatever buffer
    warning it had; it does not get a fabricated one.
    """

    per_trade_usd: float
    total_usd: float
    reserve_usd: Optional[float]
    basis: str
    available_usd: Optional[float]
    why: str


def _operator_bounds(flat_trade: float, flat_total: float,
                     basis: str, why: str) -> SizeBounds:
    """The operator's own figures, under one of the two reasons for them.

    Three branches returned a byte-identical construction differing only in
    `basis` and `why` -- the second-copy shape, inside the leaf whose subject
    is that two answers are two answers. One construction here means an added
    field cannot reach two of the three and miss the last.
    """
    return SizeBounds(
        per_trade_usd=flat_trade,
        total_usd=flat_total,
        # No balance was read, so there is no balance to reserve a share OF.
        # Quoting a share of the CEILING is the defect this module replaces.
        reserve_usd=None,
        basis=basis,
        available_usd=None,
        why=why,
    )


def _pos_float(value: Any, fallback: float) -> float:
    """A positive float from config, or ``fallback``.

    Config is `_env_float_bounded` on every field this reads, so a bad value
    cannot normally arrive — but this leaf is handed a config object by four
    callers and a stand-in by the tests, and a bound that raises would take
    down the preflight that protects live money.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return fallback
    if f != f or f in (float("inf"), float("-inf")) or f <= 0:
        return fallback
    return f


def _pct(value: Any, fallback: float) -> float:
    """A percentage in ``(0, 100]`` from config, or ``fallback``."""
    f = _pos_float(value, fallback)
    return min(f, 100.0)


def available_from_balance(payload: Any) -> Optional[float]:
    """The AVAILABLE balance the venue reported, or ``None``.

    "Available" rather than equity, because a bound on new margin is a claim
    about what can still be committed and equity counts capital already at
    work. `fetch_balance` publishes both; `read_money_field` answers ``None``
    for a key the venue did not send, which is how a USDC-margined venue used
    to produce a confident ``0.0`` beside a correct total.
    """
    return read_money_field(payload, "free")


def resolve(
    available_usd: Optional[float],
    cfg: Any,
    *,
    enabled: Optional[bool] = None,
    flat_per_trade: Optional[float] = None,
    flat_total: Optional[float] = None,
) -> SizeBounds:
    """The bounds for one account.

    ``cfg`` is ``CONFIG.execution`` (or a stand-in carrying the same names).
    ``enabled`` overrides the config flag for a caller that has already read it.

    ``flat_per_trade`` / ``flat_total`` let a caller supply the flat figures it
    is already holding. `live_executor` reads MICRO_MAX_POSITION_USD and
    MICRO_MAX_TOTAL_EXPOSURE once at import and a long list of guards patches
    those module constants; re-reading config here would mean two answers to
    "what is the flat cap" and the patched one would stop binding.
    """
    flat_trade = _pos_float(
        flat_per_trade if flat_per_trade is not None
        else getattr(cfg, "max_live_position_usd", None), 100.0)
    flat_total = _pos_float(
        flat_total if flat_total is not None
        else getattr(cfg, "max_live_total_exposure_usd", None), 500.0)

    on = getattr(cfg, "balance_relative_bounds_enabled", False) if enabled is None else enabled
    if not on:
        return _operator_bounds(flat_trade, flat_total,
                                "flat", "balance-relative bounds are off")

    if available_usd is None:
        # Never the reason a bound moves, in either direction. The order is
        # refused one layer down by `clamp_to_free_margin` if the margin really
        # cannot be read; what this says is only that the bounds are the
        # operator's figures and were not measured.
        return _operator_bounds(flat_trade, flat_total,
                                "unread", "available balance was not read")

    avail = float(available_usd)
    if avail != avail or avail in (float("inf"), float("-inf")) or avail < 0:
        # Not money. A negative available margin is not a reading this can
        # divide an account by, and it is not zero either.
        return _operator_bounds(flat_trade, flat_total,
                                "unread", "available balance was not a figure")

    trade_pct = _pct(getattr(cfg, "balance_bounds_per_trade_pct", None), 10.0)
    total_pct = _pct(getattr(cfg, "balance_bounds_total_pct", None), 50.0)
    reserve_pct = _pct(getattr(cfg, "balance_bounds_reserve_pct", None), 20.0)

    # The ceiling is the GROWTH half and defaults to the caller's flat figure,
    # so arming the flag alone can only tighten. `max(..., flat)` is not a
    # floor on the BOUND -- it is a floor on the CEILING, so a config default
    # left at $100 cannot silently re-tighten a caller whose own flat cap was
    # raised (or patched by a guard) to something larger.
    ceil_trade = max(
        _pos_float(getattr(cfg, "balance_bounds_max_position_usd", None), flat_trade),
        flat_trade)
    ceil_total = max(
        _pos_float(getattr(cfg, "balance_bounds_max_total_usd", None), flat_total),
        flat_total)

    per_trade = min(avail * (trade_pct / 100.0), ceil_trade)
    total = min(avail * (total_pct / 100.0), ceil_total)
    reserve = avail * (reserve_pct / 100.0)

    # Which bound bit, per figure. "$41.20 per trade" alone sends an operator
    # to raise a percentage that was never binding; naming the ceiling sends
    # them to the number that was.
    trade_by = "balance" if avail * (trade_pct / 100.0) <= ceil_trade else "ceiling"
    total_by = "balance" if avail * (total_pct / 100.0) <= ceil_total else "ceiling"
    why = (
        f"${avail:,.2f} available: "
        f"per-trade {trade_pct:g}% ({trade_by}), total {total_pct:g}% ({total_by})"
    )

    return SizeBounds(
        per_trade_usd=round(per_trade, 2),
        total_usd=round(total, 2),
        reserve_usd=round(reserve, 2),
        basis="balance",
        available_usd=round(avail, 2),
        why=why,
    )


# ── The preflight's own refusals, over ONE set of bounds ────────────────────
#
# `LiveExecutor._preflight_check` used to spell these comparisons inline. The
# bounds shadow (bot/core/bounds_shadow.py) asks the same question of the
# bounds that WOULD be in force with the flag on, and a second copy of the
# comparison is a second answer about what refuses an order -- so the reading
# lives here and both callers hand it their bounds. The sentences are the
# preflight's, byte for byte: every guard that pins them is a guard over this
# function now.

class BoundsVerdict(NamedTuple):
    """What one set of bounds says about one order.

    ``state`` is ``ok``, ``per_trade`` (the order is over the per-trade
    bound), ``exposure_unread`` / ``exposure_partial`` (the total cannot be
    enforced because the book's committed margin was not read, wholly or in
    part), or ``total`` (the order would take the book over the total bound).
    ``sentence`` is the preflight's refusal, None for ``ok``. ``exposure_total``
    is the book's READ committed margin, set only when the verdict could read
    it -- ``ok`` and ``total`` -- and None otherwise, never 0.0.
    """

    state: str
    sentence: Optional[str]
    exposure_total: Optional[float]


def bounds_verdict(size_usd: float, bounds: SizeBounds, exposure: Any) -> BoundsVerdict:
    """The preflight's refusals in the preflight's own ORDER: the per-trade
    bound first (it needs no book), then the book's readability, then the
    total. ``exposure`` is a `live_executor.CommittedMargin` (duck-typed:
    ``total``, ``complete``, ``unread``, ``counted``, ``scored``)."""
    if size_usd > bounds.per_trade_usd:
        return BoundsVerdict("per_trade", (
            f"Position size ${size_usd:.2f} exceeds the "
            f"${bounds.per_trade_usd:,.2f} per-trade margin limit "
            f"({bounds.why})"), None)
    names = ", ".join(exposure.unread)
    them = "them" if len(exposure.unread) > 1 else "it"
    if exposure.total is None:
        # Nothing in a non-empty book could be read. A different fact from
        # the partial case below, so it gets its own sentence: there is no
        # floor to quote, and quoting one would be a figure nobody measured
        # printed as the account's committed capital.
        return BoundsVerdict("exposure_unread", (
            f"Total exposure cannot be measured: the venue stated no "
            f"margin for any of the {exposure.counted} open position(s) "
            f"on this account ({names}). Close {them} with "
            f"/liveclose, or the ${bounds.total_usd:,.2f} cap "
            f"cannot be enforced."), None)
    total = float(exposure.total)
    if not exposure.complete:
        # A margin nobody read is not a margin of zero, and this is the one
        # reader where that is a HARD CAP rather than a card. An adopted
        # position the venue stated no margin for carries `cost_usd == 0.0`
        # and names "margin" in `adoption_unread`, so the raw sum read that
        # capital as free. Refusing is fail-closed and it NAMES the position,
        # because "exposure cannot be measured" does not say what to go and
        # change - and adoption never re-reads a position it already tracks,
        # so the figure never arrives on its own.
        return BoundsVerdict("exposure_partial", (
            f"Total exposure cannot be measured: the venue never stated "
            f"a margin for {names}, so the ${total:,.2f} "
            f"on record over {exposure.scored} of {exposure.counted} "
            f"position(s) is a floor and not the total. Close "
            f"{them} with /liveclose, or the "
            f"${bounds.total_usd:,.2f} cap cannot be enforced."), None)
    if total + size_usd > bounds.total_usd:
        return BoundsVerdict("total", (
            f"Total exposure ${total + size_usd:,.2f} would exceed "
            f"the ${bounds.total_usd:,.2f} total margin limit "
            f"({bounds.why})"), total)
    return BoundsVerdict("ok", None, total)


class ReserveRead(NamedTuple):
    """The capital buffer after one order, over one set of bounds.

    ``state`` is ``ok`` (the buffer holds), ``warn`` (something is left and it
    is under the reserve) or ``spent`` (nothing is left). ``basis`` says what
    the 20% was OF: the available balance when one was read, the configured
    total limit when none was -- the distinction the bounds slice records as
    the sharpest of its four.
    """

    state: str
    remaining: float
    needed: float
    basis: str


def reserve_read(size_usd: float, bounds: SizeBounds, exposure_total: float) -> ReserveRead:
    if bounds.reserve_usd is not None and bounds.available_usd is not None:
        remaining = float(bounds.available_usd) - exposure_total - size_usd
        needed = float(bounds.reserve_usd)
        basis = "available balance"
    else:
        remaining = bounds.total_usd - exposure_total - size_usd
        needed = bounds.total_usd * 0.20
        basis = "the configured total limit"
    if remaining <= 0:
        state = "spent"
    elif remaining < needed:
        state = "warn"
    else:
        state = "ok"
    return ReserveRead(state, remaining, needed, basis)

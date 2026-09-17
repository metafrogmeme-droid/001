"""What one round trip costs, and the reward:risk that survives it.

**A FEE RATE IS PER LEG, AND A LEG IS MAKER OR TAKER.** That one rule had four
names and seven spellings. ``CONFIG.risk.commission_pct`` is the cards' rate
("the DEFAULT rate used in risk calcs (taker)", says its own config comment);
``taker_fee_pct`` and ``maker_fee_pct`` are the executor's; and the LEG RULE —
``maker_fee_pct if is_limit_entry else taker_fee_pct`` — is written out six
times in ``live_executor.py`` plus once more in prose, at
``pos.order_type = "limit"  # limit fill = maker fee rate``. Every card charged
``commission_pct`` on both legs and no card knew the rule at all.

**Driven on the live ARB/USDT ticket of 2026-09-17** — a LONG limit at $0.4530,
stop 0.1846% below, target 0.5785% above, $33.84 of margin at 5x — the resting
card printed ``R:R at fill: 3.1`` and, two lines under it, ``Est. fees
$0.2030``. Neither number was right and they were wrong in opposite directions:

====================================  =======
what                                  value
====================================  =======
``live_rr`` — price geometry only      3.13
net of what the venue charges          1.88
net if both legs were taker            1.50
card's ``Est. fees`` (taker x2)       $0.2030
the fee a maker entry really pays     $0.1354
====================================  =======

The round trip is **0.43x the stop distance**: 43% of the risk budget is spent
before the market moves. The co-pilot said both of these about that ticket, in
the same block, four lines apart --

    * Stop is only 0.18% away -- likely to be wicked out by noise.
    * Strong reward:risk (3.13).

-- and the first sentence is the reason the second is false. Two checks over
the same two distances, neither knowing the other exists.

**BOTH LIVE EXITS ARE TAKER, and that is a reading rather than an assumption.**
``_place_sl_tp`` places the take-profit as ``create_order(type="market", ...)``
with trigger params, exactly like the stop: a plan order that fires as a market
fill. ``CONFIG.limit_orders.maker_take_profit_enabled`` is deliberately NOT
read here -- its own config comment says "this flag alone does NOT alter live
order placement", so it is a BACKTEST model knob, and pricing a live card off a
simulation switch would be the second-copy defect wearing a config flag.

**SLIPPAGE IS NOT IN THIS READING.** A fee is a rate the venue charges and the
executor books; slippage is a model. Folding a modelled cost into a measured
one publishes an estimate with the authority of a charge, so the fee-aware
entry gate keeps its own ``fee_aware_slippage_pct`` term and this reading names
the omission instead. It leans the optimistic way and says so, which is the
honest half: the real net ratio is lower than the one printed here, never
higher.

**AN UNSTATED ORDER TYPE IS TAKER.** That is the venue's default rate and the
one direction that cannot flatter a ticket -- and it is what every existing
reader already did (``getattr(pos, 'order_type', '')`` compared against
``'limit'``). It is a stated choice, not a fallback nobody chose.

**The entry fee is charged here**, because every caller is asking about a trade
that has not been placed. An OPEN position's entry fee is SUNK, so its
reward:risk from the mark carries the two exit legs and not the entry one --
a different term, with no caller today. It arrives with the code that reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bot.config import CONFIG
from bot.core.position_telemetry import live_rr, price_on_record

#: A leg that RESTS on the book and is filled by somebody else crossing it.
MAKER = "maker"
#: A leg that crosses the book. Every market order, and every plan/trigger
#: order when it fires.
TAKER = "taker"

#: Every LIVE exit is a taker leg -- the stop and the take-profit are both
#: placed as ``type="market"`` trigger orders. Named rather than inlined so the
#: day one of them starts resting, this constant is the one thing to change.
EXIT_LIQUIDITY = TAKER


def leg_rate_pct(liquidity: str) -> float:
    """The venue's percentage rate for one leg.

    RAISES on a word it cannot place. A quiet fallback here would be the
    defect this module exists to remove: a rate nobody chose, printed with the
    authority of one the venue quoted.
    """
    if liquidity == MAKER:
        return float(CONFIG.risk.maker_fee_pct)
    if liquidity == TAKER:
        return float(CONFIG.risk.taker_fee_pct)
    raise ValueError(f"not a liquidity side this venue charges: {liquidity!r}")


def entry_liquidity(order_type: object) -> str:
    """Which side of the book the ENTRY leg is, from the order's own type.

    A resting limit entry is filled by somebody crossing to it, so it is
    MAKER. Anything else -- a market order, an absent field, a word this
    build does not know -- is TAKER, for the reason the header gives.
    """
    return MAKER if str(order_type or "").strip().lower() == "limit" else TAKER


def entry_rate_pct(order_type: object) -> float:
    """The percentage rate the ENTRY leg pays."""
    return leg_rate_pct(entry_liquidity(order_type))


def exit_rate_pct() -> float:
    """The percentage rate EITHER exit leg pays."""
    return leg_rate_pct(EXIT_LIQUIDITY)


def round_trip_pct(order_type: object = None) -> float:
    """One position's round trip -- its entry leg plus one exit -- in percent.

    An approximation in one respect, stated: both legs are charged at the
    ENTRY price, where the venue charges each on its own notional. Over the
    sub-percent distances this is used for the difference is a fraction of a
    basis point, and a caller that needs the exact figure asks
    `net_reward_risk`, which prices each leg where it fills.
    """
    return entry_rate_pct(order_type) + exit_rate_pct()


def taker_legs_pct(legs: int) -> float:
    """``legs`` taker legs, in percent -- a hedge's four, or a single two.

    The delta-neutral pair's "4 taker legs @ ~0.06%" was a hand-written
    ``0.24`` that stopped following ``TAKER_FEE_PCT`` the moment anybody set
    it. Same rule, more legs.
    """
    if legs < 0:
        raise ValueError(f"a round trip cannot have {legs} legs")
    return legs * leg_rate_pct(TAKER)


def fee_usd(notional_usd: float, rate_pct: float) -> float:
    """What one leg costs in dollars, so no caller spells the ``/ 100``."""
    return float(notional_usd) * float(rate_pct) / 100.0


@dataclass(frozen=True)
class NetRewardRisk:
    """One ticket's reward:risk, before and after the fees it will pay.

    ``net`` is ``None`` for EXACTLY ONE fact -- the target does not clear the
    round trip, so hitting it loses money -- and `fee_losing` is the name to
    read rather than the ``is None`` check. An unreadable leg never reaches
    here: `net_reward_risk` answers ``None`` for that, so the two absences
    cannot arrive as one value.
    """

    #: The price ratio, exactly as `live_rr` answers it.
    gross: float
    #: The ratio after the entry fee and the fee at whichever level is hit.
    #: ``None`` when the target does not clear the round trip.
    net: Optional[float]
    #: The round trip on the RISK leg (entry + the fee paid at the stop), as a
    #: percentage of entry. The risk leg, because the sentence a reader needs
    #: is about what the stop has to cover.
    cost_pct: float
    #: That round trip divided by the distance to the stop. ``0.43`` means 43%
    #: of the risk budget is spent before the market moves.
    cost_over_stop: float
    #: The two rates used, so a card can name them rather than restate them.
    entry_rate_pct: float
    exit_rate_pct: float

    @property
    def fee_losing(self) -> bool:
        """The target, if it is hit, is a loss."""
        return self.net is None


def net_reward_risk(entry: object, stop: object, target: object, *,
                    order_type: object = None) -> Optional[NetRewardRisk]:
    """Reward-to-risk on a ticket that has not been placed, net of its fees.

    ``None`` when any leg could not be read, which is `live_rr`'s own rule and
    is read FROM it rather than restated -- a second copy of the gross ratio
    beside the net one is two answers about the same two distances, which is
    the defect one layer down.

    A net reward of exactly zero is a MEASURED ``0.0`` and is returned as one,
    the distinction `live_rr` already draws: the target pays the fees and
    nothing more. Only a target that does not reach the fees answers ``None``.
    """
    gross = live_rr(entry, stop, target)
    if gross is None:
        return None
    e = price_on_record(entry)
    s = price_on_record(stop)
    t = price_on_record(target)
    assert e is not None and s is not None and t is not None  # live_rr said so
    risk_px = abs(e - s)

    er = entry_rate_pct(order_type)
    xr = exit_rate_pct()
    entry_fee = fee_usd(e, er)
    stop_fee = fee_usd(s, xr)
    target_fee = fee_usd(t, xr)

    reward = abs(t - e) - entry_fee - target_fee
    risk = risk_px + entry_fee + stop_fee
    return NetRewardRisk(
        gross=gross,
        net=(reward / risk) if reward >= 0 else None,
        cost_pct=(entry_fee + stop_fee) / e * 100.0,
        cost_over_stop=(entry_fee + stop_fee) / risk_px,
        entry_rate_pct=er,
        exit_rate_pct=xr,
    )

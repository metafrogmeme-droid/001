"""The borrow curve, the truncation tolerance, and the payoff bound.

Hyperliquid's portfolio-margin borrow APY for stablecoins is

    0.05 + 4.75 * max(0, utilization - 0.8)

compounded continuously, and indexed hourly. Utilization is
borrowed / supplied, recomputed from the raw totals. The precompile
publishes utilization and the yearly rate as integer basis points,
floored, so a quote can sit under the rate from the raw totals by
almost a basis point of utilization times the slope, plus one more
basis point from flooring the rate itself.

The funding crossover is that formula inverted. It is not a stored
utilization. A funding APR quoted as a continuous rate matches the
compounded borrow APY at exp(funding) - 1, and the utilization is
whatever the curve says that APY sits at.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Optional, Sequence

BASE_RATE = Decimal("0.05")
KINK = Decimal("0.8")
SLOPE = Decimal("4.75")
BPS = Decimal(10000)
HOURS_PER_YEAR = Decimal(8760)
TRAILING_HOURS = 6
# The curve's own ceiling: 100% utilization is 100% APY.
RATE_CAP = Decimal(1)
# Design choice, not a protocol parameter: a cap-hit hour pays while
# borrowed is at least this share of the published borrow cap.
CAP_HIT_TRIGGER = Decimal("0.98")
# 90% utilization on the curve. 0.05 + 4.75 * 0.10 = 0.525.
SHOCK_UTILIZATION = Decimal("0.90")
SHOCK_RATE = BASE_RATE + SLOPE * (SHOCK_UTILIZATION - KINK)
# HyperCore token amounts in the 0x812 words are raw integers.
# On 23 Oct 2026 the mainnet USDC word, divided by 1e8, matched the
# info API's human amount to the accrual between the two reads.
RAW_PER_TOKEN = 10**8


def truncation_tolerance() -> Decimal:
    """How far a floored bps quote may sit from the rate on the raw totals.

    One floored basis point of utilization moves the borrow rate by
    ``SLOPE`` basis points above the kink. The rate word is floored by
    another basis point. A tolerance of one basis point rejects a quote
    the precompile actually produced.
    """
    return (SLOPE + Decimal(1)) / BPS


def utilization_of(supplied: Optional[int], borrowed: Optional[int]) -> Optional[Decimal]:
    """borrowed / supplied from the raw integers.

    Zero supplied is unreadable, not a utilization of zero. A negative
    word is unreadable. The published utilization bps is not read here:
    it is the same ratio, floored.
    """
    if type(supplied) is not int or type(borrowed) is not int:
        return None
    if supplied <= 0 or borrowed < 0:
        return None
    return Decimal(borrowed) / Decimal(supplied)


def truncated_utilization_bps(supplied: Optional[int], borrowed: Optional[int]) -> Optional[int]:
    """The integer bps the precompile stores: the ratio, floored."""
    if type(supplied) is not int or type(borrowed) is not int:
        return None
    if supplied <= 0 or borrowed < 0:
        return None
    return (borrowed * 10000) // supplied


def borrow_apy(utilization: Optional[Decimal]) -> Optional[Decimal]:
    """The curve at ``utilization``, capped at 100% APY.

    None stays None. A measured utilization of zero is the base rate,
    which is what the curve says below the kink, not a stand-in for a
    missing read.
    """
    if utilization is None or not isinstance(utilization, Decimal):
        return None
    if utilization < 0:
        return None
    rate = BASE_RATE
    if utilization > KINK:
        rate = BASE_RATE + SLOPE * (utilization - KINK)
    if rate > RATE_CAP:
        return RATE_CAP
    return rate


def quoted_rate_matches(utilization: Optional[Decimal], quoted: Optional[Decimal]) -> bool:
    """Whether a quoted rate agrees with the curve on the raw ratio.

    ``quoted`` is the rate itself (1 = 100% APY), not basis points. A gap
    of one basis point is inside the truncation the precompile actually
    produces, so the tolerance is wider than that and no wider.
    """
    if quoted is None or not isinstance(quoted, Decimal) or quoted < 0:
        return False
    expected = borrow_apy(utilization)
    if expected is None:
        return False
    return abs(expected - quoted) <= truncation_tolerance()


def floor_rate_bps(rate: Optional[Decimal]) -> Optional[int]:
    """The quote word: the curve rate, floored to a basis point."""
    if rate is None or not isinstance(rate, Decimal) or rate < 0:
        return None
    return int((rate * BPS).to_integral_value(rounding=ROUND_DOWN))


def quote_matches(utilization: Optional[Decimal], rate_bps: Optional[int]) -> bool:
    """Whether a floored bps quote agrees with the curve on the raw ratio."""
    if type(rate_bps) is not int or rate_bps < 0:
        return False
    return quoted_rate_matches(utilization, Decimal(rate_bps) / BPS)


def borrow_apy_matching_funding(funding_apr: Optional[Decimal]) -> Optional[Decimal]:
    """The compounded borrow APY that matches a continuous funding APR.

    The borrow quote is an APY compounded continuously. A funding rate
    quoted as a continuous APR is the same cost at exp(funding) - 1.
    """
    if funding_apr is None or not isinstance(funding_apr, Decimal):
        return None
    if funding_apr < 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return ctx.exp(funding_apr) - Decimal(1)


def utilization_for_borrow_apy(apy: Optional[Decimal]) -> Optional[Decimal]:
    """Invert the curve. At or below the base rate, the kink itself."""
    if apy is None or not isinstance(apy, Decimal):
        return None
    if apy < BASE_RATE:
        return None
    if apy == BASE_RATE:
        return KINK
    if apy > RATE_CAP:
        return None
    return KINK + (apy - BASE_RATE) / SLOPE


def crossover_utilization(funding_apr: Optional[Decimal]) -> Optional[Decimal]:
    """Utilization where the curve meets funding, derived, not stored."""
    return utilization_for_borrow_apy(borrow_apy_matching_funding(funding_apr))


def carry_strike(funding_apr: Optional[Decimal], delta: Optional[Decimal]) -> Optional[Decimal]:
    """Carry-flip strike: the like-for-like borrow APY plus a margin.

    A missing funding rate or a missing margin is an unreadable strike.
    Zero is a real margin and stays zero.
    """
    matched = borrow_apy_matching_funding(funding_apr)
    if matched is None or delta is None or not isinstance(delta, Decimal):
        return None
    if delta < 0:
        return None
    strike = matched + delta
    if strike > RATE_CAP:
        return RATE_CAP
    return strike


def choose_settlement(
    precompile_rate: Optional[Decimal],
    feed_rate: Optional[Decimal],
) -> dict:
    """Which rate an hour settles at.

    Each argument is the curve's rate for a source that already passed
    the cross-check, or None when that source is not available. When
    both are available and they differ by more than the truncation
    tolerance, the hour settles at the lower rate. When neither is
    available the hour pays 0, and the rate stays None: paying 0 is the
    rule for a missing hour, not a measurement that the rate was 0%.
    """
    if precompile_rate is not None and not isinstance(precompile_rate, Decimal):
        precompile_rate = None
    if feed_rate is not None and not isinstance(feed_rate, Decimal):
        feed_rate = None
    if precompile_rate is None and feed_rate is None:
        return {"rate": None, "source": "none", "payout_zero": True}
    if precompile_rate is None:
        return {"rate": feed_rate, "source": "signed_feed", "payout_zero": False}
    if feed_rate is None:
        return {"rate": precompile_rate, "source": "precompile", "payout_zero": False}
    if abs(precompile_rate - feed_rate) <= truncation_tolerance():
        return {"rate": precompile_rate, "source": "precompile", "payout_zero": False}
    lower = precompile_rate if precompile_rate < feed_rate else feed_rate
    return {"rate": lower, "source": "lower_of", "payout_zero": False}


def trailing_mean(rates: Sequence[Optional[Decimal]]) -> Optional[Decimal]:
    """The mean of the last six settled rates.

    A window shorter than six hours, or a window with a missing hour,
    is unreadable. A missing hour is not a 0% rate in the average.
    """
    if len(rates) < TRAILING_HOURS:
        return None
    window = list(rates[-TRAILING_HOURS:])
    total = Decimal(0)
    for item in window:
        if not isinstance(item, Decimal):
            return None
        total += item
    return total / Decimal(TRAILING_HOURS)


def hour_payoff(
    notional: Optional[int],
    mean: Optional[Decimal],
    strike: Optional[Decimal],
) -> Optional[int]:
    """Notional times max(0, mean - strike) / 8760, in token units.

    Integer token units, rounded down. None when the mean or the strike
    was not read: the caller pays 0 for that hour and says why. A mean
    at or under the strike is a measured payoff of 0.
    """
    if type(notional) is not int or notional < 0:
        return None
    if mean is None or strike is None:
        return None
    if not isinstance(mean, Decimal) or not isinstance(strike, Decimal):
        return None
    gap = mean - strike
    if gap <= 0:
        return 0
    return int((Decimal(notional) * gap) / HOURS_PER_YEAR)


def collateral_for(
    notional: Optional[int],
    strike: Optional[Decimal],
    hours: Optional[int],
) -> Optional[int]:
    """Worst-case payoff, rounded up by one token unit when there is a remainder.

    Max payoff is notional * (1 - strike) * hours / 8760 because the
    curve tops at 100% APY. A carry-flip series passes the lowest strike
    it can print, so the deposit still covers the bound.
    """
    if type(notional) is not int or notional < 0:
        return None
    if type(hours) is not int or hours < 0:
        return None
    if strike is None or not isinstance(strike, Decimal):
        return None
    if strike < 0 or strike > RATE_CAP:
        return None
    if hours == 0 or notional == 0 or strike == RATE_CAP:
        return 0
    span = (Decimal(1) - strike) * Decimal(hours)
    raw = Decimal(notional) * span / HOURS_PER_YEAR
    whole = int(raw)
    if Decimal(whole) < raw:
        return whole + 1
    return whole


def cap_hit_pays(borrowed: Optional[int], cap: Optional[int]) -> Optional[bool]:
    """Whether this hour's borrowed amount is at the cap-hit trigger.

    None when either input was not read. A borrowed amount of zero is a
    real reading and does not pay.
    """
    if type(borrowed) is not int or type(cap) is not int:
        return None
    if borrowed < 0 or cap <= 0:
        return None
    return Decimal(borrowed) >= CAP_HIT_TRIGGER * Decimal(cap)


def rate_shock_loss_24h(borrows: Optional[Decimal]) -> Optional[Decimal]:
    """Interest on ``borrows`` for 24 hours at the 90% utilization rate.

    This is a dollar amount. Callers that are not the admin view leave
    it out. None when borrows were not read. A measured zero stays zero.
    """
    if borrows is None or not isinstance(borrows, Decimal):
        return None
    if borrows < 0:
        return None
    return borrows * SHOCK_RATE * Decimal(24) / HOURS_PER_YEAR


def replay_withdrawal(
    supplied: Optional[int],
    borrowed: Optional[int],
    withdraw: Optional[int],
) -> dict:
    """Utilization and borrow APY after removing ``withdraw`` from supply.

    All three integers are the same unit. The result carries the rate
    and the utilization, not the withdrawal size.
    """
    if type(supplied) is not int or type(borrowed) is not int or type(withdraw) is not int:
        return {"readable": False, "reason": "unreadable"}
    if supplied <= 0 or borrowed < 0 or withdraw < 0:
        return {"readable": False, "reason": "unreadable"}
    if withdraw >= supplied:
        return {"readable": False, "reason": "withdrawal_exceeds_supply"}
    util = utilization_of(supplied - withdraw, borrowed)
    rate = borrow_apy(util)
    if util is None or rate is None:
        return {"readable": False, "reason": "unreadable"}
    return {"readable": True, "utilization": util, "borrow_apy": rate}


KIND_CAP = 0
KIND_SPREAD = 1
KIND_DIGITAL = 2
DEFAULT_SPREAD_CEIL = Decimal("0.40")
CAP_CEIL = Decimal("1")
GAP_LIMIT_HOURS = 2
UNFREEZE_GOOD_HOURS = 2
UNFREEZE_WINDOW_HOURS = 24
MODEL_VER = "HRCModel.v1"


def ceil_for(kind: int, ceil: Optional[Decimal]) -> Optional[Decimal]:
    """The ceiling a series collateralizes.

    A cap is 100%. A spread with no ceiling uses 40%. A digital has no
    ceiling. A missing spread ceiling is the default, not zero.
    """
    if kind == KIND_CAP:
        return CAP_CEIL
    if kind == KIND_SPREAD:
        if ceil is None:
            return DEFAULT_SPREAD_CEIL
        if not isinstance(ceil, Decimal) or ceil <= 0:
            return None
        return ceil
    return None


def accrual_per_unit(
    kind: int,
    mean: Optional[Decimal],
    strike: Optional[Decimal],
    ceil: Optional[Decimal] = None,
    digital: Optional[Decimal] = None,
) -> Optional[Decimal]:
    """Hourly accrual per 1 of notional.

    Cap: max(0, mean − strike) / 8760. Spread: max(0, min(mean, ceiling) −
    strike) / 8760. Digital: D when the mean is strictly above the strike,
    else a measured 0. None when the mean or the strike was not read. A
    mean sitting on the strike is a measured 0, including the digital.
    """
    if kind not in (KIND_CAP, KIND_SPREAD, KIND_DIGITAL):
        return None
    if mean is None or strike is None:
        return None
    if not isinstance(mean, Decimal) or not isinstance(strike, Decimal):
        return None
    if strike < 0 or mean < 0:
        return None
    if kind == KIND_DIGITAL:
        if digital is None or not isinstance(digital, Decimal) or digital < 0:
            return None
        if mean > strike:
            return digital
        return Decimal(0)
    ceiling = ceil_for(kind, ceil)
    if ceiling is None:
        return None
    capped = mean if mean < ceiling else ceiling
    gap = capped - strike
    if gap <= 0:
        return Decimal(0)
    return gap / HOURS_PER_YEAR


def _round_up(raw: Decimal) -> int:
    whole = int(raw)
    if Decimal(whole) < raw:
        return whole + 1
    return whole


def collateral_for_series(
    kind: int,
    notional: Optional[int],
    strike: Optional[Decimal],
    hours: Optional[int],
    ceil: Optional[Decimal] = None,
    digital: Optional[Decimal] = None,
) -> Optional[int]:
    """N · (C − K) · T / 8760, rounded up. A cap uses C = 1.

    A digital collateralizes N · D · T, because that payoff is not divided
    by 8760. None when an input was not read. A measured zero notional is 0.
    """
    if kind not in (KIND_CAP, KIND_SPREAD, KIND_DIGITAL):
        return None
    if type(notional) is not int or notional < 0:
        return None
    if type(hours) is not int or hours < 0:
        return None
    if strike is None or not isinstance(strike, Decimal) or strike < 0:
        return None
    if hours == 0 or notional == 0:
        return 0
    if kind == KIND_DIGITAL:
        if digital is None or not isinstance(digital, Decimal) or digital < 0:
            return None
        return _round_up(Decimal(notional) * digital * Decimal(hours))
    ceiling = ceil_for(kind, ceil)
    if ceiling is None or ceiling < strike:
        return None
    if ceiling == strike:
        return 0
    raw = Decimal(notional) * (ceiling - strike) * Decimal(hours) / HOURS_PER_YEAR
    return _round_up(raw)


def freeze_reason(
    previous_hour: Optional[int],
    hour: Optional[int],
    formula_ok: Optional[bool],
    model_ok: Optional[bool],
) -> Optional[str]:
    """Why this hour freezes the series, or None when it does not.

    A missing previous hour is the first sample, not a gap. A formula
    failure and a model failure are separate reasons. None is not "no
    freeze" when the hour itself was not read: that returns None only
    alongside an unreadable hour number, and the caller does not sample it.
    """
    if type(hour) is not int or hour < 0:
        return None
    if type(previous_hour) is int and hour > previous_hour + GAP_LIMIT_HOURS:
        return "gap"
    if model_ok is False:
        return "rate_model"
    if formula_ok is False:
        return "formula"
    return None


def can_unfreeze(
    frozen_at: Optional[int],
    now: Optional[int],
    good_hours: Optional[Sequence[int]],
    is_guardian: bool,
) -> bool:
    """Guardian, two good hours immediately after the freeze, inside 24h.

    ``good_hours`` are hour ids whose samples were good. Hours that were
    not read are absent from the sequence. They are not good hours.
    """
    if is_guardian is not True:
        return False
    if type(frozen_at) is not int or type(now) is not int:
        return False
    if now > frozen_at + UNFREEZE_WINDOW_HOURS:
        return False
    if not isinstance(good_hours, (list, tuple)):
        return False
    needed = {frozen_at + 1, frozen_at + 2}
    seen = {item for item in good_hours if type(item) is int}
    return needed <= seen


def target_utilization(strike: Optional[Decimal]) -> Optional[Decimal]:
    """Utilization at which the curve's rate reaches ``strike``.

    None when the strike is above 100% (the curve cannot get there) or
    was not read. A strike at or under the base rate is already met at
    every utilization, and the result is the kink's base, not zero: the
    caller treats a non-positive withdrawal as "already there".
    """
    if strike is None or not isinstance(strike, Decimal):
        return None
    if strike < 0 or strike > RATE_CAP:
        return None
    if strike <= BASE_RATE:
        return KINK
    return KINK + (strike - BASE_RATE) / SLOPE


def supply_to_hold(
    supplied: Optional[Decimal],
    borrowed: Optional[Decimal],
    strike: Optional[Decimal],
) -> dict:
    """Supply that must be withdrawn so the curve's rate stays at ``strike``.

    The amount is tokens, in the same unit as ``supplied``. Zero is a real
    reading: utilization is already high enough. None means the strike
    cannot be reached by withdrawing supply, which is not a withdrawal of
    zero. Holding it for six hours is the caller's window; this function
    does not turn the amount into interest.
    """
    if supplied is None or borrowed is None or not isinstance(supplied, Decimal) or not isinstance(borrowed, Decimal):
        return {"reachable": None, "withdrawal": None, "target_utilization": None}
    if supplied <= 0 or borrowed < 0:
        return {"reachable": None, "withdrawal": None, "target_utilization": None}
    if strike is None or not isinstance(strike, Decimal) or strike < 0:
        return {"reachable": None, "withdrawal": None, "target_utilization": None}
    if strike > RATE_CAP:
        return {"reachable": False, "withdrawal": None, "target_utilization": None}
    target = target_utilization(strike)
    if target is None:
        return {"reachable": False, "withdrawal": None, "target_utilization": None}
    if strike <= BASE_RATE:
        return {"reachable": True, "withdrawal": Decimal(0), "target_utilization": target}
    current = borrowed / supplied
    if current >= target:
        return {"reachable": True, "withdrawal": Decimal(0), "target_utilization": target}
    if borrowed == 0:
        return {"reachable": False, "withdrawal": None, "target_utilization": target}
    withdrawal = supplied - (borrowed / target)
    if withdrawal < 0:
        withdrawal = Decimal(0)
    return {"reachable": True, "withdrawal": withdrawal, "target_utilization": target}


def points_below(level: Optional[Decimal], value: Optional[Decimal]) -> Optional[Decimal]:
    """How many percentage points ``value`` sits under ``level``.

    Negative when value is above the level. None when either was not read.
    """
    if level is None or value is None:
        return None
    if not isinstance(level, Decimal) or not isinstance(value, Decimal):
        return None
    return (level - value) * Decimal(100)

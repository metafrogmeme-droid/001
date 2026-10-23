"""P = ER · exp(−max(0, r_borrow − y_stake) · T_p95 / 365) · (1 − s).

``s`` always carries the base haircut. The restake term is added only when
the asset is restaked. A missing input is None, never a zero price.
"""
from __future__ import annotations

from decimal import Decimal

from bot.t2c.config import S_CAP
from bot.t2c.numbers import as_decimal

YEAR_DAYS = Decimal(365)
LIDO_ASSETS = frozenset({"steth", "wsteth", "lido"})


def stake_yield(asset: str, reading: object = None) -> dict:
    """Lido stake yield is 0 by rule. Any other absent reading stays absent."""
    name = str(asset or "").strip().lower()
    if name in LIDO_ASSETS:
        return {"y_stake": Decimal(0), "source": "lido_rule"}
    number = as_decimal(reading)
    if number is None:
        return {"y_stake": None, "source": "absent"}
    return {"y_stake": number, "source": "reading"}


def slash_haircut(
    t_p95_days: object,
    restaked: bool,
    t_eigen_days: object,
    h_base: object,
    h_restake: object,
) -> Decimal | None:
    """s = min(0.05, (h_base·T_p95 + 1[restaked]·h_restake·T_eigen) / 365)."""
    duration = as_decimal(t_p95_days)
    base = as_decimal(h_base)
    restake = as_decimal(h_restake)
    if duration is None or base is None or restake is None:
        return None
    if duration < 0 or base < 0 or restake < 0:
        return None
    eigen_term = Decimal(0)
    if restaked:
        eigen = as_decimal(t_eigen_days)
        if eigen is None or eigen < 0:
            return None
        eigen_term = restake * eigen
    raw = (base * duration + eigen_term) / YEAR_DAYS
    if raw < 0:
        return None
    if raw > S_CAP:
        return S_CAP
    return raw


def duration_price(
    er: object,
    r_borrow: object,
    y_stake: object,
    t_p95_days: object,
    s: object,
) -> Decimal | None:
    exchange = as_decimal(er)
    borrow = as_decimal(r_borrow)
    stake = as_decimal(y_stake)
    duration = as_decimal(t_p95_days)
    haircut = as_decimal(s)
    if None in (exchange, borrow, stake, duration, haircut):
        return None
    assert exchange is not None and borrow is not None
    assert stake is not None and duration is not None and haircut is not None
    if exchange <= 0 or duration < 0 or haircut < 0 or haircut >= 1:
        return None
    carry = borrow - stake
    if carry < 0:
        carry = Decimal(0)
    exponent = -carry * duration / YEAR_DAYS
    return exchange * exponent.exp() * (Decimal(1) - haircut)

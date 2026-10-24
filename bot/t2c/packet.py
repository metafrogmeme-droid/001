"""T2CPacket. Rates are basis points. P is 1e18. Unread stays null."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from bot.t2c.numbers import as_decimal, json_num
from bot.t2c.price import carry_term, duration_price, slash_haircut

FIELDS = (
    "lst", "sizeBucket", "tSecs", "rBorrowBps", "yBps",
    "ammBpsDisc", "queueBpsDisc", "pE18", "ts",
)
_BPS = Decimal(10000)
_E18 = Decimal(10) ** 18


def _whole(number: Decimal | None) -> str | None:
    if number is None:
        return None
    return str(int(number.to_integral_value(ROUND_HALF_UP)))


def bps_text(value: object) -> str | None:
    """A basis-point figure already in bps, or None when it was not read."""
    return _whole(as_decimal(value))


def rate_bps(rate: object) -> str | None:
    """A fraction (0.05 is 500 bps). None stays None."""
    number = as_decimal(rate)
    if number is None:
        return None
    return _whole(number * _BPS)


def price_e18(price: object) -> str | None:
    number = as_decimal(price)
    if number is None:
        return None
    return _whole(number * _E18)


def build_packet(
    lst: str,
    size_bucket: object,
    t_secs: object,
    r_borrow: object,
    y_stake: object,
    amm_bps: object,
    queue_bps: object,
    er: object,
    s: object,
    ts: object,
) -> dict:
    """One packet. P uses T in days from ``t_secs``. A missing input leaves P null."""
    seconds = as_decimal(t_secs)
    days = None if seconds is None or seconds < 0 else seconds / Decimal(86400)
    size = as_decimal(size_bucket)
    term = carry_term(r_borrow, y_stake)
    price = duration_price(er, r_borrow, y_stake, days, s)
    discount = queue_discount_bps(er, price) if queue_bps is None else as_decimal(queue_bps)
    packet = {
        "lst": lst,
        "sizeBucket": None if size is None else str(int(size)),
        "tSecs": json_num(seconds),
        "rBorrowBps": rate_bps(r_borrow),
        "yBps": rate_bps(y_stake),
        "ammBpsDisc": bps_text(amm_bps),
        "queueBpsDisc": bps_text(discount),
        "pE18": price_e18(price),
        "ts": None if ts is None else str(ts),
        "p": json_num(price),
        "carry": json_num(term["carry"]),
        "carry_source": term["source"],
    }
    return packet


def queue_discount_bps(er: object, price: object) -> Decimal | None:
    """(1 − P/ER) in bps. Unread when either side is missing or ER is not positive."""
    exchange = as_decimal(er)
    model = as_decimal(price)
    if exchange is None or model is None or exchange <= 0 or model < 0:
        return None
    return (Decimal(1) - model / exchange) * _BPS


def haircut_for(t_secs: object, restaked: bool, t_eigen: object, h_base: object, h_restake: object) -> Decimal | None:
    seconds = as_decimal(t_secs)
    if seconds is None:
        return None
    return slash_haircut(seconds / Decimal(86400), restaked, t_eigen, h_base, h_restake)

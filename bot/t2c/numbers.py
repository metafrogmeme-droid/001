"""Decimal helpers. A binary float is not accepted: it would be a second price."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def as_decimal(value: object) -> Decimal | None:
    """A finite Decimal, or None when the value is absent or not a decimal.

    ``False`` is rejected on purpose. ``bool`` is an ``int``, and ``Decimal(True)``
    is ``1``, which would turn a missing flag into a price.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, str)):
        text = str(value).strip() if isinstance(value, str) else value
        if text == "":
            return None
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    if not number.is_finite():
        return None
    return number


def json_num(value: Decimal | None) -> str | None:
    """A decimal as a string, so JSON does not round it into a second answer."""
    if value is None:
        return None
    return format(value, "f")

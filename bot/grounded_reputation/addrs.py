"""Address and 32-byte word normalisation.

Comparisons use the lowercase hex form. A value that is not an address is
None. It is not the zero address.
"""
from __future__ import annotations

ZERO_ADDR = "0x" + ("0" * 40)


def norm_addr(value: object) -> str | None:
    """20-byte hex address, or None when the value is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith(("0x", "0X")):
        hexpart = text[2:]
    else:
        hexpart = text
    if len(hexpart) != 40:
        return None
    try:
        int(hexpart, 16)
    except ValueError:
        return None
    return "0x" + hexpart.lower()


def norm_word(value: object) -> str | None:
    """32-byte hex word, or None when the value is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith(("0x", "0X")):
        hexpart = text[2:]
    else:
        hexpart = text
    if len(hexpart) != 64:
        return None
    try:
        int(hexpart, 16)
    except ValueError:
        return None
    return "0x" + hexpart.lower()


def same_addr(left: object, right: object) -> bool:
    a = norm_addr(left)
    b = norm_addr(right)
    return a is not None and a == b


def as_int(value: object) -> int | None:
    """An int that is not a bool. Strings of digits are accepted. Floats are not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text.startswith(("0x", "0X")):
                return int(text, 16)
            return int(text, 10)
        except ValueError:
            return None
    return None

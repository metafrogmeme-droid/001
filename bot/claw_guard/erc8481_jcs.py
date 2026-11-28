"""RFC 8785 JSON canonicalization for the ERC-8481 digests, and the digest itself.

Only what a report and a request can hold is written: objects, arrays, strings, booleans, null and integers.
ERC-8481 puts every number that can pass 2^53 in a string, so a float, or an integer outside the I-JSON range,
is refused here rather than written in a way another implementation might not reproduce.
"""

from __future__ import annotations

from typing import Any

from bot.hrc.keccak import keccak256

_MAX_SAFE = 2**53 - 1
_ESCAPES = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r", 0x22: '\\"', 0x5C: "\\\\"}


def _string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:  # a lone surrogate has no UTF-8 form
        raise ValueError("string is not valid Unicode") from exc
    out = ['"']
    for ch in value:
        code = ord(ch)
        if code in _ESCAPES:
            out.append(_ESCAPES[code])
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _write(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > _MAX_SAFE:
            raise ValueError("integer outside the I-JSON range; write it as a string")
        out.append(str(value))
    elif isinstance(value, float):
        raise ValueError("floating point numbers are not written; use an integer or a string")
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError("object keys must be strings")
        out.append("{")
        # Keys sort by UTF-16 code unit, which is not the same as by code point above U+FFFF.
        for index, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be", "strict"))):
            if index:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _write(value[key], out)
        out.append("}")
    else:
        raise TypeError(f"cannot canonicalize {type(value).__name__}")


def jcs(value: Any) -> bytes:
    """The canonical bytes of a JSON value."""
    out: list[str] = []
    _write(value, out)
    return "".join(out).encode("utf-8")


def digest(value: Any) -> str:
    """``keccak256`` of the canonical bytes, as 0x-hex."""
    return "0x" + keccak256(jcs(value)).hex()

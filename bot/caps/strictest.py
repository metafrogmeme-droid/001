"""The strictest cap wins.

Each source holds its own proposal. The cap in force is the minimum of
the proposals that exist. No proposals is no cap: None, not zero and not
a number large enough to ignore. A source may replace only its own
proposal, and only with a strictly smaller one. The first proposal is the
lowering from absent.

Every accepted lowering appends a log entry. A refusal appends nothing.
An unknown source is refused. A proposal that was not read, or a negative
one, is refused. Zero is a real proposal from a source that is allowed to
write it; it is not what an absent book means.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Optional

from bot.utils.atomic_write import atomic_write_json

SOURCES = ("CGC", "RXB", "T2C", "HRC")


class CapRefused(Exception):
    """The lowering was not applied. Callers report the class, not the text."""

    pass


def empty_book() -> dict:
    """A book with no proposals. Its effective cap is None."""
    return {"proposals": {}, "log": [], "readable": True}


def effective_cap(book: dict, vault: str) -> Optional[Decimal]:
    """The minimum proposal on ``vault``, or None when nobody has proposed."""
    if not isinstance(book, dict) or book.get("readable") is False:
        return None
    props = book.get("proposals")
    if not isinstance(props, dict):
        return None
    vault_props = props.get(vault)
    if not isinstance(vault_props, dict) or not vault_props:
        return None
    vals = [v for v in vault_props.values() if isinstance(v, Decimal)]
    if not vals:
        return None
    return min(vals)


def apply_cap_down(book: dict, source: str, vault: str, proposed) -> dict:
    """Record one lowering, or raise ``CapRefused`` and leave the book.

    ``proposed`` is a Decimal or an int. A float is unreadable. A bool is
    unreadable. Negative is refused. A number that is not strictly below
    this source's own current proposal is refused. The first proposal has
    no current value, so it is not compared with zero.
    """
    if book.get("readable") is False:
        raise CapRefused()
    if source not in SOURCES:
        raise CapRefused()
    if isinstance(proposed, bool) or proposed is None:
        raise CapRefused()
    if isinstance(proposed, int):
        proposed = Decimal(proposed)
    if not isinstance(proposed, Decimal) or proposed < 0:
        raise CapRefused()
    props = book.setdefault("proposals", {})
    vault_props = props.setdefault(vault, {})
    if not isinstance(vault_props, dict):
        raise CapRefused()
    current = vault_props.get(source)
    if current is not None and (not isinstance(current, Decimal) or proposed >= current):
        raise CapRefused()
    before = effective_cap(book, vault)
    vault_props[source] = proposed
    after = effective_cap(book, vault)
    book.setdefault("log", []).append({
        "source": source,
        "vault": vault,
        "previous": current,
        "proposed": proposed,
        "effective_before": before,
        "effective_after": after,
    })
    return book


def apply_pending(book: dict, updates: list) -> dict:
    """Apply a list of lowerings. The first refusal stops the list."""
    if not isinstance(updates, list):
        raise CapRefused()
    for item in updates:
        if not isinstance(item, dict):
            raise CapRefused()
        source = item.get("source")
        vault = item.get("vault")
        if not isinstance(source, str) or not isinstance(vault, str):
            raise CapRefused()
        apply_cap_down(book, source, vault, item.get("proposed"))
    return book


def _encode(value):
    if isinstance(value, Decimal):
        return {"__decimal__": format(value, "f")}
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _decode(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"__decimal__"} and isinstance(value["__decimal__"], str):
        return Decimal(value["__decimal__"])
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def load_book(path: Optional[str]) -> dict:
    """The ledger on disk.

    A path that was never written is an empty book: no proposals, so no
    cap. A path that does not parse is unreadable, which is not an empty
    book — a cap we cannot read is not the absence of a cap.
    """
    if not path or not os.path.exists(path):
        return empty_book()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"proposals": {}, "log": [], "readable": False}
    if not isinstance(payload, dict):
        return {"proposals": {}, "log": [], "readable": False}
    decoded = _decode(payload)
    if not isinstance(decoded, dict):
        return {"proposals": {}, "log": [], "readable": False}
    decoded["readable"] = True
    decoded.setdefault("proposals", {})
    decoded.setdefault("log", [])
    return decoded


def save_book(path: str, book: dict) -> None:
    """Write the ledger. Refuses an unreadable book so a failed read is not stored as empty."""
    if book.get("readable") is False:
        raise CapRefused()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(
        path,
        _encode({"proposals": book.get("proposals", {}), "log": book.get("log", [])}),
        separators=(", ", ": "),
    )

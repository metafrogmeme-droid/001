"""Lookalike destination warning.

The match is the one Claw Guard already uses: the first 4 and the last 4
hex characters, and not the same address
(``app/public/js/address-poison.js`` ``isLookalike``). This copy exists
because the Telegram process does not run that file. A shared fixture
locks the two together.

It warns. It does not block, it does not rewrite the destination, and it
does not pick a replacement. An address that cannot be read is not "ok".
"""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence

_ADDR = re.compile(r"^0x([0-9a-fA-F]{40})$")


def canon(addr: object) -> Optional[str]:
    if not isinstance(addr, str):
        return None
    match = _ADDR.fullmatch(addr.strip())
    if match is None:
        return None
    return match.group(1).lower()


def is_lookalike(dest: str, other: str) -> bool:
    """First 4 and last 4 hex characters, and not the same address."""
    if not dest or not other or dest == other:
        return False
    return dest[:4] == other[:4] and dest[-4:] == other[-4:]


def _first_match(dest: str, rows: Sequence[object]) -> tuple[Optional[str], int]:
    compared = 0
    match: Optional[str] = None
    for raw in rows:
        body = canon(raw)
        if body is None:
            continue
        compared += 1
        if match is None and is_lookalike(dest, body):
            match = body
    return match, compared


def destination_warning(
    destination: object,
    *,
    counterparties: Optional[Sequence[object]] = None,
    own: Optional[Sequence[object]] = None,
) -> dict[str, Any]:
    """Warn when ``destination`` looks like a known counterparty or an own address."""
    dest = canon(destination)
    if dest is None:
        return {"ok": False, "error": "destination"}
    party, party_n = _first_match(dest, counterparties or ())
    mine, own_n = _first_match(dest, own or ())
    level = "ok"
    reason = None
    if party is not None:
        level = "warn"
        reason = "prefix_suffix_match"
    elif mine is not None:
        level = "warn"
        reason = "own_prefix_suffix_match"
    return {
        "ok": True,
        "result": {
            "check": "address_poisoning",
            "level": level,
            "reason": reason,
            "lookalike_of": None if party is None else "0x" + party,
            "lookalike_of_own": None if mine is None else "0x" + mine,
            "compared": party_n,
            "own_compared": own_n,
        },
    }

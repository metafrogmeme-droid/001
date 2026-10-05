"""The $RCLAW token that exists: one record, read, never retyped.

``token/config/rclaw.mainnet.json`` holds what was read back from Solana
mainnet for the mint (address, token program, decimals, supply, the two
revoked authorities) and what has been announced about the presale. Today
that is nothing: every presale field is ``null``, and every reader renders
``null`` as *not announced yet* -- never as a date of 0, an empty cell or a
placeholder. The website reads the same file (``app/lib/rclaw_token.js``),
so the bot's /token card and the /token page cannot name two mints.

A record that cannot be read RAISES. The mint address is the one thing a
caller comes for, and a guessed or defaulted address is worse than none:
the caller names the exception class and shows no address in its place.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECORD_PATH = Path(__file__).resolve().parents[2] / "token" / "config" / "rclaw.mainnet.json"

REQUIRED_KEYS = (
    "name", "symbol", "chain", "cluster", "mint", "token_program", "standard",
    "decimals", "supply_tokens", "mint_authority", "freeze_authority",
    "explorer", "presale",
)

_BASE58 = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


class TokenRecordInvalid(ValueError):
    """The file parsed, but what it holds does not describe a token."""


def is_base58_address(value: Any) -> bool:
    return isinstance(value, str) and 32 <= len(value) <= 44 and not (set(value) - _BASE58)


def load_record(path: Path = RECORD_PATH) -> dict[str, Any]:
    """The record, validated. ``OSError`` and ``JSONDecodeError`` propagate."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TokenRecordInvalid("the record is not an object")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise TokenRecordInvalid(f"the record lacks {missing}")
    if not is_base58_address(data["mint"]):
        raise TokenRecordInvalid("the mint is not a base58 address")
    presale = data["presale"]
    if not isinstance(presale, dict) or "status" not in presale:
        raise TokenRecordInvalid("the presale block has no status")
    return data

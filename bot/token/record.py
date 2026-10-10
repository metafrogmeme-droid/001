"""The $RCLAW token that exists: one record, read, never retyped.

``token/config/rclaw.mainnet.json`` holds what was read back from Solana
mainnet for the mint (address, token program, decimals, supply, the two
revoked authorities) and what has been announced about the presale: since
2026-10-10 the Smithii sale's terms. A presale field still ``null`` is
rendered *not announced yet* by every reader -- never as a date of 0, an
empty cell or a placeholder. The website reads a byte-identical copy
(``app/content/rclaw.mainnet.json``, written by
``app/scripts/sync_content.js``; the web deploy ships ``app/`` alone), and a
web test fails when the two differ, so the bot's /rclaw card and the /token
page cannot name two mints.

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


# The file parsed, but what it holds does not describe a token. A comment, not
# a docstring: a class whose whole body is a docstring stops parsing once
# `tests/source_scan.py::code_only` blanks docstrings.
class TokenRecordInvalid(ValueError):
    pass


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

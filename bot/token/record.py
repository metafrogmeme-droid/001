"""The $RCLAW token that exists: one record, read, never retyped.

``token/config/rclaw.mainnet.json`` holds what was read back from Solana
mainnet for the mint (address, token program, decimals, supply, the two
revoked authorities), its locks and its multisig. The website reads a copy
(``app/content/rclaw.mainnet.json``, written by
``app/scripts/sync_content.js``; the web deploy ships ``app/`` alone), and a
web test fails when the two differ, so the bot's /rclaw card and the /token
page cannot name two mints.

The record may still hold a ``presale`` block: the token tooling's account
of the Smithii sale (``token/presale/``). The bot and the website carry no
presale since 22 October 2026, so it is neither required nor read here, and
the website's copy is written without it.

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
    "explorer",
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
    return data

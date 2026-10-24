"""One reading of the unverified XNAV placeholders.

The file is ``placeholders.json`` beside this module. A missing key or a
file that cannot be parsed raises. It does not fill in a zero.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PATH = Path(__file__).with_name("placeholders.json")
_REQUIRED = (
    "staleness_seconds",
    "max_swing_bps",
    "flow_threshold_bps",
    "delta_cap_bps",
    "twap_snapshots",
    "max_feed_bps",
    "stress_depth_num",
    "stress_depth_den",
    "stress_shift_bps",
    "equity_scale",
    "l1_read_gas_base",
    "l1_read_gas_per_word",
    "epoch_seconds",
    "hyperevm_testnet_chain_id",
    "hyperevm_mainnet_chain_id",
    "eip712_name",
    "eip712_version",
    "eip712_domain",
    "eip712_type",
    "add_api_wallet_action",
)


class PlaceholdersUnreadable(Exception):
    """The placeholder file could not be read. Not a set of zeros."""


def placeholders(path: Path | None = None) -> dict[str, Any]:
    src = path or _PATH
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlaceholdersUnreadable(src.name) from exc
    if not isinstance(data, dict):
        raise PlaceholdersUnreadable(src.name)
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise PlaceholdersUnreadable(",".join(missing))
    return data


def l1_read_gas(in_words: int, out_words: int, table: dict[str, Any] | None = None) -> int:
    """Documented L1 read gas. The two factors come from the placeholder file."""
    src = table if table is not None else placeholders()
    return int(src["l1_read_gas_base"]) + int(src["l1_read_gas_per_word"]) * (in_words + out_words)

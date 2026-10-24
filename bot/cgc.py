"""Conservation-brake status for Telegram.

``CGC_ENABLED`` unset is off. The words that count as on are ``1``,
``true``, ``on`` and ``yes``, the same set ``app/lib/cgc_flag.js`` reads.

Not named ``enabled``. A module-level function of that name drops out of
the method-ambiguity sweep.

The windows are the fields in ``contracts/cgc/chains.json``. This module
formats those fields. It does not recompute them, and a file that cannot
be read is said to be unread rather than printed as an empty list.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Mapping, Optional

_ON = frozenset({"1", "true", "on", "yes"})
FLAG = "CGC_ENABLED"
BANNER = "Admin preview. Not public."
NOT_FOUND = "Not found."
UNREAD = "The chain list could not be read."
_WINDOW_KEYS = ("w_brake", "w_prov", "w_final", "w_final_max")
_WINDOW_LABELS = ("brake", "prove", "final", "refund")
_CHAINS = Path(__file__).resolve().parents[1] / "contracts" / "cgc" / "chains.json"


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether CGC_ENABLED is on. Unset is off."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in _ON


def _load(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    chains = data.get("chains")
    if not isinstance(chains, list):
        return None
    return data


def _chain_line(row: object) -> str:
    if not isinstance(row, dict):
        return "One chain row could not be read."
    raw_name = row.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else "A chain"
    safe = html.escape(name, quote=False)
    values: list[int] = []
    for key in _WINDOW_KEYS:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{safe}: its windows could not be read."
        values.append(value)
    if not values[3] > values[2]:
        return f"{safe}: its windows could not be read."
    bits = ", ".join(f"{label} {value}s" for label, value in zip(_WINDOW_LABELS, values))
    brake = row.get("brake")
    if brake is True:
        tail = ""
    elif brake is False:
        tail = ". No on-chain brake in v0"
    else:
        tail = ". Whether a brake is deployed could not be read"
    return f"{safe}: {bits}{tail}"


def telegram_text(
    is_admin: object,
    env: Optional[Mapping[str, str]] = None,
    *,
    chains_path: Optional[Path] = None,
) -> str:
    """The reply. A non-admin, including None, is answered Not found.

    Always a sentence. Silence is not an answer this command is allowed
    to give.
    """
    if not is_admin:
        return NOT_FOUND
    lines = []
    if not flag_on(env):
        lines.append(BANNER)
    lines.append("Conservation brake, testnet. This bot does not sign.")
    loaded = _load(chains_path if chains_path is not None else _CHAINS)
    if loaded is None:
        lines.append(UNREAD)
        return "\n".join(lines)
    chains = loaded["chains"]
    if not chains:
        lines.append("No chains were listed.")
        return "\n".join(lines)
    for row in chains:
        lines.append(_chain_line(row))
    lines.append("gwrsETH is testnet only.")
    return "\n".join(lines)

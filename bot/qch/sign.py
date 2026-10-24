"""Whether this process may sign a QCH feed.

Mainnet chain ids are refused before the key is read. A testnet without
a key does not invent a signature. Scheme ids come from SaRVerifier.
"""

from __future__ import annotations

from typing import Mapping, Optional

from bot.qch.sar_bind import scheme_table

TESTNET_CHAIN_IDS = frozenset({11155111, 84532, 421614, 998})
MAINNET_CHAIN_IDS = frozenset({1, 8453, 42161, 999})
_KEY = "QCH_SIGNING_KEYS"


def signing_posture(chain_id: object, env: Optional[Mapping[str, str]] = None) -> dict:
    """``key_read`` is true only after the chain id is one of the four testnets."""
    source: Mapping[str, str] = {} if env is None else env
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return {"ok": False, "reason": "chain_unreadable", "key_read": False, "chain_id": None}
    if chain_id in MAINNET_CHAIN_IDS:
        return {"ok": False, "reason": "mainnet_refused", "key_read": False, "chain_id": chain_id}
    if chain_id not in TESTNET_CHAIN_IDS:
        return {"ok": False, "reason": "not_testnet", "key_read": False, "chain_id": chain_id}
    table = scheme_table()
    if table is None:
        return {"ok": False, "reason": "scheme_unreadable", "key_read": False, "chain_id": chain_id}
    raw = source.get(_KEY)
    if raw is None or str(raw).strip() == "":
        return {
            "ok": False,
            "reason": "keys_absent",
            "key_read": False,
            "chain_id": chain_id,
            "scheme": table["ecdsa"],
        }
    return {
        "ok": True,
        "reason": "testnet",
        "key_read": True,
        "chain_id": chain_id,
        "scheme": table["ecdsa"],
        "threshold": "2-of-3",
    }

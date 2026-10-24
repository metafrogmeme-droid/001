"""The mainnet pool's config path. This process does not deploy it.

A permitted config is the owner's go plus an external audit, a cap, and an
owner address. Permission does not broadcast, and it does not hold a key.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from bot.t2c.pool import MAINNET


def _flag(env: Mapping[str, str], key: str) -> bool:
    raw = env.get(key)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def _cap(env: Mapping[str, str]) -> tuple[str | None, str]:
    raw = env.get("T2C_DEPOSIT_CAP_WEI")
    if raw is None or str(raw).strip() == "":
        return None, "absent"
    text = str(raw).strip()
    if text.startswith("-") or text.startswith("+") or not text.isdigit():
        return None, "unreadable"
    if int(text) <= 0:
        return None, "unreadable"
    return text, "config"


def _owner(env: Mapping[str, str]) -> bool:
    raw = env.get("T2C_POOL_OWNER")
    if not isinstance(raw, str) or len(raw) != 42 or not raw.startswith("0x"):
        return False
    hexdigits = set("0123456789abcdefABCDEF")
    return all(char in hexdigits for char in raw[2:])


def pool_config(env: Mapping[str, str] | None = None) -> dict:
    src = os.environ if env is None else env
    cap, cap_source = _cap(src)
    owner_set = _owner(src)
    owner_go = _flag(src, "T2C_POOL_OWNER_GO")
    audit = _flag(src, "T2C_POOL_AUDIT")
    permitted = bool(owner_go and audit and cap is not None and owner_set)
    return {
        "chain_id": MAINNET,
        "deployed": False,
        "broadcast": False,
        "server_signs": False,
        "server_holds_key": False,
        "users_deposit_from_own_wallet": True,
        "deposit_cap_wei": cap,
        "deposit_cap_source": cap_source,
        "owner_set": owner_set,
        "owner_go": owner_go,
        "external_audit": audit,
        "deploy_permitted": permitted,
        "paused_on_deploy": True,
        "lp_shares": "testnet_or_fork",
        "withdrawal_nft": "testnet_or_fork",
        "mainnet_pool": "not_deployed",
    }


def public_pool_status(env: Mapping[str, str] | None = None) -> dict:
    """The page. The wei cap stays off this object."""
    cfg = pool_config(env)
    cap = cfg["deposit_cap_source"]
    if cap == "config":
        cap = "set"
    return {
        "deployed": False,
        "broadcast": False,
        "server_signs": False,
        "server_holds_key": False,
        "users_deposit_from_own_wallet": True,
        "deposit_cap": cap,
        "paused_on_deploy": True,
        "lp_shares": cfg["lp_shares"],
        "withdrawal_nft": cfg["withdrawal_nft"],
        "mainnet_pool": "not_deployed",
        "owner_go": cfg["owner_go"],
        "external_audit": cfg["external_audit"],
        "deploy_permitted": cfg["deploy_permitted"],
    }


def admin_pool_status(env: Mapping[str, str] | None = None) -> dict:
    cfg = pool_config(env)
    status = public_pool_status(env)
    status["deposit_cap_wei"] = cfg["deposit_cap_wei"]
    return status

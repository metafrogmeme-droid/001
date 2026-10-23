"""Config inputs. Unset is absent, not a number we invented.

``h_base`` and ``h_restake`` ship as unverified defaults. ``T_p95`` does not:
an unset ``T2C_T_P95_DAYS`` is no duration. An estimator reading overrides it.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal

from bot.t2c.numbers import as_decimal

# Unverified. They are config, not a measurement of slash history.
H_BASE_DEFAULT = Decimal("0.001")
H_RESTAKE_DEFAULT = Decimal("0.01")
S_CAP = Decimal("0.05")


def _env_decimal(env: Mapping[str, str], key: str) -> tuple[Decimal | None, str]:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return None, "absent"
    number = as_decimal(str(raw).strip())
    if number is None:
        return None, "unreadable"
    return number, "config"


def haircut_config(env: Mapping[str, str] | None = None) -> dict:
    src = os.environ if env is None else env
    h_base, base_src = _env_decimal(src, "T2C_H_BASE")
    h_restake, restake_src = _env_decimal(src, "T2C_H_RESTAKE")
    if base_src == "absent":
        h_base, base_src = H_BASE_DEFAULT, "unverified_config"
    if restake_src == "absent":
        h_restake, restake_src = H_RESTAKE_DEFAULT, "unverified_config"
    return {
        "h_base": h_base,
        "h_base_source": base_src,
        "h_restake": h_restake,
        "h_restake_source": restake_src,
        "s_cap": S_CAP,
    }


def t_p95_days(env: Mapping[str, str] | None = None) -> tuple[Decimal | None, str]:
    src = os.environ if env is None else env
    return _env_decimal(src, "T2C_T_P95_DAYS")


def t_eigen_days(env: Mapping[str, str] | None = None) -> tuple[Decimal | None, str]:
    src = os.environ if env is None else env
    return _env_decimal(src, "T2C_T_EIGEN_DAYS")


def _truthy(env: Mapping[str, str], key: str) -> bool:
    raw = env.get(key)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def weeth_restaked(env: Mapping[str, str] | None = None) -> bool:
    """Live weETH is not restaked unless ``T2C_WEETH_RESTAKED`` is on.

    ether.fi removed restaking in August 2026. July 2025 is a different fact
    and is not applied here.
    """
    src = os.environ if env is None else env
    return _truthy(src, "T2C_WEETH_RESTAKED")

"""Config inputs. Unset is absent, not a number we invented.

``h_base`` and ``h_restake`` ship as unverified defaults. An unset
``T2C_T_P95_DAYS`` falls back to 10 days, above a recorded p95 of 9.17.
An estimator reading overrides both. A value that does not parse stays unread.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal

from bot.t2c.numbers import as_decimal, json_num

# Unverified. They are config, not a measurement of slash history.
H_BASE_DEFAULT = Decimal("0.001")
H_RESTAKE_DEFAULT = Decimal("0.01")
S_CAP = Decimal("0.05")
# Fallback duration. Recorded observation: p50 5.98 days, p95 9.17 days.
FALLBACK_T_P95_DAYS = Decimal(10)
OBSERVED_P50_DAYS = Decimal("5.98")
OBSERVED_P95_DAYS = Decimal("9.17")


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
    number, source = _env_decimal(src, "T2C_T_P95_DAYS")
    if source == "absent":
        return FALLBACK_T_P95_DAYS, "fallback"
    return number, source


def observed_duration() -> dict:
    """The recorded p50 and p95 the 10-day fallback sits above. Not a live read."""
    return {
        "p50_days": json_num(OBSERVED_P50_DAYS),
        "p95_days": json_num(OBSERVED_P95_DAYS),
        "fallback_days": json_num(FALLBACK_T_P95_DAYS),
        "source": "recorded_observation",
    }


def t_eigen_days(env: Mapping[str, str] | None = None) -> tuple[Decimal | None, str]:
    src = os.environ if env is None else env
    return _env_decimal(src, "T2C_T_EIGEN_DAYS")


def weeth_restaked(env: Mapping[str, str] | None = None) -> bool:
    """weETH is not restaked, so the restaking term is 0.

    ``env`` is accepted so a caller can pass the process environment. It does
    not turn the term on.
    """
    del env
    return False

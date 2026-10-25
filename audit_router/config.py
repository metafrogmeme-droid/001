"""Owner-editable tier mapping. Model ids have to be ones the Claude client
already prices or lists. A missing weight makes the score unreadable rather
than zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bot.core.cost import LLM_PRICING
from bot.llm.provider import CHAT_HAIKU_MODEL, PROVIDER_CATALOG, LLMProvider

TIERS = ("low", "medium", "high")
WEIGHTS = (
    "payable", "value_transfer", "token_approval", "upgradeable",
    "external_call", "oracle", "access_complex", "per_100_loc",
    "per_extra_contract", "loc_cap", "contract_cap",
    "mainnet_documented", "mainnet_target",
)
MAX_MODELS = 2

DEFAULT_PATH = Path(__file__).resolve().parent / "models.json"


class ConfigError(ValueError):
    """The mapping file is not usable. The router does not guess a model."""


@dataclass(frozen=True)
class BudgetConfig:
    max_tokens: int
    max_cost_usd: float
    max_output_tokens: int
    chunk_input_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class RouterConfig:
    path: Path
    tiers: dict[str, tuple[str, ...]]
    low_below: int
    high_at: int
    weights: dict[str, int]
    budget: BudgetConfig


def supported_model_ids() -> set[str]:
    """Model ids the existing Claude client lists or prices.

    The catalog's recommended list, its default, the chat Haiku constant,
    and every ``claude-`` row in ``bot.core.cost.LLM_PRICING``. One set,
    read from those objects. An id in none of them is not a model this
    client already supports.
    """
    catalog = PROVIDER_CATALOG[LLMProvider.ANTHROPIC]
    ids: set[str] = set()
    default = catalog.get("default_model")
    if isinstance(default, str) and default:
        ids.add(default)
    recommended = catalog.get("recommended_models") or []
    if isinstance(recommended, (list, tuple)):
        ids.update(m for m in recommended if isinstance(m, str) and m)
    if CHAT_HAIKU_MODEL:
        ids.add(CHAT_HAIKU_MODEL)
    ids.update(k for k in LLM_PRICING if isinstance(k, str) and k.startswith("claude-"))
    return ids


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _models(raw, tier: str, supported: set[str]) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"tiers.{tier} needs one or two model ids")
    if len(raw) > MAX_MODELS:
        raise ConfigError(f"tiers.{tier} lists {len(raw)} models; the cap is {MAX_MODELS}")
    out: list[str] = []
    for mid in raw:
        if not isinstance(mid, str) or not mid.strip():
            raise ConfigError(f"tiers.{tier} has a model id that is not a string")
        mid = mid.strip()
        if mid not in supported:
            raise ConfigError(
                f"{mid} is not a model id the Claude client lists or prices")
        if mid not in out:
            out.append(mid)
    return tuple(out)


def load_config(path: Path | None = None) -> RouterConfig:
    """Read the mapping. Raises ConfigError; never substitutes a model."""
    src = Path(path) if path is not None else DEFAULT_PATH
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {src.name} ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise ConfigError("the mapping file must be a JSON object")
    supported = supported_model_ids()
    tiers_raw = data.get("tiers")
    if not isinstance(tiers_raw, dict):
        raise ConfigError("tiers must be an object")
    tiers = {tier: _models(tiers_raw.get(tier), tier, supported) for tier in TIERS}
    low_below = _positive_int(data.get("low_below"), "low_below")
    high_at = _positive_int(data.get("high_at"), "high_at")
    if low_below >= high_at:
        raise ConfigError("low_below must be below high_at")
    weights_raw = data.get("weights")
    if not isinstance(weights_raw, dict):
        raise ConfigError("weights must be an object")
    weights: dict[str, int] = {}
    for name in WEIGHTS:
        if name not in weights_raw:
            raise ConfigError(f"weights.{name} is missing; the score would be a guess")
        value = weights_raw[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"weights.{name} must be a non-negative integer")
        weights[name] = value
    budget_raw = data.get("budget")
    if not isinstance(budget_raw, dict):
        raise ConfigError("budget must be an object")
    cost = budget_raw.get("max_cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise ConfigError("budget.max_cost_usd must be a non-negative number")
    timeout = budget_raw.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("budget.timeout_seconds must be a positive number")
    budget = BudgetConfig(
        max_tokens=_positive_int(budget_raw.get("max_tokens"), "budget.max_tokens"),
        max_cost_usd=float(cost),
        max_output_tokens=_positive_int(
            budget_raw.get("max_output_tokens"), "budget.max_output_tokens"),
        chunk_input_tokens=_positive_int(
            budget_raw.get("chunk_input_tokens"), "budget.chunk_input_tokens"),
        timeout_seconds=float(timeout),
    )
    return RouterConfig(src, tiers, low_below, high_at, weights, budget)


def models_for(tier: str, config: RouterConfig, override: str | None) -> tuple[str, ...]:
    """The models for this tier, or the single override when it is supported."""
    if override is None or override.strip() == "":
        return config.tiers[tier]
    mid = override.strip()
    if mid not in supported_model_ids():
        raise ConfigError(f"{mid} is not a model id the Claude client lists or prices")
    return (mid,)

"""Strategy-Agent marketplace catalogue (read-only, PUBLIC-safe).

A browsable catalogue of the engine's named strategy agents. Every agent here
is one of the REAL engine presets (``RunStrategySkill.PRESETS``) — so the
"how it trades" line is DERIVED from the live config and can never drift from
what the agent actually does. Editorial fields (tagline, regime fit, risk tag,
horizon) sit alongside.

§4 compliance: this is public-safe. It carries strategy DESIGN + regime +
qualitative risk only — never a dollar amount and never a fabricated return.
Verified performance is shown as percent/ratio elsewhere (the honest Strategy
Lab backtester + the verifiable leaderboard), never invented here.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

# Committed, reproducible per-agent benchmark scorecards (generated offline by
# scripts/gen_agent_scorecards.py). Percent/ratio only, stamped with the dataset
# hash — see that script. Loaded fail-soft so a missing scorecard just omits the
# stats from the card, never breaks the catalogue.
# Repo-root anchored, not CWD-relative: the catalogue is imported from the web
# gateway and the bridge as well as the bot, and a scorecard that silently
# vanishes just omits stats from a public card rather than raising.
#
# Tries both homes. The snapshot moved out from under the data/ symlink so it
# could be committed at all (bot.backtest.snapshot.benchmark_root has the
# reasoning); the old path stays readable so a box that has not moved its copy
# keeps its cards populated.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# An OVERRIDE, not the location. None means "resolve at call time". Kept
# settable because a test points it at a nonexistent path to prove the loader
# fails soft; replacing it with a bare function removed that seam.
_SCORECARD_DIR = None


def _scorecard_dir() -> str:
    """Resolved per call — a module-level constant cannot see which of the two
    locations exists, and this one is imported before either is guaranteed."""
    if _SCORECARD_DIR is not None:
        return str(_SCORECARD_DIR)
    for parts in (("benchmark", "scorecards"), ("data", "benchmark", "scorecards")):
        cand = os.path.join(_REPO_ROOT, *parts)
        if os.path.isdir(cand):
            return cand
    return os.path.join(_REPO_ROOT, "benchmark", "scorecards")

# Editorial, marketplace-facing metadata keyed by the real preset id. Kept
# deliberately small — the substance (how it trades) is derived, not authored.
_META: dict[str, dict[str, str]] = {
    "dip sniper": {
        "tagline": "Shorts a downtrend only once RSI has lifted off capitulation. "
                   "It does not sell the oversold print.",
        "regime": "Downtrends",
        "risk": "balanced",
        "horizon": "swing",
    },
    "momentum hunter": {
        "tagline": "Longs a volume spike of 3× or more. It does not wait for an "
                   "uptrend label, and it does not short the spike.",
        "regime": "Volume bursts",
        "risk": "aggressive",
        "horizon": "intraday",
    },
    "safe scalper": {
        "tagline": "The most liquid pairs, a high conviction bar, and no entry "
                   "while RSI is still at capitulation.",
        "regime": "Any / liquidity-led",
        "risk": "tight",
        "horizon": "scalp",
    },
    "full scan": {
        "tagline": "The house strategy — the full fail-closed pipeline with every "
                   "proven default on.",
        "regime": "All regimes",
        "risk": "balanced",
        "horizon": "adaptive",
    },
    "eth ma trend": {
        "tagline": "Follows ETHUSDT in the direction of a closed-bar 50/200 "
                   "moving average, and reverses only when that relationship changes.",
        "regime": "ETH trend",
        "risk": "tight",
        "horizon": "position",
    },
}

_RISK_LABEL = {
    "tight": "🟢 Tight risk",
    "balanced": "🟡 Balanced",
    "aggressive": "🟠 Aggressive",
}


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-")


def _how_it_trades(cfg: dict[str, Any]) -> str:
    """Human 'how it trades' line derived from the preset's real config, so it
    stays honest to actual behaviour. No numbers are invented — only the
    thresholds the engine actually applies are surfaced."""
    parts: list[str] = []
    sym = cfg.get("symbols")
    if sym == "top3_volume":
        parts.append("the 3 most-liquid pairs")
    elif isinstance(sym, (list, tuple)):
        parts.append("only " + ", ".join(str(s) for s in sym) if sym else "no market")
    elif sym:
        parts.append(str(sym))
    else:
        parts.append("all scanned pairs")
    fast = cfg.get("fast_period")
    slow = cfg.get("slow_period")
    if (isinstance(fast, int) and not isinstance(fast, bool)
            and isinstance(slow, int) and not isinstance(slow, bool)):
        parts.append(
            f"the closed-bar {fast}/{slow} simple moving-average direction, "
            "reversing only when that relationship changes")
    hours = cfg.get("schedule_hours")
    if isinstance(hours, int) and not isinstance(hours, bool) and hours > 0:
        parts.append(f"checked every {hours} hours")
    gross = cfg.get("max_gross_leverage")
    if isinstance(gross, (int, float)) and not isinstance(gross, bool):
        parts.append(f"gross exposure at most {gross:g}\u00d7")
    lev = cfg.get("leverage")
    if isinstance(lev, (int, float)) and not isinstance(lev, bool):
        parts.append(f"leverage {lev:g}\u00d7")
    weight = cfg.get("target_weight")
    util = cfg.get("utilization")
    if (isinstance(weight, (int, float)) and not isinstance(weight, bool)
            and isinstance(util, (int, float)) and not isinstance(util, bool)):
        parts.append(f"target weight {weight:g} at utilization {util:g}")
    regime = cfg.get("regime")
    if regime:
        parts.append(f"only in {str(regime).replace('_', ' ').lower()}")
    rsi = cfg.get("rsi_threshold")
    if rsi is not None:
        parts.append(f"RSI below {rsi}")
    rsi_min = cfg.get("rsi_min")
    if rsi_min is not None:
        parts.append(f"RSI at or above {rsi_min:g}" if isinstance(rsi_min, float)
                     else f"RSI at or above {rsi_min}")
    side = cfg.get("direction")
    if side == "long_only":
        parts.append("long only")
    elif side == "short_only":
        parts.append("short only")
    vspike = cfg.get("volume_spike_min")
    if vspike is not None:
        parts.append(f"a volume spike over {vspike:g}×")
    conf = cfg.get("confidence_threshold")
    if conf is not None:
        parts.append(f"engine confidence ≥ {round(conf * 100)}%")
    sl = cfg.get("sl_atr_mult")
    tp = cfg.get("tp_atr_mult")
    if sl is not None or tp is not None:
        bits = []
        if sl is not None:
            bits.append(f"{sl:g}-ATR stop")
        if tp is not None:
            bits.append(f"{tp:g}-ATR target")
        parts.append(" / ".join(bits))
    text = "Trades " + ", ".join(parts) + "."
    source_tf = cfg.get("ma_source_timeframe")
    target_tf = cfg.get("ma_timeframe")
    if (isinstance(fast, int) and not isinstance(fast, bool)
            and isinstance(slow, int) and not isinstance(slow, bool)
            and source_tf and target_tf and source_tf != target_tf):
        text += (
            f" The {fast}/{slow} average is read on closed {target_tf} bars "
            f"resampled from {source_tf} bars; a trailing unfinished "
            f"{target_tf} group is dropped. {source_tf} bars stay {source_tf} bars."
        )
    return text


def _load_scorecard(agent_id: str) -> Optional[dict]:
    """The committed benchmark scorecard for this agent slug, or None. Public-safe
    by construction (the generator writes percent/ratio only); we still strip any
    non-metric/dollar-ish keys defensively before it reaches a card."""
    try:
        path = os.path.join(_scorecard_dir(), f"{agent_id}.json")
        with open(path, encoding="utf-8") as fh:
            card = json.load(fh)
    except Exception:
        return None
    if not isinstance(card, dict):
        return None
    metrics = card.get("metrics") or {}
    return {
        "dataset": card.get("dataset", ""),
        "dataset_hash": (card.get("dataset_hash", "") or "")[:12],
        "symbols": card.get("symbols", []),
        "bars": card.get("bars"),
        "gates": card.get("gates", {}),
        "unmodeled": card.get("unmodeled", []),
        "metrics": {
            "total_return_pct": metrics.get("total_return_pct"),
            "profit_factor": metrics.get("profit_factor"),
            "win_rate": metrics.get("win_rate"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "total_trades": metrics.get("total_trades"),
        },
    }


def _run_alias(key: str) -> str:
    """The chat/Telegram shortcut for this agent (e.g. 'dip' -> dip sniper)."""
    from bot.skills.skill_registry import RunStrategySkill
    for alias, target in RunStrategySkill.ALIASES.items():
        if target == key:
            return alias
    return key


def catalog() -> list[dict]:
    """The marketplace catalogue — one card per real engine strategy agent.
    Public-safe: design, regime, and qualitative risk only. Fail-soft: returns
    [] if the preset source can't be read."""
    try:
        from bot.skills.skill_registry import RunStrategySkill
        presets = RunStrategySkill.PRESETS
    except Exception:
        return []
    out: list[dict] = []
    for key, cfg in presets.items():
        meta = _META.get(key, {})
        risk = meta.get("risk", "balanced")
        aid = _slug(key)
        out.append({
            "id": aid,
            "name": cfg.get("label", key.title()),
            "icon": cfg.get("icon", "🤖"),
            "tagline": meta.get("tagline", ""),
            "how": _how_it_trades(cfg),
            "regime": meta.get("regime", ""),
            "risk": risk,
            "risk_label": _RISK_LABEL.get(risk, "🟡 Balanced"),
            "horizon": meta.get("horizon", ""),
            "run": _run_alias(key),
            # Reproducible frozen-benchmark scorecard (percent/ratio only), or
            # None if not yet generated. Lets the marketplace card show verified
            # numbers with a one-tap "reproduce in the Lab".
            "scorecard": _load_scorecard(aid),
        })
    return out


def get_agent(agent_id: str) -> Optional[dict]:
    """One agent card by slug id, or None."""
    aid = _slug(agent_id or "")
    for a in catalog():
        if a["id"] == aid:
            return a
    return None

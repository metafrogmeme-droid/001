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
from pathlib import Path
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

# Tests point this at a temp tree. None reads benchmark/eligibility under
# the repo root, the same place live eligibility records live. A preset
# record is filed as presets/<slug>.json and is not the running-strategy
# hash the live gate asks for.
_ELIGIBILITY_ROOT: Optional[str] = None

_PRESET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERDICT_MARKS = ("discovery", "prospective")


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
        "tagline": "A closed-bar 50/200 moving-average rule for ETHUSDT that reverses "
                   "only when that relationship changes. The frozen backtest runs it; "
                   "the live bot does not.",
        "regime": "ETH trend",
        "risk": "tight",
        "horizon": "position",
    },
    "daily vol rotation": {
        "tagline": "A daily-momentum rotation over six markets, written down and "
                   "not run: no scan, confirm or backtest evaluates its rule. No "
                   "frozen track record is published.",
        "regime": "Daily momentum",
        "risk": "balanced",
        "horizon": "swing",
    },
    "alt sweep": {
        "tagline": "A closed-bar 36/144 moving-average rule over fifteen alt markets, "
                   "written down and not run: its sizing knobs are not applied, so "
                   "no book is opened.",
        "regime": "Alt trend",
        "risk": "aggressive",
        "horizon": "",
    },
}


def _positive_number(value: Any) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and value == value and value > 0)


def _ma_book_can_size(cfg: dict[str, Any]) -> bool:
    """True when the moving-average fill path has the margin inputs it reads.

    That path sizes from target weight, utilization and a gross cap, then
    applies leverage. A risk ratio is not those inputs, so it does not size.
    """
    return all(_positive_number(cfg.get(key)) for key in (
        "target_weight", "utilization", "max_gross_leverage"))


def _unapplied_knobs(cfg: dict[str, Any]) -> str:
    """The knobs this book records and does not apply, or empty.

    One sentence, no leading space. Empty when the preset names none.
    """
    bits: list[str] = []
    trail = cfg.get("trailing_stop_pct")
    if (isinstance(trail, int | float) and not isinstance(trail, bool)
            and trail == trail and trail > 0):
        bits.append(f"a {trail * 100:g}% trailing stop")
    ladder = cfg.get("take_profit_ladder")
    if isinstance(ladder, list | tuple) and ladder:
        bits.append(f"a {len(ladder)}-stage scale-out")
    risk = cfg.get("risk_per_trade")
    if _positive_number(risk):
        bits.append(f"a risk ratio of {risk:g}")
    leverage = cfg.get("leverage")
    if _positive_number(leverage) and not _ma_book_can_size(cfg):
        bits.append(f"leverage {leverage:g}\u00d7")
    mode = cfg.get("margin_mode")
    if isinstance(mode, str) and mode.strip():
        bits.append(f"{mode.strip()} margin")
    cap = cfg.get("max_portfolio_positions")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
        bits.append(f"at most {cap} positions")
    floor_n = cfg.get("minimum_target_positions")
    if isinstance(floor_n, int) and not isinstance(floor_n, bool) and floor_n > 0:
        bits.append(f"a minimum target of {floor_n} positions")
    if not bits:
        return ""
    joined = ", ".join(bits)
    verb = "is" if len(bits) == 1 else "are"
    sentence = f"{joined} {verb} recorded on this preset and {verb} not applied."
    return sentence[:1].upper() + sentence[1:]


def publishes_scorecard(cfg: dict[str, Any]) -> bool:
    """Whether a frozen-benchmark scorecard may be written for this preset.

    The one reading, for the generator and for the card's own "no
    scorecard is published" sentence. A preset whose recorded knobs do not
    size a fill (`_unapplied_knobs`) opens no book the runner can measure:
    ALT Sweep's moving-average run gets no sizing flags, the engine opens
    nothing, and a card written for it would be a zero-trade book, printed
    as "+0.00%, profit factor 0.00, 0 trades" under a how-line saying no
    card is published. The generator decided by universe coverage alone, so
    the first snapshot holding its fifteen symbols would have written one.
    The daily rotation is refused for its bar size too.
    """
    from bot.core.vol_rotation import publishes_scorecard as _rotation_publishes
    return _rotation_publishes(cfg) and not _unapplied_knobs(cfg)


def _unapplied_clause(cfg: dict[str, Any]) -> str:
    """Preset knobs this book records and does not apply. Empty when none."""
    knobs = _unapplied_knobs(cfg)
    if not knobs:
        return ""
    return (
        " " + knobs
        + " No frozen-benchmark scorecard is published, because a run of it"
        " does not size a fill."
    )


def catalogue_note() -> str:
    """The one lineup sentence. Percent and ratio, a missing record stays
    missing, and verified live ranks are on the leaderboard."""
    return (
        "Every agent is a preset from the engine's own config, and a card "
        "says when the engine does not run its rule. "
        "Where a frozen backtest is attached, it is percent and ratio only, "
        "never a dollar figure, and Reproduce in Lab re-runs that backtest. "
        "A card with no track record has none. "
        "Verified live ranks are on the leaderboard."
    )


def unpublished_scorecard(cfg: dict[str, Any]) -> Optional[dict]:
    """The metrics slot when no frozen scorecard file exists.

    Daily volatility rotation names its own percent exits. Any other preset
    whose recorded knobs do not size a fill gets the same kind of omission:
    no track record, not a backtest that is about to appear. A preset with
    nothing unapplied returns None so a missing house file stays a missing
    file rather than a sentence this helper invented.
    """
    from bot.core.vol_rotation import omitted_scorecard
    vol = omitted_scorecard(cfg)
    if vol is not None:
        return vol
    knobs = _unapplied_knobs(cfg)
    if not knobs:
        return None
    return {"omitted": "No track record published. " + knobs}

# Recorded on a preset and not asked of the runner, unless a key names a
# flag ``_gate_args`` actually emits (leverage, and only when the fill path
# can size). A scorecard lists these under ``unmodeled``.
UNAPPLIED_PRESET_KEYS = (
    "trailing_stop_pct",
    "take_profit_ladder",
    "risk_per_trade",
    "margin_mode",
    "max_portfolio_positions",
    "minimum_target_positions",
    "leverage",
)

_RISK_LABEL = {
    "tight": "🟢 Tight risk",
    "balanced": "🟡 Balanced",
    "aggressive": "🟠 Aggressive",
}


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-")


def live_runs(cfg: dict[str, Any]) -> bool:
    """Whether the live bot runs this preset's rule.

    The one reading, for `/run` and for the card. A moving-average or
    rotation preset is a reading, not a scan: `/run` shows its how-line and
    places nothing, and no scan or confirm evaluates it. The cards said
    "Trades only ETHUSDT" and "Follows fifteen alt markets" over that.
    """
    from bot.core.ma_trend import preset_is_ma_trend
    from bot.core.vol_rotation import preset_is_vol_rotation
    return not (preset_is_ma_trend(cfg) or preset_is_vol_rotation(cfg))


#: The how-line's first sentence for a preset the live bot does not run.
NOT_RUN_LIVE = ("The live bot does not run this rule: /run shows this text "
                "and places nothing.")


def _not_run_sentence(cfg: dict[str, Any]) -> str:
    """Who evaluates a preset the live bot does not run, if anyone."""
    from bot.core.vol_rotation import preset_is_vol_rotation
    if preset_is_vol_rotation(cfg):
        return NOT_RUN_LIVE + " No backtest evaluates it either."
    if _unapplied_knobs(cfg):
        return (NOT_RUN_LIVE + " A backtest of it opens nothing, because the "
                "knobs below do not size a fill.")
    return NOT_RUN_LIVE + " The frozen backtest runs it."


def _how_it_trades(cfg: dict[str, Any]) -> str:
    """Human 'how it trades' line derived from the preset's real config, so it
    stays honest to actual behaviour. No numbers are invented — only the
    thresholds the engine actually applies are surfaced."""
    from bot.core.vol_rotation import how_line, preset_is_vol_rotation
    if preset_is_vol_rotation(cfg):
        return _not_run_sentence(cfg) + " " + how_line(cfg)
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
    target_tf = cfg.get("ma_timeframe")
    source_tf = cfg.get("ma_source_timeframe")
    fast_ok = isinstance(fast, int) and not isinstance(fast, bool)
    slow_ok = isinstance(slow, int) and not isinstance(slow, bool)
    # The closed target bar is the check. Saying "every N hours" beside
    # "closed Nh bars" is the same cadence twice.
    schedule_is_the_bar = (
        fast_ok and slow_ok
        and isinstance(hours, int) and not isinstance(hours, bool) and hours > 0
        and isinstance(source_tf, str) and isinstance(target_tf, str)
        and source_tf.strip() and target_tf.strip()
        and source_tf.strip() != target_tf.strip()
        and target_tf.strip() == f"{hours}h"
    )
    if (isinstance(hours, int) and not isinstance(hours, bool) and hours > 0
            and not schedule_is_the_bar):
        parts.append(f"checked every {hours} hours")
    gross = cfg.get("max_gross_leverage")
    if isinstance(gross, (int, float)) and not isinstance(gross, bool):
        parts.append(f"gross exposure at most {gross:g}\u00d7")
    lev = cfg.get("leverage")
    # Leverage is stated as applied only when the fill path can reach it.
    # A moving-average book reads it after the margin inputs; without those,
    # the number stays on the preset and the unapplied clause names it.
    if (isinstance(lev, (int, float)) and not isinstance(lev, bool)
            and _ma_book_can_size(cfg)):
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
    if live_runs(cfg):
        text = "Trades " + ", ".join(parts) + "."
    else:
        text = _not_run_sentence(cfg) + " The rule covers " + ", ".join(parts) + "."
    text += _unapplied_clause(cfg)
    if (fast_ok and slow_ok
            and isinstance(source_tf, str) and isinstance(target_tf, str)
            and source_tf.strip() and target_tf.strip()
            and source_tf.strip() != target_tf.strip()):
        target = target_tf.strip()
        source = source_tf.strip()
        # The periods were already named above. This sentence is the bars.
        text += (
            f" That average is read on closed {target} bars "
            f"resampled from {source} bars; a trailing unfinished "
            f"{target} group is dropped. {source} bars stay {source} bars."
        )
    return text


def _finite_number(v: Any) -> Optional[float]:
    """A finite number, or None. ``bool`` is not a number. Infinity is not a ratio."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    value = float(v)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _count(v: Any) -> Optional[int]:
    """An integer count, or None. ``True`` and ``1.5`` are not counts. ``0`` is."""
    # Return inside the narrowed arm. ``isinstance(v, bool) or not isinstance``
    # leaves ``v`` as Any, and returning Any is not an int.
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _data_mark(card: dict) -> Optional[str]:
    """``discovery`` or ``prospective`` when the card says so.

    Every scorecard the generator writes is a design backtest on frozen
    data (``honest`` and a dataset name). That absence of an explicit mark
    is the discovery mark, not an unknown study. A card that is neither
    stays unmarked.
    """
    raw = card.get("data_mark")
    if isinstance(raw, str) and raw in _VERDICT_MARKS:
        return raw
    dataset = card.get("dataset")
    if card.get("honest") is True and isinstance(dataset, str) and dataset:
        return "discovery"
    return None


def _folds(card: dict) -> Optional[dict]:
    """Walk-forward counts, or None when the card does not carry them.

    Both ``run`` and ``profitable`` have to be integers. A missing block
    is not zero folds. A measured ``profitable`` of 0 is kept.
    """
    raw = card.get("folds")
    if not isinstance(raw, dict):
        return None
    run = _count(raw.get("run"))
    profitable = _count(raw.get("profitable"))
    if run is None or profitable is None:
        return None
    return {
        "requested": _count(raw.get("requested")),
        "run": run,
        "profitable": profitable,
    }


def follow_listing(scorecard: Optional[dict], eligibility_state: str) -> tuple[bool, str]:
    """Whether this preset is listed for copy/follow, and why.

    A complete verdict is a finite profit factor, an integer trade count,
    and a discovery or prospective mark. Folds may be unmeasured; that
    sentence is the fold reading, not a zero.

    Profit factor below 1 is withheld unless ``eligibility_state`` is
    ``eligible`` (an artefact for this preset says it survives). An
    unreadable artefact is not "no record" and it is not a grant. A
    missing verdict is not a profit factor of 0, and it is not an offer.
    """
    metrics = None
    mark = None
    if isinstance(scorecard, dict):
        raw_metrics = scorecard.get("metrics")
        if isinstance(raw_metrics, dict):
            metrics = raw_metrics
        raw_mark = scorecard.get("data_mark")
        if isinstance(raw_mark, str):
            mark = raw_mark
    pf = _finite_number(metrics.get("profit_factor")) if metrics is not None else None
    trades = _count(metrics.get("total_trades")) if metrics is not None else None
    if pf is None or trades is None or mark not in _VERDICT_MARKS:
        return False, "no_verdict"
    if pf < 1:
        from bot.core.live_eligibility import ELIGIBLE, UNREADABLE
        if eligibility_state == ELIGIBLE:
            return True, "offered"
        if eligibility_state == UNREADABLE:
            return False, "eligibility_unreadable"
        return False, "below_one"
    return True, "offered"


def _profit_factor_below_one(scorecard: Optional[dict]) -> bool:
    """True only for a finite measured profit factor below 1."""
    if not isinstance(scorecard, dict):
        return False
    metrics = scorecard.get("metrics")
    if not isinstance(metrics, dict):
        return False
    pf = _finite_number(metrics.get("profit_factor"))
    return pf is not None and pf < 1


def _preset_eligibility_state(agent_id: str) -> str:
    """The preset's own eligibility artefact, not the running-strategy hash.

    Filed at ``benchmark/eligibility/presets/<slug>.json``. The live gate
    looks up the hash of ``bot/*.py`` and never opens this path.
    """
    from bot.core.live_eligibility import UNREADABLE, read_eligibility
    if not isinstance(agent_id, str) or _PRESET_ID.fullmatch(agent_id) is None:
        return UNREADABLE
    root = Path(_ELIGIBILITY_ROOT) if isinstance(_ELIGIBILITY_ROOT, str) and _ELIGIBILITY_ROOT else None
    return read_eligibility(f"presets/{agent_id}", root).state


#: The scorecard slot for a committed file that could not be read. Every
#: renderer prints an ``omitted`` sentence verbatim, so this is what the
#: lineup, the agent page and the compare table say instead of a figure.
SCORECARD_UNREADABLE = ("The track record on file could not be read. It is not "
                        "absent, and no figure is shown from it.")


def _load_scorecard(agent_id: str) -> Optional[dict]:
    """The committed benchmark scorecard for this agent slug, or None. Public-safe
    by construction (the generator writes percent/ratio only); we still strip any
    non-metric/dollar-ish keys defensively before it reaches a card.

    None is NO FILE, and only that. Every other failure (corrupt JSON, a
    permission error, a payload that is not a card) was None too, and the
    catalogue then fell through to "No track record published." on four
    public surfaces for a preset whose frozen record is committed. A file
    that could not be read is its own slot, `SCORECARD_UNREADABLE`.
    """
    try:
        path = os.path.join(_scorecard_dir(), f"{agent_id}.json")
        with open(path, encoding="utf-8") as fh:
            card = json.load(fh)
    except FileNotFoundError:
        return None
    except Exception:
        return {"omitted": SCORECARD_UNREADABLE}
    if not isinstance(card, dict):
        return {"omitted": SCORECARD_UNREADABLE}
    raw_metrics = card.get("metrics")
    # Bind once so isinstance narrows. A second ``card.get`` stays ``Any | None``
    # and ``.get`` on that union is not a dict read.
    metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
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
        "folds": _folds(card),
        "data_mark": _data_mark(card),
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
        score = _load_scorecard(aid)
        if score is None:
            score = unpublished_scorecard(cfg)
        state = _preset_eligibility_state(aid) if _profit_factor_below_one(score) else "missing"
        offered, reason = follow_listing(score, state)
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
            # A frozen scorecard (percent/ratio only), an omission when no
            # frozen run exists, or None when this preset has neither. None
            # is not a backtest that is about to appear.
            "scorecard": score,
            # Copy/follow is a listing decision. Profit factor below 1 stays
            # off the follow door until this preset has an eligibility artefact.
            # A missing verdict is not offered either.
            "copy_follow": offered,
            "copy_follow_reason": reason,
        })
    return out


def get_agent(agent_id: str) -> Optional[dict]:
    """One agent card by slug id, or None."""
    aid = _slug(agent_id or "")
    for a in catalog():
        if a["id"] == aid:
            return a
    return None

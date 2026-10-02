#!/usr/bin/env python3
"""Generate committed, reproducible per-agent benchmark scorecards.

For each real marketplace Strategy-Agent (``RunStrategySkill.PRESETS``) this runs
the engine's HONEST frozen-benchmark backtester with that agent's real entry
gates (confidence / symbols / volume-spike / regime / RSI — see Phase 2a) and
writes a percent/ratio-only scorecard to ``benchmark/scorecards/<slug>.json``
— the directory the catalogue reads. ``benchmark_root()`` resolves it; a path
under ``data/`` is the symlink deploy does not commit.

§4-safe by construction: only percent/ratio metrics are recorded — never a
dollar figure. Every scorecard is stamped with the dataset name + ``dataset_hash``
+ bar count + the exact gates, plus ``code_sha`` and ``recorded_at`` the same
way ``benchmark/majors_1h/result.json`` is stamped, so anyone can re-run the
identical backtest (in the web Strategy Lab or via ``python -m bot.backtest.runner``)
and reproduce it.

The six public metrics are a projection of the runner JSON
(``project_public_metrics``). A missing runner field stays missing — it is
not written as zero.

Usage:
    python -m scripts.gen_agent_scorecards            # default dataset/symbols
    python -m scripts.gen_agent_scorecards --dataset benchmark/majors_1h \
        --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT --last-bars 1500

This is an OFFLINE batch (frozen data, no network). Regenerate + commit whenever
the presets or the benchmark snapshot change.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from bot.backtest.benchmark_record import code_sha
from bot.backtest.runner import public_trade_breakdown
from bot.backtest.snapshot import benchmark_root, default_benchmark_dir
from bot.compat import UTC

REPO = Path(__file__).resolve().parents[1]

# Percent/ratio metrics only — NEVER a dollar field (§4). These are the keys
# copied from the backtester's result into the public scorecard.
_METRIC_KEYS = (
    "total_return_pct", "profit_factor", "win_rate", "max_drawdown_pct",
    "sharpe_ratio", "sortino_ratio", "calmar_ratio", "total_trades",
)

# The six the public card paints. A projection of the runner JSON, not a
# second arithmetic.
PUBLIC_METRICS = (
    "total_return_pct", "profit_factor", "win_rate",
    "max_drawdown_pct", "sharpe_ratio", "total_trades",
)

# Exit-geometry knobs. Listed as unmodeled until ``_gate_args`` actually
# passes them to the runner.
_EXIT_KEYS = ("sl_atr_mult", "tp_atr_mult")


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-")


def _scorecard_dir() -> Path:
    """The catalogue's directory, anchored at the repo so a caller whose
    cwd is not the repo root still writes the tree git can commit."""
    root = benchmark_root()
    if not root.is_absolute():
        root = REPO / root
    return root / "scorecards"


def _as_number(v):
    """A real number, or None. Missing, bool, and non-numeric stay None —
    unreadable is not zero."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def project_public_metrics(runner: dict) -> dict:
    """The six public numbers, read off a runner JSON.

    Floats are rounded to 4 decimal places, the scorecard's published
    precision. An absent or non-numeric field is None, never 0.
    """
    out: dict = {}
    for key in PUBLIC_METRICS:
        v = _as_number(runner.get(key) if isinstance(runner, dict) else None)
        if isinstance(v, float):
            out[key] = round(v, 4)
        else:
            out[key] = v
    return out


def measured_metrics(runner: dict) -> dict:
    """The runner's own metric block, percent/ratio only, unrounded beyond
    what the runner already wrote. This is the source ``metrics`` projects."""
    out: dict = {}
    for key in _METRIC_KEYS:
        v = _as_number(runner.get(key) if isinstance(runner, dict) else None)
        out[key] = v
    return out


def project_metrics(runner: dict) -> dict:
    """Every published metric, at the scorecard's 4-decimal precision."""
    out: dict = {}
    src = measured_metrics(runner)
    for key, v in src.items():
        out[key] = round(v, 4) if isinstance(v, float) else v
    return out


def _gate_args(cfg: dict) -> list[str]:
    """Map a preset's real filters onto the runner's flags.
    Exit multiples are emitted because the runner applies them. A multiple
    this does not emit stays in ``unmodeled``."""
    args: list[str] = []
    if cfg.get("confidence_threshold") is not None:
        args += ["--confidence-threshold", str(cfg["confidence_threshold"])]
    if cfg.get("volume_spike_min") is not None:
        args += ["--volume-spike-min", str(cfg["volume_spike_min"])]
    if cfg.get("regime"):
        args += ["--regime-filter", str(cfg["regime"])]
    if cfg.get("rsi_threshold") is not None:
        args += ["--rsi-max", str(cfg["rsi_threshold"])]
    if cfg.get("rsi_min") is not None:
        args += ["--rsi-min", str(cfg["rsi_min"])]
    if cfg.get("direction"):
        args += ["--direction", str(cfg["direction"])]
    if cfg.get("sl_atr_mult") is not None:
        args += ["--sl-atr-mult", str(cfg["sl_atr_mult"])]
    if cfg.get("tp_atr_mult") is not None:
        args += ["--tp-atr-mult", str(cfg["tp_atr_mult"])]
    fast = cfg.get("fast_period")
    slow = cfg.get("slow_period")
    if (isinstance(fast, int) and not isinstance(fast, bool)
            and isinstance(slow, int) and not isinstance(slow, bool)):
        args += ["--ma-fast", str(fast), "--ma-slow", str(slow)]
        if cfg.get("ma_timeframe"):
            args += ["--ma-timeframe", str(cfg["ma_timeframe"])]
        syms = cfg.get("symbols")
        if isinstance(syms, (list, tuple)) and syms:
            args += ["--ma-symbols", ",".join(str(s) for s in syms)]
        from bot.core.strategy_catalog import _ma_book_can_size
        sized = _ma_book_can_size(cfg)
        for flag, key in (
            ("--ma-target-weight", "target_weight"),
            ("--ma-max-gross-leverage", "max_gross_leverage"),
            ("--ma-utilization", "utilization"),
            ("--leverage", "leverage"),
            ("--ma-signal-confidence", "signal_confidence"),
        ):
            # Leverage is the fill's leverage. The fill reads it only after
            # the margin inputs produce a size. Without those, passing the
            # flag would describe a leverage the book does not apply.
            if key == "leverage" and not sized:
                continue
            val = cfg.get(key)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            args += [flag, str(val)]
    return args


# Percent exits the runner does not apply. The trail is an ATR stage table
# and partial closes are R-multiples, so these stay named rather than filled.
_PERCENT_EXIT_KEYS = ("trailing_stop_pct", "take_profit_pct", "hard_stop_loss_pct")


def publishes_scorecard(cfg: dict) -> bool:
    """Whether ``generate`` writes a frozen card for this preset.

    Daily volatility rotation's exits are recorded and not applied, and its
    bar size is daily. A majors 1h house run is not its track record, so the
    file stays absent.
    """
    from bot.core.vol_rotation import publishes_scorecard as _publishes
    return _publishes(cfg)


def _unmodeled(cfg: dict) -> list[str]:
    """Knobs the runner was not asked to apply.

    An exit multiple ``_gate_args`` emits is in the number. A percent trail,
    target, or hard stop is never emitted. A recorded scale-out, risk ratio,
    margin mode or position count has no runner flag, so it stays named.
    Leverage stays named when the fill path cannot size, because that is when
    ``_gate_args`` does not emit it. The daily rotation names leverage on the
    card and does not publish a scorecard, so its unmodeled list is the three
    percent exits only.
    """
    from bot.core.strategy_catalog import UNAPPLIED_PRESET_KEYS
    from bot.core.vol_rotation import preset_is_vol_rotation
    emitted = set(_gate_args(cfg))
    out: list[str] = []
    if cfg.get("sl_atr_mult") is not None and "--sl-atr-mult" not in emitted:
        out.append("sl_atr_mult")
    if cfg.get("tp_atr_mult") is not None and "--tp-atr-mult" not in emitted:
        out.append("tp_atr_mult")
    seen = set(out)
    for key in _PERCENT_EXIT_KEYS:
        if cfg.get(key) is not None and key not in seen:
            out.append(key)
            seen.add(key)
    if preset_is_vol_rotation(cfg):
        return out
    flag_for = {"leverage": "--leverage"}
    for key in UNAPPLIED_PRESET_KEYS:
        if cfg.get(key) is None or key in seen:
            continue
        flag = flag_for.get(key)
        if flag is not None and flag in emitted:
            continue
        out.append(key)
        seen.add(key)
    return out


def preset_universe_covered(cfg: dict, run_symbols: list[str]) -> bool:
    """True when this run's markets are the preset's universe.

    A preset with no explicit list, or the top-volume rule, is the house scan
    the frozen window already scores. An explicit list is covered only when
    every symbol is in the run. One overlapping name is not the book, and a
    run that does not cover the list does not get a scorecard.
    """
    from bot.core.strategy_gate import _base
    syms = cfg.get("symbols") if isinstance(cfg, dict) else None
    if syms is None or syms == "top3_volume":
        return True
    if isinstance(syms, str):
        return True
    if not isinstance(syms, list | tuple) or not syms:
        return False
    run = {_base(s) for s in run_symbols if str(s).strip()}
    need = {_base(s) for s in syms if str(s).strip()}
    if not need:
        return False
    return need <= run


def _breakdown_rows(runner: dict) -> list[dict]:
    """The runner's percent-only trade list. A missing list is not an empty
    book — the caller refuses to publish."""
    rows = runner.get("trade_breakdown") if isinstance(runner, dict) else None
    if not isinstance(rows, list):
        raise ValueError("runner JSON has no trade_breakdown")
    # Re-project through the one breakdown function so a runner that stuffed
    # a dollar key into a row cannot land it on the card.
    class _Row:
        def __init__(self, raw: dict):
            self.direction = raw.get("direction")
            self.entry_regime = raw.get("regime", raw.get("entry_regime"))
            self.setup = raw.get("setup")
            self.signal_type = raw.get("signal_type")
            self.exit_reason = raw.get("exit_reason")
            self.pnl_pct = raw.get("pnl_pct")
            self.confidence = raw.get("confidence")
            self.volume_spike_ratio = raw.get("volume_spike_ratio")

    return public_trade_breakdown(_Row(r) if isinstance(r, dict) else r for r in rows)


def scorecard_gates(cfg: dict) -> dict:
    """The gate block written onto a scorecard. One reading of the preset.

    Moving-average keys are added only when this preset has them, so the
    other presets' gate blocks stay the shape already on their cards.
    Dollar budgets are not gates.
    """
    gates = {
        "confidence_threshold": cfg.get("confidence_threshold"),
        "volume_spike_min": cfg.get("volume_spike_min"),
        "regime_filter": cfg.get("regime") or None,
        "rsi_max": cfg.get("rsi_threshold"),
        "rsi_min": cfg.get("rsi_min"),
        "direction": cfg.get("direction"),
        "symbols": cfg.get("symbols"),
    }
    fast = cfg.get("fast_period")
    slow = cfg.get("slow_period")
    if (isinstance(fast, int) and not isinstance(fast, bool)
            and isinstance(slow, int) and not isinstance(slow, bool)):
        syms = cfg.get("symbols")
        joined = ",".join(str(s) for s in syms) if isinstance(syms, (list, tuple)) else None
        gates["ma_fast"] = fast
        gates["ma_slow"] = slow
        gates["ma_timeframe"] = cfg.get("ma_timeframe") or None
        gates["ma_symbols"] = joined
        gates["max_gross_leverage"] = cfg.get("max_gross_leverage")
        gates["target_weight"] = cfg.get("target_weight")
        gates["utilization"] = cfg.get("utilization")
        gates["leverage"] = cfg.get("leverage")
        gates["signal_confidence"] = cfg.get("signal_confidence")
        gates["schedule_hours"] = cfg.get("schedule_hours")
    # A knob the runner was not asked to apply is not a gate of the number.
    for key in _unmodeled(cfg):
        gates.pop(key, None)
    return gates


def scorecard_note(cfg: dict) -> str:
    """The card's note. The moving-average sentence says which bars were read."""
    note = ("Design backtest on FROZEN benchmark data — percent/ratio "
            "only, never a dollar figure. Re-run the identical backtest "
            "in the Strategy Lab to reproduce.")
    fast = cfg.get("fast_period")
    slow = cfg.get("slow_period")
    if not (isinstance(fast, int) and not isinstance(fast, bool)
            and isinstance(slow, int) and not isinstance(slow, bool)):
        return note
    note += (
        f" Direction is the closed-bar {fast}/{slow} simple moving average, "
        "and the position reverses only when that relationship changes. "
    )
    target_raw = cfg.get("ma_timeframe")
    source_raw = cfg.get("ma_source_timeframe")
    if (isinstance(target_raw, str) and target_raw.strip()
            and isinstance(source_raw, str) and source_raw.strip()
            and target_raw.strip() != source_raw.strip()):
        target = target_raw.strip()
        source = source_raw.strip()
        note += (
            f"The average is read on closed {target} bars resampled from {source} "
            f"bars; a trailing unfinished {target} group is dropped. "
        )
    else:
        note += "The average is read on the run's closed bars. "
    note += (
        "The fill is that closed bar's close. "
        "The house risk gate does not size or exit this book."
    )
    conf = cfg.get("signal_confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        note += (
            f" Each row's confidence is the signal's stated {conf}, "
            "not a measured probability."
        )
    pending = _unmodeled(cfg)
    if pending:
        note += (
            " Recorded and not applied: " + ", ".join(pending) + "."
            " These knobs are not in the number."
        )
    return note


def build_card(*, preset_key: str, cfg: dict, runner: dict, dataset_name: str,
               dataset_hash: str, symbols: list[str], last_bars: int,
               code_sha_value: str | None, recorded_at: str) -> dict:
    """One scorecard. Metrics are ``project_metrics(runner)`` and nothing else."""
    rows = _breakdown_rows(runner)
    n = _as_number(runner.get("total_trades"))
    if isinstance(n, int) and len(rows) != n:
        raise ValueError(
            f"{preset_key}: trade_breakdown has {len(rows)} rows, "
            f"runner total_trades is {n}")
    measured = measured_metrics(runner)
    return {
        "format": "runeclaw.agent.scorecard.v1",
        "agent_id": _slug(preset_key),
        "preset": preset_key,
        "dataset": dataset_name,
        "dataset_hash": dataset_hash,
        "symbols": list(symbols),
        "bars": last_bars,
        "gates": scorecard_gates(cfg),
        "unmodeled": _unmodeled(cfg),
        # The runner's own figures. ``metrics`` is the projection of this
        # block; editing one percent without the other fails the check.
        "measured": measured,
        "metrics": project_metrics(runner),
        "trades": rows,
        "engine": "runeclaw.backtest",
        "honest": True,
        "recorded_at": recorded_at,
        "code_sha": code_sha_value,
        "note": scorecard_note(cfg),
    }


def _run_one(preset_key: str, cfg: dict, dataset: str, symbols: str,
             last_bars: int) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        out_path = tf.name
    cmd = [
        sys.executable, "-m", "bot.backtest.runner",
        "--dataset", dataset, "--symbols", symbols,
        "--last-bars", str(last_bars), "--honest", "--strict-data",
        "-o", out_path,
    ] + _gate_args(cfg)
    print(f"  [{preset_key}] {' '.join(cmd[4:])}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO), timeout=1800)
    if proc.returncode != 0:
        raise SystemExit(f"runner failed for {preset_key} (exit {proc.returncode})")
    with open(out_path) as fh:
        res = json.load(fh)
    Path(out_path).unlink(missing_ok=True)
    return res


def generate(dataset: str, symbols: str, last_bars: int,
             preset: str = "") -> list[str]:
    from bot.backtest.snapshot import load_manifest_multi
    from bot.skills.skill_registry import RunStrategySkill

    man = load_manifest_multi(dataset)
    dataset_hash = man.get("dataset_hash", "")
    if not dataset_hash:
        raise SystemExit(f"manifest for {dataset} has no dataset_hash")
    dataset_name = Path(dataset).name
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    stamped_at = datetime.now(UTC).isoformat(timespec="seconds")
    stamped_sha = code_sha()

    # The catalogue reads benchmark/scorecards (the committable tree). Writing
    # under data/benchmark lands on the runtime symlink and the Agents tab
    # keeps serving the previous file.
    out_dir = _scorecard_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    want = preset.strip().lower().replace("-", " ")
    matched = False
    for key, cfg in RunStrategySkill.PRESETS.items():
        if want and want not in (key, _slug(key).replace("-", " ")):
            continue
        matched = True
        if not publishes_scorecard(cfg):
            if want:
                raise SystemExit(
                    f"{key}: no scorecard is published. The percent exits are "
                    "recorded and not applied, and a majors 1h run is not "
                    "this daily book.")
            continue
        if not preset_universe_covered(cfg, sym_list):
            print(f"  [{key}] omitted: this run's symbols are not its universe. "
                  "No scorecard written.", flush=True)
            continue
        res = _run_one(key, cfg, dataset, symbols, last_bars)
        card = build_card(
            preset_key=key, cfg=cfg, runner=res,
            dataset_name=dataset_name, dataset_hash=dataset_hash,
            symbols=sym_list, last_bars=last_bars,
            code_sha_value=stamped_sha, recorded_at=stamped_at,
        )
        path = out_dir / f"{card['agent_id']}.json"
        path.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n")
        written.append(str(path.relative_to(REPO)))
        m = card["metrics"]
        print(f"    -> {path.name}: ret {m.get('total_return_pct')}% "
              f"PF {m.get('profit_factor')} trades {m.get('total_trades')}",
              flush=True)
    if want and not written:
        if matched:
            raise SystemExit(
                f"{preset!r} is a preset, and this run's symbols are not its "
                "universe. No scorecard was written.")
        raise SystemExit(f"no preset matched {preset!r}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=default_benchmark_dir())
    ap.add_argument("--symbols",
                    default="BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT")
    ap.add_argument("--last-bars", type=int, default=1500)
    ap.add_argument("--preset", default="",
                    help="Only this preset key or slug. Other scorecards are left as they are.")
    args = ap.parse_args()
    print(f"Generating agent scorecards on {args.dataset} "
          f"({args.symbols}, {args.last_bars} bars)…")
    written = generate(args.dataset, args.symbols, args.last_bars, preset=args.preset)
    print(f"\nWrote {len(written)} scorecards:\n  " + "\n  ".join(written))


if __name__ == "__main__":
    main()

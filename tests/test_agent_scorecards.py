"""MARKETPLACE Phase 2b — committed per-agent benchmark scorecards.

Every marketplace agent carries a REAL, reproducible frozen-benchmark scorecard
(generated offline by scripts/gen_agent_scorecards.py, gated by the Phase-2a
preset filters). §4-safe: percent/ratio only, never a dollar figure, stamped
with the dataset hash so anyone can reproduce it in the Lab.
"""
import glob
import json
import os

import pytest

from bot.core import strategy_catalog as sc
from bot.skills.skill_registry import RunStrategySkill

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Absolute, and tries both homes: the snapshots moved out from under the data/
# symlink so they could be committed at all (bot.backtest.snapshot has the
# reasoning). Falls back to the old path so a checkout that has not moved its
# copy still finds them.
_SC_DIR = next(
    (p for p in (os.path.join(_REPO, "benchmark", "scorecards"),
                 os.path.join(_REPO, "data", "benchmark", "scorecards"))
     if os.path.isdir(p)),
    os.path.join(_REPO, "benchmark", "scorecards"))

# Metric KEYS that would leak an absolute dollar figure onto a public card (§4).
# Checked as JSON keys, never as raw substrings (symbols like BTC/USDT contain
# "usd", which is not a leak).
_FORBIDDEN = ("net_pnl", "total_pnl", "final_equity", "balance", "avg_win_usd",
              "avg_loss_usd", "total_commission", "max_drawdown_usd")


def _all_keys(obj):
    """Every dict key anywhere in a nested structure."""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _all_keys(v)
    return out


_WINDOW = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]


def test_a_scorecard_exists_for_every_preset_the_frozen_window_covers():
    from scripts.gen_agent_scorecards import (
        preset_universe_covered,
        publishes_scorecard,
    )
    files = {os.path.basename(p) for p in glob.glob(os.path.join(_SC_DIR, "*.json"))}
    published = []
    for key, cfg in RunStrategySkill.PRESETS.items():
        name = f"{sc._slug(key)}.json"
        if not publishes_scorecard(cfg):
            assert name not in files, f"{key} must not publish a track record"
            continue
        if preset_universe_covered(cfg, _WINDOW):
            published.append(name)
            assert name in files, f"missing scorecard for {key}"
        else:
            assert name not in files, f"{key} must not publish this window"
    assert published


def test_scorecards_are_section4_safe_percent_ratio_only():
    for path in glob.glob(os.path.join(_SC_DIR, "*.json")):
        raw = open(path, encoding="utf-8").read()
        assert "$" not in raw, f"dollar sign in {path}"
        card = json.loads(raw)
        keys = _all_keys(card)
        for bad in _FORBIDDEN:
            assert bad not in keys, f"forbidden dollar key '{bad}' in {path}"
        assert card.get("format") == "runeclaw.agent.scorecard.v1"
        assert card.get("dataset_hash"), "scorecard must carry a dataset hash"
        # Only percent/ratio metrics.
        for mk in card["metrics"]:
            assert mk in {"total_return_pct", "profit_factor", "win_rate",
                          "max_drawdown_pct", "sharpe_ratio", "sortino_ratio",
                          "calmar_ratio", "total_trades"}


def test_catalog_attaches_scorecard_with_provenance():
    from scripts.gen_agent_scorecards import (
        preset_universe_covered,
        publishes_scorecard,
    )
    by_slug = {sc._slug(key): cfg for key, cfg in RunStrategySkill.PRESETS.items()}
    for card in sc.catalog():
        s = card.get("scorecard")
        cfg = by_slug[card["id"]]
        if not publishes_scorecard(cfg):
            assert isinstance(s, dict) and s.get("omitted"), card["id"]
            assert "not applied" in s["omitted"]
            assert "backtest pending" not in s["omitted"].lower()
            assert "metrics" not in s
            assert "not applied" in card["how"]
            continue
        if not preset_universe_covered(cfg, _WINDOW):
            assert isinstance(s, dict) and s.get("omitted"), card["id"]
            assert "No track record published" in s["omitted"]
            assert "pending" not in s["omitted"].lower()
            assert "metrics" not in s
            continue
        assert s is not None, f"{card['id']} has no scorecard attached"
        assert s["dataset"] and len(s["dataset_hash"]) == 12   # truncated for display
        m = s["metrics"]
        for mk in ("total_return_pct", "profit_factor", "win_rate",
                   "max_drawdown_pct", "sharpe_ratio", "total_trades"):
            assert mk in m
        # No dollar field survives onto the card.
        assert not any(k in m for k in _FORBIDDEN)


def test_catalog_scorecard_is_failsoft(monkeypatch):
    # A missing scorecard dir does not invent metrics. A preset whose knobs
    # do not size a fill still says the track record is unpublished. A house
    # preset with no file stays None — missing, not pending.
    monkeypatch.setattr(sc, "_SCORECARD_DIR", "/nonexistent/path/xyz")
    cat = sc.catalog()
    assert cat
    for card in cat:
        slot = card["scorecard"]
        if card["id"] in ("daily-vol-rotation", "alt-sweep"):
            assert slot["omitted"]
            assert "No track record published" in slot["omitted"]
            assert "metrics" not in slot
        else:
            assert slot is None


def test_generator_gate_args_map_preset_filters():
    from scripts.gen_agent_scorecards import _gate_args
    # Dip sniper: confidence + downtrend + RSI floor + short only.
    # The old ceiling (--rsi-max 35) kept the capitulation shorts.
    dip = _gate_args(RunStrategySkill.PRESETS["dip sniper"])
    assert "--confidence-threshold" in dip and "--regime-filter" in dip
    assert "--rsi-min" in dip and "35" in dip and "TREND_DOWN" in dip
    assert "--rsi-max" not in dip and "--direction" in dip and "short_only" in dip
    # Momentum hunter: the 3x spike, long only. No uptrend label, no confidence gate.
    mom = _gate_args(RunStrategySkill.PRESETS["momentum hunter"])
    assert "--volume-spike-min" in mom and "3.0" in mom
    assert "--direction" in mom and "long_only" in mom
    assert "--confidence-threshold" not in mom and "--regime-filter" not in mom
    # Safe scalper stands aside under RSI 35 and keeps its conviction floor.
    scalp = _gate_args(RunStrategySkill.PRESETS["safe scalper"])
    assert "--rsi-min" in scalp and "--confidence-threshold" in scalp
    # Full scan: no gates at all.
    assert _gate_args(RunStrategySkill.PRESETS["full scan"]) == []


# It reruns every published preset's backtest in a subprocess, one after
# another, so it outlives the gate's 60 s per-test limit on a slower box:
# 64.7-66.5 s on a 4-core container on 2026-10-07, over five presets. The
# limit is for a hung test; this one is slow by construction, as the
# preflight's own subprocess test is.
@pytest.mark.timeout(300)
def test_committed_scorecards_are_the_rerun_of_these_rules():
    """The six stats on the Agents card are this preset, on this frozen window.

    Restoring the old rule (Dip Sniper's RSI ceiling of 35, Momentum Hunter's
    TREND_UP filter, Safe Scalper with no RSI floor) changes the rerun, so it
    no longer matches the committed card.
    """
    from scripts.gen_agent_scorecards import (
        _METRIC_KEYS,
        _run_one,
        preset_universe_covered,
        publishes_scorecard,
        scorecard_gates,
    )
    dataset = os.path.join(_REPO, "benchmark", "majors_1h")
    symbols = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT"
    window = [s.strip() for s in symbols.split(",")]
    for key, cfg in RunStrategySkill.PRESETS.items():
        slug = sc._slug(key)
        if (not publishes_scorecard(cfg)
                or not preset_universe_covered(cfg, window)):
            assert not os.path.exists(os.path.join(_SC_DIR, f"{slug}.json"))
            continue
        with open(os.path.join(_SC_DIR, f"{slug}.json"), encoding="utf-8") as fh:
            card = json.loads(fh.read())
        assert card["gates"] == scorecard_gates(cfg), slug
        res = _run_one(key, cfg, dataset, symbols, 1500)
        got = {}
        for mk in _METRIC_KEYS:
            v = res.get(mk)
            got[mk] = round(v, 4) if isinstance(v, (int, float)) else v
        assert got == card["metrics"], (slug, got, card["metrics"])


def test_lab_run_request_accepts_preset_gates():
    from bot.api.lab import LabRunRequest
    req = LabRunRequest(dataset="majors_1h", symbols=["BTC/USDT:USDT"],
                        volume_spike_min=3.0, regime_filter="TREND_UP", rsi_max=35.0)
    assert req.volume_spike_min == 3.0 and req.regime_filter == "TREND_UP"
    assert req.rsi_max == 35.0
    # Defaults are OFF.
    plain = LabRunRequest(dataset="majors_1h")
    assert plain.volume_spike_min is None and plain.regime_filter == ""
    assert plain.rsi_max is None
    assert plain.rsi_min is None and plain.direction == ""

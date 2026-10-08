"""The public scorecard is a projection of the runner JSON.

The six numbers a card paints are read off the runner's own fields by
``project_public_metrics``. A missing field stays missing. A hand-edited
percent does not match that projection. The committed cards are that
projection of the runner that wrote them, pinned to the manifest hash and
to the commit the run was measured at.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from bot.backtest.runner import public_row
from scripts.gen_agent_scorecards import (
    PUBLIC_METRICS,
    build_card,
    project_public_metrics,
)

_REPO = Path(__file__).resolve().parents[1]
_CARDS = _REPO / "benchmark" / "scorecards"
_MANIFEST = _REPO / "benchmark" / "majors_1h" / "manifest.json"


def _runner(**over):
    base = {
        "total_return_pct": -0.1119,
        "profit_factor": 0.9123,
        "win_rate": 0.47368,
        "max_drawdown_pct": 0.874,
        "sharpe_ratio": -1.592,
        "sortino_ratio": -0.701,
        "calmar_ratio": -0.744,
        "total_trades": 2,
        "net_pnl": -12.5,
        "final_equity": 9987.5,
        "trade_breakdown": [
            {"direction": "SHORT", "regime": "TREND_DOWN", "setup": "swing",
             "signal_type": "regime_trend", "exit_reason": "SL",
             "pnl_pct": -1.2, "confidence": 0.81, "volume_spike_ratio": 2.4,
             "fills": 3, "pnl_usd": -8.0, "size_usd": 100.0},
            {"direction": "LONG", "regime": "TREND_DOWN", "setup": "scalp",
             "signal_type": "vwap_reversion", "exit_reason": "TP",
             "pnl_pct": 0.4, "confidence": 0.9, "volume_spike_ratio": None},
        ],
    }
    base.update(over)
    return base


def _cfg():
    return {
        "confidence_threshold": 0.7, "regime": "TREND_DOWN",
        "rsi_threshold": 35, "volume_spike_min": None, "symbols": None,
        "sl_atr_mult": None, "tp_atr_mult": None,
    }


def test_public_metrics_are_a_projection_of_the_runner_json():
    runner = _runner()
    projected = project_public_metrics(runner)
    assert projected == {
        "total_return_pct": -0.1119,
        "profit_factor": 0.9123,
        "win_rate": 0.4737,
        "max_drawdown_pct": 0.874,
        "sharpe_ratio": -1.592,
        "total_trades": 2,
    }
    card = build_card(
        preset_key="dip sniper", cfg=_cfg(), runner=runner,
        dataset_name="majors_1h", dataset_hash="abc",
        symbols=["BTC/USDT:USDT"], last_bars=1500,
        code_sha_value="a" * 40, recorded_at="2026-10-01T00:00:00+00:00",
    )
    for key, value in projected.items():
        assert card["metrics"][key] == value
    # The dollar keys on the runner and on a trade row do not survive.
    blob = json.dumps(card)
    assert "$" not in blob
    assert "net_pnl" not in blob and "final_equity" not in blob
    assert "pnl_usd" not in blob and "size_usd" not in blob
    assert card["trades"][0]["direction"] == "SHORT"
    assert card["trades"][0]["regime"] == "TREND_DOWN"
    assert card["trades"][0]["pnl_pct"] == -1.2
    assert card["trades"][0]["volume_spike_ratio"] == 2.4
    assert card["trades"][1]["volume_spike_ratio"] is None
    # A row is a position (audit B4-04) and says how many fills closed it. A
    # row that did not say is not one fill and not none.
    assert card["trades"][0]["fills"] == 3
    assert card["trades"][1]["fills"] is None


def test_a_missing_metric_is_not_written_as_zero():
    runner = _runner()
    del runner["total_return_pct"]
    runner["sharpe_ratio"] = None
    projected = project_public_metrics(runner)
    assert projected["total_return_pct"] is None
    assert projected["sharpe_ratio"] is None
    assert projected["profit_factor"] == 0.9123


def test_a_hand_edited_percent_fails_the_projection():
    runner = _runner()
    card = build_card(
        preset_key="dip sniper", cfg=_cfg(), runner=runner,
        dataset_name="majors_1h", dataset_hash="abc",
        symbols=["BTC/USDT:USDT"], last_bars=1500,
        code_sha_value="b" * 40, recorded_at="2026-10-01T00:00:00+00:00",
    )
    edited = json.loads(json.dumps(card))
    edited["metrics"]["total_return_pct"] = 12.34
    assert edited["metrics"]["total_return_pct"] != project_public_metrics(
        edited["measured"])["total_return_pct"]
    assert card["metrics"]["total_return_pct"] == project_public_metrics(
        card["measured"])["total_return_pct"]


def test_breakdown_drops_dollar_fields_and_keeps_a_missing_ratio():
    class _Trade:
        direction = "SHORT"
        entry_regime = "TREND_DOWN"
        setup = "swing"
        signal_type = "regime_trend"
        exit_reason = "SL"
        pnl_pct = -0.4
        confidence = 0.72
        volume_spike_ratio = None
        pnl_usd = -15.0
        size_usd = 200.0
        fills = 2

    rows = [public_row(_Trade())]
    assert rows == [{
        "direction": "SHORT", "regime": "TREND_DOWN", "setup": "swing",
        "signal_type": "regime_trend", "exit_reason": "SL",
        "pnl_pct": -0.4, "confidence": 0.72, "volume_spike_ratio": None,
        "fills": 2,
    }]
    assert "pnl_usd" not in rows[0]


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=_REPO, text=True).strip()


def _committed_cards():
    paths = sorted(_CARDS.glob("*.json"))
    assert paths, "no committed scorecards"
    return paths


def test_committed_public_metrics_project_the_measured_runner():
    manifest_hash = json.loads(_MANIFEST.read_text())["dataset_hash"]
    head = _git("rev-parse", "HEAD")
    for path in _committed_cards():
        card = json.loads(path.read_text())
        projected = project_public_metrics(card["measured"])
        for key in PUBLIC_METRICS:
            assert card["metrics"][key] == projected[key], path.name
        assert card["dataset_hash"] == manifest_hash
        assert card["metrics"]["total_trades"] == len(card["trades"])
        if path.name == "safe-scalper.json":
            # The runner applies 1.5 / 2.0, so the card must not say otherwise.
            assert card["unmodeled"] == []
        sha = card["code_sha"]
        assert re.fullmatch(r"[0-9a-f]{40}", sha), sha
        assert _git("cat-file", "-t", sha) == "commit"
        # Generated at the current HEAD (the json is not committed yet) or
        # at the parent of the commit that wrote this file (the code the
        # run measured).
        last = _git("log", "-1", "--format=%H", "--", str(path.relative_to(_REPO)))
        produced = _git("rev-parse", f"{last}^")
        assert sha == head or sha == produced, (path.name, sha, head, produced)
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", card["recorded_at"])


def test_a_hand_edited_committed_percent_fails():
    path = _CARDS / "dip-sniper.json"
    card = json.loads(path.read_text())
    assert card["metrics"]["total_return_pct"] == project_public_metrics(
        card["measured"])["total_return_pct"]
    card["metrics"]["total_return_pct"] = 12.34
    assert card["metrics"]["total_return_pct"] != project_public_metrics(
        card["measured"])["total_return_pct"]


def test_safe_scalper_exits_are_applied_so_the_card_does_not_call_them_unmodeled():
    from scripts.gen_agent_scorecards import _gate_args
    cfg = _cfg()
    cfg["sl_atr_mult"] = 1.5
    cfg["tp_atr_mult"] = 2.0
    args = _gate_args(cfg)
    assert "--sl-atr-mult" in args and "1.5" in args
    assert "--tp-atr-mult" in args and "2.0" in args
    card = build_card(
        preset_key="safe scalper", cfg=cfg, runner=_runner(),
        dataset_name="majors_1h", dataset_hash="abc",
        symbols=["BTC/USDT:USDT"], last_bars=1500,
        code_sha_value="c" * 40, recorded_at="2026-10-01T00:00:00+00:00",
    )
    assert card["unmodeled"] == []

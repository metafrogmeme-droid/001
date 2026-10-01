"""The public scorecard is a projection of the runner JSON.

The six numbers a card paints are read off the runner's own fields by
``project_public_metrics``. A missing field stays missing. A hand-edited
percent does not match that projection.
"""
from __future__ import annotations

import json

from bot.backtest.runner import public_trade_breakdown
from scripts.gen_agent_scorecards import (
    build_card,
    project_public_metrics,
)


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
             "pnl_usd": -8.0, "size_usd": 100.0},
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

    rows = public_trade_breakdown([_Trade()])
    assert rows == [{
        "direction": "SHORT", "regime": "TREND_DOWN", "setup": "swing",
        "signal_type": "regime_trend", "exit_reason": "SL",
        "pnl_pct": -0.4, "confidence": 0.72, "volume_spike_ratio": None,
    }]
    assert "pnl_usd" not in rows[0]


def test_safe_scalper_exits_stay_unmodeled_until_the_runner_is_given_them():
    cfg = _cfg()
    cfg["sl_atr_mult"] = 1.5
    cfg["tp_atr_mult"] = 2.0
    card = build_card(
        preset_key="safe scalper", cfg=cfg, runner=_runner(),
        dataset_name="majors_1h", dataset_hash="abc",
        symbols=["BTC/USDT:USDT"], last_bars=1500,
        code_sha_value="c" * 40, recorded_at="2026-10-01T00:00:00+00:00",
    )
    assert card["unmodeled"] == ["sl_atr_mult", "tp_atr_mult"]

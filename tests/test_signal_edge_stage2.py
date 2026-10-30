"""scripts/signal_edge_stage2.py: the sealed rule, both arms."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location(
        "signal_edge_stage2", ROOT / "scripts" / "signal_edge_stage2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(values, start=datetime(2025, 1, 6)):
    """One position a day, so a week block holds seven."""
    return [{"entry_time": (start + timedelta(days=i)).isoformat(), "net_pnl_pct": v,
             "symbol": "ETH/USDT"} for i, v in enumerate(values)]


def test_a_steady_winner_makes_money():
    j = _m().judge(_rows([0.5, 0.3, 0.4, 0.6] * 20))
    assert j["mean"] > 0 and j["lower"] > 0 and j["makes_money"] is True


def test_a_positive_mean_on_noise_does_not():
    j = _m().judge(_rows([3.0, -2.9] * 40))
    assert j["mean"] > 0 and j["lower"] < 0 and j["makes_money"] is False


def test_a_loser_does_not():
    assert _m().judge(_rows([-0.2, -0.1] * 40))["makes_money"] is False


def test_unreadable_rows_are_counted_not_zeroed():
    rows = _rows([0.5] * 20) + [{"entry_time": "2025-03-01T00:00:00", "net_pnl_pct": None,
                                 "symbol": "ETH/USDT"}]
    j = _m().judge(rows)
    assert j["unreadable"] == 1 and j["n"] == 20 and j["mean"] == 0.5


def test_no_rows_is_no_verdict():
    j = _m().judge([])
    assert j["mean"] is None and j["makes_money"] is False


def test_an_arm_must_make_money_on_both_datasets():
    m = _m()
    yes, no = {"makes_money": True, "mean": 1.0}, {"makes_money": False, "mean": -1.0}
    per = {"A0": {"F1": yes, "F2": no}, "A80": {"F1": yes, "F2": yes},
           "A0_gross": {"F1": yes, "F2": no}}
    v = m.verdict(per)
    assert v["A0"] is False and v["A80"] is True and v["information"] is False

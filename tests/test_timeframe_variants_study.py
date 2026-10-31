"""scripts/timeframe_variants_study.py and scripts/freeze_timeframe_variants.py:
the sealed rule and the universe rule, both arms."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(values, start=datetime(2026, 5, 4)):
    return [{"entry_time": (start + timedelta(days=i)).isoformat(), "net_pnl_pct": v,
             "size_rel": 1.0, "exit_reason": "SL"} for i, v in enumerate(values)]


def test_a_steady_winner_with_winning_folds_makes_money():
    j = _load("timeframe_variants_study").judge(_rows([0.6, 0.4, 0.5, 0.7] * 15), [1, 2, 1, 1, 2, 1])
    assert j["makes_money"] is True


def test_a_winning_mean_with_losing_folds_does_not():
    j = _load("timeframe_variants_study").judge(_rows([0.6, 0.4, 0.5, 0.7] * 15), [-1, -2, 1, -1, -2, -1])
    assert j["mean"] > 0 and j["lower"] > 0 and j["makes_money"] is False


def test_a_positive_mean_on_noise_does_not():
    j = _load("timeframe_variants_study").judge(_rows([3.0, -2.9] * 40), [1] * 6)
    assert j["mean"] > 0 and j["lower"] < 0 and j["makes_money"] is False


def test_too_few_positions_is_no_verdict_not_a_fail():
    j = _load("timeframe_variants_study").judge(_rows([1.0] * 39), [1] * 6)
    assert j["makes_money"] is None and "no verdict" in j["verdict"]


def test_the_bound_is_shared_by_the_four_tested_arms():
    m = _load("timeframe_variants_study")
    assert m.TESTED == ["H1", "H3", "H2a", "H2b"] and m.LOWER_PCT == 2.5 / 4


def test_the_universe_rule_skips_used_coins_and_unread_turnover():
    m = _load("freeze_timeframe_variants")
    perps = [{"symbol": s + "USDT", "baseCoin": s, "symbolType": t, "status": "online"}
             for s, t in (("AAA", "crypto"), ("BTC", "crypto"), ("GOLD", "metal"), ("CCC", "crypto"),
                          ("DDD", "crypto"))]
    tickers = [{"symbol": "AAAUSDT", "turnover24h": "5"}, {"symbol": "BTCUSDT", "turnover24h": "99"},
               {"symbol": "GOLDUSDT", "turnover24h": "50"}, {"symbol": "CCCUSDT", "turnover24h": "7"},
               {"symbol": "DDDUSDT", "turnover24h": None}]
    assert [r["base"] for r in m.rank(perps, tickers, {"BTC"})] == ["CCC", "AAA"]


def test_the_pick_keeps_rank_order_and_needs_every_series_in_full():
    m = _load("freeze_timeframe_variants")
    ranked = [{"symbol": s} for s in ("A", "B", "C", "D")]
    served = {"1h": {"A", "B", "C", "D"}, "15m": {"B", "C", "D"}, "5m": {"C", "B", "A"}}
    assert m.pick(ranked, served) == ["B", "C"]

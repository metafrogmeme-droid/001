"""ETH Moving-Average Trend: the 50/200 relationship, and nothing else.

The preset is the real one. Direction comes from the closed-bar simple
averages. A flip reverses. The same relationship holds. A non-ETH symbol is
outside the universe. An unreadable average is not a long and not a short.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.compat import UTC
from bot.core import strategy_catalog as sc
from bot.core.ma_trend import (
    closed_ohlcv,
    ma_margin,
    ma_trend_step,
    symbol_allowed,
)
from bot.core.strategy_gate import check_confirm, describe_gates, resolve_key
from bot.skills.skill_registry import RunStrategySkill

_HOUR = timedelta(hours=1)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _preset():
    return RunStrategySkill.PRESETS["eth ma trend"]


def _closes_long_then_short(fast: int, slow: int) -> list[float]:
    """150 bars at 100, then `fast` at 130, then `fast` at 40, for 50/200.

    At the first full slow window the fast average is 130 and the slow
    average is 107.5. After the drop the fast average is 40 and the slow
    average is 92.5.
    """
    return [100.0] * (slow - fast) + [130.0] * fast + [40.0] * fast


def _bars(closes, symbol: str, *, wick_at: int | None = None) -> list[BacktestBar]:
    out = []
    for i, close in enumerate(closes):
        low = 1.0 if wick_at is not None and i == wick_at else close
        high = close
        out.append(BacktestBar(
            timestamp=_T0 + i * _HOUR,
            open=close, high=max(high, close), low=min(low, close),
            close=close, volume=1.0, symbol=symbol,
        ))
    return out


def _cfg(**overrides) -> BacktestConfig:
    cfg = _preset()
    base = dict(
        symbol="ETH/USDT:USDT",
        timeframe="1h",
        initial_balance=10_000.0,
        leverage=cfg["leverage"],
        lookback_size=50,
        scan_interval=1000,
        ma_fast=cfg["fast_period"],
        ma_slow=cfg["slow_period"],
        ma_timeframe="",
        ma_symbols="ETHUSDT",
        ma_target_weight=cfg["target_weight"],
        ma_max_gross_leverage=cfg["max_gross_leverage"],
        ma_utilization=cfg["utilization"],
        ma_signal_confidence=cfg["signal_confidence"],
    )
    base.update(overrides)
    return BacktestConfig(**base)


def _run(bars, config) -> object:
    eng = BacktestEngine(config)
    try:
        return asyncio.run(eng.run(bars))
    finally:
        eng.cleanup()


def test_the_preset_is_the_playbook_parameters():
    cfg = _preset()
    assert cfg["label"] == "ETH Moving-Average Trend"
    assert cfg["fast_period"] == 50
    assert cfg["slow_period"] == 200
    assert cfg["target_weight"] == 1
    assert cfg["max_gross_leverage"] == 1
    assert cfg["leverage"] == 1
    assert cfg["utilization"] == 1
    assert cfg["symbols"] == ["ETHUSDT"]
    assert cfg["schedule_hours"] == 4
    assert cfg["ma_timeframe"] == "4h"
    assert cfg["ma_source_timeframe"] == "1h"
    assert cfg["signal_confidence"] == 0.7
    # Private dollars live on the preset. They are not a public gate.
    assert cfg["margin_budget_usd"] == 10_000
    assert cfg["starting_capital_usd"] == 10_000
    assert cfg.get("confidence_threshold") is None


def test_fifty_two_hundred_decides_direction_flip_and_hold():
    cfg = _preset()
    fast, slow = cfg["fast_period"], cfg["slow_period"]
    long_closes = [100.0] * (slow - fast) + [130.0] * fast
    step = ma_trend_step(long_closes, fast, slow, None)
    assert step["side"] == "LONG"
    assert step["action"] == "enter"
    assert step["fast"] == 130.0
    assert step["slow"] == 107.5

    held = ma_trend_step(long_closes, fast, slow, "LONG")
    assert held["action"] == "hold"
    assert held["side"] == "LONG"

    other = ma_trend_step(long_closes, fast, slow, "SHORT")
    assert other["action"] == "reverse"
    assert other["side"] == "LONG"

    dropped = long_closes + [40.0] * fast
    flip = ma_trend_step(dropped, fast, slow, "LONG")
    assert flip["side"] == "SHORT"
    assert flip["action"] == "reverse"
    assert flip["fast"] == 40.0
    assert flip["slow"] == 92.5

    stay = ma_trend_step(dropped, fast, slow, "SHORT")
    assert stay["action"] == "hold"
    assert stay["side"] == "SHORT"


def test_an_unreadable_average_is_not_a_side():
    cfg = _preset()
    fast, slow = cfg["fast_period"], cfg["slow_period"]
    long_closes = [100.0] * (slow - fast) + [130.0] * fast
    short = ma_trend_step(long_closes[:-1], fast, slow, None)
    assert short["side"] is None
    assert short["action"] == "stand_aside"

    bad = list(long_closes)
    bad[-1] = float("nan")
    unread = ma_trend_step(bad, fast, slow, None)
    assert unread["side"] is None
    assert unread["action"] == "stand_aside"
    held = ma_trend_step(bad, fast, slow, "LONG")
    assert held["side"] is None
    assert held["action"] == "hold"

    flat = ma_trend_step([100.0] * slow, fast, slow, None)
    assert flat["side"] is None
    assert flat["action"] == "stand_aside"
    flat_held = ma_trend_step([100.0] * slow, fast, slow, "SHORT")
    assert flat_held["side"] is None
    assert flat_held["action"] == "hold"


def test_eth_is_the_universe_and_btc_is_not():
    assert symbol_allowed("ETHUSDT", "ETHUSDT") is True
    assert symbol_allowed("ETH/USDT:USDT", "ETHUSDT") is True
    assert symbol_allowed("BTC/USDT:USDT", "ETHUSDT") is False
    assert symbol_allowed("ETHUSDT", "") is False
    cfg = _preset()
    refused = check_confirm("eth ma trend", cfg, "BTC/USDT:USDT", 0.9)
    assert refused["ok"] is False
    assert "symbols" in refused["enforced"]
    allowed = check_confirm("eth ma trend", cfg, "ETH/USDT:USDT", 0.9)
    assert allowed["ok"] is True
    eth_spot = check_confirm("eth ma trend", cfg, "ETHUSDT", None)
    assert eth_spot["ok"] is True
    confirm, scan = describe_gates(cfg)
    assert "symbols" in confirm
    assert "fast_period" in scan and "slow_period" in scan
    assert "margin_budget_usd" not in confirm and "margin_budget_usd" not in scan


def test_the_slug_and_alias_resolve_to_the_preset():
    presets, aliases = RunStrategySkill.PRESETS, RunStrategySkill.ALIASES
    assert resolve_key("eth-ma-trend", presets, aliases) == "eth ma trend"
    assert resolve_key("ethma", presets, aliases) == "eth ma trend"
    assert resolve_key("eth ma trend", presets, aliases) == "eth ma trend"


def test_run_does_not_place_or_close():
    class _Boom:
        def __getattr__(self, _name):
            raise AssertionError("the moving-average preset must not scan")

    text = asyncio.run(RunStrategySkill().execute(_Boom(), strategy="ethma"))
    assert "50/200" in text
    assert "No order is placed" in text
    assert "$" not in text
    assert "10000" not in text and "10,000" not in text


def test_the_public_card_has_no_dollar_budget():
    card = sc.get_agent("eth-ma-trend")
    assert card is not None
    assert card["name"] == "ETH Moving-Average Trend"
    blob = repr(card)
    assert "$" not in blob
    assert "margin_budget" not in blob
    assert "starting_capital" not in blob
    assert "10000" not in blob and "10,000" not in blob
    assert "50/200" in card["how"]
    assert "reversing only when that relationship changes" in card["how"]
    assert "ETHUSDT" in card["how"]
    assert "resampled from 1h" in card["how"]
    assert "1\u00d7" in card["how"]
    dip = sc.get_agent("dip-sniper")
    assert "moving-average" not in dip["how"]


def test_gate_args_carry_the_average_and_leave_full_scan_alone():
    from scripts.gen_agent_scorecards import _gate_args, scorecard_gates
    args = _gate_args(_preset())
    assert args[args.index("--ma-fast") + 1] == "50"
    assert args[args.index("--ma-slow") + 1] == "200"
    assert args[args.index("--ma-timeframe") + 1] == "4h"
    assert args[args.index("--ma-symbols") + 1] == "ETHUSDT"
    assert args[args.index("--leverage") + 1] == "1"
    assert args[args.index("--ma-max-gross-leverage") + 1] == "1"
    assert "--confidence-threshold" not in args
    assert _gate_args(RunStrategySkill.PRESETS["full scan"]) == []
    gates = scorecard_gates(_preset())
    assert gates["ma_fast"] == 50 and gates["ma_slow"] == 200
    assert "margin_budget_usd" not in gates
    assert "starting_capital_usd" not in gates
    assert "margin_budget" not in scorecard_note_text()


def scorecard_note_text() -> str:
    from scripts.gen_agent_scorecards import scorecard_note
    return scorecard_note(_preset())


def test_the_note_says_the_bars_are_resampled():
    note = scorecard_note_text()
    assert "resampled from 1h" in note
    assert "trailing unfinished" in note
    assert "stated 0.7" in note
    assert "$" not in note


def test_a_resampled_4h_bar_is_not_the_1h_close():
    # Four 100s, four 200s, then one extra 200 that has not closed a 4h group.
    rows = []
    start = int(_T0.timestamp() * 1000)
    hour = 3_600_000
    for i, close in enumerate([100, 100, 100, 100, 200, 200, 200, 200, 200]):
        rows.append([start + i * hour, close, close, close, close, 1])
    eight = closed_ohlcv(rows[:8], "1h", "4h")
    nine = closed_ohlcv(rows, "1h", "4h")
    assert [r[4] for r in eight] == [100, 200]
    assert [r[4] for r in nine] == [100, 200]
    # fast 1 / slow 2 on those 4h closes is long. The same periods on the
    # last two 1h closes are equal, so a 1h reading would not be long.
    step = ma_trend_step([r[4] for r in nine], 1, 2, None)
    assert step["side"] == "LONG"
    assert step["fast"] == 200
    assert step["slow"] == 150
    one_hour = ma_trend_step([r[4] for r in rows][-2:], 1, 2, None)
    assert one_hour["side"] is None


def test_margin_keeps_leverage_and_gross_cap_apart():
    assert ma_margin(1000, 1, 1, 1, 1) == 1000
    assert ma_margin(1000, 1, 1, 1, 10) == 100
    assert ma_margin(1000, 0, 1, 1, 1) is None
    assert ma_margin(None, 1, 1, 1, 1) is None
    assert ma_margin(float("nan"), 1, 1, 1, 1) is None


def test_the_book_flips_and_a_stop_wick_does_not():
    cfg = _preset()
    closes = _closes_long_then_short(cfg["fast_period"], cfg["slow_period"])
    result = _run(_bars(closes, "ETH/USDT:USDT"), _cfg())
    assert [t.exit_reason for t in result.trades] == ["MA_FLIP", "END_OF_DATA"]
    assert [t.direction for t in result.trades] == ["LONG", "SHORT"]
    assert result.total_trades == 2


def test_a_flat_average_and_a_short_window_do_not_trade():
    flat = _run(_bars([100.0] * 250, "ETH/USDT:USDT"), _cfg())
    assert flat.total_trades == 0
    unread = _run(
        _bars([100.0] * 250, "ETH/USDT:USDT"),
        _cfg(ma_slow=400))
    assert unread.total_trades == 0


def test_btc_is_not_traded_and_the_portfolio_path_is_the_same_rule():
    closes = _closes_long_then_short(50, 200)
    btc = _run(_bars(closes, "BTC/USDT:USDT"), _cfg(symbol="BTC/USDT:USDT"))
    assert btc.total_trades == 0

    data = {
        "ETH/USDT:USDT": _bars(closes, "ETH/USDT:USDT"),
        "BTC/USDT:USDT": _bars(closes, "BTC/USDT:USDT"),
    }
    pb = PortfolioBacktester(_cfg(symbol="ETH/USDT:USDT"), symbols=list(data))
    try:
        result = asyncio.run(pb.run(data))
    finally:
        pb.cleanup()
    assert result.total_trades == 2
    assert [t.direction for t in result.trades] == ["LONG", "SHORT"]
    assert all(t.symbol == "ETH/USDT:USDT" for t in result.trades)
    assert pb.per_symbol["BTC/USDT:USDT"]["trades"] == 0


def test_four_hour_resample_in_the_engine_ignores_a_partial_group():
    # 16 closed hours: three groups of 100 and one of 130. A 17th hour at 1
    # would flip a 1-and-2 average of the 1h closes, and must not flip the
    # 4h book.
    closes = [100.0] * 12 + [130.0] * 4 + [1.0]
    result = _run(
        _bars(closes, "ETH/USDT:USDT"),
        _cfg(ma_fast=1, ma_slow=2, ma_timeframe="4h", lookback_size=4,
             scan_interval=1000))
    assert result.total_trades == 1
    assert result.trades[0].direction == "LONG"
    assert result.trades[0].exit_reason == "END_OF_DATA"


def test_lab_forwards_the_average_and_refuses_a_half_pair():
    import pytest

    from bot.api.lab import LabRunRequest, _ma_gate_args
    empty = LabRunRequest(dataset="majors_1h")
    assert _ma_gate_args(empty) == ([], {})
    req = LabRunRequest(
        dataset="majors_1h", ma_fast=50, ma_slow=200, ma_timeframe="4h",
        ma_symbols="ETHUSDT", ma_target_weight=1, ma_max_gross_leverage=1,
        ma_utilization=1, leverage=1, signal_confidence=0.7)
    args, params = _ma_gate_args(req)
    assert args[args.index("--ma-fast") + 1] == "50"
    assert args[args.index("--leverage") + 1] == "1"
    assert params["ma_symbols"] == "ETHUSDT"
    with pytest.raises(Exception):
        _ma_gate_args(LabRunRequest(dataset="majors_1h", ma_fast=200, ma_slow=50))

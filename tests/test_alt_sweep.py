"""ALT Sweep: the 36/144 relationship, on its own fifteen markets.

Direction is the closed-bar simple average this repo already reads. A flip
reverses. The same relationship holds. A symbol outside the fifteen is not
this universe. An unreadable average is not a long and not a short. The
trailing stop and the six scale-outs are recorded and not applied: the fill
path has no percent trail and no six-rung percent ladder, and this preset
does not invent those fills. No majors-window scorecard is published.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.compat import UTC
from bot.core import strategy_catalog as sc
from bot.core.ma_trend import ma_trend_step, symbol_allowed
from bot.core.strategy_gate import check_confirm, describe_gates, resolve_key
from bot.skills.skill_registry import RunStrategySkill

_HOUR = timedelta(hours=1)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_WINDOW = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
_OUTSIDERS = ("BTCUSDT", "ETH/USDT:USDT", "SOLUSDT", "DOGEUSDT")


def _preset():
    return RunStrategySkill.PRESETS["alt sweep"]


def _long_then_short(fast: int, slow: int) -> list[float]:
    """`slow - fast` bars at 100, `fast` at 130, then `fast` at 40.

    The first full slow window has a fast average of 130 and a slow average
    of 107.5 when fast/slow is 36/144. After the drop the fast average is 40
    and the slow average is 92.5.
    """
    return [100.0] * (slow - fast) + [130.0] * fast + [40.0] * fast


def _bars(closes, symbol: str) -> list[BacktestBar]:
    out = []
    for i, close in enumerate(closes):
        out.append(BacktestBar(
            timestamp=_T0 + i * _HOUR,
            open=close, high=close, low=close, close=close,
            volume=1.0, symbol=symbol,
        ))
    return out


def _cfg(**overrides) -> BacktestConfig:
    cfg = _preset()
    base = dict(
        symbol="ADA/USDT:USDT",
        timeframe="1h",
        initial_balance=10_000.0,
        leverage=cfg["leverage"],
        lookback_size=50,
        scan_interval=1000,
        ma_fast=cfg["fast_period"],
        ma_slow=cfg["slow_period"],
        ma_timeframe="",
        ma_symbols=",".join(cfg["symbols"]),
    )
    base.update(overrides)
    return BacktestConfig(**base)


def _run(bars, config):
    eng = BacktestEngine(config)
    try:
        return asyncio.run(eng.run(bars))
    finally:
        eng.cleanup()


def test_the_preset_records_the_given_parameters():
    cfg = _preset()
    assert cfg["label"] == "ALT Sweep"
    assert cfg["fast_period"] == 36
    assert cfg["slow_period"] == 144
    assert cfg["trailing_stop_pct"] == 0.04
    assert cfg["risk_per_trade"] == 0.015
    assert cfg["leverage"] == 5
    assert cfg["margin_mode"] == "isolated"
    assert cfg["max_portfolio_positions"] == 8
    assert cfg["minimum_target_positions"] == 6
    assert cfg["symbols"] == [
        "ADAUSDT", "VETUSDT", "ALGOUSDT", "HBARUSDT", "GRTUSDT",
        "CHZUSDT", "SANDUSDT", "MANAUSDT", "GALAUSDT", "PYTHUSDT",
        "CROUSDT", "ZILUSDT", "WIFUSDT", "IOTAUSDT", "XLMUSDT",
    ]
    assert cfg.get("schedule_hours") is None
    assert cfg.get("ma_timeframe") is None
    assert cfg.get("confidence_threshold") is None
    ladder = cfg["take_profit_ladder"]
    assert [rung["trigger_pct"] for rung in ladder] == [
        0.05, 0.10, 0.15, 0.22, 0.30, 0.40]
    assert [rung["close_fraction"] for rung in ladder] == [
        0.20, 0.20, 0.15, 0.15, 0.10, 0.20]


def test_the_close_fractions_sum_to_one_and_a_short_ladder_does_not():
    ladder = _preset()["take_profit_ladder"]
    total = sum(Decimal(str(rung["close_fraction"])) for rung in ladder)
    short = sum(Decimal(str(rung["close_fraction"])) for rung in ladder[:-1])
    assert total == Decimal("1")
    assert short != Decimal("1")
    assert short == Decimal("0.80")


def test_thirty_six_one_forty_four_decides_direction_flip_and_hold():
    cfg = _preset()
    fast, slow = cfg["fast_period"], cfg["slow_period"]
    long_closes = [100.0] * (slow - fast) + [130.0] * fast
    step = ma_trend_step(long_closes, fast, slow, None)
    assert step["side"] == "LONG"
    assert step["action"] == "enter"
    assert step["fast"] == 130.0
    assert step["slow"] == 107.5
    assert step["fast"] > step["slow"]

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
    assert flip["fast"] < flip["slow"]

    stay = ma_trend_step(dropped, fast, slow, "SHORT")
    assert stay["action"] == "hold"
    assert stay["side"] == "SHORT"


def test_an_unreadable_or_equal_average_is_not_a_side():
    cfg = _preset()
    fast, slow = cfg["fast_period"], cfg["slow_period"]
    long_closes = [100.0] * (slow - fast) + [130.0] * fast
    readable = ma_trend_step(long_closes, fast, slow, None)
    assert readable["side"] == "LONG"

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
    assert flat["fast"] == flat["slow"] == 100.0
    assert flat["side"] is None
    assert flat["action"] == "stand_aside"
    flat_held = ma_trend_step([100.0] * slow, fast, slow, "SHORT")
    assert flat_held["side"] is None
    assert flat_held["action"] == "hold"


def test_the_fifteen_are_the_universe_and_a_major_is_not():
    cfg = _preset()
    universe = ",".join(cfg["symbols"])
    for symbol in cfg["symbols"]:
        assert symbol_allowed(symbol, universe) is True
    assert symbol_allowed("ADA/USDT:USDT", universe) is True
    for symbol in _OUTSIDERS:
        assert symbol_allowed(symbol, universe) is False
    assert symbol_allowed("ADAUSDT", "") is False

    allowed = check_confirm("alt sweep", cfg, "ADA/USDT:USDT", 0.9)
    assert allowed["ok"] is True
    assert "symbols" in allowed["enforced"]
    refused = check_confirm("alt sweep", cfg, "BTC/USDT:USDT", 0.9)
    assert refused["ok"] is False
    assert "symbols" in refused["enforced"]
    eth = check_confirm("alt sweep", cfg, "ETHUSDT", None)
    assert eth["ok"] is False
    confirm, scan = describe_gates(cfg)
    assert "symbols" in confirm
    assert "fast_period" in scan and "slow_period" in scan
    assert "trailing_stop_pct" not in confirm and "trailing_stop_pct" not in scan
    assert "take_profit_ladder" not in confirm and "take_profit_ladder" not in scan


def test_the_slug_and_alias_resolve_to_the_preset():
    presets, aliases = RunStrategySkill.PRESETS, RunStrategySkill.ALIASES
    assert resolve_key("alt-sweep", presets, aliases) == "alt sweep"
    assert resolve_key("altsweep", presets, aliases) == "alt sweep"
    assert resolve_key("alt sweep", presets, aliases) == "alt sweep"
    assert resolve_key("eth-ma-trend", presets, aliases) == "eth ma trend"


def test_run_states_the_rule_and_does_not_place_or_close():
    class _Boom:
        def __getattr__(self, _name):
            raise AssertionError("this preset must not scan or place")

    text = asyncio.run(RunStrategySkill().execute(_Boom(), strategy="alt-sweep"))
    assert "36/144" in text
    assert "not applied" in text
    assert "No order is placed" in text
    assert "No position is closed" in text or "no position is closed" in text
    assert "$" not in text
    alias = asyncio.run(RunStrategySkill().execute(_Boom(), strategy="altsweep"))
    assert alias == text
    with pytest.raises(AssertionError):
        asyncio.run(RunStrategySkill().execute(_Boom(), strategy="full scan"))


def test_the_public_card_has_no_dollar_and_no_measured_scorecard():
    card = sc.get_agent("alt-sweep")
    assert card is not None
    assert card["name"] == "ALT Sweep"
    blob = repr(card)
    assert "$" not in blob
    assert card["scorecard"] is None
    # USDT in a symbol is a market name. A dollar field is a key.
    keys: set[str] = set()

    def _walk(obj) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                keys.add(str(key).lower())
                _walk(value)
        elif isinstance(obj, list):
            for value in obj:
                _walk(value)

    _walk(card)
    for forbidden in ("net_pnl", "balance", "equity", "margin_budget_usd",
                      "starting_capital_usd", "size_usd", "pnl_usd"):
        assert forbidden not in keys
    assert "ADAUSDT" in card["how"]
    assert "total_return" not in blob
    how = card["how"]
    assert "36/144" in how
    assert "not applied" in how
    assert "No frozen-benchmark scorecard is published" in how
    assert "applied" not in how.replace("not applied", "")
    assert "trailing stop" in how
    assert "+" not in how
    dip = sc.get_agent("dip-sniper")
    assert dip["scorecard"] is not None
    assert "36/144" not in dip["how"]
    assert "not applied" not in dip["how"]
    eth = sc.get_agent("eth-ma-trend")
    assert eth["scorecard"] is not None
    assert "1×" in eth["how"]
    assert "not applied" not in eth["how"]


def test_exits_are_unmodeled_and_the_note_does_not_call_them_applied():
    from scripts.gen_agent_scorecards import (
        _gate_args,
        _unmodeled,
        preset_universe_covered,
        scorecard_note,
    )
    cfg = _preset()
    args = _gate_args(cfg)
    assert args[args.index("--ma-fast") + 1] == "36"
    assert args[args.index("--ma-slow") + 1] == "144"
    joined = args[args.index("--ma-symbols") + 1]
    assert joined.split(",") == cfg["symbols"]
    blob = " ".join(args)
    assert "trailing" not in blob
    assert "take_profit" not in blob
    assert "risk_per_trade" not in blob
    assert "--leverage" not in args
    missing = _unmodeled(cfg)
    for key in (
        "trailing_stop_pct", "take_profit_ladder", "risk_per_trade",
        "margin_mode", "max_portfolio_positions", "minimum_target_positions",
        "leverage",
    ):
        assert key in missing
    assert _unmodeled(RunStrategySkill.PRESETS["eth ma trend"]) == []
    assert _unmodeled(RunStrategySkill.PRESETS["safe scalper"]) == []
    note = scorecard_note(cfg)
    assert "not applied" in note
    assert "resampled from" not in note
    assert "$" not in note
    eth_note = scorecard_note(RunStrategySkill.PRESETS["eth ma trend"])
    assert "resampled from 1h" in eth_note
    assert "not applied" not in eth_note
    assert preset_universe_covered(cfg, _WINDOW) is False
    assert preset_universe_covered(
        RunStrategySkill.PRESETS["eth ma trend"], _WINDOW) is True
    assert preset_universe_covered(
        RunStrategySkill.PRESETS["full scan"], _WINDOW) is True


def test_the_generator_does_not_run_the_majors_window(monkeypatch):
    from scripts import gen_agent_scorecards as gen

    def _no_run(*_a, **_k):
        raise AssertionError("the majors runner must not start")

    monkeypatch.setattr(gen, "_run_one", _no_run)
    monkeypatch.setattr(
        "bot.backtest.snapshot.load_manifest_multi",
        lambda _dataset: {"dataset_hash": "abc"},
    )
    with pytest.raises(SystemExit) as exc:
        gen.generate(
            "benchmark/majors_1h",
            "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT",
            1500, preset="alt-sweep")
    assert "No scorecard was written" in str(exc.value)


def test_a_clear_direction_does_not_invent_a_fill():
    """The relationship is long, then short. No margin inputs, so no fill.

    A fill here would be a trail or a scale-out the book does not model.
    """
    cfg = _preset()
    closes = _long_then_short(cfg["fast_period"], cfg["slow_period"])
    step = ma_trend_step(closes, cfg["fast_period"], cfg["slow_period"], None)
    assert step["side"] == "SHORT"
    result = _run(_bars(closes, "ADA/USDT:USDT"), _cfg())
    assert result.total_trades == 0
    assert result.trades == []
    outsider = _run(
        _bars(closes, "BTC/USDT:USDT"),
        _cfg(symbol="BTC/USDT:USDT"))
    assert outsider.total_trades == 0
    assert outsider.trades == []

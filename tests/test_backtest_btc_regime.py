"""`--btc-regime-days`: an entry only on BTC's side of its N-day mean.

docs/research/prereg/long_regime_2026-11. Driven through the real
`BacktestEngine._apply_timeframe_variants` on a real frozen snapshot written
with the repo's own snapshot tools, both sides of the mean, and the readings
that cannot be made.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.runner import _preset_gate_kwargs, build_parser
from bot.backtest.snapshot import _manifest_path, build_manifest, safe_symbol
from bot.utils.models import Direction, TradeIdea

DAY0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ENGINES: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    while _ENGINES:
        _ENGINES.pop().cleanup()


def _snapshot(tmp_path, closes):
    """A frozen daily BTC snapshot, one bar per close, from DAY0."""
    sym = "BTC/USDT:USDT"
    bars = [BacktestBar(timestamp=DAY0 + timedelta(days=i), open=c, high=c, low=c,
                        close=c, volume=1.0, symbol=sym) for i, c in enumerate(closes)]
    DataLoader.save_csv(bars, str(tmp_path / f"{safe_symbol(sym)}.csv.gz"))
    m = build_manifest(tmp_path, "1d", len(bars), {sym: bars})
    _manifest_path(tmp_path).write_text(json.dumps(m))
    return str(tmp_path)


def _engine(dataset, days=3):
    eng = BacktestEngine(BacktestConfig(symbol="SOL/USDT:USDT", timeframe="1h",
                                        btc_regime_days=days, btc_regime_dataset=dataset))
    _ENGINES.append(eng)
    return eng


def _at(eng, when: datetime):
    """The engine deciding on the 1h bar that opens at ``when``."""
    eng._all_raw = [[int(when.timestamp() * 1000), 1, 1, 1, 1, 1]]
    return 0


def _idea(direction):
    return TradeIdea(id="t", asset="SOL/USDT:USDT", direction=direction, entry_price=100.0,
                     stop_loss=98.0 if direction == Direction.LONG else 102.0,
                     take_profit=104.0 if direction == Direction.LONG else 96.0,
                     confidence=0.8, reasoning="x")


def test_above_the_mean_a_long_enters_and_a_short_does_not(tmp_path):
    eng = _engine(_snapshot(tmp_path, [100, 100, 100, 130]))
    # Day 3's bar closes at DAY0 + 4d; decided on the bar that closes then.
    i = _at(eng, DAY0 + timedelta(days=4, hours=-1))
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), i) is not None
    assert eng._apply_timeframe_variants(_idea(Direction.SHORT), i) is None


def test_below_the_mean_a_short_enters_and_a_long_does_not(tmp_path):
    eng = _engine(_snapshot(tmp_path, [100, 100, 100, 70]))
    i = _at(eng, DAY0 + timedelta(days=4, hours=-1))
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), i) is None
    assert eng._apply_timeframe_variants(_idea(Direction.SHORT), i) is not None


def test_a_day_not_yet_closed_is_not_read(tmp_path):
    """One hour earlier, day 3 has not closed: the reading is days 0-2, flat,
    and a flat close is above nothing, so neither side enters."""
    eng = _engine(_snapshot(tmp_path, [100, 100, 100, 130]))
    i = _at(eng, DAY0 + timedelta(days=4, hours=-2))
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), i) is None
    assert eng._apply_timeframe_variants(_idea(Direction.SHORT), i) is None


def test_too_few_closed_days_admit_nothing(tmp_path):
    eng = _engine(_snapshot(tmp_path, [100, 130]), days=3)
    i = _at(eng, DAY0 + timedelta(days=10))
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), i) is None


def test_an_unreadable_snapshot_admits_nothing(tmp_path):
    eng = _engine(str(tmp_path / "absent"))
    i = _at(eng, DAY0 + timedelta(days=10))
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), i) is None
    assert eng._apply_timeframe_variants(_idea(Direction.SHORT), i) is None


def test_off_is_off(tmp_path):
    eng = _engine(str(tmp_path / "absent"), days=0)
    i = _at(eng, DAY0)
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), i) is not None


def test_the_runner_flags_reach_the_config():
    args = build_parser().parse_args(["--dataset", "x", "--btc-regime-days", "150",
                                      "--btc-regime-dataset", "benchmark/b"])
    kw = _preset_gate_kwargs(args)
    assert kw["btc_regime_days"] == 150 and kw["btc_regime_dataset"] == "benchmark/b"
    kw = _preset_gate_kwargs(build_parser().parse_args(["--dataset", "x"]))
    assert kw["btc_regime_days"] == 0 and kw["btc_regime_dataset"] == ""

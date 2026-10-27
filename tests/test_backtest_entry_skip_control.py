"""The random-skip control drops a share of approved entries, releases what they held, and is off by default."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.backtest.engine import BacktestEngine, entry_skip_draw
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.runner import _limit_realism_kwargs, build_parser
from bot.compat import UTC
from bot.utils.models import Direction, TradeIdea

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _engine(**cfg):
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=100_000.0,
        slippage_pct=0.0, commission_pct=0.0, **cfg))


def _bar(hours=0):
    return BacktestBar(timestamp=_T0 + timedelta(hours=hours), open=100.0, high=101.0,
                       low=99.0, close=100.0, volume=1000.0, symbol="BTC/USDT")


def _idea(tid="TI-1"):
    return TradeIdea(id=tid, asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=95.0, take_profit=110.0, confidence=0.7, reasoning="x",
                     source="t", timestamp=_T0, order_type="market")


def _rc():
    return SimpleNamespace(position_size_usd=1_000.0, verdict=SimpleNamespace(value="APPROVED"))


def test_the_draw_is_deterministic_uniform_and_depends_on_the_seed():
    assert entry_skip_draw("BTC", 1.0, 0) == entry_skip_draw("BTC", 1.0, 0)
    assert entry_skip_draw("BTC", 1.0, 0) != entry_skip_draw("BTC", 1.0, 1)
    draws = [entry_skip_draw("BTC", float(i), 7) for i in range(20000)]
    assert all(0.0 <= d < 1.0 for d in draws)
    assert abs(sum(d < 0.3 for d in draws) / len(draws) - 0.3) < 0.01


def test_skip_everything_queues_nothing_and_releases_the_intent():
    eng = _engine(fill_mode="next_open", entry_skip_p=1.0)
    idea = _idea()
    # `register_pending_intent` is a no-op unless the forward-intents flag is on,
    # so plant the intent: a fixture that cannot hold one cannot test its release.
    eng.risk._pending_intents[idea.id] = ("majors", "LONG", 0.0)
    assert idea.id in eng.risk._pending_intents
    eng._queue_entry(idea, _rc(), _bar())
    assert eng._pending_entry is None
    assert eng._entries_skipped_random == 1
    assert idea.id not in eng.risk._pending_intents


def test_skip_nothing_and_the_default_both_queue_the_entry():
    for cfg in ({"entry_skip_p": 0.0}, {}):
        eng = _engine(fill_mode="next_open", **cfg)
        idea = _idea()
        eng.risk.register_pending_intent(idea)
        eng._queue_entry(idea, _rc(), _bar())
        assert eng._pending_entry is not None
        assert eng._entries_skipped_random == 0


def test_the_skip_share_is_close_to_p_over_many_bars():
    eng = _engine(fill_mode="next_open", entry_skip_p=0.35, entry_skip_seed=3)
    n = 3000
    for i in range(n):
        eng._pending_entry = None
        idea = _idea(f"TI-{i}")
        eng.risk.register_pending_intent(idea)
        eng._queue_entry(idea, _rc(), _bar(hours=i))
    assert abs(eng._entries_skipped_random / n - 0.35) < 0.03


def test_the_flags_reach_the_config_and_are_absent_by_default():
    assert "entry_skip_p" not in _limit_realism_kwargs(build_parser().parse_args([]))
    kw = _limit_realism_kwargs(build_parser().parse_args(
        ["--entry-skip-p", "0.3", "--entry-skip-seed", "5"]))
    assert kw["entry_skip_p"] == 0.3 and kw["entry_skip_seed"] == 5
    cfg = BacktestConfig(symbol="BTC/USDT", **kw)
    assert cfg.entry_skip_p == 0.3 and cfg.entry_skip_seed == 5

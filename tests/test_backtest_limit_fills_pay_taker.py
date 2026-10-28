"""`limit_fills_pay_taker` keeps the fills and swaps the maker cost for the taker cost."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.runner import _limit_realism_kwargs, build_parser
from bot.compat import UTC
from bot.utils.models import Direction, TradeIdea

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _engine(**cfg):
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=100_000.0, fill_mode="live",
        slippage_pct=0.05, commission_pct=0.06, **cfg))


def _bar(low, high, hours=0):
    return BacktestBar(timestamp=_T0 + timedelta(hours=hours), open=(low + high) / 2, high=high,
                       low=low, close=(low + high) / 2, volume=1000.0, symbol="BTC/USDT")


def _idea(entry=95.0):
    return TradeIdea(id="TI-1", asset="BTC/USDT", direction=Direction.LONG, entry_price=entry,
                     stop_loss=entry * 0.95, take_profit=entry * 1.10, confidence=0.7,
                     reasoning="x", source="t", timestamp=_T0, order_type="limit")


def _fill(eng):
    rc = SimpleNamespace(position_size_usd=1_000.0, verdict=SimpleNamespace(value="APPROVED"))
    eng._rest_limit(_idea(), rc, _bar(99.0, 101.0))
    eng._drain_pending_limits(_bar(94.0, 99.0, hours=1))
    return eng


def test_by_default_a_filled_limit_is_a_maker_at_its_own_price():
    eng = _fill(_engine())
    assert eng._limits_filled == 1 and eng._maker_fills == 1
    assert [round(float(p.entry_price), 6) for p in eng.portfolio.open_positions] == [95.0]


def test_with_the_knob_the_same_fill_pays_taker_slippage_and_no_maker_fill_is_counted():
    eng = _fill(_engine(limit_fills_pay_taker=True))
    assert eng._limits_filled == 1                       # the fill itself is unchanged
    assert eng._maker_fills == 0                         # but it is not priced as a maker
    entry = float(eng.portfolio.open_positions[0].entry_price)
    assert abs(entry - 95.0 * (1 + 0.05 / 100)) < 1e-9   # long pays the slippage above its limit


def test_the_flag_reaches_the_config_and_is_absent_by_default():
    assert "limit_fills_pay_taker" not in _limit_realism_kwargs(build_parser().parse_args([]))
    kw = _limit_realism_kwargs(build_parser().parse_args(["--limit-fills-pay-taker"]))
    assert kw == {"limit_fills_pay_taker": True}
    assert BacktestConfig(symbol="BTC/USDT", **kw).limit_fills_pay_taker is True

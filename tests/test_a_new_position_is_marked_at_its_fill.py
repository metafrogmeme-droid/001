"""A new position is marked at its fill, never at an older position's last price.

The tracker kept an asset's last mark after its position closed and valued
the next position in that asset at it. On the Limit Entry Scanner's card run
an ETH long filled at 1,670 was marked at 2,329, a price from a position
closed months before: `_update_peak` kept the fake +39% as the high-water
mark, the drawdown gate read 10-15% against a peak the book never had, and it
refused every entry for the run's last 94 days. Driven on the real tracker.
"""
from __future__ import annotations

import pytest

from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeIdea


def _idea(price, direction=Direction.LONG, asset="ETH/USDT:USDT"):
    stop = price * (0.98 if direction == Direction.LONG else 1.02)
    target = price * (1.04 if direction == Direction.LONG else 0.96)
    return TradeIdea(asset=asset, direction=direction, entry_price=price, stop_loss=stop,
                     take_profit=target, confidence=0.7, reasoning="t", source="scan")


def _book(tmp_path):
    return PortfolioTracker(initial_balance=10_000.0, state_file=str(tmp_path / "p.json"))


def test_a_reopened_asset_is_valued_at_its_new_fill_not_the_old_mark(tmp_path):
    book = _book(tmp_path)
    first = book.open_position(_idea(2_329.0), 500.0, leverage=5)
    book.mark_to_market({"ETH/USDT:USDT": 2_329.0})
    book.close_position(first.trade_id, 2_329.0)
    peak = book._peak_equity
    # Months later ETH is at 1,670; nothing has marked it since the close.
    book.open_position(_idea(1_670.0), 500.0, leverage=5)
    state = book.snapshot()
    assert state.equity_usd == round(book.balance + 500.0, 2), "a fill is worth its margin"
    assert book._peak_equity == peak, "the high-water mark moved on a mark nobody saw"
    assert state.current_drawdown_pct == round((peak - state.equity_usd) / peak * 100, 2)


def test_a_short_reopened_below_the_old_mark_is_not_a_fake_loss(tmp_path):
    book = _book(tmp_path)
    first = book.open_position(_idea(1_670.0, Direction.SHORT), 500.0, leverage=5)
    book.mark_to_market({"ETH/USDT:USDT": 1_670.0})
    book.close_position(first.trade_id, 1_670.0)
    book.open_position(_idea(2_329.0, Direction.SHORT), 500.0, leverage=5)
    state = book.snapshot()
    assert state.equity_usd == round(book.balance + 500.0, 2)
    assert state.current_drawdown_pct < 1.0


def test_a_fresher_mark_still_moves_the_position(tmp_path):
    """The other arm: marking at the fill does not freeze the valuation."""
    book = _book(tmp_path)
    book.open_position(_idea(1_670.0), 500.0, leverage=5)
    book.mark_to_market({"ETH/USDT:USDT": 1_670.0 * 1.02})
    state = book.snapshot()
    assert state.equity_usd > book.balance + 500.0
    assert book._peak_equity == pytest.approx(state.equity_usd, abs=0.01)

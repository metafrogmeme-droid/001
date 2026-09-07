"""QC-2 entry/limit quality round (2026-07 quant audit, each re-verified).

1. Round numbers above $100k: the $100 step failed the module's own
   step/price >= 0.1% meaningfulness guard, so six-figure BTC had NO
   round-number confluence level at all — the code's own comment cited
   $105,000 as intended behavior. Now a $1000 step applies up there.
2. Entry safeguards 0a/0b: the entry path keyed everything off
   ticker['last'] with no staleness check (thin TradFi perps go 40+ min
   between ticks) and no bid/ask spread gate. Both guards run BEFORE any
   money-path step and abort with nothing placed.
3. Drift->market chase bound: the momentum fallback would market-chase a
   drifted limit at ANY distance; it now refuses beyond
   DRIFT_MARKET_MAX_CHASE_PCT (default 5%) before touching the exchange.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.limit_entry import _round_number_near
from bot.core.live_executor import LiveExecutor


class TestRoundNumbersAboveSixFigures:
    def test_btc_at_six_figures_has_round_levels_again(self):
        assert _round_number_near(105000.0) == 105000.0
        assert _round_number_near(104950.0) == 105000.0   # within 0.3%

    def test_far_from_a_thousand_level_still_returns_none(self):
        assert _round_number_near(105400.0) is None       # 0.38% from 105k

    def test_lower_magnitudes_unchanged(self):
        assert _round_number_near(10500.0) == 10500.0     # $100 step band
        assert _round_number_near(4150.0) == 4150.0       # $50 step band
        assert _round_number_near(245.0) == 245.0         # $5 step band
        assert _round_number_near(0.15) == 0.15           # $0.01 step band


class TestEntrySafeguardsExistBeforeMoneyPath:
    def test_stale_ticker_and_spread_gates_precede_safeguard_1(self):
        # The ORDERING claim, restated at the granularity the code now has.
        # The stale/spread gate and SAFEGUARD 1 each left execute() for a
        # method of their own (`_entry_market_gate`, `_size_or_block`), so
        # "the guard runs before any money step" is a statement about the
        # order execute() CALLS them in — which is what it always meant; the
        # old version measured it by the position of two strings inside one
        # 1,800-line body and could only ever be a proxy for this.
        src = inspect.getsource(LiveExecutor.execute)
        gate = src.find("await self._entry_market_gate(")
        sg1 = src.find("self._size_or_block(")
        assert gate > 0, "the stale-ticker/spread gate is no longer consulted by execute()"
        assert sg1 > 0, "SAFEGUARD 1's sizing step is gone from execute()"
        assert gate < sg1, "staleness/spread guard must run before any money step"
        # The two blocks still live in that gate, and SAFEGUARD 1 in the sizer.
        gate_src = inspect.getsource(LiveExecutor._entry_market_gate)
        assert "BLOCKED_STALE_TICKER" in gate_src
        assert "BLOCKED_WIDE_SPREAD" in gate_src
        assert "current_price <= idea.stop_loss" in inspect.getsource(LiveExecutor._size_or_block)
        # Both are audited blocks that place nothing.
        assert "nothing was placed" in gate_src


class TestDriftMarketChaseBound:
    @pytest.mark.asyncio
    async def test_refuses_beyond_max_chase_without_touching_exchange(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        pos = SimpleNamespace(direction="LONG", entry_price=100.0,
                              symbol="BTC/USDT:USDT")
        exchange = MagicMock()
        exchange.fetch_ohlcv = AsyncMock()
        # 6% past the planned limit > the 5% default bound -> refuse,
        # and the exchange is never even queried for candles.
        assert await ex._check_drift_market_fallback(exchange, pos, 106.0) is False
        exchange.fetch_ohlcv.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_within_bound_still_evaluates_momentum(self):
        ex = LiveExecutor.__new__(LiveExecutor)
        pos = SimpleNamespace(direction="LONG", entry_price=100.0,
                              symbol="BTC/USDT:USDT")
        exchange = MagicMock()
        exchange.fetch_ohlcv = AsyncMock(return_value=[])   # -> False, but reached
        assert await ex._check_drift_market_fallback(exchange, pos, 103.0) is False
        exchange.fetch_ohlcv.assert_awaited()

"""A trail starts only where its strategy trails, on every entry path.

Two switches decide whether a position trails: `TRAILING_STOP_ENABLED`
(global) and the strategy's own (`SCALP_TRAILING_ENABLED` defaults False,
the others True). The market entry read only the strategy's switch; the three
limit-fill paths read only the global one. So a scalp that entered by limit
got a trailing state its strategy says it must not have, and the trail then
moved its stop: driven, a scalp limit fill at 100 had its stop moved to 100.5
at a mark of 102.5, while the same scalp entered at market never trailed.
`tests/test_a_restart_does_not_turn_a_scalp_into_a_swing.py` records "scalp
trailing OFF" as the rule.

`trail_starts_for` is the one reading: both switches. Every path that builds a
trailing state asks it.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from bot.config import CONFIG
from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition


def _executor(price: float) -> LiveExecutor:
    ex = LiveExecutor()
    venue = AsyncMock()
    venue.fetch_ticker = AsyncMock(
        return_value={"last": price, "timestamp": time.time() * 1000})
    ex._exchange = venue
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = time.time()
    ex.close_position = AsyncMock(return_value="CLOSED")
    ex._place_sl_tp = AsyncMock(return_value=("SL1", "TP1"))
    ex._save_positions = lambda *a, **k: None
    ex._record_warning = lambda *a, **k: None
    ex._fetch_order = AsyncMock(
        return_value={"status": "closed", "average": 100.0, "filled": 1.0})
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._guard_fill_leverage = AsyncMock(return_value=None)
    return ex


def _resting(strategy: str) -> LivePosition:
    p = LivePosition(
        trade_id="TI-s1", symbol="ETH/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=20.0, stop_loss=98.5, take_profit=103.0,
        leverage=5, opened_at=datetime.now(UTC) - timedelta(minutes=30),
        status="pending_fill", order_type="limit", limit_order_id="OID1",
        atr_at_entry=1.0)
    p.strategy_type = strategy
    p.signal_type = "momentum_confluence"
    return p


# ── the reading ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("strategy", ["scalp", "intraday", "swing", "position"])
def test_the_reading_is_both_switches(strategy):
    own = CONFIG.strategy_types.get_trailing_enabled(strategy)
    assert le.trail_starts_for(strategy) is (CONFIG.trailing.enabled and own)
    object.__setattr__(CONFIG.trailing, "enabled", False)
    try:
        assert le.trail_starts_for(strategy) is False
    finally:
        object.__setattr__(CONFIG.trailing, "enabled", True)


def test_a_scalp_does_not_trail_by_default():
    assert le.trail_starts_for("scalp") is False
    assert le.trail_starts_for("swing") is True


# ── the limit fill ──────────────────────────────────────────────────────

@pytest.mark.parametrize("strategy, trails", [("scalp", False), ("swing", True)])
def test_a_limit_fill_trails_only_if_its_strategy_does(strategy, trails, monkeypatch):
    monkeypatch.setattr("bot.core.live_executor.asyncio.sleep", AsyncMock())
    ex = _executor(100.0)
    p = _resting(strategy)
    ex._positions[p.trade_id] = p
    asyncio.run(ex.check_positions())
    assert p.status == "open"
    assert (p.trailing_state is not None) is trails, p.trailing_state


def test_a_scalp_limit_fills_stop_is_not_moved_by_a_trail(monkeypatch):
    monkeypatch.setattr("bot.core.live_executor.asyncio.sleep", AsyncMock())
    ex = _executor(100.0)
    p = _resting("scalp")
    ex._positions[p.trade_id] = p
    asyncio.run(ex.check_positions())
    p.filled_at = datetime.now(UTC) - timedelta(minutes=5)
    p.partial_tp_state = {"ladder": "off", "reason": "isolate"}
    ex._exchange.fetch_ticker = AsyncMock(
        return_value={"last": 102.5, "timestamp": time.time() * 1000})
    moves: list = []

    async def _move(exch, pos, new_sl):
        moves.append(new_sl)
        return True

    ex._update_exchange_sl = _move
    ex._struct_candles = AsyncMock(return_value=None)
    asyncio.run(ex.check_positions())
    assert moves == [] and p.stop_loss == 98.5, (moves, p.stop_loss)


# ── every path that builds a trail asks the reading ─────────────────────

def _builders() -> list[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(le)))
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                 for n in ast.walk(fn) if isinstance(n, ast.Call)}
        if "make_trailing_state" in calls:
            out.append((fn.name, "trail_starts_for" in calls))
    return out


def test_every_trail_builder_asks_the_reading():
    builders = _builders()
    assert len(builders) >= 4, builders          # the market entry and three fills
    assert all(asks for _, asks in builders), builders


@pytest.mark.parametrize("strategy, trails", [("scalp", False), ("swing", True)])
def test_a_partial_fill_adopted_at_cancel_trails_only_if_its_strategy_does(
        strategy, trails):
    # The second fill path: a cancelled limit that had partly filled.
    ex = _executor(100.0)
    ex._reattempt_post_fill_sl = AsyncMock(return_value=("SL1", "TP1", ""))
    p = _resting(strategy)
    ex._positions[p.trade_id] = p
    asyncio.run(ex._adopt_partial_fill(ex._exchange, p.trade_id, p, 0.5, 100.0, "cancel"))
    assert p.status == "open"
    assert (p.trailing_state is not None) is trails, p.trailing_state

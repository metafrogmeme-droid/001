"""A stop or target of 0.0 was never stated, so it is never hit.

The per-tick static SL/TP check in `check_positions` compared the price with
`pos.stop_loss` and `pos.take_profit` with no `> 0` guard. For a LONG
`price >= take_profit` is true when the target is 0; for a SHORT
`price >= stop_loss` is true when the stop is 0. `_local_stop_breached`, the
grace sub-loop's reading, had the guards and a docstring calling itself a
"pure mirror ... kept in lock-step" with the per-tick check, which it was not.

Adoption writes `stop_loss=0, take_profit=0` for a limit order it finds on the
venue (placed by hand, or the bot's own order whose record was lost), and for
an adopted position whose levels the venue did not state. The fill places no
stop (side-sanity refuses 0/0), and the next tick closed the position at
market as "TP HIT" (LONG) or "SL HIT" (SHORT): an operator's own order,
closed by the bot a minute after it filled.

The per-tick check asks `_local_stop_breached` now, so there is one reading.
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

from bot.core.live_executor import LiveExecutor, LivePosition

SYM = "ETH/USDT:USDT"


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
    ex._place_sl_tp = AsyncMock(return_value=(None, None))
    ex._save_positions = lambda *a, **k: None
    ex._record_warning = lambda *a, **k: None
    return ex


def _pos(direction: str, sl: float, tp: float, *, entry: float = 2500.0,
         sl_id=None, tp_id=None) -> LivePosition:
    p = LivePosition(
        trade_id="A1", symbol=SYM, direction=direction, entry_price=entry,
        quantity=1.0, cost_usd=0.0, stop_loss=sl, take_profit=tp, leverage=0,
        status="open", opened_at=datetime.now(UTC) - timedelta(minutes=30),
        origin="adopted")
    p.sl_order_id = sl_id
    p.tp_order_id = tp_id
    return p


def _closes(p: LivePosition, price: float) -> list[tuple]:
    ex = _executor(price)
    ex._positions[p.trade_id] = p
    asyncio.run(ex.check_positions())
    return [c.args for c in ex.close_position.await_args_list]


# ── an unstated level closes nothing ────────────────────────────────────

@pytest.mark.parametrize("direction, sl, tp, entry, sl_id, tp_id", [
    ("LONG", 0.0, 0.0, 0.0, None, None),         # the survey's first drive
    ("SHORT", 0.0, 0.0, 0.0, None, None),
    ("LONG", 0.0, 0.0, 2500.0, None, None),      # entry read, no levels
    ("SHORT", 0.0, 0.0, 2500.0, None, None),
    ("LONG", 2400.0, 0.0, 0.0, "SL1", None),     # a stop, no target
    ("SHORT", 0.0, 2300.0, 0.0, None, "TP1"),    # a target, no stop
])
def test_an_unstated_level_closes_nothing(direction, sl, tp, entry, sl_id, tp_id):
    p = _pos(direction, sl, tp, entry=entry, sl_id=sl_id, tp_id=tp_id)
    assert _closes(p, 2500.0) == []


# ── a stated level still closes, both arms, both sides ──────────────────

@pytest.mark.parametrize("direction, sl, tp, price, reason", [
    ("LONG", 2400.0, 2600.0, 2390.0, "SL HIT"),
    ("LONG", 2400.0, 2600.0, 2610.0, "TP HIT"),
    ("SHORT", 2600.0, 2400.0, 2610.0, "SL HIT"),
    ("SHORT", 2600.0, 2400.0, 2390.0, "TP HIT"),
    # One level stated and the other not: the stated one still closes.
    ("LONG", 0.0, 2600.0, 2610.0, "TP HIT"),
    ("SHORT", 2600.0, 0.0, 2610.0, "SL HIT"),
    ("LONG", 2400.0, 0.0, 2390.0, "SL HIT"),
    ("SHORT", 0.0, 2400.0, 2390.0, "TP HIT"),
])
def test_a_stated_level_still_closes(direction, sl, tp, price, reason):
    got = _closes(_pos(direction, sl, tp), price)
    assert len(got) == 1, got
    assert got[0][0] == "A1" and got[0][1].startswith(reason) and got[0][2] == price, got


def test_inside_the_band_closes_nothing():
    assert _closes(_pos("LONG", 2400.0, 2600.0), 2500.0) == []
    assert _closes(_pos("SHORT", 2600.0, 2400.0), 2500.0) == []


# ── the adopted limit order, from fill to the tick after ────────────────

@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_an_adopted_limit_fill_is_not_closed_on_the_next_tick(direction, monkeypatch):
    # The grace sub-loop sleeps between its reads; that wait measures nothing.
    monkeypatch.setattr("bot.core.live_executor.asyncio.sleep", AsyncMock())
    ex = _executor(2500.0)
    # The record adopt_exchange_limit_orders builds for an order it found.
    p = LivePosition(
        trade_id="TI-abc123", symbol=SYM, direction=direction,
        entry_price=2500.0, quantity=0.1, cost_usd=0.0, stop_loss=0,
        take_profit=0, leverage=0,
        opened_at=datetime.now(UTC) - timedelta(minutes=30),
        status="pending_fill", order_type="limit", limit_order_id="OID1",
        origin="reclaimed")
    ex._positions[p.trade_id] = p
    ex._fetch_order = AsyncMock(
        return_value={"status": "closed", "average": 2500.0, "filled": 0.1})
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._guard_fill_leverage = AsyncMock(return_value=None)

    first = asyncio.run(ex.check_positions())
    assert any("LIMIT FILLED" in m for m in first), first
    assert p.status == "open" and p.stop_loss == 0 and p.take_profit == 0
    asyncio.run(ex.check_positions())
    assert ex.close_position.await_args_list == []


# ── one reading: the helper decides, and the per-tick check asks it ─────

@pytest.mark.parametrize("direction, sl, tp, price", [
    ("LONG", 0.0, 0.0, 2500.0), ("SHORT", 0.0, 0.0, 2500.0),
    ("LONG", 2400.0, 0.0, 2500.0), ("SHORT", 0.0, 2300.0, 2500.0),
])
def test_the_helper_reads_an_unstated_level_as_not_hit(direction, sl, tp, price):
    assert LiveExecutor._local_stop_breached(
        LiveExecutor.__new__(LiveExecutor), _pos(direction, sl, tp), price) == (False, "")


def _check_positions_tree() -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(LiveExecutor.check_positions)))


def test_the_per_tick_check_asks_the_helper():
    calls = [n for n in ast.walk(_check_positions_tree())
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_local_stop_breached"]
    assert calls, "check_positions no longer asks _local_stop_breached"


def test_the_per_tick_check_keeps_no_private_copy_of_the_comparison():
    # A price compared with a level inside check_positions is a second reading
    # of "breached", which is how the unguarded copy came to disagree.
    hits = []
    for c in ast.walk(_check_positions_tree()):
        if not isinstance(c, ast.Compare):
            continue
        parts = [c.left, *c.comparators]
        spelled = {ast.unparse(p) for p in parts}
        if ({"pos.stop_loss", "pos.take_profit"} & spelled
                and any(isinstance(p, ast.Name) and p.id == "price" for p in parts)):
            hits.append(ast.unparse(c))
    assert not hits, hits


@pytest.mark.parametrize("direction, sl, tp", [
    ("LONG", 2400.0, 2600.0), ("SHORT", 2600.0, 2400.0),
    ("LONG", 0.0, 0.0), ("SHORT", 0.0, 0.0),
])
@pytest.mark.parametrize("price", [0.0, -1.0])
def test_a_price_nobody_read_breaches_nothing(direction, sl, tp, price):
    # This early return is why a LONG stop of 0 and a SHORT target of 0 need no
    # guard of their own: only a non-positive price could reach them, and it
    # is refused here first. Their guards stay so the rule reads the same on
    # all four arms; this pins the property that makes two of them redundant.
    assert LiveExecutor._local_stop_breached(
        LiveExecutor.__new__(LiveExecutor), _pos(direction, sl, tp), price) == (False, "")

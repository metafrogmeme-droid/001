"""The per-strategy trailing distance reaches no position under the default rule.

`StrategyTypeConfig` declares a trailing ATR multiplier per strategy (scalp 1.0,
intraday 1.2, swing 1.5, position 2.0) and `TrailingStopConfig` declares
`TRAILING_ATR_MULT`, which nothing read and is deleted. The live monitor hands
the strategy's figure to `update_trailing_stop`, and that function reads it on
one path only: a state with no ``"stage"`` key. `make_trailing_state` writes ``"stage": 0`` on every
state it builds and the default rule is ``multistage``, whose distance is the
stage table (`TRAIL_STAGE{1,2,3}_ATR_MULT`: 2.0, 1.5, 1.0). The backtest and the
paper book never pass it at all.

So the config comments, the class docstring ("position: trailing after 1.5R")
and the income map ("trailing ENABLED at 1.5 ATR") described a distance no
position trails at. This drives the claim the comments now make: under the
default rule the figure moves nothing, and it moves the stop only for a state
saved without a stage.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.trailing import make_trailing_state, update_trailing_stop

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("intraday", "swing", "position")
# A long from 100 with a 2.0 stop distance and ATR 1.0; the path walks it past
# every stage threshold (1R, 2R, 3R) and back.
PATH = (101.0, 102.5, 104.5, 106.5, 105.0)


def _walk(mult: float, *, staged: bool) -> list[float]:
    state = make_trailing_state(100.0, "LONG", 2.0, 1.0)
    if not staged:
        state.pop("stage")
    sl, out = 98.0, []
    for price in PATH:
        sl, _ = update_trailing_stop(state, price, sl, "LONG", trail_atr_mult=mult)
        out.append(round(sl, 6))
    return out


def test_a_new_state_carries_a_stage():
    assert "stage" in make_trailing_state(100.0, "LONG", 2.0, 1.0)


def test_the_default_rule_is_multistage():
    assert CONFIG.trailing.trail_rule == "multistage"


@pytest.mark.parametrize("mult", [0.5, 1.0, 1.5, 2.0, 3.0])
def test_under_the_stage_table_the_figure_moves_nothing(mult):
    assert _walk(mult, staged=True) == _walk(1.5, staged=True)


def test_a_state_with_no_stage_trails_at_the_figure():
    assert _walk(1.0, staged=False) != _walk(2.0, staged=False)
    # 106.5 best, 1.0 ATR: the legacy trail sits one figure behind the best.
    assert _walk(1.0, staged=False)[-1] == pytest.approx(105.5)
    assert _walk(2.0, staged=False)[-1] == pytest.approx(104.5)


# ── through the live monitor, one position per strategy ─────────────────

def _position(strategy: str, *, staged: bool = True) -> LivePosition:
    p = LivePosition(
        trade_id=f"TI-{strategy}", symbol="ETH/USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=20.0, stop_loss=98.0,
        take_profit=120.0, leverage=5,
        opened_at=datetime.now(UTC) - timedelta(minutes=30),
        status="open", atr_at_entry=1.0)
    p.strategy_type = strategy
    p.signal_type = "momentum_confluence"
    p.filled_at = p.opened_at
    p.trailing_state = make_trailing_state(100.0, "LONG", 2.0, 1.0)
    if not staged:
        p.trailing_state.pop("stage")
    p.partial_tp_state = {"ladder": "off", "reason": "isolate"}
    return p


def _stop_after(strategy: str, price: float, *, staged: bool = True) -> float:
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
    ex._save_positions = lambda *a, **k: None
    ex._record_warning = lambda *a, **k: None
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._struct_candles = AsyncMock(return_value=None)

    async def _move(exch, pos, new_sl):
        return True

    ex._update_exchange_sl = _move
    p = _position(strategy, staged=staged)
    ex._positions[p.trade_id] = p
    asyncio.run(ex.check_positions())
    return p.stop_loss


def test_the_strategies_declare_different_distances():
    mults = {s: CONFIG.strategy_types.get_trailing_atr_mult(s) for s in STRATEGIES}
    assert len(set(mults.values())) == len(STRATEGIES), mults


@pytest.mark.parametrize("price", [102.5, 104.5, 106.5])
def test_every_strategy_trails_to_the_same_stop(price, monkeypatch):
    monkeypatch.setattr("bot.core.live_executor.asyncio.sleep", AsyncMock())
    stops = {s: _stop_after(s, price) for s in STRATEGIES}
    assert len(set(stops.values())) == 1, stops
    assert stops["swing"] > 98.0, stops          # the trail did move it


def test_a_saved_state_with_no_stage_trails_at_its_strategys_figure(monkeypatch):
    # The one path the figure reaches, driven through the monitor: a state
    # saved before stages existed trails one strategy figure behind the best.
    monkeypatch.setattr("bot.core.live_executor.asyncio.sleep", AsyncMock())
    stops = {s: _stop_after(s, 106.5, staged=False) for s in STRATEGIES}
    for s, stop in stops.items():
        assert stop == pytest.approx(
            106.5 - CONFIG.strategy_types.get_trailing_atr_mult(s)), stops


# ── the prose ────────────────────────────────────────────────────────────

CONFIG_SRC = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")


def test_the_class_docstring_names_no_activation_or_hold_figure():
    doc = re.search(r'class StrategyTypeConfig:\n    """(.*?)"""', CONFIG_SRC, re.S).group(1)
    assert not re.search(r"\d(\.\d+)?R\b", doc), doc
    assert not re.search(r"\d+\s*(h|min)\b", doc), doc


def _own_comment(name: str) -> str:
    """The contiguous comment block directly above a field, and nothing else.

    A window of lines would be satisfied by a neighbour's note: the first
    draft of this check read twelve lines, and deleting swing's own note left
    intraday's in range, so the check still passed.
    """
    lines = CONFIG_SRC.splitlines()
    at = next(i for i, ln in enumerate(lines) if re.match(rf"\s+{name}:\s", ln))
    block = []
    for ln in reversed(lines[:at]):
        if not ln.strip().startswith("#"):
            break
        block.append(ln)
    return "\n".join(reversed(block))


def test_each_multiplier_says_what_reads_it():
    for s in ("scalp", *STRATEGIES):
        assert "stage table" in _own_comment(f"{s}_trailing_atr_mult"), s
    # The note the others point at names the one path that reads the figure.
    assert 'no "stage" key' in _own_comment("scalp_trailing_atr_mult")


def test_the_global_multiplier_nothing_read_is_gone():
    # `TRAILING_ATR_MULT` had no reader at all: the monitor passes the
    # strategy's figure, and nothing else asks for this one.
    import dataclasses

    from bot.config import TrailingStopConfig
    names = {f.name for f in dataclasses.fields(TrailingStopConfig)}
    assert "trail_atr_mult" not in names
    # Anchored: SWING_TRAILING_ATR_MULT and its siblings contain the name.
    assert not re.search(r"(?<![A-Z_])TRAILING_ATR_MULT", CONFIG_SRC)


def test_the_map_does_not_say_swing_trails_at_its_figure():
    income = (ROOT / "docs" / "INCOME_MAP.md").read_text(encoding="utf-8")
    flat = " ".join(income.split())
    assert "trailing ENABLED at 1.5 ATR" not in flat

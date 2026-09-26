"""A VWAP reversion is not closed on the distance it entered at.

The analyzer calls an idea a VWAP reversion when its price is within 0.5% of
VWAP (or half a VWAP band). The exit rule read the price's distance from VWAP
alone: past 0.3% on the wrong side the thesis was "invalidated", past 0.3% on
the right side it was "complete". So an entry 0.4% below VWAP was invalidated,
and one 0.4% above it complete, on the first smart-exit pass: driven through
`_evaluate_live_smart_exits`, a long entered at 99.6 under a VWAP of 100 was
closed at market sixty seconds after it opened, for a round trip of fees.

Each band now starts from the entry's own distance when the entry sits on that
side of VWAP, so a trade gets the band's 0.3% of room from where it entered.
An entry at VWAP reads exactly as before.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.compat import UTC
from bot.core.engine import RuneClawEngine
from bot.core.smart_exits import check_vwap_reversion_exit

ROOT = Path(__file__).resolve().parents[1]
VWAP = 100.0


def _exit(price, entry, direction="LONG", signal="vwap_reversion", vwap=VWAP):
    return check_vwap_reversion_exit(signal, price, vwap, direction, entry)


def _old(price, direction):
    """The rule as it stood, from VWAP alone."""
    d = (price - VWAP) / VWAP * 100
    if direction == "LONG":
        return d > 0.3 or d < -0.3
    return d < -0.3 or d > 0.3


# ── the rule ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("entry", [99.6, 99.65, 100.4, 100.35])
def test_an_entry_past_the_band_is_not_closed_where_it_entered(direction, entry):
    assert _old(entry, direction) is True          # the defect: fired at entry
    assert _exit(entry, entry, direction) == (False, "")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_an_entry_at_vwap_reads_as_before(direction):
    for i in range(-150, 151):
        price = VWAP * (1 + i / 10_000)
        assert _exit(price, VWAP, direction)[0] is _old(price, direction), price


@pytest.mark.parametrize("direction, entry, still_open, closes", [
    # 0.25% under VWAP: 0.05% more was "invalidated", which is noise. The band
    # is 0.3% beyond the entry now.
    ("LONG", 99.75, 99.5, 99.4),
    ("SHORT", 100.25, 100.5, 100.6),
])
def test_an_entry_on_the_wrong_side_gets_the_bands_width_of_room(
        direction, entry, still_open, closes):
    assert _old(still_open, direction) is True
    assert _exit(still_open, entry, direction) == (False, "")
    assert "failed" in _exit(closes, entry, direction)[1]


@pytest.mark.parametrize("direction, entry, price, word", [
    # A long entered 0.4% under VWAP: invalid 0.3% further under, complete
    # 0.3% above VWAP, as for an entry at VWAP.
    ("LONG", 99.6, 99.25, "failed"),
    ("LONG", 99.6, 100.35, "complete"),
    ("LONG", 99.6, 99.35, None),
    ("LONG", 99.6, 100.1, None),             # back over VWAP, not yet 0.3% past it
    # A long entered 0.4% over: complete 0.3% further over, invalid 0.3% under.
    ("LONG", 100.4, 100.75, "complete"),
    ("LONG", 100.4, 99.65, "failed"),
    ("LONG", 100.4, 100.65, None),
    ("SHORT", 100.4, 100.75, "failed"),
    ("SHORT", 100.4, 99.65, "complete"),
    ("SHORT", 100.4, 99.9, None),
    ("SHORT", 99.6, 99.25, "complete"),
    ("SHORT", 99.6, 100.35, "failed"),
    ("SHORT", 99.6, 99.35, None),
])
def test_each_band_is_measured_beyond_the_entry(direction, entry, price, word):
    fired, why = _exit(price, entry, direction)
    if word is None:
        assert (fired, why) == (False, "")
    else:
        assert fired is True and word in why, why


@pytest.mark.parametrize("signal, vwap, entry", [
    ("momentum_confluence", VWAP, 99.6),
    ("vwap_reversion", 0.0, 99.6),
    ("vwap_reversion", VWAP, 0.0),
    ("vwap_reversion", VWAP, -1.0),
])
def test_nothing_to_measure_closes_nothing(signal, vwap, entry):
    for price in (98.0, 100.5, 102.0):
        for direction in ("LONG", "SHORT"):
            assert _exit(price, entry, direction, signal=signal, vwap=vwap) == (False, "")


def test_every_caller_hands_it_the_entry():
    calls = []
    for path in (ROOT / "bot").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "check_vwap_reversion_exit"):
                kw = {k.arg for k in node.keywords}
                calls.append((f"{path.relative_to(ROOT)}:{node.lineno}",
                              len(node.args) >= 5 or "entry_price" in kw))
    assert len(calls) == 2, calls              # the live and the paper loop
    assert all(ok for _, ok in calls), calls


# ── the live smart-exit pass ────────────────────────────────────────────

class _Executor:
    def __init__(self, pos):
        self._positions = {pos.trade_id: pos}
        self.closed: list = []

    async def close_position(self, trade_id, reason="bot_auto", close_price=0):
        self.closed.append((trade_id, reason))
        return f"CLOSED {trade_id}"


def _drive(price, entry=99.6):
    pos = SimpleNamespace(
        trade_id="TI-v1", symbol="SOL/USDT", direction="LONG", entry_price=entry,
        stop_loss=98.0, take_profit=102.0, status="open", signal_type="vwap_reversion",
        strategy_type="intraday", opened_at=datetime.now(UTC) - timedelta(seconds=60),
        trailing_state={"initial_risk": entry - 98.0})
    ex = _Executor(pos)
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.ws_feed = SimpleNamespace(is_connected=lambda: True,
                                  get_prices=lambda max_age_sec=None: {"SOL/USDT": price})
    eng._last_vwap = {"SOL/USDT": VWAP}
    eng._close_notify_callback = None
    eng.live_executor = ex
    with patch("bot.core.engine.CONFIG") as cfg:
        cfg.time_stop.enabled = True
        cfg.time_stop.live_auto_close_enabled = True
        import asyncio
        asyncio.run(eng._evaluate_live_smart_exits(ex))
    return ex.closed


def test_a_long_entered_under_vwap_is_not_closed_a_minute_later():
    assert _drive(99.6) == []


def test_it_still_closes_when_the_thesis_fails_past_its_entry():
    (closed,) = _drive(99.2)
    assert "VWAP reversion failed" in closed[1]


def test_it_still_closes_when_the_reversion_completes():
    (closed,) = _drive(100.35)
    assert "VWAP reversion complete" in closed[1]

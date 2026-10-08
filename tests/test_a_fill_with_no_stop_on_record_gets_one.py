"""A fill with no stop on record gets one; a stop of 0 is never a price.

8 October, 18:09: "POSITION UNPROTECTED — NO EXCHANGE STOP. OPEN/USDT:USDT
SHORT open 41 min. Intended stop: $0.0000. Stop placement was refused:
side-sanity: non-positive SL/TP (sl=0, tp=0). Self-heal keeps retrying."

A record reaches the executor with stop 0 only by adoption (an orphan limit
order or position) or a lost local record: the risk gate refuses a live idea
without one. When such a limit order filled, the placement asked the venue for
sl=0, the side check refused it, and the post-fill ladder read 0 as "no stop
was intended": no retry, no flag, no flatten. The periodic self-heal asked for
sl=0 again on every pass. And the alert printed 0 as a price and promised a
self-heal that could not happen.

`_fill_in_missing_levels` gives such a record the levels it came from (the
donor record), else the 3%/6% safety pair adoption already uses, before the
fill's placement and before the self-heal's. The alert says "no stop on
record" and what that means.

Driven: the real `_check_pending_limit` with a planted order read, the real
`verify_and_fix_sltp`, the real `_check_unprotected_positions`; only the venue
and `_place_sl_tp` are stand-ins that record what they were asked.
"""
from __future__ import annotations

import asyncio
import dataclasses
import types
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG
from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.proactive_monitor import ProactiveMonitor
from bot.core.venues import get_venue

ENTRY = 0.1091   # the OPEN/USDT short of 8 October


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _now(*a, **k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _now)


def _record(*, sl=0.0, tp=0.0, direction="SHORT", status="pending_fill", origin="adopted",
            entry=ENTRY, sl_id=None):
    return LivePosition(
        trade_id="TI-adopted-OPEN", symbol="OPEN/USDT:USDT", direction=direction,
        entry_price=entry, quantity=64.0, cost_usd=1.4, stop_loss=sl, take_profit=tp,
        leverage=5, is_spot=False, order_type="limit", limit_order_id="o1",
        opened_at=datetime.now(UTC) - timedelta(minutes=5), status=status,
        origin=origin, sl_order_id=sl_id)


# ── the helper ──────────────────────────────────────────────────────────────

def test_a_short_with_no_stop_gets_the_safety_pair(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _record()
    assert ex._fill_in_missing_levels(pos, ENTRY) is True
    assert pos.stop_loss == pytest.approx(ENTRY * 1.03)
    assert pos.take_profit == pytest.approx(ENTRY * 0.94)
    assert getattr(pos, "sl_tp_source") == "default"
    # The side check that refused sl=0 accepts this pair.
    assert LiveExecutor._sltp_side_error(le.Direction.SHORT, pos.stop_loss, pos.take_profit) is None


def test_a_long_gets_it_on_the_other_side(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _record(direction="LONG")
    ex._fill_in_missing_levels(pos, 100.0)
    assert (pos.stop_loss, pos.take_profit) == (pytest.approx(97.0), pytest.approx(106.0))


def test_the_levels_it_came_from_win(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    donor = _record(sl=0.1125, tp=0.1010, status="pending_fill", origin="executed")
    donor.trade_id = "TI-own-OPEN"
    ex._positions[donor.trade_id] = donor
    pos = _record()
    ex._fill_in_missing_levels(pos, ENTRY)
    assert (pos.stop_loss, pos.take_profit) == (0.1125, 0.1010)
    assert getattr(pos, "sl_tp_source") == "inherited"


def _spot():
    p = _record()
    p.is_spot = True
    return p


@pytest.mark.parametrize("pos,price", [
    (_record(sl=0.112), ENTRY),            # a stop on record is left alone
    (_record(), 0.0),                       # nothing to size from
    (_spot(), ENTRY),                       # a spot holding has no perp stop
])
def test_it_touches_nothing_it_should_not(tmp_path, pos, price):
    ex = LiveExecutor(state_dir=str(tmp_path))
    before = (pos.stop_loss, pos.take_profit)
    assert ex._fill_in_missing_levels(pos, price) is False
    assert (pos.stop_loss, pos.take_profit) == before


def test_a_target_on_record_is_kept(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _record(tp=0.1000)
    ex._fill_in_missing_levels(pos, ENTRY)
    assert pos.take_profit == 0.1000 and pos.stop_loss == pytest.approx(ENTRY * 1.03)


# ── the limit fill, driven ──────────────────────────────────────────────────

class _Venue:
    def __init__(self, fill):
        self.fill = fill

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        return {"id": oid, "status": "closed", "filled": 64.0, "average": self.fill,
                "price": self.fill, "amount": 64.0, "info": {}}

    async def cancel_order(self, *a, **k):
        return {}

    async def fetch_ticker(self, symbol, *a, **k):
        return {"last": self.fill}


def _fill(tmp_path, pos):
    asked: list = []
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    venue = _Venue(ENTRY)
    ex._exchange = venue
    ex._positions[pos.trade_id] = pos

    async def _sl_tp(self_, exchange, symbol, direction, qty, sl, tp):
        asked.append((sl, tp))
        return ("sl1", "tp1")

    async def _ladder(self_, exchange, p, direction, qty, sl_id, tp_id, trade_id=None):
        return sl_id, tp_id, None

    with patch.object(le, "audit", lambda *a, **k: None), \
            patch.object(type(CONFIG), "is_live", return_value=True), \
            patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
            patch.object(LiveExecutor, "_reattempt_post_fill_sl", _ladder), \
            patch.object(LiveExecutor, "_guard_fill_leverage", AsyncMock(return_value=None)):
        asyncio.run(ex._check_pending_limit(venue, pos.trade_id, pos))
    return asked


def test_an_adopted_limit_that_fills_is_protected(tmp_path):
    pos = _record()
    asked = _fill(tmp_path, pos)
    assert pos.status == "open"
    assert asked, "no placement was asked for"
    sl, tp = asked[0]
    assert sl == pytest.approx(ENTRY * 1.03) and tp == pytest.approx(ENTRY * 0.94), \
        "the fill asked the venue for a stop of 0 again"


def test_the_bots_own_limit_keeps_its_levels(tmp_path):
    pos = _record(sl=0.1120, tp=0.1020, origin="executed")
    asked = _fill(tmp_path, pos)
    assert asked[0] == (0.1120, 0.1020)


# ── a refused stop, through the real ladder ─────────────────────────────────

def _refused_fill(tmp_path, pos):
    """The real `_check_pending_limit` and the real post-fill ladder, with a
    venue that refuses every stop. Returns the fill's message and the closes
    the ladder asked for."""
    closes: list = []
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    venue = _Venue(ENTRY)
    ex._exchange = venue
    ex._positions[pos.trade_id] = pos

    async def _refuse(self_, exchange, symbol, direction, qty, sl, tp):
        return (None, None)

    async def _close(trade_id, reason=None, **k):
        closes.append(reason)
        return f"CLOSED {trade_id}"

    ex.close_position = _close
    with patch.object(le, "audit", lambda *a, **k: None), \
            patch.object(type(CONFIG), "is_live", return_value=True), \
            patch.object(LiveExecutor, "_place_sl_tp", _refuse), \
            patch.object(LiveExecutor, "_guard_fill_leverage", AsyncMock(return_value=None)):
        msg = asyncio.run(ex._check_pending_limit(venue, pos.trade_id, pos))
    return str(msg or ""), closes


@pytest.mark.parametrize("origin", ["adopted", "reclaimed"])
def test_an_adopted_fill_whose_stop_is_refused_is_flagged_never_flattened(tmp_path, origin):
    # RC-AUD-022: an adopted position is never auto-closed because its safety
    # stop would not place. The fill-in gives an adopted limit adoption's
    # safety pair, so its fill keeps adoption's rule.
    pos = _record(origin=origin)
    msg, closes = _refused_fill(tmp_path, pos)
    assert closes == [], f"the ladder flattened an order the bot did not place: {msg}"
    assert pos.status == "open" and getattr(pos, "unprotected", False) is True
    assert pos.stop_loss == pytest.approx(ENTRY * 1.03)
    assert "STOP-LOSS not placed" in msg and "ENTRY ABORTED" not in msg


def test_the_bots_own_fill_whose_stop_is_refused_is_still_flattened(tmp_path):
    # The other arm: RC-AUD-001 parity is unchanged for an order the bot placed.
    pos = _record(sl=0.1120, tp=0.1020, origin="executed")
    msg, closes = _refused_fill(tmp_path, pos)
    assert closes == ["sl_placement_failed"], msg
    assert "ENTRY ABORTED" in msg


# ── the self-heal, driven ───────────────────────────────────────────────────

def _heal(tmp_path, pos):
    asked: list = []
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions[pos.trade_id] = pos

    async def _exchange():
        return object()

    async def _place(exchange, symbol, direction, qty, sl, tp):
        asked.append((sl, tp))
        return ("sl1", "tp1")

    ex._get_exchange = _exchange
    ex._place_sl_tp = _place
    with patch.object(le, "audit", lambda *a, **k: None):
        asyncio.run(ex.verify_and_fix_sltp())
    return asked


def test_the_self_heal_sizes_a_stop_instead_of_asking_for_zero(tmp_path):
    asked = _heal(tmp_path, _record(status="open"))
    assert asked and asked[0][0] == pytest.approx(ENTRY * 1.03)


def test_a_record_that_names_its_stop_orders_keeps_its_levels(tmp_path):
    pos = _record(status="open", sl_id="A")
    pos.tp_order_id = "B"
    asked = _heal(tmp_path, pos)
    assert asked == [] and pos.stop_loss == 0.0


# ── the alert ───────────────────────────────────────────────────────────────

def _alert(stop_loss):
    pos = types.SimpleNamespace(
        status="open", symbol="OPEN/USDT:USDT", trade_id="T-1",
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=41),
        sl_order_id=None, unprotected=True, stop_loss=stop_loss, direction="SHORT")
    ex = types.SimpleNamespace(user_id=None, open_positions=[pos],
                               _last_sltp_reason=lambda s: "side-sanity: non-positive SL/TP")
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = types.SimpleNamespace(_all_live_executors=lambda: [ex])
    m._enabled_chats, m._chart_fn, m._admin_fn = set(), None, None
    was = (CONFIG.simulation_mode, CONFIG.live_trading_enabled, CONFIG.telegram)
    object.__setattr__(CONFIG, "simulation_mode", False)
    object.__setattr__(CONFIG, "live_trading_enabled", True)
    object.__setattr__(CONFIG, "telegram", dataclasses.replace(was[2], chat_id="999"))
    try:
        assert CONFIG.is_live()
        (alert,) = m._check_unprotected_positions()
    finally:
        object.__setattr__(CONFIG, "simulation_mode", was[0])
        object.__setattr__(CONFIG, "live_trading_enabled", was[1])
        object.__setattr__(CONFIG, "telegram", was[2])
    return alert.body


def test_a_stop_of_zero_is_said_as_none_on_record():
    body = _alert(0.0)
    assert "No stop on record" in body
    assert "$0.0000" not in body
    assert "Self-heal keeps retrying" not in body
    assert "stale record" in body


def test_a_real_stop_is_still_said_as_a_price():
    body = _alert(0.1124)
    assert "Intended stop: <code>$0.1124</code>" in body
    assert "Self-heal keeps retrying" in body
    assert "No stop on record" not in body

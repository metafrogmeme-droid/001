"""Three live exit rules acted on a reading nobody took.

Each was driven through the real ``check_positions`` before it was fixed:

- **The ladder sold slices of a position the bot has no plan for.** An
  adopted position (opened by hand, or by another process, and swept in) is
  built with the dataclass defaults for its strategy, and every time exit
  already stands down for it (``time_exits.thesis_recorded``). The partial-TP
  ladder did not: at 1.5R of a risk nobody chose it closed half the position
  at market. It stands down now, said once, and its stop and target apply.
- **A stale price took profits and trailed the stop.** When the ticker is
  older than ``LIVE_TICKER_MAX_AGE_SEC`` and the position has no exchange
  stop, the monitor still runs, because a stale price is a better guardian
  than none (the XPD incident). That argument is about the STOP. The same
  pass also banked the target, moved the trail, ran the ladder and the time
  stop, all on an hour-old price. Only the stop runs on it now.
- **The runner trailed on an ATR nobody measured.** With no ATR recorded at
  entry the ladder was built with a stand-in of 2% of the entry, and the
  runner trailed at a multiple of that. ``atr`` 0 is unread now: the runner
  holds its stop, and a stage lock it has not reached is still asked for.
"""

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition, _entry_atr
from bot.core.partial_tp import (
    PartialTPState,
    check_partial_tp,
    create_partial_tp_state,
)
from bot.utils.trailing import make_trailing_state


def _executor(monkeypatch, *, stale: bool = False):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    ex = LiveExecutor()
    venue = AsyncMock()
    box = {"p": None}

    def _ticker(*_a, **_k):
        stamp = (time.time() - 3600) * 1000 if stale else time.time() * 1000
        return {"last": box["p"], "timestamp": stamp}

    venue.fetch_ticker = AsyncMock(side_effect=_ticker)
    ex._exchange = venue
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = time.time()
    ex._save_positions = lambda *a, **k: None
    ex._record_warning = lambda *a, **k: None
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._struct_candles = AsyncMock(return_value=None)
    ex._place_missing_sltp = AsyncMock(return_value=(None, None))
    rec = {"moves": [], "closes": [], "partials": [], "audits": []}

    async def _move(_exch, _pos, new_sl):
        rec["moves"].append(round(new_sl, 4))
        return True

    async def _close(tid, reason, price=None, *a, **k):
        rec["closes"].append(reason)
        return f"CLOSED {reason}"

    async def _partial(_exch, pos, qty, stage):
        rec["partials"].append((stage, qty))
        return qty, "filled", "P-1"

    real_audit = le.audit

    def _audit(logger, msg, **kw):
        rec["audits"].append((kw.get("action"), kw.get("result"), msg))
        return real_audit(logger, msg, **kw)

    monkeypatch.setattr(le, "audit", _audit)
    ex._update_exchange_sl = _move
    ex.close_position = _close
    ex._partial_close = _partial
    return ex, box, rec


def _position(ex, *, origin="executed", atr=1.0, protected=True,
              hours=0.5, trailing=False) -> LivePosition:
    p = LivePosition(
        trade_id="TI-exit", symbol="ETH/USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=20.0, stop_loss=98.0,
        take_profit=120.0, leverage=5,
        opened_at=datetime.now(UTC) - timedelta(hours=hours),
        status="open", atr_at_entry=atr)
    p.strategy_type = "swing"
    p.signal_type = "momentum_confluence"
    p.filled_at = p.opened_at
    p.origin = origin
    p.sl_order_id = "SL-1" if protected else None
    p.tp_order_id = "TP-1" if protected else None
    p.trailing_state = (make_trailing_state(100.0, "LONG", 2.0, 1.0)
                        if trailing else None)
    ex._positions[p.trade_id] = p
    return p


async def _tick(ex, box, price):
    box["p"] = price
    return await ex.check_positions()


def _results(rec, action):
    return [r for a, r, _ in rec["audits"] if a == action]


# ── The ladder stands down for a position with no strategy on record ─────────

class TestTheLadderNeedsAThesis:
    @pytest.mark.asyncio
    async def test_an_adopted_position_is_not_scaled_out(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        pos = _position(ex, origin="adopted")
        await _tick(ex, box, 103.5)          # 1.75R: past TP1
        await _tick(ex, box, 106.0)          # 3R: past TP2
        assert rec["partials"] == []
        assert rec["moves"] == []
        assert pos.quantity == 1.0 and pos.stop_loss == 98.0
        # Said once, and the record is left as it was.
        assert _results(rec, "partial_tp").count("NO_THESIS") == 1
        assert pos.partial_tp_state is None

    @pytest.mark.asyncio
    async def test_a_position_that_inherited_a_thesis_still_is(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        pos = _position(ex, origin="adopted")
        pos.thesis_source = "inherited"
        await _tick(ex, box, 103.5)
        assert [s for s, _ in rec["partials"]] == ["tp1"]
        assert "NO_THESIS" not in _results(rec, "partial_tp")

    @pytest.mark.asyncio
    async def test_the_bots_own_position_still_is(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        _position(ex)
        await _tick(ex, box, 103.5)
        assert [s for s, _ in rec["partials"]] == ["tp1"]

    @pytest.mark.asyncio
    async def test_its_target_still_closes_it(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        _position(ex, origin="adopted")
        await _tick(ex, box, 121.0)
        assert rec["closes"] == ["TP HIT"]


# ── On a stale price, only the stop runs ─────────────────────────────────────

class TestAStalePriceRunsOnlyTheStop:
    @pytest.mark.asyncio
    async def test_the_stop_still_closes_an_unprotected_position(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch, stale=True)
        _position(ex, protected=False)
        await _tick(ex, box, 97.0)
        assert len(rec["closes"]) == 1 and "SL" in rec["closes"][0]
        assert "MONITORING_UNPROTECTED" in _results(rec, "ticker_stale")

    @pytest.mark.asyncio
    async def test_the_target_is_not_banked_on_it(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch, stale=True)
        _position(ex, protected=False)
        await _tick(ex, box, 121.0)
        assert rec["closes"] == []

    @pytest.mark.asyncio
    async def test_the_target_is_banked_on_a_fresh_price(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        _position(ex, protected=False)
        await _tick(ex, box, 121.0)
        assert rec["closes"] == ["TP HIT"]

    @pytest.mark.asyncio
    async def test_the_ladder_does_not_run_on_it(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch, stale=True)
        pos = _position(ex, protected=False)
        await _tick(ex, box, 106.0)
        assert rec["partials"] == []
        assert pos.quantity == 1.0

    @pytest.mark.asyncio
    async def test_the_trail_does_not_move_on_it(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch, stale=True)
        pos = _position(ex, protected=False, trailing=True)
        pos.partial_tp_state = {"ladder": "off", "reason": "isolate the trail"}
        await _tick(ex, box, 110.0)
        assert rec["moves"] == [] and pos.stop_loss == 98.0

    @pytest.mark.asyncio
    async def test_the_trail_moves_on_a_fresh_price(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        pos = _position(ex, protected=False, trailing=True)
        pos.partial_tp_state = {"ladder": "off", "reason": "isolate the trail"}
        await _tick(ex, box, 110.0)
        assert rec["moves"] and pos.stop_loss > 98.0

    @pytest.mark.asyncio
    async def test_the_time_stop_does_not_run_on_it(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch, stale=True)
        _position(ex, protected=False, hours=400)
        await _tick(ex, box, 99.0)           # under entry, above the stop
        assert rec["closes"] == []

    @pytest.mark.asyncio
    async def test_the_time_stop_runs_on_a_fresh_price(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        _position(ex, protected=False, hours=400)
        await _tick(ex, box, 99.0)
        assert len(rec["closes"]) == 1 and rec["closes"][0].startswith("TIME_STOP")

    def test_the_breach_reading_reads_only_the_stop_when_asked(self):
        ex = LiveExecutor()
        for direction, sl, tp, hit_tp, hit_sl in (("LONG", 98.0, 120.0, 121.0, 97.0),
                                                   ("SHORT", 102.0, 80.0, 79.0, 103.0)):
            p = LivePosition(trade_id="T", symbol="ETH/USDT", direction=direction,
                             entry_price=100.0, quantity=1.0, cost_usd=20.0,
                             stop_loss=sl, take_profit=tp, leverage=5)
            assert ex._local_stop_breached(p, hit_tp) == (True, "TP HIT")
            assert ex._local_stop_breached(p, hit_tp, stop_only=True) == (False, "")
            assert ex._local_stop_breached(p, hit_sl, stop_only=True)[0] is True


# ── The runner holds its stop with no ATR on record ──────────────────────────

def _runner_state(atr: float) -> PartialTPState:
    st = create_partial_tp_state(
        trade_id="T", direction="LONG", entry_price=100.0, stop_loss=98.0,
        take_profit=120.0, quantity=1.0, atr=atr, fee_round_trip_pct=0.12)
    st.tp1_hit = st.tp2_hit = True
    st.current_sl = 102.0                    # TP2's lock, already resting
    st.remaining_qty = 0.2
    return st


class TestTheRunnerNeedsAMeasuredATR:
    @pytest.mark.parametrize("atr", [0.0, -1.0, float("nan"), float("inf")])
    def test_an_unmeasured_atr_is_recorded_as_unread(self, atr):
        st = create_partial_tp_state(
            trade_id="T", direction="LONG", entry_price=100.0, stop_loss=98.0,
            take_profit=120.0, quantity=1.0, atr=atr)
        assert st.atr == 0.0

    def test_a_measured_atr_is_kept(self):
        assert _runner_state(1.5).atr == 1.5

    def test_the_runner_trails_on_a_measured_atr(self):
        acts = check_partial_tp(_runner_state(1.0), 110.0)
        assert [a.action for a in acts] == ["move_sl"]
        assert acts[0].new_sl > 102.0

    def test_the_runner_holds_its_stop_on_none(self):
        st = _runner_state(0.0)
        assert check_partial_tp(st, 110.0) == []
        assert st.current_sl == 102.0

    def test_the_runner_still_asks_for_its_lock_on_none(self):
        st = _runner_state(0.0)
        st.current_sl = 100.1                # TP2's lock refused: stop at TP1's
        acts = check_partial_tp(st, 110.0)
        assert [a.action for a in acts] == ["move_sl"]
        assert acts[0].new_sl == pytest.approx(102.0)

    def test_the_runner_still_closes_at_its_stop_on_none(self):
        acts = check_partial_tp(_runner_state(0.0), 101.9)
        assert [a.action for a in acts] == ["close_runner"]

    def test_a_short_runner_holds_too(self):
        st = create_partial_tp_state(
            trade_id="T", direction="SHORT", entry_price=100.0, stop_loss=102.0,
            take_profit=80.0, quantity=1.0, atr=0.0, fee_round_trip_pct=0.12)
        st.tp1_hit = st.tp2_hit = True
        st.current_sl = 98.0
        st.remaining_qty = 0.2
        assert check_partial_tp(st, 90.0) == []
        assert check_partial_tp(_short_measured(), 90.0)[0].new_sl < 98.0

    @pytest.mark.parametrize("value,expected", [
        (1.25, 1.25), (0.0, 0.0), (-1.0, 0.0), (None, 0.0), (True, 0.0),
        ("1.2", 0.0), (float("nan"), 0.0), (float("inf"), 0.0)])
    def test_the_entry_atr_reading(self, value, expected):
        assert _entry_atr(type("P", (), {"atr_at_entry": value})()) == expected

    @pytest.mark.asyncio
    async def test_a_ladder_saved_with_a_stand_in_atr_holds_the_runner(self, monkeypatch):
        """A ladder written before this read carries atr = 2% of the entry
        for a position with none recorded. The pass reads the book's ATR."""
        ex, box, rec = _executor(monkeypatch)
        pos = _position(ex, atr=0.0)
        pos.stop_loss = 102.0
        st = _runner_state(2.0)              # the stand-in, persisted
        pos.partial_tp_state = dataclasses.asdict(st)
        pos.quantity = 0.2
        await _tick(ex, box, 110.0)
        assert rec["moves"] == [] and pos.stop_loss == 102.0

    @pytest.mark.asyncio
    async def test_a_ladder_with_a_measured_atr_trails_the_runner(self, monkeypatch):
        ex, box, rec = _executor(monkeypatch)
        pos = _position(ex, atr=1.0)
        pos.stop_loss = 102.0
        st = _runner_state(1.0)
        pos.partial_tp_state = dataclasses.asdict(st)
        pos.quantity = 0.2
        await _tick(ex, box, 110.0)
        assert rec["moves"] and pos.stop_loss > 102.0


def _short_measured() -> PartialTPState:
    st = create_partial_tp_state(
        trade_id="T", direction="SHORT", entry_price=100.0, stop_loss=102.0,
        take_profit=80.0, quantity=1.0, atr=1.0, fee_round_trip_pct=0.12)
    st.tp1_hit = st.tp2_hit = True
    st.current_sl = 98.0
    st.remaining_qty = 0.2
    return st

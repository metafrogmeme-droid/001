"""The partial take-profit ladder survives a restart, and an unread fill is re-read.

Two defects in one ladder, both on real positions with the ladder on by default.

A RESTART SOLD THE RUNNER TWICE. `pos.partial_tp_state` -- which stages have
fired, and the entry-time 1R they are measured in -- was never written by
`_save_positions`, so every restart rebuilt the ladder from the LIVE stop. TP1
moves that stop to breakeven (+0.1%), so the rebuilt 1R was about 0.1% of
price: every tick then read as many R, and TP1 and TP2 fired again at once on
what TP1 had left. This deployment redeploys often.

AN UNREAD FILL WAS NEVER RETRIED. When a TP1 close order went out and its fill
could not be confirmed, the audit said "retrying next pass" -- but the ladder
had already marked the stage done, so the next pass skipped it for good and
the stop never reached breakeven. Retrying by RESUBMITTING would be worse: the
first order may have filled, and a second closes the position twice. The order
is recorded and RE-READ instead.

Driven through the real `_run_partial_tp`, `_save_positions` and
`_load_positions`, on the fixture shape `test_audit_fixes_batch_3` uses.
"""
from __future__ import annotations

import dataclasses as _dc
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition, restored_ladder
from bot.core.partial_tp import create_partial_tp_state, rebuild_ladder

ENTRY, STOP, QTY = 100.0, 90.0, 1.0          # 1R = 10: TP1 at 115, TP2 at 125


def _pos(**over) -> LivePosition:
    kw = dict(trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=ENTRY,
              quantity=QTY, cost_usd=ENTRY * QTY / 5, stop_loss=STOP, take_profit=140.0,
              leverage=5, status="open", atr_at_entry=2.0, origin="executed",
              opened_at=datetime.now(UTC) - timedelta(hours=2),
              trailing_state={"entry_price": ENTRY, "best_price": ENTRY,
                              "trailing_active": False, "initial_risk": ENTRY - STOP,
                              "atr": 2.0, "stage": 0})
    kw.update(over)
    return LivePosition(**kw)


def _executor(pos, path=None) -> LiveExecutor:
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._positions = {pos.trade_id: pos} if pos is not None else {}
    # `id` because every saved row names its venue, and a save that raises
    # inside its own `except` writes nothing: this stand-in once had no `id`,
    # so every drive below saved no file at all.
    ex._venue = SimpleNamespace(id="bitget", order_symbol=lambda s: s,
                                close_params=lambda uta: {"reduceOnly": True})
    ex._is_uta = False
    ex._foreign_position_rows = {}
    ex._record_warning = lambda k: None
    ex._venue_market_price = AsyncMock(return_value=None)
    ex._update_exchange_sl = AsyncMock(return_value=True)
    ex._recovered_from_closing = set()
    if path is not None:
        ex._positions_file = str(path)
    else:
        ex._save_positions = lambda: None
    return ex


def _exchange(*orders, verify=None):
    """Each `create_order` answers the next response; `fetch_order` answers
    `verify` (a dict, or a list read in order)."""
    x = MagicMock()
    x.amount_to_precision = lambda s, q: q
    x.create_order = AsyncMock(side_effect=list(orders))
    if isinstance(verify, list):
        x.fetch_order = AsyncMock(side_effect=verify)
    else:
        x.fetch_order = AsyncMock(return_value=verify or {})
    return x


def _run(ex, x, pos, price):
    import asyncio
    asyncio.run(ex._run_partial_tp(x, pos, price))


# ── the rebuild: 1R is the entry-time risk, never the moved stop ─────────


class TestTheRebuild:
    def _build(self, **over):
        kw = dict(trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=STOP,
                  take_profit=140.0, quantity=QTY, atr=2.0, entry_risk=ENTRY - STOP,
                  restored_without_ladder=False)
        kw.update(over)
        return rebuild_ladder(**kw)

    def test_the_entry_time_1r_wins_over_a_moved_stop(self):
        st, why = self._build(stop_loss=100.1)
        assert why == "" and st.initial_risk == pytest.approx(10.0)
        assert st.original_sl == pytest.approx(90.0) and st.current_sl == pytest.approx(100.1)

    def test_without_a_record_the_stop_is_the_1r_only_on_the_loss_side(self):
        st, _ = self._build(entry_risk=None)
        assert st.initial_risk == pytest.approx(10.0)
        off, why = self._build(entry_risk=None, stop_loss=100.1)
        assert off is None and "no R can be measured" in why

    @pytest.mark.parametrize("risk", [0.0, -3.0, float("nan"), float("inf"), True, "10"])
    def test_a_recorded_1r_that_is_not_a_distance_is_not_used(self, risk):
        st, _ = self._build(entry_risk=risk)          # stop still on the loss side
        assert st.initial_risk == pytest.approx(10.0)
        off, _why = self._build(entry_risk=risk, stop_loss=100.1)
        assert off is None

    def test_a_short_reads_its_loss_side_the_other_way(self):
        st, _ = self._build(direction="SHORT", stop_loss=110.0, entry_risk=None)
        assert st.initial_risk == pytest.approx(10.0) and st.original_sl == pytest.approx(110.0)
        off, _ = self._build(direction="SHORT", stop_loss=99.9, entry_risk=None)
        assert off is None

    @pytest.mark.parametrize("direction,stop,tp1,tp2", [
        ("LONG", 95.0, False, False), ("LONG", 100.1, True, False),
        ("LONG", 100.0, True, False), ("LONG", 110.0, True, True),
        ("SHORT", 105.0, False, False), ("SHORT", 99.9, True, False),
        ("SHORT", 90.0, True, True),
    ])
    def test_a_record_without_a_ladder_reads_its_stages_off_the_stop(self, direction, stop,
                                                                     tp1, tp2):
        st, _ = self._build(direction=direction, stop_loss=stop, restored_without_ladder=True)
        assert (st.tp1_hit, st.tp2_hit) == (tp1, tp2)

    def test_a_fresh_position_is_not_read_off_its_stop(self):
        """A position that never had a ladder cannot have fired TP1, whatever a
        trailing stop did to its stop, so only a RESTORED record is read that way."""
        st, _ = self._build(stop_loss=100.1, restored_without_ladder=False)
        assert (st.tp1_hit, st.tp2_hit) == (False, False)


# ── the restart: the ladder is written, read back, and not re-fired ──────


class TestTheRestart:
    def _fired_tp1(self, tmp_path):
        pos = _pos()
        ex = _executor(pos, tmp_path / "positions.json")
        _run(ex, _exchange({"id": "O1", "filled": 0.5}), pos, 116.0)
        assert pos.quantity == pytest.approx(0.5) and pos.stop_loss > ENTRY
        ex._save_positions()
        return tmp_path / "positions.json"

    def _restart(self, path):
        ex = _executor(None, path)
        ex._load_positions()
        return ex, ex._positions["T1"]

    def test_the_ladder_is_written_with_the_position(self, tmp_path):
        saved = json.loads(self._fired_tp1(tmp_path).read_text())["T1"]
        ladder = saved["partial_tp_state"]
        assert ladder["tp1_hit"] is True and ladder["initial_risk"] == pytest.approx(10.0)

    def test_a_restart_does_not_fire_tp1_again(self, tmp_path):
        ex, pos = self._restart(self._fired_tp1(tmp_path))
        assert pos.partial_tp_state["tp1_hit"] is True
        x = _exchange({"id": "O2", "filled": 0.25})
        _run(ex, x, pos, 116.0)                   # still 1.6R: TP1 is done, TP2 is not due
        x.create_order.assert_not_awaited()
        assert pos.quantity == pytest.approx(0.5)

    def test_the_defect_as_it_was(self, tmp_path):
        """The same restart with the ladder dropped from the record, which is
        what `_save_positions` wrote until now, rebuilt from the breakeven stop
        by the old reading: 1R ~0.1, 1.6R reads as 160R, and both stages fire."""
        st = create_partial_tp_state(trade_id="T1", direction="LONG", entry_price=ENTRY,
                                     stop_loss=100.1, take_profit=140.0, quantity=0.5, atr=2.0)
        assert st.initial_risk == pytest.approx(0.1)
        from bot.core.partial_tp import check_partial_tp
        stages = [a.stage for a in check_partial_tp(st, 116.0) if a.action == "close_partial"]
        assert stages == ["tp1", "tp2"]

    def test_a_record_written_before_the_ladder_was_saved_is_rebuilt_not_re_fired(self, tmp_path):
        path = self._fired_tp1(tmp_path)
        data = json.loads(path.read_text())
        del data["T1"]["partial_tp_state"]        # a record from before this fix
        path.write_text(json.dumps(data))
        ex, pos = self._restart(path)
        assert pos.partial_tp_state == {"ladder": le.LADDER_UNRECORDED}
        x = _exchange({"id": "O2", "filled": 0.15})
        with patch.object(le, "audit") as rec:
            _run(ex, x, pos, 116.0)
        x.create_order.assert_not_awaited()       # TP1 read as done off the stop
        assert pos.partial_tp_state["initial_risk"] == pytest.approx(10.0)
        assert pos.partial_tp_state["tp1_hit"] is True
        assert [c.kwargs.get("result") for c in rec.call_args_list] == ["LADDER_REBUILT"]
        _run(ex, x, pos, 126.0)                   # 2.6R: TP2 is due, and fires once
        x.create_order.assert_awaited_once()

    def test_a_record_whose_1r_cannot_be_measured_runs_no_ladder(self, tmp_path):
        pos = _pos(stop_loss=100.1, trailing_state=None,
                   partial_tp_state={"ladder": le.LADDER_UNRECORDED})
        ex = _executor(pos)
        x = _exchange({"id": "O1", "filled": 0.5})
        with patch.object(le, "audit") as rec:
            _run(ex, x, pos, 200.0)
            _run(ex, x, pos, 200.0)
        x.create_order.assert_not_awaited()
        assert pos.partial_tp_state["ladder"] == le.LADDER_OFF
        assert [c.kwargs.get("result") for c in rec.call_args_list] == ["LADDER_OFF"]

    def test_the_loader_tells_an_absent_ladder_from_one_not_started(self):
        assert restored_ladder({}) == {"ladder": le.LADDER_UNRECORDED}
        assert restored_ladder({"partial_tp_state": None}) is None
        assert restored_ladder({"partial_tp_state": "junk"}) is None
        assert restored_ladder({"partial_tp_state": {"tp1_hit": True}}) == {"tp1_hit": True}


# ── an unread fill: re-read on the next pass, never resubmitted ──────────


def _armed() -> LivePosition:
    pos = _pos()
    st = create_partial_tp_state(trade_id="T1", direction="LONG", entry_price=ENTRY,
                                 stop_loss=STOP, take_profit=140.0, quantity=QTY, atr=2.0)
    pos.partial_tp_state = _dc.asdict(st)
    return pos


class TestAnUnreadFill:
    def _unread_then(self, second_read):
        pos = _armed()
        ex = _executor(pos)
        x = _exchange({"id": "O1"}, {"id": "O2", "filled": 0.5},
                      verify=[{"status": "open", "filled": 0}, {"status": "open", "filled": 0},
                              second_read])
        _run(ex, x, pos, 116.0)
        assert pos.partial_tp_state["pending"]["order_id"] == "O1"
        assert pos.quantity == QTY and pos.stop_loss == STOP
        return ex, x, pos

    def test_a_late_fill_is_read_and_the_stop_moves(self):
        ex, x, pos = self._unread_then({"status": "closed", "filled": 0.5, "average": 116.0})
        _run(ex, x, pos, 116.0)
        assert x.create_order.await_count == 1, "the close is re-read, never resubmitted"
        assert pos.quantity == pytest.approx(0.5)
        assert pos.stop_loss > ENTRY, "TP1's move to breakeven lands with its fill"
        assert pos.partial_tp_state["pending"] is None
        assert pos.partial_tp_state["tp1_hit"] is True

    def test_an_order_that_filled_nothing_re_arms_the_stage(self):
        ex, x, pos = self._unread_then({"status": "canceled", "filled": 0})
        _run(ex, x, pos, 116.0)
        assert x.create_order.await_count == 2, "the stage is placed again"
        assert pos.quantity == pytest.approx(0.5)
        assert pos.partial_tp_state["pending"] is None

    def test_a_cancelled_order_that_partly_filled_is_that_fill(self):
        ex, x, pos = self._unread_then({"status": "canceled", "filled": 0.2})
        _run(ex, x, pos, 116.0)
        assert x.create_order.await_count == 1
        assert pos.quantity == pytest.approx(0.8)

    def test_a_fill_still_unread_holds_the_whole_ladder(self):
        ex, x, pos = self._unread_then({"status": "open", "filled": 0})
        _run(ex, x, pos, 130.0)                   # 3R: TP2 would be due
        assert x.create_order.await_count == 1, "nothing builds on an unconfirmed stage"
        assert pos.quantity == QTY and pos.partial_tp_state["pending"]["order_id"] == "O1"

    def test_nothing_later_in_the_same_pass_builds_on_it(self):
        """Price jumps past both stages in one tick: TP1's fill is unread, and
        TP2 used to go out anyway, closing a second slice of a quantity the
        book could not state."""
        pos = _armed()
        ex = _executor(pos)
        x = _exchange({"id": "O1"}, {"id": "O2", "filled": 0.3},
                      verify={"status": "open", "filled": 0})
        _run(ex, x, pos, 130.0)
        assert x.create_order.await_count == 1

    def test_no_order_id_is_said_and_not_retried(self):
        pos = _armed()
        ex = _executor(pos)
        x = _exchange({}, {"id": "O2", "filled": 0.5})
        with patch.object(le, "audit") as rec:
            _run(ex, x, pos, 116.0)
            _run(ex, x, pos, 116.0)
        assert x.create_order.await_count == 1, "a second close could double-close"
        assert pos.partial_tp_state["pending"] is None
        (msg,) = [c.args[1] for c in rec.call_args_list if c.kwargs.get("result") == "FILL_UNREAD"]
        assert "cannot be re-read" in msg and "not retried" in msg

    def test_the_pending_order_is_written_with_the_position(self, tmp_path):
        pos = _armed()
        ex = _executor(pos, tmp_path / "positions.json")
        x = _exchange({"id": "O1"}, verify={"status": "open", "filled": 0})
        _run(ex, x, pos, 116.0)
        ex._save_positions()
        fresh = _executor(None, tmp_path / "positions.json")
        fresh._load_positions()
        assert fresh._positions["T1"].partial_tp_state["pending"]["order_id"] == "O1"


class TestTheFillReading:
    """`_read_partial_fill` is the one reading for the first check and every
    re-read, so what it says about each shape is what the ladder does."""

    @pytest.mark.parametrize("check,want", [
        ({"confirmed": True, "fill_qty": 0.5}, (0.5, "filled")),
        ({"confirmed": True, "fill_qty": 0.0, "failure_stage": ""}, (0.0, "unknown")),
        ({"failure_stage": "order_cancelled", "raw": {"filled": 0.2}}, (0.2, "filled")),
        ({"failure_stage": "order_cancelled", "raw": {"filled": 0}}, (0.0, "none")),
        # a cancel whose filled amount nobody stated may have closed some:
        # "nothing" would re-arm the stage and could close twice
        ({"failure_stage": "order_cancelled", "raw": {"filled": None}}, (0.0, "unknown")),
        ({"failure_stage": "order_cancelled", "raw": {}}, (0.0, "unknown")),
        ({"failure_stage": "order_cancelled", "raw": None}, (0.0, "unknown")),
        ({"failure_stage": "order_cancelled", "raw": {"filled": float("nan")}}, (0.0, "unknown")),
        ({"failure_stage": "post_check_unconfirmed"}, (0.0, "unknown")),
    ])
    def test_each_shape(self, check, want):
        assert le._read_partial_fill(check) == want

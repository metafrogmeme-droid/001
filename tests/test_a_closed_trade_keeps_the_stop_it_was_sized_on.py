"""A closed trade could not say what stop it began with.

`pos.stop_loss` is overwritten in place by the break-even lock, the trail and
the partial-TP ladder (`_ratchet_sl`, the trailing block in `check_positions`),
and `closed_trade_row` wrote that FINAL stop. So the one record that outlives a
position carried the stop the trade ended on and not the one it was sized and
risk-approved on, and every figure that is a ratio against the risk it took
(R, fee in R, slippage in R, the distance of a resting-limit fill from the price
the signal was read at) was unanswerable from the ledger. The journal's R, which
divides by `|entry - stop_loss| x quantity` of the closed row, was answering it
anyway, against the wrong stop and the post-ladder quantity.

This records the facts at the moment they are true (`entry_facts_for`,
`note_fill_facts`, `note_partial_close`), persists them with the open position
and writes them on the closed row. It is RECORDING ONLY: nothing here reads them
to decide anything, and a failure to record must never cost a trade.

THE RED HERRINGS, planted below: a position with no recorded facts (an adopted
orphan, a row from before this) must write the row it always wrote; a second
stamp must not rewrite the first; and an object that refuses attributes must not
raise out of the recorder.
"""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot.core.live_executor as le
from bot.config import CONFIG
from bot.core.live_executor import (
    LiveExecutor,
    LivePosition,
    closed_trade_row,
    entry_facts_for,
    note_fill_facts,
    note_partial_close,
    stamp_entry_facts,
)
from bot.core.venues import get_venue

IDEA = SimpleNamespace(entry_price=99.2, market_at_signal=100.0, stop_loss=97.0,
                       take_profit=104.0)


def _pos(**kw):
    base = dict(trade_id="t1", symbol="SOL/USDT:USDT", direction="LONG", entry_price=99.2,
                quantity=10.0, cost_usd=198.4, stop_loss=97.0, take_profit=104.0, leverage=5)
    base.update(kw)
    return LivePosition(**base)


def _restart(tmp_path, pos) -> LivePosition:
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions[pos.trade_id] = pos
    ex._save_positions()
    return LiveExecutor(state_dir=str(tmp_path))._positions[pos.trade_id]


def test_the_facts_are_the_decision_not_the_shifted_entry():
    f = entry_facts_for(IDEA, order_type="limit", order_id="o1")
    assert f["decision_price"] == 100.0          # the scan's price, not the limit's
    assert f["intended_entry"] == 99.2
    assert f["initial_stop_loss"] == 97.0 and f["initial_take_profit"] == 104.0
    assert f["placed_order_type"] == "limit" and f["order_id"] == "o1"


def test_without_a_scan_price_the_decision_price_is_the_entry():
    idea = SimpleNamespace(entry_price=99.2, market_at_signal=None, stop_loss=97.0, take_profit=104.0)
    assert entry_facts_for(idea, order_type="market")["decision_price"] == 99.2


def test_an_unreadable_level_is_recorded_as_absent_not_zero():
    idea = SimpleNamespace(entry_price=99.2, market_at_signal="", stop_loss="n/a", take_profit=None)
    f = entry_facts_for(idea, order_type="limit")
    assert f["initial_stop_loss"] is None and f["initial_take_profit"] is None


def test_the_initial_stop_survives_the_stop_being_moved():
    pos = _pos()
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit"))
    pos.stop_loss = 99.2 + 0.1                    # the break-even lock, in place
    pos.take_profit = 104.0
    row = closed_trade_row(pos)
    assert row["stop_loss"] == 99.3                              # what it always wrote
    assert row["entry_facts"]["initial_stop_loss"] == 97.0       # what it never could


def test_a_second_stamp_does_not_rewrite_the_decision():
    pos = _pos()
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit"))
    later = SimpleNamespace(entry_price=1.0, market_at_signal=1.0, stop_loss=0.5, take_profit=2.0)
    stamp_entry_facts(pos, entry_facts_for(later, order_type="market"))
    assert pos.entry_facts["initial_stop_loss"] == 97.0


def test_a_fill_records_the_stop_and_target_standing_at_the_fill():
    pos = _pos()
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit"))
    pos.stop_loss, pos.take_profit = 98.5, 106.0  # re-anchored by a drift chase
    note_fill_facts(pos, path="drift_fallback", fill_price=101.5, quantity=10.0,
                    placed_entry=99.2, chase_pct=2.3)
    fill, = pos.entry_facts["fills"]
    assert fill["path"] == "drift_fallback" and fill["price"] == 101.5
    assert fill["stop_at_fill"] == 98.5 and fill["target_at_fill"] == 106.0
    assert fill["chase_pct"] == 2.3 and fill["placed_entry"] == 99.2
    assert pos.entry_facts["initial_stop_loss"] == 97.0           # untouched


def test_a_resting_partial_then_a_chase_shows_both_fills():
    pos = _pos()
    note_fill_facts(pos, path="partial_adopt:limit", fill_price=99.2, quantity=4.0)
    note_fill_facts(pos, path="drift_fallback", fill_price=101.0, quantity=6.0)
    assert [f["path"] for f in pos.entry_facts["fills"]] == ["partial_adopt:limit", "drift_fallback"]


def test_a_ladder_slice_is_recorded():
    pos = _pos()
    note_partial_close(pos, stage="tp1", qty=5.0, ref_price=103.0, order_id="c1")
    note_partial_close(pos, stage="tp2", qty=3.0, ref_price=105.0)
    assert [(r["stage"], r["qty"], r["ref_price"]) for r in pos.partial_fills] == [
        ("tp1", 5.0, 103.0), ("tp2", 3.0, 105.0)]
    assert pos.partial_fills[1]["order_id"] is None
    assert closed_trade_row(pos)["partial_fills"] == pos.partial_fills


def test_the_facts_survive_a_restart(tmp_path):
    pos = _pos()
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit", order_id="o1"))
    note_fill_facts(pos, path="limit_fill", fill_price=99.2, quantity=10.0)
    note_partial_close(pos, stage="tp1", qty=5.0, ref_price=103.0, order_id="c1")
    pos.stop_loss = 99.3
    back = _restart(tmp_path, pos)
    assert back.entry_facts["initial_stop_loss"] == 97.0
    assert back.entry_facts["fills"][0]["path"] == "limit_fill"
    assert back.partial_fills[0]["stage"] == "tp1"
    assert back.stop_loss == 99.3                 # the live stop is still the live stop


def test_the_keys_are_on_disk(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _pos()
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit"))
    ex._positions["t1"] = pos
    ex._save_positions()
    row = json.loads(pathlib.Path(ex._positions_file).read_text())["t1"]
    assert row["entry_facts"]["initial_stop_loss"] == 97.0


def test_a_closed_row_round_trips_with_its_facts():
    pos = _pos(status="closed", close_price=103.0, pnl_usd=12.0)
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit"))
    note_partial_close(pos, stage="tp1", qty=5.0, ref_price=103.0)
    back = LiveExecutor._closed_row_to_position(json.loads(json.dumps(closed_trade_row(pos))))
    assert back.entry_facts["initial_stop_loss"] == 97.0
    assert back.partial_fills[0]["stage"] == "tp1"


# ── the red herrings ────────────────────────────────────────────────────────

def test_a_position_with_no_facts_writes_the_row_it_always_wrote():
    row = closed_trade_row(_pos(status="closed"))
    assert "entry_facts" not in row and "partial_fills" not in row


def test_an_old_saved_row_loads_with_nothing_to_restore(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions["t1"] = _pos()
    ex._save_positions()                          # no facts recorded: keys absent
    on_disk = json.loads(pathlib.Path(ex._positions_file).read_text())["t1"]
    assert "entry_facts" not in on_disk and "partial_fills" not in on_disk
    back = LiveExecutor(state_dir=str(tmp_path))._positions["t1"]
    assert getattr(back, "entry_facts", None) is None
    assert getattr(back, "partial_fills", None) is None


def test_the_recorder_never_raises_into_a_trade():
    class Refuses:
        __slots__ = ("stop_loss", "take_profit")

        def __init__(self):
            self.stop_loss, self.take_profit = 1.0, 2.0
    obj = Refuses()
    stamp_entry_facts(obj, entry_facts_for(IDEA, order_type="limit"))     # no __dict__
    note_fill_facts(obj, path="market", fill_price=1.0, quantity=1.0)
    note_partial_close(obj, stage="tp1", qty=1.0, ref_price=1.0)
    bare = entry_facts_for(object(), order_type="limit")                  # a bare object is fine too
    assert bare["initial_stop_loss"] is None and bare["decision_price"] is None


# ── driven through the real code paths ──────────────────────────────────────

_SRC = pathlib.Path(le.__file__)


class _FillVenue:
    def __init__(self, fill, qty):
        self.fill, self.qty = fill, qty

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        return {"id": oid, "status": "closed", "filled": self.qty, "average": self.fill,
                "price": self.fill, "amount": self.qty, "info": {}}

    async def cancel_order(self, *a, **k):
        return {}

    async def fetch_ticker(self, symbol, *a, **k):
        return {"last": self.fill}


def test_a_resting_limit_that_fills_records_the_stop_standing_at_the_fill(tmp_path):
    """The real `_check_pending_limit`: the fill is stamped with the stop and target on
    the position at that moment, beside the initial stop the decision was made on."""
    pos = LivePosition(
        trade_id="T-own", symbol="SOL/USDT:USDT", direction="LONG", entry_price=99.2,
        quantity=10.0, cost_usd=198.4, stop_loss=97.0, take_profit=104.0, leverage=5,
        order_type="limit", limit_order_id="o1", status="pending_fill", origin="executed",
        opened_at=datetime.now(UTC) - timedelta(minutes=5))
    stamp_entry_facts(pos, entry_facts_for(IDEA, order_type="limit", order_id="o1"))
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    venue = _FillVenue(99.15, 10.0)
    ex._exchange = venue
    ex._positions[pos.trade_id] = pos

    async def _sl_tp(self_, exchange, symbol, direction, qty, sl, tp):
        return ("sl1", "tp1")

    async def _ladder(self_, exchange, p, direction, qty, sl_id, tp_id, trade_id=None):
        return sl_id, tp_id, None

    async def _noasync(*a, **k):
        return None

    with patch.object(le, "audit", lambda *a, **k: None), \
            patch.object(type(CONFIG), "is_live", return_value=True), \
            patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
            patch.object(LiveExecutor, "_reattempt_post_fill_sl", _ladder), \
            patch.object(LiveExecutor, "_guard_fill_leverage", AsyncMock(return_value=None)), \
            patch.object(asyncio, "sleep", _noasync):
        asyncio.run(ex._check_pending_limit(venue, pos.trade_id, pos))
    assert pos.status == "open"
    fill, = pos.entry_facts["fills"]
    assert fill["path"] == "limit_fill" and fill["price"] == 99.15 and fill["qty"] == 10.0
    assert fill["stop_at_fill"] == 97.0 and fill["target_at_fill"] == 104.0
    assert pos.entry_facts["initial_stop_loss"] == 97.0
    assert pos.entry_facts["decision_price"] == 100.0


def _ladder_executor(pos):
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._positions = {pos.trade_id: pos}
    ex._venue = SimpleNamespace(id="bitget", order_symbol=lambda s: s,
                                close_params=lambda uta: {"reduceOnly": True},
                                order_read_params=lambda: {})
    ex._is_uta = False
    ex._foreign_position_rows = {}
    ex._record_warning = lambda k: None
    ex._venue_market_price = AsyncMock(return_value=None)
    ex._recovered_from_closing = set()
    ex._save_positions = lambda: None
    ex._update_exchange_sl = AsyncMock(return_value=True)
    return ex


def test_a_ladder_slice_that_closed_is_recorded_and_the_initial_stop_is_kept():
    """The real `_run_partial_tp`: TP1 closes half, moves the stop to break-even, and the
    slice is recorded; the stop the position was sized on is still on the record."""
    idea = SimpleNamespace(entry_price=100.0, market_at_signal=100.0, stop_loss=90.0,
                           take_profit=140.0)
    pos = LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0, quantity=1.0,
        cost_usd=20.0, stop_loss=90.0, take_profit=140.0, leverage=5, status="open",
        atr_at_entry=2.0, origin="executed", opened_at=datetime.now(UTC) - timedelta(hours=2),
        trailing_state={"entry_price": 100.0, "best_price": 100.0, "trailing_active": False,
                        "initial_risk": 10.0, "atr": 2.0, "stage": 0})
    stamp_entry_facts(pos, entry_facts_for(idea, order_type="limit"))
    ex = _ladder_executor(pos)
    x = MagicMock()
    x.amount_to_precision = lambda s, q: q
    x.create_order = AsyncMock(side_effect=[{"id": "O1", "filled": 0.5}])
    x.fetch_order = AsyncMock(return_value={})
    with patch.object(le, "audit", lambda *a, **k: None):
        asyncio.run(ex._run_partial_tp(x, pos, 115.0))                    # 1.5R: TP1
    assert pos.quantity == 0.5
    slice_, = pos.partial_fills
    assert slice_["stage"] == "tp1" and slice_["qty"] == 0.5
    assert slice_["ref_price"] == 115.0 and slice_["order_id"] == "O1"
    assert pos.stop_loss != 90.0, "premise: the ladder moved the stop"
    assert pos.entry_facts["initial_stop_loss"] == 90.0
    row = closed_trade_row(pos)
    assert row["stop_loss"] == pos.stop_loss and row["entry_facts"]["initial_stop_loss"] == 90.0
    assert row["quantity"] == 0.5 and row["partial_fills"][0]["qty"] == 0.5


def test_a_ladder_slice_that_filled_nothing_is_not_recorded():
    idea = SimpleNamespace(entry_price=100.0, market_at_signal=100.0, stop_loss=90.0,
                           take_profit=140.0)
    pos = LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0, quantity=1.0,
        cost_usd=20.0, stop_loss=90.0, take_profit=140.0, leverage=5, status="open",
        atr_at_entry=2.0, origin="executed", opened_at=datetime.now(UTC) - timedelta(hours=2),
        trailing_state={"entry_price": 100.0, "best_price": 100.0, "trailing_active": False,
                        "initial_risk": 10.0, "atr": 2.0, "stage": 0})
    stamp_entry_facts(pos, entry_facts_for(idea, order_type="limit"))
    ex = _ladder_executor(pos)
    x = MagicMock()
    x.amount_to_precision = lambda s, q: q
    x.create_order = AsyncMock(side_effect=[{"id": "O1", "filled": 0, "status": "canceled"}])
    x.fetch_order = AsyncMock(return_value={"id": "O1", "status": "canceled", "filled": 0})
    with patch.object(le, "audit", lambda *a, **k: None):
        asyncio.run(ex._run_partial_tp(x, pos, 115.0))
    assert pos.quantity == 1.0
    assert not getattr(pos, "partial_fills", None)


# ── the two sites too heavy to drive here are pinned by their wiring ────────

def _calls_in(func_name: str, callee: str) -> list[ast.Call]:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name)
    return [c for c in ast.walk(fn) if isinstance(c, ast.Call)
            and getattr(c.func, "id", getattr(c.func, "attr", None)) == callee]


def _path_of(call: ast.Call):
    for kw in call.keywords:
        if kw.arg == "path":
            return ast.unparse(kw.value)
    return None


def test_execute_stamps_the_decision_on_the_position_it_opens():
    assert _calls_in("execute", "stamp_entry_facts"), "execute() no longer records the decision"
    assert _calls_in("execute", "entry_facts_for")


def test_the_drift_fallback_records_its_fill_after_it_re_anchors_the_levels():
    calls = _calls_in("_execute_drift_market_fallback", "note_fill_facts")
    assert calls and _path_of(calls[0]) == "'drift_fallback'"
    src = _SRC.read_text(encoding="utf-8")
    body = src[src.index("async def _execute_drift_market_fallback"):]
    assert body.index("tp_dist_pct = abs(pos.take_profit - old_entry)") < body.index("note_fill_facts")


def test_a_partial_fill_adopted_as_a_position_is_recorded():
    assert _calls_in("_adopt_partial_fill", "note_fill_facts")


def test_the_limit_fill_and_the_ladder_record_through_the_same_helpers():
    assert _calls_in("_check_pending_limit", "note_fill_facts")
    assert _calls_in("_run_partial_tp", "note_partial_close")

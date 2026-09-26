"""A ladder stage is neither repeated nor left half done.

Three defects in the partial take-profit ladder, all driven through the real
`_run_partial_tp`:

- **A close whose order call raised was sent again.** A timeout from
  `create_order` escaped the ladder before it saved, so the record still said
  TP1 had not fired and the next pass sent the same close. When the first
  order had filled, the two closed the whole position. A raise is now read:
  a refusal (`ccxt.ExchangeError`) placed nothing and re-arms the stage; any
  other raise may have placed the order, so the stage is held and not sent
  again, as the no-order-id case already was.
- **A refused stop move was never asked for again.** TP1 moves the stop to
  breakeven and TP2 to 1R of profit, once, when the stage fires. When the
  venue refused the move, the stage had fired and nothing asked again: a
  position whose TP1 had closed half kept its original stop on the other half.
  `stage_lock` is the stop the fired stages lock in, proposed on every pass
  until the stop reaches it.
- **Every pass reset what the ladder had done.** The executor saves the ladder
  after each pass and reads it back at the start of the next, and the read ran
  `__post_init__`, which reset the runner's best price, its stop and its
  remaining quantity. So the runner trailed from the current price, not the
  best one: a move the venue refused at the peak was retried from wherever the
  price had fallen to. `PartialTPState.from_record` restores what it recorded.

The ladder's view of the stop and the size is the book's (`pos.stop_loss`,
`pos.quantity`), read at the start of each pass and the stop written back at
the end, so the record never holds a move the venue refused.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses as _dc
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt
import pytest

import bot.core.live_executor as le
from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.partial_tp import (
    PartialTPState,
    check_partial_tp,
    create_partial_tp_state,
    stage_lock,
)

ROOT = Path(__file__).resolve().parents[1]
ENTRY, QTY, ATR = 100.0, 1.0, 2.0             # 1R = 10 either side
TRAIL = ATR * CONFIG.partial_tp.runner_trail_atr_mult


def _pos(direction="LONG", **over) -> LivePosition:
    stop = 90.0 if direction == "LONG" else 110.0
    kw = dict(trade_id="T1", symbol="BTC/USDT", direction=direction, entry_price=ENTRY,
              quantity=QTY, cost_usd=ENTRY * QTY / 5, stop_loss=stop,
              take_profit=140.0 if direction == "LONG" else 60.0,
              leverage=5, status="open", atr_at_entry=ATR, origin="executed",
              opened_at=datetime.now(UTC) - timedelta(hours=2),
              trailing_state={"entry_price": ENTRY, "best_price": ENTRY,
                              "trailing_active": False, "initial_risk": 10.0,
                              "atr": ATR, "stage": 0})
    kw.update(over)
    return LivePosition(**kw)


def _executor(pos) -> LiveExecutor:
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
    moves: list = []

    async def _move(exchange, p, new_sl):
        moves.append(new_sl)
        return ex._venue_accepts.pop(0) if ex._venue_accepts else True

    ex._venue_accepts = []
    ex._update_exchange_sl = _move
    ex.moves = moves
    return ex


def _exchange(*orders):
    x = MagicMock()
    x.amount_to_precision = lambda s, q: q
    x.create_order = AsyncMock(side_effect=list(orders))
    x.fetch_order = AsyncMock(return_value={})
    return x


def _run(ex, x, pos, price):
    audits: list = []
    with patch.object(le, "audit",
                      lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        asyncio.run(ex._run_partial_tp(x, pos, price))
    return audits


def _filled(qty, oid="O"):
    return {"id": oid, "filled": qty}


# ── a raise from the order call ─────────────────────────────────────────

@pytest.mark.parametrize("exc", [ccxt.RequestTimeout("timed out"),
                                 ccxt.NetworkError("reset"),
                                 RuntimeError("driver fault")])
def test_a_close_whose_order_call_raised_is_not_sent_again(exc):
    pos = _pos()
    ex = _executor(pos)
    x = _exchange(exc, _filled(0.5, "O2"), _filled(0.5, "O3"))
    audits = _run(ex, x, pos, 116.0)
    assert x.create_order.await_count == 1
    assert pos.quantity == QTY, "an unread close changes nothing on the book"
    assert pos.stop_loss == 90.0 and ex.moves == [], "no stop move over an unread close"
    assert pos.partial_tp_state["tp1_hit"] is True, "the stage is held, not re-armed"
    (unread,) = [a for a in audits if a.get("result") == "FILL_UNREAD"]
    assert "the order call raised" in unread["message"], unread["message"]
    for price in (116.5, 117.0):
        _run(ex, x, pos, price)
    assert x.create_order.await_count == 1, "the close is never sent a second time"


def test_a_held_stage_still_brings_the_stop_to_breakeven():
    # Held, not retried, is the no-order-id rule already written: the stop
    # then protects whatever is left at TP1's lock.
    pos = _pos()
    ex = _executor(pos)
    x = _exchange(ccxt.RequestTimeout("timed out"))
    _run(ex, x, pos, 116.0)
    _run(ex, x, pos, 116.0)
    assert pos.stop_loss == pytest.approx(ENTRY * 1.001)


@pytest.mark.parametrize("exc", [ccxt.InvalidOrder("below minimum"),
                                 ccxt.InsufficientFunds("no"),
                                 ccxt.BadRequest("bad")])
def test_a_refused_close_placed_nothing_and_the_stage_is_re_armed(exc):
    pos = _pos()
    ex = _executor(pos)
    x = _exchange(exc, _filled(0.5, "O2"))
    audits = _run(ex, x, pos, 116.0)
    assert pos.partial_tp_state["tp1_hit"] is False
    assert pos.partial_tp_state["tp1_qty_closed"] == 0.0
    assert pos.quantity == QTY and pos.stop_loss == 90.0 and ex.moves == []
    (refused,) = [a for a in audits if a.get("result") == "REFUSED"]
    assert refused["data"]["stage"] == "tp1"
    assert "nothing was closed" in refused["message"]
    # Nothing was placed, so the next pass sends it: once, and it lands.
    _run(ex, x, pos, 116.0)
    assert x.create_order.await_count == 2
    assert pos.quantity == pytest.approx(0.5)
    assert pos.partial_tp_state["tp1_hit"] is True


def test_a_refused_tp1_does_not_cost_tp2_or_the_runner():
    # The refusal took TP1's slice off the ladder's remaining quantity before
    # the venue answered. Read back from the record, that left nothing for
    # TP2; read from the book, TP2 and the runner are still there.
    pos = _pos()
    ex = _executor(pos)
    x = _exchange(ccxt.InvalidOrder("no"), _filled(0.5, "O2"), _filled(0.3, "O3"))
    _run(ex, x, pos, 116.0)                     # refused
    _run(ex, x, pos, 116.0)                     # TP1 lands
    _run(ex, x, pos, 126.0)                     # TP2
    assert x.create_order.await_count == 3
    assert pos.quantity == pytest.approx(0.2)
    assert pos.partial_tp_state["tp2_hit"] is True


def test_a_failure_before_the_order_is_sent_is_not_a_placed_order():
    pos = _pos()
    ex = _executor(pos)
    ex._venue_market_price = AsyncMock(side_effect=RuntimeError("no ticker"))
    x = _exchange(_filled(0.5))
    with pytest.raises(RuntimeError):
        _run(ex, x, pos, 116.0)
    assert x.create_order.await_count == 0
    # The pass raised before it saved, so the stage is still open.
    assert not (pos.partial_tp_state or {}).get("tp1_hit")


# ── a refused stop move is asked for again ──────────────────────────────

@pytest.mark.parametrize("direction, tp1_price, lock", [
    ("LONG", 116.0, ENTRY * 1.001), ("SHORT", 84.0, ENTRY * 0.999)])
def test_a_refused_breakeven_move_is_retried_until_it_lands(direction, tp1_price, lock):
    pos = _pos(direction)
    ex = _executor(pos)
    ex._venue_accepts = [False, False, True]
    x = _exchange(_filled(0.5))
    original = pos.stop_loss
    _run(ex, x, pos, tp1_price)
    assert pos.stop_loss == original, "a refused move leaves the book's stop"
    assert pos.partial_tp_state["current_sl"] == original, (
        "the record never holds a stop the venue refused")
    _run(ex, x, pos, tp1_price)
    _run(ex, x, pos, tp1_price)
    assert ex.moves == [pytest.approx(lock)] * 3
    assert pos.stop_loss == pytest.approx(lock)
    _run(ex, x, pos, tp1_price)
    assert len(ex.moves) == 3, "once the stop holds the lock nothing is asked"


def test_a_refused_tp2_lock_is_retried():
    # A wide ATR keeps the runner's trail (125.5 - 16) under TP2's lock (110),
    # so the lock is the only stop the pass after the refusal can ask for.
    pos = _pos(atr_at_entry=20.0)
    ex = _executor(pos)
    x = _exchange(_filled(0.5), _filled(0.3))
    _run(ex, x, pos, 116.0)
    ex._venue_accepts = [False, True]
    _run(ex, x, pos, 125.5)                  # TP2 fires; its lock is refused
    assert pos.stop_loss == pytest.approx(ENTRY * 1.001)
    _run(ex, x, pos, 120.0)
    assert ex.moves[-2:] == [pytest.approx(110.0)] * 2
    assert pos.stop_loss == pytest.approx(ENTRY + 10.0)


def test_the_pass_that_fires_tp1_asks_for_the_lock_once():
    pos = _pos()
    ex = _executor(pos)
    x = _exchange(_filled(0.5))
    _run(ex, x, pos, 116.0)
    assert ex.moves == [pytest.approx(ENTRY * 1.001)]


def test_a_stop_already_past_the_lock_is_never_pulled_back():
    pos = _pos()
    ex = _executor(pos)
    x = _exchange(_filled(0.5))
    _run(ex, x, pos, 116.0)
    pos.stop_loss = 108.0                    # the trailing path moved it further
    _run(ex, x, pos, 116.0)
    assert ex.moves == [pytest.approx(ENTRY * 1.001)]
    assert pos.stop_loss == 108.0


def test_a_record_an_older_build_wrote_is_read_against_the_book():
    # An older build saved the stop a stage proposed before the venue
    # answered, so its record can claim breakeven over a stop still at 90.
    pos = _pos()
    st = create_partial_tp_state("T1", "LONG", ENTRY, 90.0, 140.0, QTY, ATR)
    st.tp1_hit, st.tp1_qty_closed, st.remaining_qty = True, 0.5, 0.5
    st.current_sl = ENTRY * 1.001
    pos.partial_tp_state = _dc.asdict(st)
    pos.quantity = 0.5
    ex = _executor(pos)
    _run(ex, _exchange(), pos, 112.0)
    assert ex.moves == [pytest.approx(ENTRY * 1.001)]


# ── the runner trails from its best price ───────────────────────────────

@pytest.mark.parametrize("direction, tp1, tp2, peak, after", [
    ("LONG", 116.0, 126.0, 130.0, 128.0),
    ("SHORT", 84.0, 74.0, 70.0, 72.0),
])
def test_a_runner_move_refused_at_the_peak_is_retried_from_the_peak(
        direction, tp1, tp2, peak, after):
    pos = _pos(direction)
    ex = _executor(pos)
    x = _exchange(_filled(0.5), _filled(0.3))
    _run(ex, x, pos, tp1)
    _run(ex, x, pos, tp2)
    ex._venue_accepts = [False, True]
    _run(ex, x, pos, peak)                   # the move at the peak is refused
    _run(ex, x, pos, after)
    want = peak - TRAIL if direction == "LONG" else peak + TRAIL
    assert pos.stop_loss == pytest.approx(want)
    assert pos.partial_tp_state["runner_trail_best"] == pytest.approx(peak)


# ── from_record ─────────────────────────────────────────────────────────

def _fired():
    st = create_partial_tp_state("t", "LONG", 100.0, 98.0, 110.0, 1.0, 1.0)
    for price in (103.1, 105.2, 107.0):
        check_partial_tp(st, price)
    return st


def test_a_record_keeps_what_the_ladder_had_done():
    st = _fired()
    back = PartialTPState.from_record(_dc.asdict(st))
    assert _dc.asdict(back) == _dc.asdict(st)
    assert (back.remaining_qty, back.runner_trail_best) == (pytest.approx(0.2), 107.0)


def test_a_new_ladder_still_starts_from_its_entry():
    st = create_partial_tp_state("t", "LONG", 100.0, 98.0, 110.0, 1.0, 1.0)
    assert (st.remaining_qty, st.current_sl, st.runner_trail_best) == (1.0, 98.0, 100.0)


def test_a_record_from_a_newer_build_keeps_the_fields_this_one_knows():
    rec = _dc.asdict(_fired()) | {"added_later": 1}
    assert PartialTPState.from_record(rec).runner_trail_best == 107.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "107", None])
def test_a_recorded_value_that_is_not_a_number_is_not_restored(bad):
    rec = _dc.asdict(_fired()) | {"runner_trail_best": bad, "remaining_qty": bad,
                                  "current_sl": bad}
    back = PartialTPState.from_record(rec)
    assert (back.remaining_qty, back.current_sl, back.runner_trail_best) == (1.0, 98.0, 100.0)
    assert all(math.isfinite(v) for v in (back.remaining_qty, back.current_sl))


def test_a_record_missing_a_field_it_needs_raises():
    rec = _dc.asdict(_fired())
    del rec["initial_risk"]
    with pytest.raises(TypeError):
        PartialTPState.from_record(rec)


def test_every_reload_in_bot_goes_through_from_record():
    # The constructor resets what a record holds, so a reload that calls it
    # directly is the defect again.
    hits = []
    for path in (ROOT / "bot").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "PartialTPState"
                    and any(isinstance(a, ast.Starred) for a in node.args)
                    or isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "PartialTPState"
                    and any(k.arg is None for k in node.keywords)):
                hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert hits == [], hits


# ── the lock, read directly ─────────────────────────────────────────────

def test_the_lock_is_the_tightest_fired_stage():
    st = create_partial_tp_state("t", "LONG", 100.0, 90.0, 140.0, 1.0, 2.0)
    assert stage_lock(st) is None
    st.tp1_hit = True
    assert stage_lock(st) == pytest.approx(100.1)
    st.tp2_hit = True
    assert stage_lock(st) == pytest.approx(110.0)
    sh = create_partial_tp_state("t", "SHORT", 100.0, 110.0, 60.0, 1.0, 2.0)
    sh.tp1_hit = True
    assert stage_lock(sh) == pytest.approx(99.9)
    sh.tp2_hit = True
    assert stage_lock(sh) == pytest.approx(90.0)


def test_the_stage_moves_and_the_lock_are_one_formula():
    st = create_partial_tp_state("t", "LONG", 100.0, 90.0, 140.0, 1.0, 2.0)
    (tp1,) = check_partial_tp(st, 116.0)
    assert tp1.new_sl == stage_lock(st)
    moves = [a for a in check_partial_tp(st, 125.1) if a.stage == "tp2"]
    assert moves[0].new_sl == stage_lock(st)


def test_a_lock_below_the_trail_is_one_move_to_the_trail():
    st = create_partial_tp_state("t", "LONG", 100.0, 90.0, 140.0, 1.0, 2.0)
    check_partial_tp(st, 116.0)
    check_partial_tp(st, 126.0)
    st.current_sl = 90.0                      # the book's stop never moved
    acts = check_partial_tp(st, 130.0)
    assert [(a.action, a.new_sl) for a in acts] == [("move_sl", pytest.approx(130.0 - TRAIL))]


def test_a_trail_below_the_lock_is_one_move_to_the_lock():
    # ATR 25: the trail sits at 126 - 20 = 106, under TP2's lock at 110.
    st = create_partial_tp_state("t", "LONG", 100.0, 90.0, 140.0, 1.0, 25.0)
    check_partial_tp(st, 116.0)
    check_partial_tp(st, 126.0)
    st.current_sl = 90.0
    acts = check_partial_tp(st, 126.0)
    assert [(a.action, a.new_sl) for a in acts] == [("move_sl", pytest.approx(110.0))]

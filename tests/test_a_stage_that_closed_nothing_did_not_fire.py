"""A ladder stage that closed nothing did not fire.

`_partial_close` answers one of five words, and the caller acted on three of
them. The one it did not act on was "nothing closed", so a stage that banked
nothing was recorded as HIT, moved the stop as though a slice had been banked,
and audited NOTHING -- the one outcome of the four with no line at all.

Driven on the head this slice starts from, a lot-size-1 market with a position
of one contract (TP1's slice is 0.5):

    orders sent    0        tp1_hit  True        tp1_qty_closed  0.5
    pos.quantity   1.0      stop     90.0 -> 100.1 (breakeven+fees)
    AUDIT LINES    []

THE ORDINARY CASE IS AN ACCOUNT TRADING AT THE VENUE'S MINIMUM.
`_exchange_minimum_gate` raises a small order's quantity to the venue's minimum
lot, and TP1 closes 50% of it -- below that minimum by construction. So every
position a small account opens has a ladder that can bank nothing, fires TP1
silently, and pulls the stop to breakeven, turning a ride to the target into a
scratch if price comes back. Nothing on any card or in any audit said so.

TWO PRODUCERS OF ONE WORD, WITH DIFFERENT REMEDIES. `_read_partial_fill` also
answered "none" for an order the venue CANCELLED having filled a stated zero --
an order that went OUT. That is not "nothing was submitted": a later pass may
fill, so it takes the re-arm treatment `refused` already had, and it is
`cancelled` now so the audit can name which happened. A reader that folds the
two retries the grid case forever or gives up on the cancelled one.

A STAGE THE SIZE CANNOT PLACE IS RECORDED, NOT RE-ARMED. Re-arming retries on
every pass forever, because a position's quantity only shrinks: a slice that
cannot be placed once cannot be placed later. `PartialTPState.unplaceable`
records it, `check_partial_tp` stops proposing it, and `stage_lock` locks no
stop for it -- a stage that banked nothing has nothing to lock. Both readers ask
`unplaceable_stages`, because a second copy of that judgement is a second
answer about whether a stage fired.

AND IT DOES NOT TURN THE WHOLE LADDER OFF. A TP2 whose slice rounds to nothing
must not cost TP1's lock the re-proposal `stage_lock` exists for -- the defect
that function was written to fix.
"""
from __future__ import annotations

import ast
import asyncio
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt
import pytest

import bot.core.live_executor as le
from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition, _read_partial_fill
from bot.core.partial_tp import (
    CLOSING_STAGES,
    PartialTPState,
    check_partial_tp,
    create_partial_tp_state,
    stage_lock,
    unplaceable_stages,
)
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
ENTRY, QTY, ATR = 100.0, 1.0, 2.0          # 1R = 10 either side
TP1_PRICE = ENTRY + 10.0 * CONFIG.partial_tp.tp1_r_multiple + 1.0
TP2_PRICE = ENTRY + 10.0 * CONFIG.partial_tp.tp2_r_multiple + 1.0


# ── fixtures ───────────────────────────────────────────────────────────

def _pos(**over) -> LivePosition:
    kw = dict(trade_id="T1", symbol="ONE/USDT", direction="LONG",
              entry_price=ENTRY, quantity=QTY, cost_usd=ENTRY * QTY / 5,
              stop_loss=90.0, take_profit=140.0, leverage=5, status="open",
              atr_at_entry=ATR, origin="executed",
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
    ex.saves = []
    ex._save_positions = lambda: ex.saves.append(1)
    moves: list = []

    async def _move(exchange, p, new_sl):
        moves.append(new_sl)
        return True

    ex._update_exchange_sl = _move
    ex.moves = moves
    return ex


def _grid_exchange(min_lot: float = 1.0):
    """A market whose amount grid is `min_lot`: ccxt RAISES below it.

    Driven against the pinned ccxt: `amount_to_precision(0.5)` on a market with
    an amount precision of 1 raises `InvalidOrder`, and 1.4 TRUNCATES to 1.
    """
    x = MagicMock()

    def _prec(sym, q):
        if float(q) < min_lot:
            raise ccxt.InvalidOrder(
                "amount must be greater than minimum amount precision of 1")
        return str(math.floor(float(q) / min_lot) * min_lot)

    x.amount_to_precision = _prec
    x.create_order = AsyncMock(side_effect=AssertionError("nothing to submit"))
    x.fetch_order = AsyncMock(return_value={})
    return x


def _run(ex, x, pos, price):
    audits: list = []
    with patch.object(le, "audit",
                      lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        asyncio.run(ex._run_partial_tp(x, pos, price))
    return audits


def _results(audits):
    return [a.get("result") for a in audits]


# ── the grid: nothing was submitted ────────────────────────────────────

class TestTheSliceThatRoundsToNothing:
    """The stage banked nothing, so it did not fire."""

    def test_it_is_audited(self):
        pos = _pos()
        ex = _executor(pos)
        audits = _run(ex, x := _grid_exchange(), pos, TP1_PRICE)
        assert x.create_order.await_count == 0, "nothing is submitted"
        (line,) = [a for a in audits if a.get("result") == "STAGE_UNPLACEABLE"]
        assert line["data"]["stage"] == "tp1"
        assert "rounds to nothing on this market's amount grid" in line["message"], \
            line["message"]
        assert "stop and take-profit still apply" in line["message"]

    def test_the_stage_is_not_recorded_as_hit(self):
        pos = _pos()
        ex = _executor(pos)
        _run(ex, _grid_exchange(), pos, TP1_PRICE)
        st = pos.partial_tp_state
        assert st["tp1_hit"] is False, "a stage that banked nothing did not fire"
        assert st["tp1_qty_closed"] == 0.0
        assert pos.quantity == QTY, "nothing was closed"

    def test_the_stop_is_not_pulled_to_breakeven(self):
        """TP1's lock says the leg costs nothing BECAUSE half is banked."""
        pos = _pos()
        ex = _executor(pos)
        _run(ex, _grid_exchange(), pos, TP1_PRICE)
        assert pos.stop_loss == 90.0, "the stop is the position's own"
        assert ex.moves == [], "no stop move over a slice that was never closed"

    def test_it_is_recorded_rather_than_retried(self):
        pos = _pos()
        ex = _executor(pos)
        x = _grid_exchange()
        first = _run(ex, x, pos, TP1_PRICE)
        assert "STAGE_UNPLACEABLE" in _results(first)
        assert ex.saves, (
            "the mark has to reach DISK: unsaved, a restart re-proposes the "
            "stage and records it again. The round found this, because every "
            "fixture's `_save_positions` was a no-op.")
        assert pos.partial_tp_state["unplaceable"] == ["tp1"] or \
            tuple(pos.partial_tp_state["unplaceable"]) == ("tp1",)
        # A quantity only shrinks, so retrying is a line of audit per tick
        # forever. Every later pass is silent and asks the venue nothing.
        for price in (TP1_PRICE + 1, TP1_PRICE + 2, TP2_PRICE):
            assert _results(_run(ex, x, pos, price)) == []
        assert x.create_order.await_count == 0
        assert pos.stop_loss == 90.0 and ex.moves == []

    def test_an_over_fill_on_tp1_leaves_tp2_with_nothing_to_close(self):
        """The `qty <= 0` door, driven rather than assumed.

        An empty book does NOT reach it: `check_partial_tp` guards its own
        `close_qty > 0` against `remaining_qty`, which the pass reads from
        `pos.quantity`, so a position with nothing on it proposes nothing and
        the caller's loop never runs. What does reach it is a TP1 whose fill
        came back LARGER than the slice -- the under/over-fill
        `_partial_close`'s docstring exists for -- which takes the book to zero
        inside the pass, so TP2's `min(slice, pos.quantity)` is 0 and the word
        is the initialised one with no venue call at all.
        """
        pos = _pos()
        ex = _executor(pos)
        x = MagicMock()
        x.amount_to_precision = lambda s, q: q
        x.create_order = AsyncMock(return_value={"id": "O1", "filled": QTY})
        x.fetch_order = AsyncMock(return_value={})
        audits = _run(ex, x, pos, TP2_PRICE)     # both stages in one pass
        assert x.create_order.await_count == 1, "TP2 asks the venue nothing"
        assert pos.quantity == 0.0
        assert "TP1" in _results(audits), _results(audits)
        (line,) = [a for a in audits if a.get("result") == "STAGE_UNPLACEABLE"]
        assert line["data"]["stage"] == "tp2"
        assert "nothing left on the book" in line["message"], line["message"]
        assert "amount grid" not in line["message"], \
            "the market answered nothing here: it is the book that is empty"
        assert pos.partial_tp_state["tp2_hit"] is False
        assert tuple(pos.partial_tp_state["unplaceable"]) == ("tp2",)

    def test_tp2_unplaceable_does_not_cost_tp1_its_lock(self):
        """The defect `stage_lock` was written to fix must stay fixed.

        A market whose lot is 0.4: TP1's 0.5 places (truncates to 0.4), TP2's
        0.3 does not. TP1 banked something, so its lock is still proposed on
        every pass until the stop reaches it -- turning the whole ladder off at
        TP2 would lose exactly that.
        """
        pos = _pos()
        ex = _executor(pos)
        x = _grid_exchange(min_lot=0.4)
        x.create_order = AsyncMock(return_value={"id": "O1", "filled": 0.4})
        # TP1 fires and banks 0.4; the venue refuses its stop move.
        moved: list = []

        async def _refuse_then_accept(exchange, p, new_sl):
            moved.append(new_sl)
            return len(moved) > 1

        ex._update_exchange_sl = _refuse_then_accept
        _run(ex, x, pos, TP1_PRICE)
        assert pos.quantity == pytest.approx(QTY - 0.4)
        assert pos.partial_tp_state["tp1_hit"] is True
        assert pos.stop_loss == 90.0, "the refused move is not recorded"
        # TP2's slice rounds to nothing: recorded, and TP1's lock is still asked.
        audits = _run(ex, x, pos, TP2_PRICE)
        assert "STAGE_UNPLACEABLE" in _results(audits)
        assert tuple(pos.partial_tp_state["unplaceable"]) == ("tp2",)
        assert pos.partial_tp_state["tp1_hit"] is True, "TP1 still fired"
        assert pos.stop_loss > ENTRY, "TP1's lock reached the stop"
        # And a third pass proposes TP2 nothing: recorded, not retried.
        sent = x.create_order.await_count
        assert _results(_run(ex, x, pos, TP2_PRICE + 1)) == []
        assert x.create_order.await_count == sent


# ── the cancelled order: it went out ───────────────────────────────────

class TestACancelledOrderWentOut:
    """A stated filled of zero is a re-arm, and it is not "none"."""

    def test_the_reading_separates_it_from_nothing_submitted(self):
        cancelled = {"confirmed": False, "failure_stage": "order_cancelled",
                     "raw": {"filled": 0}}
        assert _read_partial_fill(cancelled) == (0.0, "cancelled")
        part = {"confirmed": False, "failure_stage": "order_cancelled",
                "raw": {"filled": 0.3}}
        assert _read_partial_fill(part) == (0.3, "filled")
        unstated = {"confirmed": False, "failure_stage": "order_cancelled",
                    "raw": {}}
        assert _read_partial_fill(unstated) == (0.0, "unknown"), \
            "a filled the venue did not state may have closed some"

    def _cancelled_executor(self):
        pos = _pos()
        ex = _executor(pos)
        x = MagicMock()
        x.amount_to_precision = lambda s, q: q
        x.create_order = AsyncMock(return_value={"id": "O1"})
        x.fetch_order = AsyncMock(return_value={})
        ex._verify_order_fill = AsyncMock(return_value={
            "confirmed": False, "failure_stage": "order_cancelled",
            "raw": {"filled": 0}})
        return pos, ex, x

    def test_it_is_audited_as_a_cancellation_not_a_refusal(self):
        pos, ex, x = self._cancelled_executor()
        audits = _run(ex, x, pos, TP1_PRICE)
        (line,) = [a for a in audits if a.get("result") == "CANCELLED"]
        assert "cancelled the close having filled nothing" in line["message"]
        assert "re-armed" in line["message"]
        assert "refused" not in line["message"], \
            "the venue took this order: a refusal is a different remedy"

    def test_the_stage_is_re_armed_and_the_stop_is_not_moved(self):
        pos, ex, x = self._cancelled_executor()
        _run(ex, x, pos, TP1_PRICE)
        assert pos.partial_tp_state["tp1_hit"] is False
        assert pos.partial_tp_state["tp1_qty_closed"] == 0.0
        assert pos.quantity == QTY
        assert pos.stop_loss == 90.0 and ex.moves == []
        assert tuple(pos.partial_tp_state["unplaceable"]) == (), \
            "the size can place this slice: only the venue's answer failed"

    def test_a_later_pass_sends_it_again(self):
        """This is what separates it from the grid case."""
        pos, ex, x = self._cancelled_executor()
        _run(ex, x, pos, TP1_PRICE)
        assert x.create_order.await_count == 1
        ex._verify_order_fill = AsyncMock(return_value={
            "confirmed": True, "fill_qty": 0.5})
        _run(ex, x, pos, TP1_PRICE)
        assert x.create_order.await_count == 2, "re-armed means sent again"
        assert pos.quantity == pytest.approx(QTY - 0.5)
        assert pos.partial_tp_state["tp1_hit"] is True


# ── the reading, and its two readers ───────────────────────────────────

class TestOneReadingTwoReaders:

    def test_an_unplaceable_stage_is_never_proposed(self):
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        assert [a.stage for a in check_partial_tp(st, TP1_PRICE)] == ["tp1"]
        st2 = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st2.unplaceable = ("tp1",)
        assert check_partial_tp(st2, TP1_PRICE) == []

    def test_an_unplaceable_stage_locks_no_stop(self):
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st.tp1_hit = True
        assert stage_lock(st) is not None, "a stage that fired locks its stop"
        st.unplaceable = ("tp1",)
        assert stage_lock(st) is None, "a stage that banked nothing locks nothing"

    def test_an_unplaceable_tp2_locks_no_stop_either(self):
        """The TP2 arm needs its own fixture: with `tp2_hit` False, dropping
        its guard changes nothing, so the guard would be unmeasured."""
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st.tp1_hit = st.tp2_hit = True
        assert stage_lock(st) is not None
        st.unplaceable = ("tp2",)
        lock = stage_lock(st)
        assert lock == pytest.approx(
            check_partial_tp.__globals__["_tp1_lock"](st)), \
            "TP2 banked nothing, so the stop it locks is TP1's"
        st.unplaceable = ("tp1", "tp2")
        assert stage_lock(st) is None

    def test_tp2_unplaceable_leaves_tp1s_lock(self):
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st.tp1_hit = True
        st.unplaceable = ("tp2",)
        assert stage_lock(st) == pytest.approx(
            check_partial_tp.__globals__["_tp1_lock"](st))

    @pytest.mark.parametrize("raw,expected", [
        (None, frozenset()),
        ((), frozenset()),
        (["tp1"], frozenset({"tp1"})),
        (("tp1", "tp2"), frozenset({"tp1", "tp2"})),
        ("tp1", frozenset()),                    # a bare string is not a list
        (["tp1", "runner", "nonsense", 7, None], frozenset({"tp1"})),
        (42, frozenset()),
    ])
    def test_the_field_is_read_rather_than_trusted(self, raw, expected):
        """`from_record` hands the constructor whatever the dict holds."""
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st.unplaceable = raw
        assert unplaceable_stages(st) == expected

    def test_a_record_written_before_the_field_reads_as_none_unplaceable(self):
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        record = {k: v for k, v in st.__dict__.items() if k != "unplaceable"}
        back = PartialTPState.from_record(record)
        assert unplaceable_stages(back) == frozenset()

    def test_recording_one_stage_keeps_what_the_record_held(self):
        """A planted record, because the product cannot make this state.

        TP2 only ever triggers behind `tp1_hit`, and a TP1 the size cannot
        place leaves that False -- so the two are never recorded together by
        any sequence this build produces, and a record that arrives holding
        one must not have it dropped when the other is added.
        """
        pos = _pos()
        ex = _executor(pos)
        st = create_partial_tp_state(
            trade_id=pos.trade_id, direction="LONG", entry_price=ENTRY,
            stop_loss=90.0, take_profit=140.0, quantity=QTY, atr=ATR)
        st.tp1_hit = True                  # as a record another build wrote it
        st.unplaceable = ("tp1",)
        pos.partial_tp_state = {**st.__dict__}
        x = _grid_exchange(min_lot=0.9)    # TP2's 0.3 cannot be placed
        audits = _run(ex, x, pos, TP2_PRICE)
        assert "STAGE_UNPLACEABLE" in _results(audits)
        assert tuple(pos.partial_tp_state["unplaceable"]) == ("tp1", "tp2")

    def test_it_survives_a_round_trip_through_the_record(self):
        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st.unplaceable = ("tp1",)
        back = PartialTPState.from_record({**st.__dict__})
        assert unplaceable_stages(back) == frozenset({"tp1"})

    def test_the_runner_is_not_a_closing_stage(self):
        """The ladder never closes it: `close_runner` is deliberately unrun."""
        assert CLOSING_STAGES == ("tp1", "tp2")
        assert "runner" not in CLOSING_STAGES

    def test_both_readers_ask_the_one_reading(self):
        """A byte-identical copy agrees with every fixture, so it is DRIVEN."""
        import bot.core.partial_tp as ptp
        calls: list = []
        real = ptp.unplaceable_stages

        def _spy(state):
            calls.append(state)
            return frozenset({"tp1", "tp2"})

        st = create_partial_tp_state(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR)
        st.tp1_hit = True
        with patch.object(ptp, "unplaceable_stages", _spy):
            assert ptp.stage_lock(st) is None
            assert ptp.check_partial_tp(st, TP2_PRICE) == []
        assert len(calls) >= 2, "each reader asked it"
        assert ptp.unplaceable_stages is real


# ── the vocabulary is closed, and every word is acted on ───────────────

class TestEveryWordIsActedOn:

    @staticmethod
    def _source(fn) -> str:
        import inspect
        import textwrap
        return code_only(textwrap.dedent(inspect.getsource(fn)))

    def test_the_caller_names_every_word_the_close_can_answer(self):
        """The silent word was the defect: a fifth must not arrive unread.

        Every string a `return` can yield, not the elements of a Tuple: the
        first draft of this walk read `node.value` as an `ast.Tuple` and so
        could not see `cancelled` at all, because its return is a conditional
        expression. It reported four words and passed, over the very word this
        slice adds -- a guard one AST node short of the thing it counts.
        """
        produced = set()
        for fn in (LiveExecutor._partial_close, _read_partial_fill):
            for node in ast.walk(ast.parse(self._source(fn))):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                for sub in ast.walk(node.value):
                    if (isinstance(sub, ast.Constant)
                            and isinstance(sub.value, str) and sub.value):
                        produced.add(sub.value)
        assert produced == {"filled", "none", "refused", "cancelled", "unknown"}, \
            produced
        read = self._source(LiveExecutor._run_partial_tp)
        for word in produced:
            assert f'"{word}"' in read, (
                f'{word!r} is answered by the close and never read by the '
                f'caller: it would be the silent outcome this slice removed')

    def test_filled_is_never_answered_with_a_non_positive_quantity(self):
        """The caller guards `closed_qty > 0`; no input reaches the else."""
        for check in (
            {"confirmed": True, "fill_qty": 0},
            {"confirmed": True, "fill_qty": 0.0},
            {"confirmed": True, "fill_qty": -1},
            {"confirmed": True, "fill_qty": None},
            {"confirmed": False, "failure_stage": "order_cancelled",
             "raw": {"filled": 0}},
        ):
            qty, source = _read_partial_fill(check)
            assert not (source == "filled" and qty <= 0), (qty, source, check)

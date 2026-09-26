"""TP1's "breakeven" lock was a measured loss, in both runtimes.

`_tp1_lock` read ``entry_price * 0.001`` under a docstring calling it
"breakeven, plus a small buffer for fees", and a round trip is not 0.1% of price
in either runtime this ladder runs in. A price move of x% of entry realizes x%
of notional and the round trip costs its own percent of notional, so the two are
compared directly: the stop that costs nothing sits exactly one round trip past
the entry.

Driven at the shipped live defaults:

    live, market entry      round trip 0.1200%   old lock 0.1000%   -> -0.0200%
    live, limit entry       round trip 0.0800%   old lock 0.1000%   -> +0.0200%
    backtest --honest       round trip 0.1200%   old lock 0.1000%   -> -0.0200%
    backtest field default  round trip 0.2000%   old lock 0.1000%   -> -0.1000%

So a market-entry runner stopped out at its "breakeven" lock paid a fifth of its
round trip, printed as ``SL->breakeven``; a limit entry over-locked by a quarter,
which is the "half again too wide for a limit entry" shape the time stop's buffer
had; and the backtest -- which runs the same ladder, since `PARTIAL_TP_ENABLED`
defaults True -- lost the same fifth under `--honest` and half its round trip at
its own field default.

WHICH backtest rate applies was DRIVEN, not read off the field: `--honest` (how
the frozen benchmark is run) replaces the stale `--commission` default with the
live taker rate, so under it the two runtimes charge the same round trip.

THE ROUND TRIP IS AN INPUT, never computed in `partial_tp`, because each runtime
has its own fee model and the module must not import one. The live executor
supplies the position's own entry leg on EVERY pass (so a record written before
the field is upgraded without a rebuild that would forget which stages fired),
and the backtest supplies its `commission_pct` pair at construction -- the
division CLAUDE.md already records for fees.
"""
from __future__ import annotations

import inspect
import pathlib
import re
import textwrap

import pytest

from bot.core.live_executor import LiveExecutor
from bot.core.partial_tp import (
    LEGACY_FEE_BUFFER_PCT,
    PartialTPState,
    check_partial_tp,
    create_partial_tp_state,
    rebuild_ladder,
    stage_lock,
)
from bot.core.trade_costs import round_trip_pct
from tests.source_scan import code_only

ENTRY, QTY, ATR = 100.0, 1.0, 2.0          # 1R = 10 either side
TP1_R = 1.5


def _state(direction="LONG", rt=None):
    stop = ENTRY - 10.0 if direction == "LONG" else ENTRY + 10.0
    tp = ENTRY + 40.0 if direction == "LONG" else ENTRY - 40.0
    return create_partial_tp_state("T1", direction, ENTRY, stop, tp, QTY, ATR,
                                   fee_round_trip_pct=rt)


def _tp1_price(direction="LONG"):
    return (ENTRY + 10.0 * TP1_R + 1.0 if direction == "LONG"
            else ENTRY - 10.0 * TP1_R - 1.0)


def _lock(direction="LONG", rt=None):
    st = _state(direction, rt)
    (tp1,) = [a for a in check_partial_tp(st, _tp1_price(direction))
              if a.stage == "tp1"]
    return tp1.new_sl


def _net_pct_of_notional(lock, rt, direction="LONG"):
    """What a stop-out at `lock` realizes, net of the round trip."""
    move = ((lock - ENTRY) if direction == "LONG" else (ENTRY - lock)) / ENTRY
    return move * 100.0 - rt


class TestTheLockCostsNothing:
    @pytest.mark.parametrize("order_type", ["market", "limit", None])
    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_a_stop_out_at_the_lock_is_exactly_breakeven(self, order_type, direction):
        rt = round_trip_pct(order_type)
        lock = _lock(direction, rt)
        assert _net_pct_of_notional(lock, rt, direction) == pytest.approx(0.0), (
            f"the lock is not breakeven for a {order_type} entry: it realizes "
            f"{_net_pct_of_notional(lock, rt, direction):+.4f}% of notional")

    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_the_backtests_own_round_trip_is_followed_at_either_rate(self, direction):
        """`--honest` uses the live taker rate; a plain run its field default."""
        from bot.backtest.models import BacktestConfig
        from bot.config import CONFIG

        honest = 2.0 * CONFIG.risk.taker_fee_pct
        plain = 2.0 * BacktestConfig().commission_pct
        assert honest < plain, (
            "the honest run is meant to charge the LIVE taker rate, which is "
            "below the stale --commission default this test contrasts it with")
        for rt in (honest, plain):
            lock = _lock(direction, rt)
            assert _net_pct_of_notional(lock, rt, direction) == pytest.approx(0.0)
        old = ENTRY * (1.001 if direction == "LONG" else 0.999)
        assert _net_pct_of_notional(old, plain, direction) == pytest.approx(-0.1)
        assert _net_pct_of_notional(old, honest, direction) == pytest.approx(-0.02)

    def test_the_old_constant_lost_a_fifth_of_a_market_round_trip(self):
        """The defect, stated as arithmetic rather than remembered."""
        rt = round_trip_pct("market")
        old = ENTRY * (1.0 + LEGACY_FEE_BUFFER_PCT / 100.0)
        assert _net_pct_of_notional(old, rt) == pytest.approx(-0.02, abs=1e-9)

    def test_the_old_constant_over_locked_a_limit_entry(self):
        rt = round_trip_pct("limit")
        old = ENTRY * (1.0 + LEGACY_FEE_BUFFER_PCT / 100.0)
        assert _net_pct_of_notional(old, rt) == pytest.approx(+0.02, abs=1e-9)

    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_a_wider_round_trip_puts_the_lock_further_from_entry(self, direction):
        near = _lock(direction, 0.05)
        far = _lock(direction, 0.30)
        assert abs(far - ENTRY) > abs(near - ENTRY)

    def test_the_lock_is_on_the_profit_side_for_both_directions(self):
        assert _lock("LONG", 0.12) > ENTRY
        assert _lock("SHORT", 0.12) < ENTRY

    def test_a_zero_round_trip_is_a_reading_and_locks_exactly_at_entry(self):
        """A venue that charges nothing is not an unstated fee model."""
        assert _lock("LONG", 0.0) == pytest.approx(ENTRY)
        assert _lock("SHORT", 0.0) == pytest.approx(ENTRY)

    def test_the_stage_move_and_the_lock_are_still_one_formula(self):
        st = _state("LONG", round_trip_pct("market"))
        (tp1,) = [a for a in check_partial_tp(st, _tp1_price()) if a.stage == "tp1"]
        assert tp1.new_sl == stage_lock(st)

    def test_tp2s_lock_is_untouched(self):
        """1R of price, as before: this slice changes the breakeven stop only."""
        st = _state("LONG", round_trip_pct("market"))
        check_partial_tp(st, _tp1_price())
        check_partial_tp(st, ENTRY + 10.0 * 2.5 + 1.0)
        assert stage_lock(st) == pytest.approx(ENTRY + 10.0)


class TestTheBackstopIsNamedAsOne:
    """A ladder this build did not write carries no fee model.

    Both production builders supply the figure and the live executor re-reads it
    every pass, so this branch is reached by no product input -- it is the
    backstop, and it keeps such a record behaving exactly as it always did
    rather than moving on a figure nobody supplied.
    """

    @pytest.mark.parametrize("junk", [None, "0.12", True, float("nan"),
                                      float("inf"), -0.5])
    def test_an_unsupplied_or_unreadable_figure_uses_the_pre_slice_constant(self, junk):
        st = _state("LONG", junk)
        (tp1,) = [a for a in check_partial_tp(st, _tp1_price()) if a.stage == "tp1"]
        assert tp1.new_sl == pytest.approx(
            ENTRY * (1.0 + LEGACY_FEE_BUFFER_PCT / 100.0))

    def test_a_record_written_before_the_field_restores_without_losing_progress(self):
        """`from_record` drops nothing it knows and the field simply defaults.

        A REQUIRED field would raise `TypeError` here, and the executor's own
        `except` rebuilds through the constructor -- which resets `tp1_hit`, the
        exact defect the persistence slice fixed ("a restart sold the runner
        twice"). So the field defaults and the executor supplies it.
        """
        record = {"trade_id": "T1", "direction": "LONG", "entry_price": ENTRY,
                  "original_sl": 90.0, "original_tp": 140.0, "initial_risk": 10.0,
                  "original_qty": QTY, "atr": ATR, "tp1_hit": True,
                  "tp1_qty_closed": 0.5, "remaining_qty": 0.5,
                  "current_sl": 100.1}
        st = PartialTPState.from_record(record)
        assert st.tp1_hit is True and st.remaining_qty == pytest.approx(0.5)
        assert st.fee_round_trip_pct is None

    def test_a_record_written_after_it_round_trips(self):
        st = _state("LONG", 0.12)
        import dataclasses as _dc
        back = PartialTPState.from_record(_dc.asdict(st))
        assert back.fee_round_trip_pct == pytest.approx(0.12)

    def test_the_rebuild_path_carries_it_too(self):
        st, why = rebuild_ladder(
            trade_id="T1", direction="LONG", entry_price=ENTRY, stop_loss=90.0,
            take_profit=140.0, quantity=QTY, atr=ATR, entry_risk=10.0,
            restored_without_ladder=False, fee_round_trip_pct=0.12)
        assert why == "" and st is not None
        assert st.fee_round_trip_pct == pytest.approx(0.12)


class TestEachRuntimeSuppliesItsOwn:
    """The live executor reads the position; the backtest reads its config."""

    EXEC = pathlib.Path("bot/core/live_executor.py")
    BT = pathlib.Path("bot/backtest/engine.py")

    def _body(self):
        """`_run_partial_tp`'s own source, comments blanked.

        NOT `ast.parse(code_only(<the whole file>))`: `code_only` blanks
        docstrings, and `live_executor.py` is one of the eleven `bot/` files
        that stop parsing when it does -- a class whose body is only a
        docstring. And `inspect.getsource` of a METHOD is indented, which
        `ast.parse` refuses. Both traps are written down in CLAUDE.md, so this
        reads the method, dedents it, and blanks its comments.
        """
        src = textwrap.dedent(inspect.getsource(LiveExecutor._run_partial_tp))
        assert len(re.findall(r"^    async def _run_partial_tp\(",
                              self.EXEC.read_text(), re.M)) == 1, (
            "more than one definition of _run_partial_tp: the source read is "
            "ambiguous")
        return code_only(src)

    def test_the_executor_reads_the_positions_own_entry_leg(self):
        body = self._body()
        assert "st.fee_round_trip_pct = round_trip_pct(" in body, (
            "the ladder's fee model is not read from the position")
        assert "0.001" not in body

    def test_it_is_set_before_the_check_runs(self):
        """A figure set after the check is a figure the lock never saw."""
        body = self._body()
        assert body.index("st.fee_round_trip_pct") < body.index("check_partial_tp("), (
            "the fee model is supplied after the stages are evaluated")

    def test_the_backtest_supplies_its_own_commission_pair(self):
        src = code_only(self.BT.read_text())
        assert "fee_round_trip_pct=2.0 * self.config.commission_pct" in src, (
            "the backtest hands the ladder a fee model that is not its own")

    def test_partial_tp_imports_no_fee_model(self):
        """One ladder, two runtimes: the module must not pick a side."""
        src = code_only(pathlib.Path("bot/core/partial_tp.py").read_text())
        assert "trade_costs" not in src
        assert "commission_pct" not in src
        assert "taker_fee_pct" not in src and "maker_fee_pct" not in src


class TestTheExecutorReadsTheEntryLegItPlaced:
    """The one drive that separates reading the POSITION from reading a constant.

    Every source assertion above is satisfied by a hard-coded
    `round_trip_pct("limit")`, and every state-level test builds its own figure.
    Only a LIVE pass over two positions that differ in `order_type` says the
    executor read the position's own leg.
    """

    @staticmethod
    def _lock_asked(order_type):
        from tests.test_a_ladder_stage_is_neither_repeated_nor_left_half_done import (
            _exchange, _filled, _pos, _run,
        )
        from tests.test_a_ladder_stage_is_neither_repeated_nor_left_half_done import (
            _executor as _ladder_executor,
        )

        pos = _pos(order_type=order_type)
        ex = _ladder_executor(pos)
        _run(ex, _exchange(_filled(0.5)), pos, 116.0)     # 1.6R: TP1 fires
        (asked,) = ex.moves
        return asked

    def test_a_market_entry_and_a_limit_entry_get_different_locks(self):
        market = self._lock_asked("market")
        limit = self._lock_asked("limit")
        assert market != limit, (
            "both entry types were locked at the same stop: the ladder is not "
            "reading the position's own entry leg")
        assert market == pytest.approx(100.0 * (1.0 + round_trip_pct("market") / 100.0))
        assert limit == pytest.approx(100.0 * (1.0 + round_trip_pct("limit") / 100.0))

    def test_the_limit_entry_locks_nearer_because_it_pays_less(self):
        assert self._lock_asked("limit") < self._lock_asked("market")

    def test_an_unstated_order_type_is_taker(self):
        """The venue's default, and the one direction that cannot flatter."""
        assert self._lock_asked(None) == pytest.approx(
            100.0 * (1.0 + round_trip_pct("market") / 100.0))

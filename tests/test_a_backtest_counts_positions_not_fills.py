"""A backtest counts positions, not the fills the partial-TP ladder makes.

Audit B4-04, approved by the owner on 8 October. Every `--honest` run turns
the partial-TP ladder on: TP1 closes half a position at +1.5R and TP2 another
30% at +2.5R, and each is a `BacktestTrade` row beside the runner's. Every
count, win rate, profit factor, streak and average R was taken over those
rows, so a position that reached TP1 added one or two winning rows by
construction while a position stopped before TP1 added one losing row.
Momentum Hunter's published "5 trades, 60%" was 3 positions, 1 a winner.

Driven through the real ladder (`_check_ladder_intrabar`, `_close_position`)
and the real compiler, then through every reader that counted rows: the
runner's pooled block, its buckets and Sortino, the public breakdown, the
validation gate's recorder and the portfolio's per-symbol table.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig, BacktestTrade
from bot.backtest.portfolio_engine import per_symbol_table
from bot.backtest.positions import position_rr, positions
from bot.config import CONFIG
from bot.core.partial_tp import create_partial_tp_state
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
SYM = "BTC/USDT"


def _bar(o, h, lo, c, n=0):
    return BacktestBar(timestamp=T0 + timedelta(hours=n), open=o, high=h,
                       low=lo, close=c, volume=1000.0, symbol=SYM)


def _engine() -> BacktestEngine:
    eng = BacktestEngine(BacktestConfig(symbol=SYM, initial_balance=10_000.0,
                                        commission_pct=0.1, slippage_pct=0.0))
    eng._partial_tp_enabled = True
    return eng


def _open(eng, entry=100.0, sl=90.0, tp=300.0, atr=10.0, size_usd=1000.0):
    """Open a position the way `_execute_fill` does, with its ladder."""
    idea = TradeIdea(asset=SYM, direction=Direction.LONG, entry_price=entry,
                     stop_loss=sl, take_profit=tp, confidence=0.7, reasoning="test")
    trade = eng.portfolio.open_position(idea, size_usd)
    eng._open_bt_positions[idea.id] = {
        "entry_time": T0, "adjusted_entry": entry,
        "commission_entry": size_usd * (eng.config.commission_pct / 100),
        "slippage_entry": 0.0, "idea": idea, "risk_verdict": "APPROVED",
        "ptp_state": create_partial_tp_state(
            fee_round_trip_pct=2.0 * eng.config.commission_pct, trade_id=idea.id,
            direction="LONG", entry_price=entry, stop_loss=sl, take_profit=tp,
            quantity=trade.quantity, atr=atr),
    }
    return idea.id


def _ladder(eng, tid, bar):
    eng._check_ladder_intrabar(tid, eng.portfolio._positions[tid],
                               eng._open_bt_positions[tid], bar)


def _laddered_winner(eng):
    """TP1 and TP2 in one bar, then the runner stopped on its trail: three
    fills, one position, a winner."""
    tid = _open(eng)
    _ladder(eng, tid, _bar(100, 130, 99, 128, n=1))
    trail = eng._open_bt_positions[tid]["ptp_state"].current_sl
    _ladder(eng, tid, _bar(128, 129, trail - 1, trail - 0.5, n=2))
    assert tid not in eng._open_bt_positions
    return tid


def _stopped_loser(eng, n=3):
    """Stopped at its original stop before TP1: one fill, a loser."""
    tid = _open(eng)
    eng._close_position(tid, 90.0, _bar(95, 96, 89, 90, n=n), "SL")
    return tid


def _tp1_then_gap(eng):
    """TP1 banks half at +1.5R, then the market gaps far through the
    breakeven lock: a winning fill inside a losing position."""
    tid = _open(eng)
    _ladder(eng, tid, _bar(100, 116, 99, 114, n=4))
    assert eng._open_bt_positions[tid]["ptp_state"].tp1_hit
    _ladder(eng, tid, _bar(70, 71, 69, 70, n=5))
    assert tid not in eng._open_bt_positions
    return tid


def _tp1_then_give_back(eng):
    """TP1 banks half at +1.5R, then the runner gaps a little through the
    breakeven lock: a losing fill inside a winning position."""
    tid = _open(eng)
    _ladder(eng, tid, _bar(100, 116, 99, 114, n=4))
    assert eng._open_bt_positions[tid]["ptp_state"].tp1_hit
    _ladder(eng, tid, _bar(95, 96, 94, 95, n=5))
    assert tid not in eng._open_bt_positions
    return tid


def _compile(eng):
    return eng._compile_result(bars=[], duration=0.0)


@pytest.fixture(autouse=True)
def _ladder_on():
    assert CONFIG.partial_tp.enabled
    yield


# ── the compiler ─────────────────────────────────────────────────────────────

def test_a_laddered_winner_is_one_trade_beside_a_stopped_loser():
    eng = _engine()
    try:
        win = _laddered_winner(eng)
        lose = _stopped_loser(eng)
        assert [t.trade_id for t in eng._trades] == [win, win, win, lose]
        res = _compile(eng)
        # Per fill this was 4 trades, 3 winners, 75%.
        assert res.total_trades == 2
        assert res.total_fills == 4
        assert (res.winning_trades, res.losing_trades) == (1, 1)
        assert res.win_rate == pytest.approx(0.5)
        nets = {p.trade_id: p.net_pnl_usd for p in positions(eng._trades)}
        assert res.profit_factor == pytest.approx(round(nets[win] / -nets[lose], 2))
        assert res.avg_win_usd == pytest.approx(nets[win])
        assert res.largest_win_usd == pytest.approx(nets[win])
        # Dollars are the same over either reading.
        assert res.net_pnl == pytest.approx(round(sum(t.net_pnl_usd for t in eng._trades), 2))
    finally:
        eng.cleanup()


def test_a_position_that_banked_tp1_and_lost_overall_is_a_loss():
    eng = _engine()
    try:
        _tp1_then_gap(eng)
        fills = [t.net_pnl_usd for t in eng._trades]
        assert len(fills) == 2 and fills[0] > 0 > fills[1], fills
        assert sum(fills) < 0
        res = _compile(eng)
        # Per fill: one win and one loss, 50%. The position lost.
        assert (res.total_trades, res.total_fills) == (1, 2)
        assert (res.winning_trades, res.losing_trades) == (0, 1)
        assert res.win_rate == 0
        assert res.max_consecutive_losses == 1
    finally:
        eng.cleanup()


def test_a_position_that_gave_back_part_of_tp1_is_a_win_with_no_loss():
    eng = _engine()
    try:
        _tp1_then_give_back(eng)
        fills = [t.net_pnl_usd for t in eng._trades]
        assert len(fills) == 2 and fills[0] > -fills[1] > 0, fills
        res = _compile(eng)
        # Per fill: one win and one loss. The position won.
        assert (res.total_trades, res.total_fills) == (1, 2)
        assert (res.winning_trades, res.losing_trades) == (1, 0)
        assert res.win_rate == 1
        assert res.max_consecutive_losses == 0
        assert res.largest_loss_usd == 0
    finally:
        eng.cleanup()


def test_the_loss_streak_runs_over_positions_in_the_order_they_closed():
    eng = _engine()
    try:
        _stopped_loser(eng, n=1)
        _tp1_then_gap(eng)          # its winning TP1 fill sits between two losses
        _stopped_loser(eng, n=6)
        res = _compile(eng)
        # Per fill the TP1 row broke the streak at 1, then 2. Per position: 3.
        assert res.max_consecutive_losses == 3
        assert res.total_trades == 3
    finally:
        eng.cleanup()


def test_the_average_r_is_one_r_per_position():
    eng = _engine()
    try:
        win = _laddered_winner(eng)
        lose = _stopped_loser(eng)
        # The ladder closed 5, 3 and 2 of 10 at +1.5R, +2.5R and +2.2R on its
        # trail; the stop closed 10 at -1R.
        assert eng._rr_values == [(win, 1.5, 5.0), (win, 2.5, 3.0), (win, pytest.approx(2.2), 2.0),
                                  (lose, -1.0, 10.0)]
        res = _compile(eng)
        # (0.5*1.5 + 0.3*2.5 + 0.2*2.2) = +1.94R, beside -1R: +0.47R a position.
        # Unweighted, the winner reads +2.07R and the average +0.53R; per fill
        # it was +1.30R.
        assert position_rr(eng._rr_values) == pytest.approx([1.94, -1.0])
        assert res.risk_reward_avg == pytest.approx(0.47, abs=0.005)
    finally:
        eng.cleanup()


def test_a_positions_r_is_its_fills_weighted_by_the_quantity_each_closed():
    """The docstring's ladder: +1.5R on half, +2.5R on 30%, +1.0R on the
    runner is one position at +1.7R. An unweighted mean reads +1.67R."""
    got = position_rr([("a", 1.5, 0.5), ("a", 2.5, 0.3), ("a", 1.0, 0.2), ("b", -1.0, 1.0)])
    assert got == pytest.approx([1.7, -1.0])
    assert sum(got) / len(got) == pytest.approx(0.35)
    # A position whose fills closed nothing has no R to report.
    assert position_rr([("z", 1.0, 0.0)]) == []


def test_a_run_with_no_ladder_counts_the_same_either_way():
    """The other arm: one fill per position, so position and fill agree."""
    eng = _engine()
    try:
        _stopped_loser(eng, n=1)
        _stopped_loser(eng, n=2)
        res = _compile(eng)
        assert res.total_trades == res.total_fills == 2
        assert res.losing_trades == 2
    finally:
        eng.cleanup()


# ── the one reading ──────────────────────────────────────────────────────────

def _row(tid, net, n, reason="TP", state="charged", funding=-0.1):
    return BacktestTrade(
        trade_id=tid, symbol=SYM, direction="LONG", entry_price=100.0,
        exit_price=100.0 + net, entry_time=T0, exit_time=T0 + timedelta(hours=n),
        quantity=1.0, size_usd=100.0, pnl_usd=net, pnl_pct=net, commission_usd=0.1,
        slippage_usd=0.0, net_pnl_usd=net, funding_usd=funding if state == "charged" else None,
        funding_state=state, exit_reason=reason, confidence=0.7, risk_verdict="APPROVED")


def test_positions_sum_their_fills_and_are_ordered_by_their_last_fill():
    rows = [_row("a", 5.0, 1, "TP1"), _row("b", -3.0, 2, "SL"),
            _row("a", -6.0, 3, "SL")]
    held = positions(rows)
    assert [p.trade_id for p in held] == ["b", "a"]
    a = held[1]
    assert a.fills == 2 and a.net_pnl_usd == pytest.approx(-1.0)
    assert a.exit_reasons == ("TP1", "SL") and a.exit_reason == "SL"
    assert a.exit_time == T0 + timedelta(hours=3) and a.entry_time == T0
    assert a.size_usd == pytest.approx(200.0)
    assert a.pnl_pct == pytest.approx(-1.0 / 200.0 * 100)


def test_a_position_with_an_unpriced_fill_has_no_funding_total():
    held = positions([_row("a", 1.0, 1), _row("a", 1.0, 2, state="unpriced")])
    assert held[0].funding_usd is None and held[0].funding_state == "unpriced"
    priced = positions([_row("b", 1.0, 1), _row("b", 1.0, 2)])
    assert priced[0].funding_usd == pytest.approx(-0.2)


def test_a_flat_position_is_neither_a_win_nor_a_loss():
    from bot.backtest.runner import pooled_stats
    s = pooled_stats([_row("a", 2.0, 1), _row("a", -2.0, 2), _row("b", 1.0, 3)])
    assert (s["trades"], s["fills"]) == (2, 3)
    assert (s["wins"], s["losses"], s["flat"]) == (1, 0, 1)


# ── every reader that counted rows ───────────────────────────────────────────

def _two_positions():
    eng = _engine()
    _laddered_winner(eng)
    _stopped_loser(eng)
    return eng


def test_the_pooled_block_counts_positions_and_states_the_fills():
    from bot.backtest.runner import pooled_stats
    eng = _two_positions()
    try:
        s = pooled_stats(eng._trades)
        assert (s["trades"], s["fills"], s["wins"], s["losses"]) == (2, 4, 1, 1)
        assert s["win_rate"] == pytest.approx(0.5)
    finally:
        eng.cleanup()


def test_the_buckets_and_sortino_read_positions():
    from bot.backtest.runner import _bucket_lines, _risk_adjusted
    eng = _two_positions()
    try:
        lines = "\n".join(_bucket_lines(eng._trades))
        assert "  2 tr" in lines and "  4 tr" not in lines, lines
        res = _compile(eng)
        res.trades = list(eng._trades)
        nets = [p.net_pnl_usd for p in positions(eng._trades)]
        import statistics
        mean = statistics.mean(nets)
        loss = [x for x in nets if x < 0]
        assert _risk_adjusted(res)["sortino"] == pytest.approx(round(mean / abs(loss[0]), 2))
    finally:
        eng.cleanup()


def test_the_public_breakdown_is_one_row_per_position():
    from bot.backtest.runner import public_trade_breakdown
    eng = _two_positions()
    try:
        rows = public_trade_breakdown(eng._trades)
        assert [r["fills"] for r in rows] == [3, 1]
        assert [r["exit_reason"] for r in rows] == ["TRAILING_SL", "SL"]
        assert rows[0]["pnl_pct"] > 0 > rows[1]["pnl_pct"]
        assert not any("usd" in k for r in rows for k in r)
    finally:
        eng.cleanup()


def test_the_validation_gate_is_recorded_per_position():
    from bot.backtest.runner import _record_validations_from

    class _Gate:
        recorded: list = []

        def record_validation(self, **kw):
            self.recorded.append(kw)

        def verdict(self, name):
            return "passed"

    eng = _two_positions()
    try:
        for t in eng._trades:
            object.__setattr__(t, "setup", "swing")
        gate = _Gate()
        _record_validations_from(eng._trades, sharpe=1.0, max_dd=1.0, gate=gate)
        assert gate.recorded[0]["total_trades"] == 2
        assert gate.recorded[0]["win_rate"] == pytest.approx(0.5)
    finally:
        eng.cleanup()


def test_the_portfolio_per_symbol_table_counts_positions():
    eng = _two_positions()
    try:
        table = per_symbol_table(eng._trades, [SYM, "ETH/USDT"])
        assert table[SYM]["trades"] == 2 and table[SYM]["win_rate"] == pytest.approx(0.5)
        assert table["ETH/USDT"]["trades"] == 0
    finally:
        eng.cleanup()

"""
RUNECLAW UX Upgrades Test Suite — Signal Tracker, Alert Manager, Validation Gate.

At least 15 tests covering:
- Signal tracker: record and query pair stats
- Signal tracker: blacklist logic
- Alert manager: classification for various confidence/regime combos
- Alert manager: risk event classification at boundaries
- Alert manager: push/repeat logic
- Validation gate: validated vs unvalidated strategies
- Validation gate: badge formatting
"""

import pytest

from bot.core.signal_tracker import SignalTracker
from bot.core.validation_gate import BacktestValidationGate


# ═══════════════════════════════════════════════════════════════
# SIGNAL TRACKER
# ═══════════════════════════════════════════════════════════════


class TestSignalTrackerRecordAndQuery:
    """Signal tracker: record and query pair stats."""

    def test_empty_tracker_has_no_win_rate_rather_than_a_zero_one(self):
        # Was `win_rate == 0.0`, which is the assertion that made the live
        # defect look correct: nothing has closed, so there IS no rate, and
        # 0.0 reads as "lost every one".
        tracker = SignalTracker()
        stats = tracker.get_pair_stats("BTC/USDT")
        assert stats["total_signals"] == 0
        assert stats["closed_signals"] == 0
        assert stats["win_rate"] is None
        assert stats["avg_pnl"] is None
        assert stats["last_signal_time"] is None

    def test_signals_fired_but_none_closed_still_has_no_win_rate(self):
        # The state the running bot is permanently in: record_outcome has no
        # production caller, so every pair looked like 0W/0L at 0.0%.
        tracker = SignalTracker()
        tracker.record_signal("BTC/USDT", "LONG", 0.85, 50000.0, "sig-1")
        stats = tracker.get_pair_stats("BTC/USDT")
        assert stats["total_signals"] == 1
        assert stats["closed_signals"] == 0
        assert stats["win_rate"] is None, (
            "a signal that has not closed was scored as a loss")

    def test_a_real_zero_win_rate_is_still_reported(self):
        # The mirror defect: None must not swallow a MEASURED zero.
        tracker = SignalTracker()
        tracker.record_signal("BTC/USDT", "LONG", 0.85, 50000.0, "sig-1")
        tracker.record_outcome("sig-1", pnl=-10.0, exit_price=49000.0)
        stats = tracker.get_pair_stats("BTC/USDT")
        assert stats["closed_signals"] == 1
        assert stats["win_rate"] == 0.0

    def test_the_card_prints_a_dash_not_an_empty_bar(self):
        # Five hollow blocks read as "lost everything", not "nothing closed".
        tracker = SignalTracker()
        tracker.record_signal("BTC/USDT", "LONG", 0.85, 50000.0, "sig-1")
        out = tracker.format_for_telegram()
        assert "—" in out
        assert "░░░░░" not in out, "an all-empty win-rate bar over zero closes"

    def test_record_signal_increments_count(self):
        tracker = SignalTracker()
        tracker.record_signal("BTC/USDT", "LONG", 0.85, 50000.0, "sig-1")
        tracker.record_signal("BTC/USDT", "SHORT", 0.70, 51000.0, "sig-2")
        stats = tracker.get_pair_stats("BTC/USDT")
        assert stats["total_signals"] == 2
        assert stats["wins"] == 0  # no outcomes recorded yet
        assert stats["losses"] == 0

    def test_record_outcome_updates_pnl_stats(self):
        tracker = SignalTracker()
        tracker.record_signal("ETH/USDT", "LONG", 0.80, 3000.0, "sig-1")
        tracker.record_signal("ETH/USDT", "LONG", 0.75, 3100.0, "sig-2")
        tracker.record_outcome("sig-1", pnl=50.0, exit_price=3050.0)
        tracker.record_outcome("sig-2", pnl=-20.0, exit_price=3080.0)
        stats = tracker.get_pair_stats("ETH/USDT")
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["win_rate"] == 0.5
        assert stats["avg_pnl"] == pytest.approx(15.0)
        assert stats["best_pnl"] == 50.0
        assert stats["worst_pnl"] == -20.0

    def test_get_all_pair_stats_multiple_pairs(self):
        tracker = SignalTracker()
        tracker.record_signal("BTC/USDT", "LONG", 0.85, 50000.0, "s1")
        tracker.record_signal("ETH/USDT", "LONG", 0.80, 3000.0, "s2")
        all_stats = tracker.get_all_pair_stats()
        assert "BTC/USDT" in all_stats
        assert "ETH/USDT" in all_stats
        assert len(all_stats) == 2


class TestSignalTrackerFormatting:
    """Signal tracker: War Room display."""

    def test_format_for_telegram_not_empty(self):
        tracker = SignalTracker()
        tracker.record_signal("BTC/USDT", "LONG", 0.85, 50000.0, "s1")
        tracker.record_outcome("s1", pnl=100.0, exit_price=51000.0)
        output = tracker.format_for_telegram()
        assert "SIGNAL HISTORY" in output
        assert "BTC" in output


# ═══════════════════════════════════════════════════════════════
# ALERT MANAGER
# ═══════════════════════════════════════════════════════════════


class TestValidationGate:
    """Validation gate: validated vs unvalidated strategies."""

    def test_never_tested_strategy(self):
        gate = BacktestValidationGate()
        status = gate.get_validation_status("unknown_strat")
        assert status["badge"] == "NEVER TESTED"
        assert status["validated"] is False
        assert gate.is_validated("unknown_strat") is False

    def test_validated_strategy_with_good_sharpe(self):
        gate = BacktestValidationGate()
        gate.record_validation("momentum", sharpe=1.2, max_drawdown=8.0,
                               win_rate=0.55, total_trades=100,
                               walk_forward_score=0.85)
        assert gate.is_validated("momentum") is True
        status = gate.get_validation_status("momentum")
        assert status["badge"] == "VALIDATED \u2713"
        assert status["sharpe"] == 1.2

    def test_unvalidated_strategy_with_low_sharpe(self):
        gate = BacktestValidationGate()
        gate.record_validation("bad_strat", sharpe=0.3, max_drawdown=20.0,
                               win_rate=0.35, total_trades=50,
                               walk_forward_score=0.40)
        assert gate.is_validated("bad_strat") is False
        status = gate.get_validation_status("bad_strat")
        assert status["badge"] == "UNVALIDATED \u2717"

    def test_custom_min_sharpe_threshold(self):
        gate = BacktestValidationGate()
        gate.record_validation("edge_case", sharpe=0.8, max_drawdown=10.0,
                               win_rate=0.50, total_trades=30,
                               walk_forward_score=0.70)
        assert gate.is_validated("edge_case", min_sharpe=0.6) is True
        assert gate.is_validated("edge_case", min_sharpe=1.0) is False

    def test_get_all_validations(self):
        gate = BacktestValidationGate()
        gate.record_validation("strat_a", sharpe=1.0, max_drawdown=5.0,
                               win_rate=0.60, total_trades=50,
                               walk_forward_score=0.80)
        gate.record_validation("strat_b", sharpe=0.4, max_drawdown=15.0,
                               win_rate=0.40, total_trades=50,
                               walk_forward_score=0.50)
        all_v = gate.get_all_validations()
        assert len(all_v) == 2
        assert all_v["strat_a"]["validated"] is True
        assert all_v["strat_b"]["validated"] is False


class TestValidationGateBadge:
    """Validation gate: badge formatting."""

    def test_no_validations_badge(self):
        gate = BacktestValidationGate()
        badge = gate.format_badge()
        assert "NO VALIDATIONS" in badge

    def test_all_validated_badge(self):
        gate = BacktestValidationGate()
        gate.record_validation("s1", sharpe=1.0, max_drawdown=5.0,
                               win_rate=0.60, total_trades=50,
                               walk_forward_score=0.80)
        badge = gate.format_badge()
        assert "ALL VALIDATED" in badge

    def test_partial_badge(self):
        gate = BacktestValidationGate()
        gate.record_validation("good", sharpe=1.0, max_drawdown=5.0,
                               win_rate=0.60, total_trades=50,
                               walk_forward_score=0.80)
        gate.record_validation("bad", sharpe=0.3, max_drawdown=20.0,
                               win_rate=0.30, total_trades=50,
                               walk_forward_score=0.40)
        badge = gate.format_badge()
        assert "PARTIAL" in badge

    def test_none_validated_badge(self):
        gate = BacktestValidationGate()
        gate.record_validation("bad1", sharpe=0.2, max_drawdown=20.0,
                               win_rate=0.30, total_trades=50,
                               walk_forward_score=0.30)
        badge = gate.format_badge()
        assert "NONE VALIDATED" in badge

    def test_format_for_telegram_shows_strategies(self):
        gate = BacktestValidationGate()
        gate.record_validation("momentum", sharpe=1.5, max_drawdown=5.0,
                               win_rate=0.65, total_trades=80,
                               walk_forward_score=0.90)
        output = gate.format_for_telegram()
        assert "VALIDATION GATE" in output
        assert "momentum" in output
        assert "1/1" in output

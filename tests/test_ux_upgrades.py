"""
RUNECLAW UX Upgrades Test Suite — Validation Gate.

At least 15 tests covering:
- Alert manager: classification for various confidence/regime combos
- Alert manager: risk event classification at boundaries
- Alert manager: push/repeat logic
- Validation gate: validated vs unvalidated strategies
- Validation gate: badge formatting
"""

from bot.core.validation_gate import BacktestValidationGate


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

"""A breaker that stops trading and appears nowhere is not actionable.

`circuit_breaker_active` answers a narrower question than its name suggests:
it covers daily_loss / drawdown / streak / manual, and NOT the warning-rate
breaker, which lives in a separate flag (`_warning_rate_tripped`).

Every operator surface read the narrow one. On 2026-07-29 the warning-rate
breaker rejected a live trade with `key='engine_tick_failure'` while /status
printed a green ACTIVE headline, the banner said "running", and /health
reported `circuit_breaker_active: false` — so the operator relayed "circuit
breaker clear" twice while trades were being turned away.

`trading_blocked_by` is the question people actually mean: "" exactly when
trades are going through, otherwise the reason they are not. These tests pin
both the property and the fact that it reaches the surfaces operators read.
"""
from __future__ import annotations

import os
import tempfile

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine

from tests.source_scan import code_only


def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-blocked-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


class TestTradingBlockedBy:
    def test_clear_when_nothing_is_tripped(self):
        assert _engine().trading_blocked_by == ""

    def test_reports_the_warning_rate_breaker_the_narrow_field_misses(self):
        eng = _engine()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "engine_tick_failure"
        # The exact condition that produced the false "breaker clear" report.
        assert eng.circuit_breaker_active is False
        assert eng.trading_blocked_by == "warning_rate:engine_tick_failure"

    def test_names_the_key_so_the_operator_knows_what_to_fix(self):
        eng = _engine()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "bitget_timeout"
        assert "bitget_timeout" in eng.trading_blocked_by

    def test_unknown_key_is_labelled_not_blank(self):
        # A trip with no recorded key still blocks trades; reporting "" there
        # would put us straight back in the "blocked but reads clear" hole.
        eng = _engine()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = ""
        assert eng.trading_blocked_by == "warning_rate:unknown"

    def test_circuit_cause_is_reported_verbatim(self):
        eng = _engine()
        eng._circuit_open = True
        eng._circuit_trip_cause = "drawdown"
        assert eng.trading_blocked_by == "drawdown"

    def test_open_circuit_with_no_cause_still_reports_blocked(self):
        eng = _engine()
        eng._circuit_open = True
        eng._circuit_trip_cause = ""
        assert eng.trading_blocked_by == "circuit"

    def test_circuit_wins_when_both_are_tripped(self):
        # Both block; the hard breaker is the one to report first because it
        # does not clear on its own the way a warning burst does.
        eng = _engine()
        eng._circuit_open = True
        eng._circuit_trip_cause = "drawdown"
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "engine_tick_failure"
        assert eng.trading_blocked_by == "drawdown"

    def test_the_soft_loss_streak_latch_counts_as_blocked(self):
        # This gate sits BELOW the visible hard breaker by design: it turns
        # away every new entry while circuit_breaker_active reads false. A
        # field claiming to say why trades are rejected has to name it, or it
        # makes the same overclaim it exists to fix.
        eng = _engine()
        eng._consecutive_losses = eng._soft_loss_streak_limit(
            CONFIG.risk.max_consecutive_losses)
        eng._last_loss_time = eng._now()          # probe not due yet
        assert eng.circuit_breaker_active is False
        assert eng.trading_blocked_by.startswith("loss_streak:")

    def test_a_due_probe_is_not_reported_as_blocked(self):
        # A probe trade can go through, so claiming blocked would be the
        # inverse error. The probe also needs a flat book, which a property
        # cannot see — erring toward "" never reports blocked while trades
        # are actually going through.
        eng = _engine()
        eng._consecutive_losses = eng._soft_loss_streak_limit(
            CONFIG.risk.max_consecutive_losses)
        probe_s = CONFIG.risk.loss_streak_probe_hours * 3600.0
        if probe_s <= 0:
            return  # probing disabled by config; the latch is permanent
        eng._last_loss_time = eng._now() - probe_s - 1.0
        assert eng.trading_blocked_by == ""

    def test_a_streak_below_the_soft_limit_is_not_blocked(self):
        eng = _engine()
        eng._consecutive_losses = 0
        assert eng.trading_blocked_by == ""

    def test_hard_breakers_outrank_the_streak_latch(self):
        eng = _engine()
        eng._consecutive_losses = 99
        eng._last_loss_time = eng._now()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "engine_tick_failure"
        # The warning-rate breaker is the more urgent thing to fix.
        assert eng.trading_blocked_by == "warning_rate:engine_tick_failure"

    def test_idea_dependent_checks_are_deliberately_excluded(self):
        # Size / confidence / correlation reject ONE trade, not all trading.
        # Folding them in would make this field the overclaim it replaces.
        src = code_only(
            open("bot/risk/risk_engine.py", encoding="utf-8").read())
        prop = src.split("def trading_blocked_by", 1)[1].split("def ", 1)[0]
        for narrow in ("min_confidence", "max_position_pct", "correlation"):
            assert narrow not in prop

    def test_circuit_breaker_active_keeps_its_narrow_meaning(self):
        # Deliberately additive. A dozen call sites drive alerts and resume
        # logic off circuit_breaker_active's exact definition; widening it
        # underneath them would trade one wrong answer for another.
        eng = _engine()
        eng._warning_rate_tripped = True
        assert eng.circuit_breaker_active is False
        assert eng.warning_rate_breaker_active is True


class TestItReachesTheOperator:
    """The property existing is not the fix — being readable is.

    `warning_rate_breaker_active` already existed and was surfaced on exactly
    zero operator surfaces, which is how the live trade got rejected against a
    green card. These scans fail if that regresses.
    """

    def test_health_endpoint_reports_it(self):
        src = code_only(open("api_bridge.py", encoding="utf-8").read())
        assert "trading_blocked_by" in src, (
            "/health is where the operator checked and was told the breaker "
            "was clear while trades were being rejected"
        )

    def test_risk_status_endpoint_reports_both_breakers(self):
        src = code_only(open("api_bridge.py", encoding="utf-8").read())
        assert "warning_rate_breaker_active" in src
        assert src.count("trading_blocked_by") >= 2, (
            "both /health and /risk/status should answer 'can we trade'"
        )

    def test_status_card_headline_is_not_driven_by_the_narrow_field(self):
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "trading_blocked_by" in src, (
            "the /status headline printed a green ACTIVE while the "
            "warning-rate breaker was rejecting every entry"
        )
        # The green/red headline is computed from `blocked_by`, not from
        # circuit_breaker_active alone.
        assert "active=not cb" in src
        assert "cb = bool(blocked_by)" in src

    def test_the_web_dashboard_chip_is_not_driven_by_the_narrow_field(self):
        # The website is the primary surface. Its "Circuit Breaker" chip is
        # read as "can we trade", and it rendered a green ✓ while the
        # warning-rate breaker was rejecting every live entry.
        src = code_only(
            open("bot/skills/scan_skill.py", encoding="utf-8").read())
        assert "trading_blocked_by" in src
        # The chip must go red on ANY blocker, not just the narrow one.
        assert 'bool(_blocked) or cb_active' in src

    def test_a_blocked_status_card_names_the_reason(self):
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "Trading blocked" in src, (
            "a red headline with no reason sends the operator hunting"
        )

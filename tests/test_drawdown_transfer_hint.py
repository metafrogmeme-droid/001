"""A withdrawal reads as a drawdown, and the trip card could not say so.

The live drawdown breaker gates on a high-water mark:
100 * (peak - equity) / peak. That arithmetic cannot tell a withdrawal from
a loss. On 2026-07-30 the operator moved funds out and live equity fell 41%
below the session peak with recorded trade PnL of roughly nothing — only a
restart (which re-seeds the in-memory peak) kept the breaker from tripping
on a phantom loss. Without one, the trip card would have read "max drawdown
breached" and the operator would be diagnosing a 41% loss that never
happened.

The design line, stated as tests:

- The breaker STILL TRIPS. Guessing "probably a transfer" to keep trading
  would gut a safety control — a bug or a compromised balance feed looks
  exactly like a withdrawal. Fail closed; explain well.
- The hint fires only when recorded trade losses explain less than half the
  drop. A real losing streak carries its own PnL trail and keeps the plain
  message — a hint on every trip would teach the operator to dismiss it.
- The remedy named is the one that already exists: confirm it was your
  transfer, /resume re-seeds the peak at current equity.
"""
from __future__ import annotations

import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-ddhint-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _hint(peak: float, equity: float, live_daily_pnl: float) -> str:
    eng = _engine()
    eng._live_equity_peak = peak
    eng._live_daily_pnl = live_daily_pnl
    return eng._drawdown_transfer_hint(equity)


class TestWhenTheHintFires:
    def test_the_live_incident_shape_gets_the_hint(self):
        # Peak $883, equity $522 after a transfer, ~no recorded trade PnL.
        out = _hint(883.18, 522.44, live_daily_pnl=-0.50)
        assert "transfer" in out
        assert "/resume" in out
        # Both numbers the operator needs to check the arithmetic themselves.
        assert "360.74" in out          # the drop from peak
        assert "0.50" in out            # what trading actually lost

    def test_a_real_losing_streak_keeps_the_plain_message(self):
        # Equity fell $360 and recorded losses ARE $360 — that is a real
        # drawdown, and a transfer hint here would teach dismissal.
        assert _hint(883.18, 522.44, live_daily_pnl=-360.74) == ""

    def test_losses_explaining_half_the_drop_stay_plain(self):
        assert _hint(1000.0, 800.0, live_daily_pnl=-100.0) == ""

    def test_losses_explaining_less_than_half_get_the_hint(self):
        assert "transfer" in _hint(1000.0, 800.0, live_daily_pnl=-99.0)

    def test_it_warns_the_other_direction_too(self):
        # "If you did NOT move funds, treat this as real" — the hint must
        # never read as permission to dismiss.
        out = _hint(883.18, 522.44, live_daily_pnl=0.0)
        assert "treat this as real" in out


class TestWhenItStaysSilent:
    def test_no_live_equity_no_hint(self):
        eng = _engine()
        eng._live_equity_peak = 883.18
        assert eng._drawdown_transfer_hint(None) == ""
        assert eng._drawdown_transfer_hint(0.0) == ""

    def test_no_seeded_peak_no_hint(self):
        assert _hint(0.0, 522.44, live_daily_pnl=0.0) == ""

    def test_equity_at_or_above_peak_no_hint(self):
        assert _hint(500.0, 522.44, live_daily_pnl=0.0) == ""


class TestTheBreakerStillTrips:
    def test_the_hint_is_a_suffix_never_a_suppression(self):
        # The trip call site appends the hint to the reason — pin that the
        # trip itself is unconditional on the hint's verdict. Source-level:
        # the hint helper must not touch _circuit_open or call the trip.
        import inspect
        src = inspect.getsource(RiskEngine._drawdown_transfer_hint)
        assert "_circuit_open" not in src
        assert "_trip_circuit_breaker" not in src

    def test_trip_reason_carries_the_hint_on_the_incident_shape(self):
        eng = _engine()
        eng._live_equity_peak = 883.18
        eng._live_daily_pnl = -0.50
        reason = ("max drawdown breached"
                  + eng._drawdown_transfer_hint(522.44))
        eng._trip_circuit_breaker(reason, cause="drawdown")
        assert eng.circuit_breaker_active is True
        assert eng.circuit_trip_cause == "drawdown"

    def test_helper_never_raises(self):
        eng = _engine()
        eng._live_equity_peak = "garbage"          # type: ignore[assignment]
        assert eng._drawdown_transfer_hint(522.44) == ""

"""The cleared card says HOW the breaker cleared; the halted card prints the
engine's own transition; a second trip is never deduped against the first;
and a manual reset re-seeds the PERSON-level peak.

Pasted from the live bot on 2026-10-05, ninety-one seconds apart:

    ⛔ ENGINE HALTED · Previous State: IDLE · Halted At: 12:21:22 UTC
    ✅ CIRCUIT BREAKER CLEARED · Risk limits are back within tolerance.
       Trading operations have resumed. · Cleared At: 12:22:53 UTC

with no trip card between them. The cause was a drawdown trip, whose only
exit is a manual /reset: nothing was measured as back within tolerance, the
peak was discarded. "Halted At" was the monitor's notice time and "Previous
State" its last sample. And the trip card's dedup key was the constant
``cb_tripped``, so a trip inside five minutes of the previous trip card was
swallowed as a repeat while the halt was announced with no reason on it.

One dimension over: `PersonPeakStore.reseed` was written for the manual-reset
path and had no caller, so an engine with a person identity re-tripped on the
very next evaluation after /reset -- the "still halted after reset" loop the
engine-level re-seed exists to end.
"""
from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor
from bot.formatters import breaker_card
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.risk.venue_aggregate import VenueReading, aggregate
from bot.utils.models import AgentState, Direction, StateTransition, TradeIdea

SIM_AT = 1_700_000_000.0            # 2023-11-14 22:13:20 UTC
SIM_STAMP = "22:13:20 UTC"


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=0.8,
                     reasoning="t", risk_reward_ratio=3.0)


def _risk(tmp_path, name="risk.json"):
    pt = PortfolioTracker(initial_balance=1000.0,
                          state_file=str(tmp_path / f"pf-{name}"))
    return RiskEngine(pt, state_file=str(tmp_path / name))


def _engine(risk, **extra):
    base = dict(risk=risk,
                live_executor=types.SimpleNamespace(open_positions=[]),
                portfolio=types.SimpleNamespace(open_positions=[]),
                state="IDLE", _pending_ideas={}, _last_scan_signals=[])
    base.update(extra)
    return types.SimpleNamespace(**base)


def _trip_on_drawdown(risk):
    """Peak $10,000, equity $9,141: 8.59% against the 7.0% live limit."""
    risk._live_equity_peak = 10000.0
    risk.evaluate(_idea(), live_equity=9141.0)
    assert risk.circuit_breaker_active and risk.circuit_trip_cause == "drawdown"


def _line(body, label):
    for ln in body.split("\n"):
        if ln.startswith(f"- {label}:"):
            return ln
    raise AssertionError(f"no {label!r} line in:\n{body}")


# ── the cleared card ──────────────────────────────────────────────────────

class TestTheClearedCard:

    def test_a_manual_reset_of_a_drawdown_trip_says_nothing_was_measured_back(self, live, tmp_path):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        m = ProactiveMonitor(_engine(risk))
        assert m._check_circuit_breaker() == []
        _trip_on_drawdown(risk)
        assert m._check_circuit_breaker(), "the trip card"
        risk._sim_now = SIM_AT + 91
        risk.reset_circuit_breaker()
        (card,) = m._check_circuit_breaker()
        assert card.severity == "INFO" and "Cleared" in card.title
        assert "CIRCUIT BREAKER CLEARED" in card.body
        assert _line(card.body, "Cleared by") == (
            "- Cleared by: <code>the operator (/reset or /resume) at 22:14:51 UTC</code>")
        assert _line(card.body, "It had tripped on").startswith(
            f"- It had tripped on: <code>drawdown</code>, at <code>{SIM_STAMP}</code>")
        assert "Nothing was measured as back within tolerance" in card.body
        assert "re-measured from the next equity read" in card.body
        # The two sentences the old card asserted for every clear.
        assert "back within tolerance." not in card.body.replace(
            "Nothing was measured as back within tolerance", "")
        assert "resumed" not in card.body
        assert m._check_circuit_breaker() == [], "unchanged: nothing more"

    def test_an_open_gate_after_the_clear_says_entries_are_open(self, live, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        m._check_circuit_breaker()
        risk.reset_circuit_breaker()
        assert risk.trading_blocked_by == "", "fixture: the gate must be open here"
        (card,) = m._check_circuit_breaker()
        assert "New entries are <b>open</b>." in card.body
        assert "resumes scanning on its next cycle" in card.body

    def test_a_gate_still_refusing_after_the_clear_is_named_not_called_resumed(self, live, tmp_path, monkeypatch):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        m._check_circuit_breaker()
        risk.reset_circuit_breaker()
        monkeypatch.setattr(RiskEngine, "trading_blocked_by",
                            property(lambda self: "warning_rate:macro"))
        (card,) = m._check_circuit_breaker()
        assert "still <b>refused</b>" in card.body and "warning-rate breaker" in card.body
        assert "New entries are <b>open</b>." not in card.body
        assert "resumes scanning" not in card.body

    def test_a_gate_that_cannot_be_read_is_said_not_rounded(self, live, tmp_path, monkeypatch):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        m._check_circuit_breaker()
        risk.reset_circuit_breaker()

        def _boom(self):
            raise RuntimeError("apiKey=SECRETVALUE")
        monkeypatch.setattr(RiskEngine, "trading_blocked_by", property(_boom))
        (card,) = m._check_circuit_breaker()
        assert "Could not read the entry gate after the clear" in card.body
        assert "SECRETVALUE" not in card.body
        assert "resumes scanning" not in card.body

    def test_the_daily_rollover_auto_reset_says_the_day_rolled_over(self, live, tmp_path):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        m = ProactiveMonitor(_engine(risk))
        risk._trip_circuit_breaker("daily loss limit breached", cause="daily_loss")
        risk._circuit_trip_day = "2023-11-13"
        assert m._check_circuit_breaker(), "the trip card"
        # The next evaluation on the following UTC day clears a daily-loss
        # trip on its own (default ON); nobody pressed anything.
        risk.evaluate(_idea(), live_equity=1000.0,
                      as_of=datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))
        assert not risk.circuit_breaker_active
        assert risk.last_breaker_clear["how"] == "daily_rollover"
        (card,) = m._check_circuit_breaker()
        assert "the daily-loss auto-reset at UTC day rollover" in card.body
        assert "daily-loss budget is fresh" in card.body
        assert "operator" not in card.body
        assert "Nothing was measured" not in card.body, "that sentence is the manual reset's"

    def test_a_streak_cool_off_says_the_streak_is_zero(self, live, tmp_path, monkeypatch):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        m = ProactiveMonitor(_engine(risk))
        risk._trip_circuit_breaker("3 consecutive losses", cause="streak")
        risk._last_loss_time = SIM_AT
        assert m._check_circuit_breaker()
        import dataclasses

        from bot.config import CONFIG
        monkeypatch.setattr("bot.risk.risk_engine.CONFIG", dataclasses.replace(
            CONFIG, risk=dataclasses.replace(CONFIG.risk, streak_breaker_autoreset_hours=1.0)))
        risk._sim_now = SIM_AT + 2 * 3600
        risk.evaluate(_idea(), live_equity=1000.0)
        assert not risk.circuit_breaker_active
        assert risk.last_breaker_clear["how"] == "streak_cooloff"
        (card,) = m._check_circuit_breaker()
        assert "loss-streak cool-off" in card.body and "streak counter is zero" in card.body
        assert "operator" not in card.body

    def test_a_clear_this_process_did_not_record_is_said_to_be_missing(self):
        """The monitor test's stand-in: a `risk` with only the flag. The old
        card asserted tolerance over it; this one says the record is missing
        and that it cannot read the gate."""
        class FakeRisk:
            circuit_breaker_active = False
        risk = FakeRisk()
        m = ProactiveMonitor(_engine(risk))
        risk.circuit_breaker_active = True
        m._check_circuit_breaker()
        risk.circuit_breaker_active = False
        (card,) = m._check_circuit_breaker()
        assert card.severity == "INFO" and "Cleared" in card.title
        assert "not on record (this process did not close it" in card.body
        assert "How it cleared is not on record" in card.body
        assert "Could not read the entry gate" in card.body
        assert "back within tolerance." not in card.body.replace(
            "what is back within tolerance", "")
        assert card.dedup_key == "cb_cleared"

    def test_the_pure_card_never_prints_a_mock_as_a_figure(self):
        from unittest.mock import MagicMock
        title, body = breaker_card.cleared_card(clear=MagicMock(), gate=MagicMock(),
                                                noticed_at=SIM_AT)
        assert "MagicMock" not in body and "Cleared" in title
        assert "Could not read the entry gate" in body


# ── one dedup key per trip ────────────────────────────────────────────────

class TestASecondTripIsNotARepeatOfTheFirst:

    def test_two_trips_inside_the_cooldown_both_send(self, live, tmp_path):
        risk = _risk(tmp_path)
        risk._sim_now = SIM_AT
        m = ProactiveMonitor(_engine(risk))
        m.enable_chat("123")
        m._check_circuit_breaker()
        _trip_on_drawdown(risk)
        (first,) = m._check_circuit_breaker()
        assert m._should_send(first)
        m._mark_sent(first)
        risk._sim_now = SIM_AT + 60
        risk.reset_circuit_breaker()
        (cleared,) = m._check_circuit_breaker()
        risk._sim_now = SIM_AT + 61
        _trip_on_drawdown(risk)
        (second,) = m._check_circuit_breaker()
        assert first.dedup_key == f"cb_tripped:{int(SIM_AT)}"
        assert second.dedup_key == f"cb_tripped:{int(SIM_AT) + 61}"
        assert m._should_send(second), "the second trip was deduped against the first"
        # The other arm: the SAME trip announced twice is still one card.
        m._mark_sent(second)
        assert not m._should_send(second)
        assert cleared.dedup_key == f"cb_cleared:{int(SIM_AT) + 60}"

    def test_a_trip_with_no_time_on_record_keeps_the_constant_key(self):
        assert breaker_card.trip_dedup_key(None) == "cb_tripped"
        assert breaker_card.trip_dedup_key(float("nan")) == "cb_tripped"
        assert breaker_card.trip_dedup_key(SIM_AT) == f"cb_tripped:{int(SIM_AT)}"


# ── the halted card ───────────────────────────────────────────────────────

class TestTheHaltedCard:

    def test_it_prints_the_engines_own_transition(self, tmp_path):
        risk = _risk(tmp_path)
        hist = [
            StateTransition(from_state=AgentState.IDLE, to_state=AgentState.SCANNING),
            StateTransition(from_state=AgentState.SCANNING, to_state=AgentState.HALTED,
                            reason="circuit breaker active",
                            timestamp=datetime.fromtimestamp(SIM_AT, tz=timezone.utc)),
        ]
        m = ProactiveMonitor(_engine(risk, state="HALTED", state_history=hist))
        m._last_state = "IDLE"            # the monitor's last SAMPLE
        (card,) = m._check_state_changes()
        assert card.severity == "CRITICAL" and card.alert_type == "STATE_CHANGE"
        assert _line(card.body, "Previous state") == "- Previous state: <code>SCANNING</code>"
        assert _line(card.body, "Halted at") == f"- Halted at: <code>{SIM_STAMP}</code>"
        assert _line(card.body, "Reason") == "- Reason: <code>circuit breaker active</code>"
        assert "IDLE" not in card.body, "the monitor's sample was printed as the engine's"
        assert card.dedup_key == f"state_halted:{int(SIM_AT)}"

    def test_the_latest_halt_is_the_one_printed(self, tmp_path):
        risk = _risk(tmp_path)
        hist = [
            StateTransition(from_state=AgentState.SCANNING, to_state=AgentState.HALTED,
                            reason="old", timestamp=datetime.fromtimestamp(SIM_AT - 3600, tz=timezone.utc)),
            StateTransition(from_state=AgentState.HALTED, to_state=AgentState.IDLE,
                            reason="circuit breaker cleared"),
            StateTransition(from_state=AgentState.MONITORING, to_state=AgentState.HALTED,
                            reason="circuit breaker active",
                            timestamp=datetime.fromtimestamp(SIM_AT, tz=timezone.utc)),
        ]
        m = ProactiveMonitor(_engine(risk, state="HALTED", state_history=hist))
        m._last_state = "IDLE"
        (card,) = m._check_state_changes()
        assert "MONITORING" in card.body and "<code>old</code>" not in card.body

    def test_without_a_record_the_monitors_sample_is_labelled_as_such(self, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk, state="HALTED"))   # no state_history
        m._last_state = "IDLE"
        (card,) = m._check_state_changes()
        assert card.severity == "CRITICAL"
        assert _line(card.body, "Last state seen by the monitor") == (
            "- Last state seen by the monitor: <code>IDLE</code>")
        assert "Noticed at" in card.body and "not on record" in card.body
        assert "Previous state:" not in card.body
        assert card.dedup_key == "state_halted"

    def test_a_history_that_is_not_a_list_is_no_record_not_a_crash(self, tmp_path):
        risk = _risk(tmp_path)
        m = ProactiveMonitor(_engine(risk, state="HALTED", state_history="garbage"))
        m._last_state = "IDLE"
        (card,) = m._check_state_changes()
        assert "Last state seen by the monitor" in card.body


# ── the person-level peak is re-seeded by a manual reset ──────────────────

def _r(venue, eq):
    return VenueReading(venue=venue, open_positions=0, equity_usd=eq, daily_pnl_usd=0.0)


@pytest.fixture
def person_store(tmp_path, monkeypatch):
    import bot.risk.person_peak as pp
    from bot.risk.person_peak import PersonPeakStore
    store = PersonPeakStore(path=str(tmp_path / "peaks.json"))
    monkeypatch.setattr(pp, "_STORE", store)
    return store


class TestAManualResetReseedsThePersonPeak:

    def _person_engine(self, tmp_path, equities):
        risk = _risk(tmp_path)
        risk.set_person_totals_fn(lambda: aggregate([_r("bitget", equities[0]),
                                                     _r("bybit", equities[1])]))
        risk.set_person_identity("alice")
        return risk

    def test_the_reset_sticks_instead_of_retripping_on_the_next_evaluation(self, tmp_path, person_store):
        eq = [500.0, 500.0]
        risk = self._person_engine(tmp_path, eq)
        assert risk._person_drawdown_pct()[0] == pytest.approx(0.0)   # seeds at 1000
        assert person_store.peak("alice") == 1000.0
        # The person is down 50%: bybit collapsed. The trip is correct.
        eq[1] = 0.0 + 0.0 or 1e-9  # keep the reading complete
        eq[0], eq[1] = 250.0, 250.0
        risk.evaluate(_idea(), live_equity=500.0)
        assert risk.circuit_breaker_active and risk.circuit_trip_cause == "drawdown"

        risk.reset_circuit_breaker()
        assert risk.last_breaker_clear["person_peak"] == "reseeded"
        assert person_store.peak("alice") is None, "the person-level peak was kept"
        # The next evaluation at the same equity is the NEW peak, not a 50%
        # drawdown: the reset sticks.
        risk.evaluate(_idea(), live_equity=500.0)
        assert not risk.circuit_breaker_active, "re-tripped on the peak the reset kept"
        assert person_store.peak("alice") == pytest.approx(500.0)

    def test_an_engine_with_no_person_identity_touches_no_peak(self, tmp_path, person_store):
        person_store.observe("alice", 1000.0)
        risk = _risk(tmp_path)
        risk.emergency_halt("test")
        risk.reset_circuit_breaker()
        assert risk.last_breaker_clear["person_peak"] == ""
        assert person_store.peak("alice") == 1000.0, "somebody else's peak was forgotten"

    def test_an_unreadable_store_keeps_its_peaks_and_the_card_says_so(self, tmp_path, monkeypatch, live):
        """A store that will not read cannot forget one peak without writing
        the file over everybody's, so it keeps them all and the reset says
        so. The store here was never readable: `_read` caches a successful
        load, so a file broken AFTER the first read is still "read" in
        memory and a re-seed of it succeeds."""
        import bot.risk.person_peak as pp
        from bot.risk.person_peak import PersonPeakStore
        (tmp_path / "peaks.json").write_text("{ not json")
        monkeypatch.setattr(pp, "_STORE", PersonPeakStore(path=str(tmp_path / "peaks.json")))
        risk = self._person_engine(tmp_path, [500.0, 500.0])
        risk.emergency_halt("test")
        m = ProactiveMonitor(_engine(risk))
        m._check_circuit_breaker()
        risk.reset_circuit_breaker()
        assert not risk.circuit_breaker_active, "the reset itself must still clear"
        assert risk.last_breaker_clear["person_peak"] == "unreadable:StoreUnreadable"
        assert (tmp_path / "peaks.json").read_text() == "{ not json", "a failed read was written over"
        (card,) = m._check_circuit_breaker()
        assert "could <b>not</b> be re-seeded" in card.body
        assert "StoreUnreadable" in card.body
        assert "can re-trip on the next evaluation" in card.body

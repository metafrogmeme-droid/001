"""An unreadable risk-state file halted trading anonymously, then erased itself.

Both fail-closed branches of `_load_state` did this:

    except (json.JSONDecodeError, ValueError, KeyError):
        self._circuit_open = True
        self._circuit_breaker_trips += 1
        audit(...)

Opening the breaker is right. An unknown risk state is not a safe one. Two
things about HOW it opened were not.

THE HALT WAS ANONYMOUS. `_circuit_trip_cause` and `_circuit_trip_day` stayed
"". `trading_blocked_by` returns `self._circuit_trip_cause or "circuit"`, so
every operator surface — /status, /health, the war room, the proactive
CIRCUIT_BREAKER alert — printed the bare word "circuit". Restarting into a
halt, there was no way to tell "you lost 5% today" from "the disk hiccuped
while reading one file". The proactive alert's own fallback was the word
"unknown", which is at least honest and equally unactionable.

It also meant no auto-reset predicate could match. Both compare against a
cause that was never recorded:

    _should_autoreset_daily_breaker(...)   cause == "daily_loss"
    the streak self-recovery branch        cause == "streak"

so a transient EINTR at startup latched trading off until a human noticed.

THE EVIDENCE WAS DESTROYED. `_save_state` overwrites unconditionally, so the
next state change replaced the unreadable file — consecutive_losses, the live
equity peak, the trip count and the trip history with it. Same shape as the
credential store (#1048): an unreadable file is not an empty one.

    THE REMEDY IS NOT THE SAME, AND THE DIFFERENCE IS THE POINT.

The credential store REFUSES to save while the load failed. This one must
not: the open breaker itself has to reach disk, or the next restart resumes
trading into the same unknown state. So the damaged file is moved aside
first, and the save then proceeds into a clean path.

WHAT DELIBERATELY DOES NOT CHANGE: the halt is still manual-reset.
`state_unreadable` matches no auto-reset branch either, and that is the
correct posture — the streak and drawdown history are exactly what was just
lost, so there is nothing left to judge a safe resume against. This makes the
halt dated and legible; it does not make it shorter.
"""
from __future__ import annotations

import json
import os

import pytest

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _engine(tmp_path, contents=None, name="risk_state.json"):
    p = tmp_path / name
    if contents is not None:
        p.write_text(contents, encoding="utf-8")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=str(p))


class TestTheHaltStillHappens:
    """Fail-closed is the contract. Break this and the fix is a regression."""

    @pytest.mark.parametrize("body", ["{not json", "[1,2,3]", '{"a": '])
    def test_a_corrupt_file_halts_trading(self, tmp_path, body):
        e = _engine(tmp_path, body)
        assert e._circuit_open is True
        assert e.circuit_breaker_active is True

    def test_an_unreadable_file_halts_trading(self, tmp_path, monkeypatch):
        (tmp_path / "risk_state.json").write_text('{"circuit_open": false}')
        real = open

        def boom(path, *a, **kw):
            if str(path).endswith("risk_state.json"):
                raise OSError(13, "Permission denied")
            return real(path, *a, **kw)

        monkeypatch.setattr("builtins.open", boom)
        e = _engine(tmp_path)
        monkeypatch.undo()
        assert e._circuit_open is True

    def test_a_missing_file_is_a_fresh_start_not_a_halt(self, tmp_path):
        assert _engine(tmp_path)._circuit_open is False

    def test_an_empty_file_is_a_fresh_start_not_a_halt(self, tmp_path):
        # Absent and empty are the same statement. Only UNREADABLE differs.
        assert _engine(tmp_path, "")._circuit_open is False
        assert _engine(tmp_path, "   \n")._circuit_open is False


class TestTheHaltSaysWhy:
    def test_the_cause_is_recorded(self, tmp_path):
        e = _engine(tmp_path, "{not json")
        assert e._circuit_trip_cause == "state_unreadable"

    def test_the_day_is_recorded(self, tmp_path):
        from datetime import datetime

        from bot.compat import UTC
        e = _engine(tmp_path, "{not json")
        assert e._circuit_trip_day == datetime.now(UTC).strftime("%Y-%m-%d")

    def test_the_operator_surface_no_longer_says_just_circuit(self, tmp_path):
        # `trading_blocked_by` is what /status, /health, the war room and
        # trade_gate all read. It used to fall through to "circuit".
        e = _engine(tmp_path, "{not json")
        assert e.trading_blocked_by == "state_unreadable"
        assert e.trading_blocked_by != "circuit"

    def test_the_proactive_alert_no_longer_says_unknown(self, tmp_path):
        # proactive_monitor: `getattr(risk, 'circuit_trip_cause', '') or 'unknown'`
        e = _engine(tmp_path, "{not json")
        assert (e.circuit_trip_cause or "unknown") == "state_unreadable"

    def test_the_trip_is_still_counted(self, tmp_path):
        assert _engine(tmp_path, "{not json")._circuit_breaker_trips >= 1


class TestThePostureIsUnchanged:
    """The halt is dated and legible. It is not shorter."""

    def test_it_does_not_match_the_daily_loss_autoreset(self, tmp_path):
        e = _engine(tmp_path, "{not json")
        assert RiskEngine._should_autoreset_daily_breaker(
            e._circuit_open, e._circuit_trip_cause, e._circuit_trip_day,
            "2099-01-01", True, 0, CONFIG.risk.max_consecutive_losses) is False, (
            "an infrastructure halt must not clear itself at day rollover — "
            "the streak and drawdown history it would be judged against are "
            "exactly what was lost"
        )

    def test_it_does_not_match_the_streak_autorecovery(self, tmp_path):
        # That branch is `cause == "streak"`, checked here as the literal it is.
        assert _engine(tmp_path, "{not json")._circuit_trip_cause != "streak"

    def test_a_real_daily_loss_trip_still_auto_resets(self, tmp_path):
        # The path this change must not disturb.
        e = _engine(tmp_path)
        e._trip_circuit_breaker("daily loss limit breached", cause="daily_loss")
        assert RiskEngine._should_autoreset_daily_breaker(
            e._circuit_open, e._circuit_trip_cause, e._circuit_trip_day,
            "2099-01-01", True, 0, CONFIG.risk.max_consecutive_losses) is True


class TestTheEvidenceSurvives:
    def test_the_damaged_file_is_preserved(self, tmp_path):
        _engine(tmp_path, '{corrupt BUT REAL "consecutive_losses": 4')
        kept = tmp_path / "risk_state.json.corrupt"
        assert kept.exists(), "the only record of the risk state was erased"
        assert "BUT REAL" in kept.read_text()

    def test_the_next_save_no_longer_overwrites_it(self, tmp_path):
        e = _engine(tmp_path, '{corrupt "consecutive_losses": 4')
        e._save_state_individual()          # the write that used to destroy it
        kept = tmp_path / "risk_state.json.corrupt"
        assert "consecutive_losses" in kept.read_text()

    def test_the_save_still_happens_unlike_the_credential_store(self, tmp_path):
        """The open breaker MUST reach disk or a restart resumes trading."""
        e = _engine(tmp_path, "{not json")
        e._save_state_individual()
        written = json.loads((tmp_path / "risk_state.json").read_text())
        assert written["circuit_open"] is True
        assert written["circuit_trip_cause"] == "state_unreadable"

    def test_a_second_failure_keeps_the_first_rescue(self, tmp_path):
        # Overwriting it would lose the good copy on the second restart.
        (tmp_path / "risk_state.json.corrupt").write_text("FIRST RESCUE")
        _engine(tmp_path, "{bad again")
        assert "FIRST RESCUE" in (tmp_path / "risk_state.json.corrupt").read_text()

    def test_a_healthy_load_leaves_no_corrupt_sidecar(self, tmp_path):
        _engine(tmp_path, json.dumps({"circuit_open": False}))
        assert not os.path.exists(str(tmp_path / "risk_state.json.corrupt"))

    def test_a_rescue_failure_does_not_prevent_the_halt(self, tmp_path, monkeypatch):
        # If the file cannot even be moved, halting still matters more.
        def no_replace(*a, **kw):
            raise OSError(30, "Read-only file system")

        (tmp_path / "risk_state.json").write_text("{not json")
        monkeypatch.setattr(os, "replace", no_replace)
        e = _engine(tmp_path)
        monkeypatch.undo()
        assert e._circuit_open is True
        assert e._circuit_trip_cause == "state_unreadable"

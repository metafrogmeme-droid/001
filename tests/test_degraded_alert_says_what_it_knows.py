"""The alert that wakes you named the dying phase and hedged about the money.

Live, 2026-09-02, 03:2x UTC. This is what an operator was sent:

    🚨 ENGINE LOOP DEGRADED
    The main loop has failed 3 times in a row.
    Cause: phase analyze exceeded its 300s cap.
    Scanning and position monitoring may be impaired — open positions could
    be unmonitored.

Two facts were already in the process and neither travelled:

  * WHETHER MONEY WAS EXPOSED. `_backstop_position_monitor` records, every
    tick, whether the SL/TP monitor actually ran. "Could be" is a hedge the
    process does not have to make.
  * WHAT TO CHANGE. `_forecast_analyze_capacity` had already worked out that
    85 signals at 4.1s each needs ~349s against a 300s cap and that 12 would
    not be analysed — and it names the setting to move. It rendered on
    /status, a screen you have to go and open, from an alert that woke you.

Both now render in the alert, in the SAME vocabulary as /status, /positions
and the runbook. That is deliberate: an operator should not have to translate
between the screen that woke them and the screen they open next.

The red herring in these scenarios is the loop reporting itself alive and
recently ticked. A live loop is exactly the state in which stops go unwatched
— analyze blows its cap, the tick unwinds before its position check, and the
engine keeps ticking on schedule.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bot.core.proactive_monitor import ProactiveMonitor
from bot.formatters.rich_cards import analyze_budget_line

ROOT = Path(__file__).resolve().parents[1]

#: The real forecast from the live incident.
CAPACITY = {"of": 85, "per_signal_s": 4.1, "needed_s": 348.5, "cap_s": 300.0,
            "fits": 73, "shortfall": 12, "measured_from": 75}
PHASE = {"phase": "analyze", "cap_s": 300.0, "count": 28}

#: The sentence this whole file exists to remove.
OLD_HEDGE = "could be <b>unmonitored</b>"


def _alert(watch, capacity=CAPACITY, phase=PHASE, has_reader=True, raises=False):
    """Drive the real alert builder with a planted engine state."""
    m = object.__new__(ProactiveMonitor)
    m._last_tick_degraded = False

    def _watch():
        if raises:
            raise RuntimeError("engine busy")
        return watch

    eng = NS(_tick_consecutive_failures=3, _last_phase_timeout=phase,
             _analyze_capacity=capacity)
    if has_reader:
        eng.position_watch = _watch
    m.engine = eng
    out = m._check_tick_failures()
    assert out, "no alert produced at 3 consecutive failures"
    return out[0].body


# ── Whether money is exposed ─────────────────────────────────────────────

def test_unwatched_stops_are_stated_not_hedged():
    body = _alert({"outcome": "incomplete", "unwatched_streak": 4, "age_s": 300})
    assert "DID NOT RUN" in body
    assert "unwatched" in body
    assert "\U0001f534" in body, "money at risk gets the red accent"
    assert "×4" in body, "a run of unwatched ticks is the incident; show it"
    assert OLD_HEDGE not in body


def test_watched_stops_say_so_and_do_not_cry_wolf():
    """The loop is still broken — CRITICAL is right — but telling someone
    their stops may be off when the back-stop watched them is a false alarm
    on the one number they cannot check quickly."""
    body = _alert({"outcome": "backstop", "unwatched_streak": 0, "age_s": 8})
    assert "back-stop watched the stops" in body
    assert "unwatched" not in body
    assert "\U0001f534 <b>SL/TP" not in body, "no red verdict on watched stops"
    assert OLD_HEDGE not in body


def test_no_reading_is_stated_as_no_reading_not_as_could_be():
    body = _alert(None)
    assert "not recorded" in body
    assert "⚪" in body, "unknown gets the muted accent"
    assert OLD_HEDGE not in body


@pytest.mark.parametrize("kwargs", [
    {"has_reader": False},   # engine predates position_watch (mid-rollout)
    {"raises": True},        # the reader itself fails
])
def test_the_alert_still_fires_when_the_verdict_cannot_be_read(kwargs):
    """An alert that dies while assembling itself is worse than a vague one.
    The verdict is an ADDITION to a warning that must still go out."""
    body = _alert(None, **kwargs)
    assert "ENGINE LOOP DEGRADED" in body
    assert "failed <b>3</b> times" in body


def test_the_alert_is_never_silent_about_the_stops():
    """Every verdict renders here, the healthy one included.

    /status may omit the healthy line to stay short. This alert may not: it
    fires on three consecutive tick failures, and a tick can fail AFTER its
    position check completes — the check runs last, so failures one and two
    being analyze timeouts does not mean the third was. Silence on the stops,
    in the message that woke someone at 3am, reads as "nothing to say about
    your money", which is the exact inference this whole file removes.

    This is what `verbose=True` at the call site buys. A mutation flipping it
    to False survived the rest of this suite.
    """
    body = _alert({"outcome": "tick", "unwatched_streak": 0, "age_s": 9})
    assert "SL/TP monitor" in body, "the alert went silent about the stops"
    assert "ran this tick" in body
    assert "unwatched" not in body


@pytest.mark.parametrize("outcome", ["tick", "backstop", "incomplete", "error"])
def test_every_verdict_reaches_the_alert(outcome):
    body = _alert({"outcome": outcome, "unwatched_streak": 1, "age_s": 12})
    assert "SL/TP monitor" in body, f"{outcome} rendered nothing in the alert"


# ── What to change ───────────────────────────────────────────────────────

def test_the_alert_carries_the_measured_remedy():
    body = _alert({"outcome": "incomplete", "unwatched_streak": 1, "age_s": 10})
    assert "Analyze budget short" in body
    assert "85" in body and "300s cap" in body
    assert "SCAN_ANALYSIS_CONCURRENCY" in body, (
        "the alert names the phase that died but not the setting to move")


def test_no_budget_line_when_the_work_fits_or_was_never_measured():
    """Omitted, never guessed. A fabricated remedy is worse than none: it
    sends someone to change a setting that was not the problem."""
    for cap in (dict(CAPACITY, shortfall=0), None, {}, {"shortfall": 12}):
        assert "Analyze budget short" not in _alert(None, capacity=cap)


def test_budget_renderer_omits_rather_than_half_renders():
    assert analyze_budget_line(None) == ""
    assert analyze_budget_line(dict(CAPACITY, shortfall=0)) == ""
    assert analyze_budget_line({"shortfall": 5}) == "", "malformed is not a measurement"
    assert "85" in analyze_budget_line(CAPACITY)


# ── Scanning vs positions are separate claims ────────────────────────────

def test_scanning_is_still_reported_impaired_whatever_the_stops_did():
    """A failing tick did not finish looking for entries. That stays true
    when the back-stop watched the stops, and the old wording bundled the two
    into one sentence so neither could be stated precisely."""
    body = _alert({"outcome": "backstop", "unwatched_streak": 0, "age_s": 8})
    assert "Scanning is impaired" in body


def test_the_red_herring_does_not_reach_the_verdict():
    """Planted: a loop reporting itself alive, recently ticked, with a
    green-looking phase count. None of it says the stops were watched."""
    body = _alert({"outcome": "incomplete", "unwatched_streak": 2, "age_s": 45},
                  phase={"phase": "analyze", "cap_s": 300.0, "count": 1})
    assert "DID NOT RUN" in body


# ── One vocabulary, one source ───────────────────────────────────────────

def test_status_uses_the_shared_renderer_and_keeps_no_private_copy():
    """Behaviour is covered by the renderer's own tests above; this pins the
    WIRING — that /status reaches the shared function and that the inline
    copy it was extracted from is gone. Two renderings of one fact drift,
    and the drifted one is always the surface nobody re-read."""
    src = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
    assert "analyze_budget_line(" in code, "/status no longer calls the renderer"
    assert "Analyze budget short" not in code, (
        "/status has grown a private copy of the budget line again")


def test_the_alert_and_status_speak_the_same_words():
    mon = (ROOT / "bot" / "core" / "proactive_monitor.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in mon.split("\n") if not ln.lstrip().startswith("#"))
    assert "position_watch_line(" in code and "analyze_budget_line(" in code
    assert "Analyze budget short" not in code, "the alert has its own copy now"

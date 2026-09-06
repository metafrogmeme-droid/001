"""The breaker alert guessed a subsystem the engine had already measured.

Live, 2026-09-02:

    🟠 WARNING-RATE BREAKER TRIPPED
    Repeated infrastructure warnings have suppressed new entries.
    - Trigger: engine_tick_failure
    Usually transient (exchange API / WS). It clears as the error rate falls.
    👉 /status — review engine health

Two things were wrong with that, and the second is worse.

"Usually transient (exchange API / WS)" names a subsystem from nothing. The
engine audits the real exception one line above where it feeds this breaker,
then dropped it. CLAUDE.md already records what reading a heuristic as a
verdict cost here: 37 timed-out ticks pointed at the wrong subsystem.

And the operator's own /status carried the answer on a different line —
`Tick phase timed out: analyze (exceeded its 300s, ×5)`. `_phase` re-raises on
a fatal cap, the tick's `except Exception` counts it as `engine_tick_failure`,
and >5 in an hour trips the breaker. One event, two surfaces, never joined:
/status said "analyze", the alert said "exchange".

`/status` itself said nothing about tick FAILURES at all —
`_tick_consecutive_failures` was stored and rendered nowhere — so the surface
the alert sent the reader to could not answer either.
"""
from __future__ import annotations

import re
from pathlib import Path

from bot.formatters.rich_cards import tick_error_line
from tests.source_scan import code_only

REPO = Path(__file__).resolve().parents[1]

REC = {"type": "TimeoutError", "consecutive": 6, "last_phase_timeout": "analyze"}


# --------------------------------------------------------------------------
# It reports what was measured, and nothing else.
# --------------------------------------------------------------------------

def test_it_names_the_exception_the_engine_recorded():
    out = tick_error_line(REC)
    assert "TimeoutError" in out
    assert "6" in out


def test_it_never_names_a_subsystem_it_did_not_measure():
    """The whole defect: a guessed cause on the alert that suppresses entries."""
    for rec in (REC, {"type": "ConnectionError", "consecutive": 1}, {}):
        out = tick_error_line(rec).lower()
        for guess in ("exchange", "usually transient", "ws", "websocket", "api"):
            assert guess not in out, f"{guess!r} is a guess, not a measurement"


def test_a_tick_that_has_not_failed_says_nothing():
    """Omit. An empty verdict here would read as a measured all-clear."""
    assert tick_error_line(None) == ""
    assert tick_error_line("nonsense") == ""
    assert tick_error_line(123) == ""


def test_a_failure_with_no_detail_is_its_own_outcome():
    """Third outcome: the tick failed and we could not say what raised.

    Silence here would be indistinguishable from a healthy tick, which is the
    defect one level down from the one this file exists for.
    """
    out = tick_error_line({})
    assert out, "a recorded failure with no type must not render as nothing"
    assert "no error detail" in out.lower()
    assert "Engine tick error" in out, "it must say where to look instead"


# --------------------------------------------------------------------------
# The phase is a separate fact, not an attribution.
# --------------------------------------------------------------------------

def test_the_phase_is_offered_without_claiming_it_caused_this():
    """`_last_phase_timeout` is the last phase TIMEOUT and may be stale.

    Rendering it as "the phase this error happened in" would manufacture the
    same false cause the guessed subsystem did, one step more subtly.
    """
    out = tick_error_line(REC)
    assert "analyze" in out
    assert "may or may not be related" in out
    assert not re.search(r"error in (the )?phase", out, re.I)


def test_no_phase_recorded_means_no_phase_clause():
    out = tick_error_line({"type": "ValueError", "consecutive": 2})
    assert "phase" not in out.lower()


def test_a_zero_count_still_reads_as_a_failure():
    """It was recorded because a tick FAILED; "0 in a row" contradicts that."""
    out = tick_error_line({"type": "OSError", "consecutive": 0})
    assert "<b>1</b>" in out
    assert ">0<" not in out


def test_a_malformed_count_does_not_crash_the_card():
    assert "OSError" in tick_error_line({"type": "OSError", "consecutive": "many"})


# --------------------------------------------------------------------------
# The secret rule. This text reaches Telegram.
# --------------------------------------------------------------------------

def test_only_the_exception_class_is_recorded():
    """`str(exc)` carries URLs, query strings and occasionally credentials.

    /readyz answers with a fixed reason vocabulary for exactly this reason.
    The engine must store `type(exc).__name__` and never the message.
    """
    src = code_only((REPO / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    # Anchor on the DICT assignment that records it, not the bare name: the
    # first `_last_tick_error` in the file is the `= None` reset on the clean
    # tick path, and a window taken from there proves nothing about what the
    # failure path stores.
    m = re.search(r"_last_tick_error\s*=\s*\{", src)
    assert m, "the engine no longer records the tick error"
    window = src[m.start():m.start() + 700]
    assert "type ( exc ) . __name__" in window or "type(exc).__name__" in window
    assert "str ( exc )" not in window and "str(exc)" not in window, \
        "the exception MESSAGE must never be stored — it reaches Telegram"
    assert "{ exc }" not in window and "{exc}" not in window


def test_the_html_is_escaped():
    """A class name is tame, but the renderer must not trust its input."""
    out = tick_error_line({"type": "<script>x</script>", "consecutive": 1})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# --------------------------------------------------------------------------
# Wiring: both surfaces, and the guess is gone.
# --------------------------------------------------------------------------

def _src(rel: str) -> str:
    if rel == "bot/skills/telegram_handler.py":
        # Every file the handler class is made of: /status lives in the
        # start-here mixin since the handler split.
        from tests.source_scan import handler_sources
        return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
    return code_only((REPO / rel).read_text(encoding="utf-8"))


def test_the_breaker_alert_no_longer_guesses():
    raw = (REPO / "bot" / "core" / "proactive_monitor.py").read_text(encoding="utf-8")
    src = _src("bot/core/proactive_monitor.py")
    assert "Usually transient (exchange API / WS)" not in src, \
        "the guessed subsystem is back in the alert body"
    assert "tick_error_line(" in src, "the alert must carry the measured error"
    assert "Usually transient" in raw or True   # prose may discuss it; code may not


def test_status_can_answer_what_the_alert_points_at():
    """The alert says "👉 /status"; /status must render the tick error."""
    assert "tick_error_line(" in _src("bot/formatters/rich_cards.py")
    assert "tick_error=" in _src("bot/skills/telegram_handler.py"), \
        "/status must be passed the record, or the alert points at silence"


def test_a_clean_tick_clears_the_record():
    """Stale state is a claim about now. A tick that succeeded must reset it."""
    src = _src("bot/core/engine.py")
    i = src.find("_tick_consecutive_failures = 0")
    assert i > 0
    assert "_last_tick_error = None" in src[i:i + 400], \
        "a successful tick must clear the stored error, not keep showing it"

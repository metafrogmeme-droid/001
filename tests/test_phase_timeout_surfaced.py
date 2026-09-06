"""The cause of a failing tick must travel with the symptom.

Live incident, 2026-07-28: the operator received

    🚨 ENGINE LOOP DEGRADED
    The main loop has failed 3 times in a row.

…and nothing else. The process already KNEW which phase had blown its cap —
_phase audits it — but that line went only to system_log, a surface the
operator had no practical way to read. So the alert named the symptom, told
them to check /status, and /status repeated the same symptom. The cause sat
in memory the whole time.

This is the same failure the web app had twice today: a diagnosis that
cannot be reached is not a diagnosis. The contract now:

  - a phase timeout is REMEMBERED on the engine, with its cap and a repeat
    count;
  - the degraded alert carries it, so the page names the cause;
  - /status carries it, because that is where the alert sends the operator;
  - and none of it is invented — with no timeout recorded, every surface
    stays silent rather than guessing.
"""

import re
from pathlib import Path
from types import SimpleNamespace

from bot.core.proactive_monitor import ProactiveMonitor
from bot.formatters.rich_cards import render_status_card


def _card(**kw):
    base = dict(
        mode="LIVE", active=True, equity=495.69, open_positions=0,
        daily_pnl=0.2, drawdown=0.0, max_drawdown=5.0, market_bias="Normal",
    )
    base.update(kw)
    return render_status_card(**base)


def _pm(fails, phase_timeout=None):
    pm = ProactiveMonitor(SimpleNamespace(
        _tick_consecutive_failures=fails,
        _last_phase_timeout=phase_timeout))
    pm._last_tick_degraded = False
    return pm


# ── the alert ─────────────────────────────────────────────────────────────

def test_degraded_alert_names_the_phase():
    alerts = _pm(3, {"phase": "scan", "cap_s": 300.0, "at": 1.0, "count": 2})._check_tick_failures()
    assert len(alerts) == 1
    body = alerts[0].body
    assert "scan" in body, "the page must name the phase, not just the count"
    assert "300s cap" in body
    # the original operator guidance survives
    assert "failed <b>3</b> times in a row" in body
    assert "/positions" in body


def test_degraded_alert_without_a_recorded_phase_says_nothing_extra():
    """No timeout recorded → no cause line. Never a guess."""
    body = _pm(3, None)._check_tick_failures()[0].body
    assert "Cause:" not in body
    assert "failed <b>3</b> times in a row" in body


def test_a_malformed_record_is_ignored_rather_than_rendered():
    for bad in ({}, {"phase": ""}, {"cap_s": 300.0}, "not-a-dict", 42):
        body = _pm(3, bad)._check_tick_failures()[0].body
        assert "Cause:" not in body, f"malformed record leaked: {bad!r}"


def test_the_alert_still_fires_only_on_the_edge():
    pm = _pm(3, {"phase": "scan", "cap_s": 300.0, "at": 1.0, "count": 1})
    assert len(pm._check_tick_failures()) == 1
    assert pm._check_tick_failures() == [], "one degradation, one page"


# ── /status ───────────────────────────────────────────────────────────────

def test_status_card_carries_the_cause():
    out = _card(tick_age_s=120.0, tick_stalled=False, next_tick_in_s=45.0,
                phase_timeout={"phase": "scan", "cap_s": 300.0, "at": 1.0, "count": 3})
    assert "scan" in out
    assert "300s" in out
    assert "3" in out, "a repeat count distinguishes one blip from a pattern"


def test_status_card_omits_the_line_when_nothing_timed_out():
    out = _card(tick_age_s=12.0, tick_stalled=False)
    assert "phase" not in out.lower(), "silence, not a guess"
    out2 = _card(tick_age_s=12.0, tick_stalled=False, phase_timeout={})
    assert "phase" not in out2.lower()


def test_status_card_drops_the_count_when_it_is_a_single_occurrence():
    out = _card(tick_age_s=12.0, tick_stalled=False,
                phase_timeout={"phase": "analyze", "cap_s": 300.0, "at": 1.0, "count": 1})
    assert "analyze" in out
    assert "×1" not in out and "×1" not in out, "a count of one is noise"


# ── the engine records it ─────────────────────────────────────────────────

def test_engine_remembers_the_phase_and_counts_repeats():
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    block = src[src.index("async def _phase"):src.index("async def _with_maintenance_cap")]
    assert "_last_phase_timeout" in block, "the phase must be remembered, not only logged"
    assert '"cap_s": cap' in block and '"count"' in block
    # and the failure audit carries it
    assert '"phase": (getattr(self, "_last_phase_timeout", None)' in src, \
        "the tick-failure audit must record which phase, not just the count"


def test_status_handler_passes_the_engine_record_through():
    from tests.source_scan import handler_sources
    # Every file the handler class is made of: /status lives in the
    # start-here mixin since the handler split.
    src = "\n".join(p.read_text(encoding="utf-8") for p in handler_sources())
    assert re.search(r'phase_timeout=getattr\(self\.engine,\s*"_last_phase_timeout",\s*None\)', src)


def test_the_bilingual_labels_exist():
    from bot.utils.i18n import _STRINGS
    for key in ("lbl_last_phase_timeout", "val_exceeded_cap"):
        assert key in _STRINGS, f"{key} missing"
        assert _STRINGS[key].get("en") and _STRINGS[key].get("zh"), \
            f"{key} must be bilingual"

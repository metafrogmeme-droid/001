"""A slow scan must not take the tick loop down with it.

Live incident, 2026-07-28. /status read:

    - Last tick: 350s ago (next due in 256s)

350 + 256 = 606s: a tick failing at the 300s phase cap, then the failure
backoff at its own 300s ceiling. Every tick was failing, three failures
tripped the warning-rate breaker, and the bot stopped taking entries — over
exchange latency. Equity was flat, zero positions were open, and the LLM was
healthy; the only thing wrong was that scanning had become slow.

The priority was backwards. Scanning is how new entries are FOUND;
monitoring open positions is how money already at risk is PROTECTED.
Positions are checked EARLIER in the same tick, so skipping a scan cycle
costs only the chance of a new entry — strictly conservative — while failing
the whole tick costs the monitoring too.

So a scan timeout no longer fails the tick. The hard part is that it must not
become invisible either: a bot that looks healthy while silently finding
nothing is the quiet degradation this codebase exists to refuse. The timeout
is recorded, named in /status, and paged on once it becomes a pattern.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.proactive_monitor import ProactiveMonitor


@pytest.fixture(autouse=True)
def _restore_flags():
    fatal = getattr(CONFIG.monitoring, "tick_scan_timeout_fatal", False)
    cap = getattr(CONFIG.monitoring, "tick_phase_timeout_sec", 300.0)
    yield
    object.__setattr__(CONFIG.monitoring, "tick_scan_timeout_fatal", fatal)
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", cap)


class _Engine:
    """Only what _phase touches."""
    def __init__(self):
        self._last_phase_timeout = None


def _phase(engine, coro, what, fatal):
    from bot.core.engine import RuneClawEngine
    return RuneClawEngine._phase(engine, coro, what, fatal)


async def _hang():
    await asyncio.sleep(30)


# ── the engine behaviour ──────────────────────────────────────────────────

def test_a_non_fatal_phase_returns_none_instead_of_raising():
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.05)
    eng = _Engine()
    out = asyncio.run(_phase(eng, _hang(), "scan", False))
    assert out is None, "a non-fatal timeout yields no signals, not an exception"


def test_a_non_fatal_timeout_is_still_RECORDED():
    """Degraded is not the same as hidden."""
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.05)
    eng = _Engine()
    asyncio.run(_phase(eng, _hang(), "scan", False))
    rec = eng._last_phase_timeout
    assert rec and rec["phase"] == "scan"
    assert rec["cap_s"] == pytest.approx(0.05)
    assert rec["count"] == 1


def test_repeats_accumulate_so_a_pattern_is_distinguishable():
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.05)
    eng = _Engine()
    for _ in range(3):
        asyncio.run(_phase(eng, _hang(), "scan", False))
    assert eng._last_phase_timeout["count"] == 3


def test_a_fatal_phase_still_raises():
    """positions/analyze keep the old contract — a parked call there is a
    real outage, and the opposite remedy applies."""
    object.__setattr__(CONFIG.monitoring, "tick_phase_timeout_sec", 0.05)
    eng = _Engine()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_phase(eng, _hang(), "positions", True))
    assert eng._last_phase_timeout["phase"] == "positions"


def test_the_scan_call_site_is_non_fatal_and_reversible():
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    assert 'self.scanner.scan(), "scan",' in src
    assert 'fatal=bool(getattr(CONFIG.monitoring, "tick_scan_timeout_fatal", False))' in src, \
        "the behaviour must be reversible by config, not baked in"
    # every OTHER phase stays fatal (no explicit fatal= argument)
    for other in ('self._check_open_positions(), "positions"',
                  'self._check_open_positions(), "positions (halted)"'):
        assert other in src


def test_the_flag_exists_and_defaults_to_non_fatal():
    assert hasattr(CONFIG.monitoring, "tick_scan_timeout_fatal")
    src = Path("bot/config.py").read_text(encoding="utf-8")
    assert '_env_bool("TICK_SCAN_TIMEOUT_FATAL", False)' in src, \
        "default non-fatal: protecting open money outranks finding new entries"


# ── it must never be silent ───────────────────────────────────────────────

def _pm(pt):
    pm = ProactiveMonitor(SimpleNamespace(_last_phase_timeout=pt))
    pm._scan_timeout_alerted_at = None
    return pm


def test_one_slow_scan_says_nothing():
    assert _pm({"phase": "scan", "cap_s": 300.0, "count": 1})._check_scan_timeouts() == []
    assert _pm({"phase": "scan", "cap_s": 300.0, "count": 2})._check_scan_timeouts() == []


def test_a_pattern_pages_once_and_says_what_still_works():
    pm = _pm({"phase": "scan", "cap_s": 300.0, "count": 3})
    alerts = pm._check_scan_timeouts()
    assert len(alerts) == 1
    body = alerts[0].body
    assert "300s" in body and "<b>3</b>" in body
    # The operator must know precisely what is and is not degraded.
    assert "still monitored" in body
    assert "tick loop is intact" in body
    assert alerts[0].severity == "WARNING", "not CRITICAL — the loop is alive"
    assert pm._check_scan_timeouts() == [], "one condition, one page"


def test_a_worsening_streak_re_pages():
    pm = _pm({"phase": "scan", "cap_s": 300.0, "count": 3})
    assert len(pm._check_scan_timeouts()) == 1
    pm.engine._last_phase_timeout = {"phase": "scan", "cap_s": 300.0, "count": 4}
    assert len(pm._check_scan_timeouts()) == 1, "a growing streak is new information"


def test_a_different_phase_or_none_clears_the_arming():
    pm = _pm({"phase": "scan", "cap_s": 300.0, "count": 3})
    assert len(pm._check_scan_timeouts()) == 1
    pm.engine._last_phase_timeout = {"phase": "positions", "cap_s": 300.0, "count": 1}
    assert pm._check_scan_timeouts() == []
    assert pm._scan_timeout_alerted_at is None, "re-arms for the next scan streak"
    pm.engine._last_phase_timeout = None
    assert pm._check_scan_timeouts() == []


def test_a_malformed_record_never_pages():
    for bad in ({}, "nope", 7, {"phase": "scan"}, {"phase": "scan", "count": "x"}):
        assert _pm(bad)._check_scan_timeouts() == [], f"leaked on {bad!r}"

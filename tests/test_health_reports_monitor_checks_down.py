"""/health, the bridge /health and the scan payload say when monitor checks are down.

The monitor isolates each of its checks and counts the ones that raise, and
/status prints the count. This is the corollary CLAUDE.md insists on: which
OTHER surface makes the same claim? Three did. The Telegram /health card said
"SYSTEM HEALTH: HEALTHY" over a monitor whose checks were down; the bridge's
/health -- the surface the operator checked during the 2026-07-29 incident --
carried nothing about it; and the scan payload's `features`, which is what the
website's engine panel renders, could not say "alerting degraded" because
nothing told it. All three read the same accounting now, and all three keep
the omit rule: absent when no monitor is attached, an empty list when the
monitor answered "all ran".
"""
from __future__ import annotations

import asyncio
import io
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.skills.scan_skill import _build_features_block

ROOT = Path(__file__).resolve().parent.parent


def _mon(down: dict):
    return SimpleNamespace(check_failures=lambda: dict(down))


DOWN = {"state_changes": {"count": 4, "since_s": 120.0, "last_error": "KeyError: x"},
        "ws_health": {"count": 3, "since_s": 90.0, "last_error": "RuntimeError: y"}}


# ── the scan payload (what the website renders) ─────────────────────────

def test_features_carry_the_down_checks_sorted():
    f = _build_features_block(SimpleNamespace(_proactive_monitor=_mon(DOWN)))
    assert f["monitor_checks_down"] == ["state_changes", "ws_health"]


def test_features_carry_an_empty_list_when_the_monitor_answered_all_ran():
    f = _build_features_block(SimpleNamespace(_proactive_monitor=_mon({})))
    assert f["monitor_checks_down"] == []


def test_features_omit_the_key_when_no_monitor_is_attached():
    assert "monitor_checks_down" not in _build_features_block(SimpleNamespace())
    assert "monitor_checks_down" not in _build_features_block(None)


def test_features_omit_the_key_when_the_monitor_cannot_answer():
    def boom():
        raise RuntimeError("no")
    f = _build_features_block(SimpleNamespace(_proactive_monitor=SimpleNamespace(check_failures=boom)))
    assert "monitor_checks_down" not in f


# ── the bridge /health ──────────────────────────────────────────────────

@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "0" * 64)
    return pytest.importorskip("api_bridge")


def _health(mod):
    # A private loop, neither the thread's current one (which an earlier
    # test in the full suite can leave unset, so get_event_loop() raised
    # RuntimeError here) nor asyncio.run (which unsets the current loop
    # on exit and broke the neighbouring health tests that still read it).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(mod.health())
    finally:
        loop.close()


def test_bridge_health_names_the_down_checks(bridge, monkeypatch):
    eng = SimpleNamespace(risk=SimpleNamespace(circuit_breaker_active=False),
                          portfolio=SimpleNamespace(open_positions=[]),
                          _proactive_monitor=_mon(DOWN))
    monkeypatch.setattr(bridge, "engine", eng, raising=False)
    assert _health(bridge)["monitor_checks_down"] == ["state_changes", "ws_health"]


def test_bridge_health_reports_an_empty_list_as_a_real_reading(bridge, monkeypatch):
    eng = SimpleNamespace(risk=SimpleNamespace(circuit_breaker_active=False),
                          portfolio=SimpleNamespace(open_positions=[]),
                          _proactive_monitor=_mon({}))
    monkeypatch.setattr(bridge, "engine", eng, raising=False)
    assert _health(bridge)["monitor_checks_down"] == []


def test_bridge_health_omits_the_field_without_a_monitor(bridge, monkeypatch):
    eng = SimpleNamespace(risk=SimpleNamespace(circuit_breaker_active=False),
                          portfolio=SimpleNamespace(open_positions=[]))
    monkeypatch.setattr(bridge, "engine", eng, raising=False)
    assert "monitor_checks_down" not in _health(bridge)


def test_bridge_health_omits_the_field_when_the_monitor_raises(bridge, monkeypatch):
    def boom():
        raise RuntimeError("no")
    eng = SimpleNamespace(risk=SimpleNamespace(circuit_breaker_active=False),
                          portfolio=SimpleNamespace(open_positions=[]),
                          _proactive_monitor=SimpleNamespace(check_failures=boom))
    monkeypatch.setattr(bridge, "engine", eng, raising=False)
    body = _health(bridge)
    assert "monitor_checks_down" not in body
    assert body["status"] == "ok", "an unreadable monitor is omitted, not a 500"


# ── the Telegram /health card ───────────────────────────────────────────

def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return " ".join(tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type != tokenize.COMMENT)


def test_health_command_prints_the_line_and_says_unread():
    """Wiring: /health must read the monitor through the same seam /status
    uses. Source-scanned because the card itself needs a live engine."""
    from tests.source_scan import handler_sources
    # Every file the handler class is made of: /health is leaving for the
    # start-here mixin, and a scan of one file reads the move as the line
    # vanishing from the card.
    code = "\n".join(_code_only(p) for p in handler_sources())
    i = code.find("async def _cmd_health")
    body = code[i:code.find("async def ", i + 10)]
    assert "monitor_checks_line (" in body
    assert "check_failures ( )" in body
    assert "fmt_monitor_checks_unread" in body, "an unreadable state is said, not omitted"

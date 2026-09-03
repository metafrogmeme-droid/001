"""One broken check must not silence the proactive monitor.

`_check_all` was thirty bare calls in a row and `run()` caught the exception
at debug level AFTER stamping the heartbeat. So one check that raised every
tick -- a KeyError, an attribute the engine no longer had -- ended the pass at
that line: the alerts built before it were discarded with the exception, the
checks after it never ran, the engine's reciprocal liveness watch saw a fresh
heartbeat and stayed quiet, and nothing was logged above debug. A channel
that looks calm because nothing can reach it, on the loop that delivers the
circuit-breaker, halt, SL/TP-proximity and unprotected-position alerts.

Reproduced before the fix: a circuit-breaker alert collected, then a later
check raised; the tick sent nothing and the heartbeat read fresh. These tests
are that reproduction, inverted, plus the accounting that makes the outage
visible: every check is isolated (omit, per CLAUDE.md's table), failures are
counted and logged at WARNING, a check down for CHECK_DOWN_ALERT_AFTER ticks
pages the admin once and its recovery pages once, and /status names them.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from types import SimpleNamespace

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import Alert, ProactiveMonitor
from bot.formatters.rich_cards import monitor_checks_line
from bot.utils.i18n import t


def _quiet_monitor() -> ProactiveMonitor:
    """A real monitor whose every check has nothing to say."""
    m = ProactiveMonitor(SimpleNamespace())
    for n in dir(ProactiveMonitor):
        if n.startswith("_check_") and n != "_check_all":
            setattr(m, n, lambda: [])
    m._check_reports_push = lambda: None
    return m


def _cb_alert() -> list:
    return [Alert(alert_type="CIRCUIT_BREAKER", severity="CRITICAL",
                  title="cb", body="engine halted", dedup_key="cb")]


def _boom() -> list:
    raise KeyError("current_drawdown")


def _monitor_alerts(alerts) -> list:
    return [a for a in alerts if a.alert_type.startswith("MONITOR_CHECK")]


# ── isolation ────────────────────────────────────────────────────────────

def test_an_alert_built_before_the_broken_check_survives_it():
    m = _quiet_monitor()
    m._check_circuit_breaker = _cb_alert        # runs first
    m._check_state_changes = _boom              # raises, eighteenth
    out = m._check_all()
    assert [a.alert_type for a in out] == ["CIRCUIT_BREAKER"]


def test_the_checks_after_the_broken_one_still_run():
    m = _quiet_monitor()
    m._check_state_changes = _boom
    ran: list = []
    m._check_time_stops = lambda: (ran.append("time_stops"), [])[1]
    m._check_arb_tracker = lambda: (ran.append("arb_tracker"), [])[1]
    m._check_all()
    assert ran == ["time_stops", "arb_tracker"]


def test_the_tick_still_sends_what_the_working_checks_found():
    """The reproduction, inverted: run() itself, one check raising."""
    m = _quiet_monitor()
    m._check_circuit_breaker = _cb_alert
    m._check_state_changes = _boom
    m._enabled_chats = {"1"}
    m.CHECK_INTERVAL = 0
    sent: list = []

    async def send_fn(chat_id, msg, *a):
        sent.append(msg)

    async def one_tick():
        async def stop_soon():
            await asyncio.sleep(0.05)
            m._running = False
        await asyncio.gather(m.run(send_fn), stop_soon())

    asyncio.run(one_tick())
    assert any("engine halted" in s for s in sent), sent


def test_a_raising_probe_does_not_stop_the_checks():
    m = _quiet_monitor()
    m._check_circuit_breaker = _cb_alert
    m._enabled_chats = {"1"}
    m.CHECK_INTERVAL = 0

    async def bad_probe():
        raise RuntimeError("probe exploded")
    m._probe_public_gateway = bad_probe
    sent: list = []

    async def send_fn(chat_id, msg, *a):
        sent.append(msg)

    async def one_tick():
        async def stop_soon():
            await asyncio.sleep(0.05)
            m._running = False
        await asyncio.gather(m.run(send_fn), stop_soon())

    asyncio.run(one_tick())
    assert any("engine halted" in s for s in sent), sent


# ── accounting ───────────────────────────────────────────────────────────

def test_the_failure_is_counted_and_named():
    m = _quiet_monitor()
    m._check_state_changes = _boom
    m._check_all()
    m._check_all()
    f = m.check_failures()
    assert set(f) == {"state_changes"}
    assert f["state_changes"]["count"] == 2
    assert f["state_changes"]["last_error"].startswith("KeyError")
    assert f["state_changes"]["since_s"] >= 0.0


def test_a_check_that_recovers_leaves_the_record():
    m = _quiet_monitor()
    m._check_state_changes = _boom
    m._check_all()
    m._check_state_changes = lambda: []
    m._check_all()
    assert m.check_failures() == {}


def test_the_first_failure_is_logged_at_warning_not_debug(monkeypatch, caplog):
    log = logging.getLogger("test.monitor.isolation")
    log.propagate = True
    monkeypatch.setattr(pm, "system_log", log)
    m = _quiet_monitor()
    m._check_state_changes = _boom
    with caplog.at_level(logging.WARNING, logger=log.name):
        m._check_all()
    rec = [r for r in caplog.records if "state_changes" in r.getMessage()]
    assert rec, "the failure must be logged where an operator will see it"
    assert rec[0].levelno == logging.WARNING
    assert "KeyError" in rec[0].getMessage()
    assert rec[0].exc_info, "the first failure carries its traceback"


def test_a_still_failing_check_is_not_relogged_every_tick(monkeypatch, caplog):
    log = logging.getLogger("test.monitor.isolation.relog")
    log.propagate = True
    monkeypatch.setattr(pm, "system_log", log)
    m = _quiet_monitor()
    m._check_state_changes = _boom
    with caplog.at_level(logging.WARNING, logger=log.name):
        for _ in range(10):
            m._check_all()
    assert len([r for r in caplog.records if "state_changes" in r.getMessage()]) == 1


# ── paging ───────────────────────────────────────────────────────────────

def test_a_persistent_failure_pages_the_admin_once():
    m = _quiet_monitor()
    m._check_state_changes = _boom
    n = m.CHECK_DOWN_ALERT_AFTER
    early = []
    for _ in range(n - 1):
        early += _monitor_alerts(m._check_all())
    assert early == [], "a transient must not page"
    page = _monitor_alerts(m._check_all())          # the Nth consecutive
    assert [a.alert_type for a in page] == ["MONITOR_CHECK_DOWN"]
    a = page[0]
    assert a.severity == "CRITICAL" and a.audience == "admin"
    assert "state_changes" in a.title
    assert "KeyError" in a.body
    assert "trading continues" in a.body
    for _ in range(5):
        assert _monitor_alerts(m._check_all()) == [], "once per outage"


def test_recovery_after_a_page_pages_once_and_clears():
    m = _quiet_monitor()
    m._check_state_changes = _boom
    for _ in range(m.CHECK_DOWN_ALERT_AFTER):
        m._check_all()
    m._check_state_changes = lambda: []
    up = _monitor_alerts(m._check_all())
    assert [a.alert_type for a in up] == ["MONITOR_CHECK_UP"]
    assert up[0].audience == "admin"
    assert m.check_failures() == {}
    assert _monitor_alerts(m._check_all()) == []


def test_a_transient_failure_neither_pages_nor_lingers():
    m = _quiet_monitor()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("once")
        return []
    m._check_ws_health = flaky
    out = m._check_all() + m._check_all()
    assert _monitor_alerts(out) == []
    assert m.check_failures() == {}


def test_two_broken_checks_are_counted_separately():
    m = _quiet_monitor()
    m._check_state_changes = _boom
    m._check_ws_health = _boom
    for _ in range(m.CHECK_DOWN_ALERT_AFTER):
        pages = _monitor_alerts(m._check_all())
    assert sorted(a.title for a in pages) == [
        "Monitor check down: state_changes", "Monitor check down: ws_health"]
    assert set(m.check_failures()) == {"state_changes", "ws_health"}


def test_a_monitor_built_without_init_still_answers():
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    assert m.check_failures() == {}


# ── registration ─────────────────────────────────────────────────────────

def test_every_check_is_registered_in_check_all():
    """A check that is written but never listed is the same defect one level
    up (a module nothing calls). Names stay literal in _check_all for this."""
    src = inspect.getsource(ProactiveMonitor._check_all)
    for n in dir(ProactiveMonitor):
        if n.startswith("_check_") and n != "_check_all":
            assert f"self.{n}" in src, f"{n} is not run by _check_all"


def test_the_helpers_do_not_masquerade_as_checks():
    """_run_check / _failure_alerts / check_failures must not carry the
    `_check_` prefix, or the registration test above would demand they be
    listed as checks of their own."""
    for n in ("_run_check", "_failure_alerts", "check_failures", "_failure_records"):
        assert hasattr(ProactiveMonitor, n)
        assert not n.startswith("_check_")


# ── the surface ──────────────────────────────────────────────────────────

def test_status_line_is_omitted_when_nothing_is_down():
    assert monitor_checks_line({}) == ""
    assert monitor_checks_line(None) == ""
    assert monitor_checks_line("weird") == ""


def test_status_line_names_the_down_checks():
    line = monitor_checks_line({
        "state_changes": {"count": 4, "since_s": 120.0, "last_error": "KeyError: x"},
        "ws_health": {"count": 3, "since_s": 90.0, "last_error": "RuntimeError: y"},
    })
    assert "<b>2</b>" in line
    assert "state_changes, ws_health" in line
    assert "not being raised" in line


def test_the_keys_are_translated():
    for key in ("fmt_monitor_checks_down", "fmt_monitor_checks_unread"):
        for lang in ("en", "zh"):
            assert t(key, lang) != key


def test_status_reaches_the_line_through_the_monitor():
    """Wiring: /status must call check_failures() on the live monitor and
    print the line -- source-scanned because the whole card needs an engine."""
    import io
    import tokenize
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "bot" / "skills"
           / "telegram_handler.py").read_text(encoding="utf-8")
    code = " ".join(tok.string for tok in tokenize.generate_tokens(
        io.StringIO(src).readline) if tok.type != tokenize.COMMENT)
    i = code.find("async def _cmd_status")
    body = code[i:code.find("async def ", i + 10)]
    assert "monitor_checks_line (" in body
    assert "check_failures ( )" in body
    assert "fmt_monitor_checks_unread" in body, "an unreadable state is said, not omitted"

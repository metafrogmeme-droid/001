"""Who receives a proactive alert.

The module docstring of ``bot/core/proactive_monitor`` claimed, from the day it
was written, "Only sends to authorized admin users in the allow-list (F-04
compliant)". The code underneath never did that: ``/watch`` is
``@guard("scan")``, so every scan-tier user who ran ``/watch on`` was in
``_enabled_chats`` and received everything the monitor produced — including a
CRITICAL card instructing them to "Add or rotate an LLM API key (paid tier
avoids the daily quota wall)" about an LLM account they do not hold.

A claim in a docstring is not a gate. These tests drive ``_dispatch`` with a
planted watch list and a planted admin predicate, and read who was sent to.

The interesting half is the failure path. "Admin-only" has three wrong answers
and two of them look fine in a green test run:

  * fall back to everyone when the predicate is missing — the leak, arriving
    through the error path;
  * drop silently when nothing answers — a CRITICAL alert about a bot trading
    blind, evaporating because a user-store read failed;
  * read a raised exception as "not an admin" — CLAUDE.md's central rule, at
    the level of a per-chat lookup: unreadable is not a verdict.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from bot.core.proactive_monitor import Alert, ProactiveMonitor


@pytest.fixture
def operators():
    """Plant the configured operator chats: ``operators(chat_id=, admin_ids=)``.

    ``CONFIG`` and ``CONFIG.telegram`` are BOTH frozen dataclasses, so neither
    ``monkeypatch.setattr`` nor ``mock.patch.object`` can do it —
    ``dataclasses.replace`` builds a new telegram config and
    ``object.__setattr__`` swaps it in. A fixture rather than a helper so the
    original is restored even when the test fails, since leaking a patched
    CONFIG into the rest of the session would be a far more confusing failure
    than the one being tested.
    """
    import dataclasses

    from bot.config import CONFIG
    original = CONFIG.telegram

    def _set(chat_id="", admin_ids=""):
        object.__setattr__(CONFIG, "telegram", dataclasses.replace(
            original, chat_id=chat_id, admin_ids=admin_ids))

    yield _set
    object.__setattr__(CONFIG, "telegram", original)


def _monitor(chats, admin_fn=None) -> ProactiveMonitor:
    """A monitor with a planted watch list. ``hydrate()`` is never called, so
    nothing is loaded from disk and no operator is auto-enrolled."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m._enabled_chats = set(chats)
    m._chart_fn = None
    m._admin_fn = admin_fn
    return m


def _sent(monitor, alert) -> list:
    """Every chat id ``_dispatch`` actually sent to."""
    got: list = []

    async def send_fn(chat_id, msg, *a):
        got.append(str(chat_id))

    asyncio.run(monitor._dispatch(alert, send_fn))
    return sorted(got)


def _alert(audience="all", **kw):
    return Alert(alert_type=kw.pop("alert_type", "LLM_DEGRADED"),
                 severity=kw.pop("severity", "CRITICAL"),
                 title="LLM brain offline", body="rotate a key",
                 audience=audience, **kw)


# ── the fix ──────────────────────────────────────────────────────────────

def test_admin_alert_reaches_only_the_admin(operators):
    operators(chat_id="", admin_ids="")
    m = _monitor({"111", "222", "333"}, admin_fn=lambda c: c == "222")
    assert _sent(m, _alert("admin")) == ["222"]


def test_an_ordinary_alert_still_reaches_every_watching_chat(operators):
    """The default must not narrow. A field that decides an audience, defaulting
    to the smaller one, silently mutes every alert nobody has thought about —
    including the position and drawdown cards a trader is watching for."""
    operators(chat_id="", admin_ids="")
    m = _monitor({"111", "222"}, admin_fn=lambda c: c == "222")
    assert _sent(m, _alert("all")) == ["111", "222"]
    # And an Alert built without thinking about audience at all:
    assert Alert(alert_type="X", severity="INFO", title="t", body="b").audience == "all"


def test_the_llm_cards_are_the_ones_marked_admin():
    """Not a source scan standing in for behaviour — the behaviour is above.
    This pins WHICH alerts opted in, which no amount of dispatch testing can
    say, and it is the half that silently reverts."""
    import inspect

    from bot.core import proactive_monitor as pm
    src = inspect.getsource(pm.ProactiveMonitor._check_llm_degraded)
    assert src.count('audience="admin"') == 2, (
        "the LLM offline card and its all-clear must BOTH be admin-only — an "
        "all-clear with a wider audience than its warning answers a question "
        "those readers were never asked")


# ── the classification, as a ratchet ─────────────────────────────────────

#: Operator infrastructure: the alert is about the BOT'S OWN PLUMBING and asks
#: for an action only the operator can take. None of them names a position, a
#: symbol or a price.
ADMIN_ONLY = {
    "LLM_DEGRADED", "LLM_RESTORED",          # rotate an API key
    "GATEWAY_DOWN", "GATEWAY_OK",            # the website cannot reach the bot
    "WS_DOWN", "WS_UP",                      # the price socket
    "TICK_STALL", "TICK_FAILURE",            # the engine loop
    "SCAN_TIMEOUT", "WARNING_RATE",          # scan + error-rate breakers
    "STALE_BALANCE", "MACRO_CALENDAR_STALE",  # feeds gone stale
    "SELF_AUDIT", "PARITY_DIGEST",           # operator reports
    "LEARNING_READY",                        # a component validated
    # The monitor's own checks: one that raises every tick is DOWN, and only
    # the operator can read the traceback and fix it. Both ends are admin —
    # an all-clear with a wider audience than its warning answers a question
    # those readers were never asked (the LLM-card rule above).
    "MONITOR_CHECK_DOWN", "MONITOR_CHECK_UP",
}


def _alert_audiences() -> dict:
    """{alert_type: audience} read off the Alert() constructors by AST.

    AST, not a regex, because ``alert_type`` and ``audience`` are keywords on a
    multi-line call and a text scan cannot reliably tell which constructor a
    given line belongs to — the failure would be a misattributed audience,
    which is the thing being checked.
    """
    import ast
    import inspect

    from bot.core import proactive_monitor as pm
    out: dict = {}
    for node in ast.walk(ast.parse(inspect.getsource(pm))):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "Alert"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        t = kw.get("alert_type")
        if not isinstance(t, ast.Constant):
            continue                      # f-string types (DAILY_*) — see below
        aud = kw.get("audience")
        out[t.value] = (aud.value if isinstance(aud, ast.Constant) else "all")
    return out


def test_every_operator_infrastructure_alert_is_admin_only():
    """A ratchet in both directions, for the same reason known_failures.txt is.

    An alert that LEAVES this set is a card that started reaching every
    watching chat again, which is the bug being fixed. An alert that arrives in
    it without being listed above is somebody narrowing an audience without
    writing down why — and silently muting a card a trader was relying on is
    the failure that faces the other way.
    """
    got = _alert_audiences()
    admin = {t for t, a in got.items() if a == "admin"}
    assert admin == ADMIN_ONLY, (
        f"unexpectedly admin-only: {sorted(admin - ADMIN_ONLY)}\n"
        f"no longer admin-only: {sorted(ADMIN_ONLY - admin)}")


def test_the_alerts_a_trader_acts_on_still_reach_them():
    """The half that is easy to lose while tightening the other one. Every one
    of these names the reader's own risk — a position without a stop, a circuit
    breaker, a signal — and an audience gate that swallows them would be a
    worse bug than the leak it replaced."""
    got = _alert_audiences()
    for t in ("TRADE_SIGNAL", "POSITION_UNPROTECTED", "SL_PROXIMITY",
              "TP_PROXIMITY", "CIRCUIT_BREAKER", "BLACK_SWAN", "STATE_CHANGE",
              "TIME_STOP_WARN", "TIME_STOP_CLOSE", "DRAWDOWN_TIER"):
        assert got.get(t) == "all", f"{t} was narrowed to admins"


# ── the failure path, which is where audience gates actually break ───────

def test_a_missing_predicate_does_not_fall_back_to_everyone(operators):
    """The leak, arriving through the error door instead of the front one."""
    operators(chat_id="999", admin_ids="")
    m = _monitor({"111", "222", "999"}, admin_fn=None)
    assert _sent(m, _alert("admin")) == ["999"], (
        "with no admin predicate wired, the alert went to non-admins")


def test_an_unreadable_role_is_not_a_verdict_of_not_admin(operators):
    """A predicate that RAISES for one chat has said nothing about it. The
    configured operator is still an admin — that fact needs no lookup — so the
    alert must still land rather than being lost to somebody else's exception."""
    operators(chat_id="999", admin_ids="")

    def boom(chat_id):
        raise RuntimeError("user store down")

    m = _monitor({"111", "999"}, admin_fn=boom)
    assert _sent(m, _alert("admin")) == ["999"]


def test_the_configured_operator_is_an_admin_even_when_the_store_says_no(operators):
    operators(chat_id="", admin_ids="7, 8")
    m = _monitor({"7", "8", "9"}, admin_fn=lambda c: False)
    assert _sent(m, _alert("admin")) == ["7", "8"]


@pytest.fixture
def _propagate_system_log():
    """``system_log`` (bot/utils/logger.py) sets ``propagate=False``, so
    ``caplog`` — which attaches at the root — cannot see it. The first draft of
    the test below failed for exactly that reason while the warning was sitting
    in the captured stderr, which is the assertion being wrong rather than the
    code. Same fixture as tests/test_audit_v5_followup_risk.py."""
    lg = logging.getLogger("runeclaw.system")
    saved = lg.propagate
    lg.propagate = True
    yield
    lg.propagate = saved


def test_no_admin_to_send_to_is_logged_loudly_not_swallowed(
        operators, caplog, _propagate_system_log):
    """The other way to get this wrong. LLM_DEGRADED is CRITICAL — the bot is
    trading on rules alone — and an alert that reaches nobody because nothing
    is configured must leave a record. A silence that is logged is a different
    thing from one that is not."""
    operators(chat_id="", admin_ids="")
    m = _monitor({"111", "222"}, admin_fn=lambda c: False)
    with caplog.at_level(logging.WARNING):
        assert _sent(m, _alert("admin")) == []
    assert any("no admin recipient" in r.getMessage() for r in caplog.records), (
        "the alert went nowhere and nothing said so")


# ── the other surface making the same claim ──────────────────────────────

def test_an_admin_alert_is_not_published_to_the_public_mind_stream(monkeypatch, operators):
    """`_dispatch` also emits title + type to the agent feed, which is PUBLIC —
    it powers the landing page. Narrowing the Telegram fan-out while still
    publishing there would move the message to a WIDER audience than the one it
    was taken away from."""
    emitted: list = []
    import bot.core.agent_feed as feed
    monkeypatch.setattr(feed.FEED, "emit",
                        lambda *a, **k: emitted.append((a, k)), raising=False)
    operators(chat_id="999", admin_ids="")

    _sent(_monitor({"999"}, admin_fn=lambda c: True), _alert("admin"))
    assert emitted == [], "an admin-only alert was published to the public feed"

    _sent(_monitor({"999"}, admin_fn=lambda c: True), _alert("all"))
    assert emitted, "an ordinary alert stopped reaching the public feed"


# ── it is wired ──────────────────────────────────────────────────────────

def test_the_handler_actually_injects_the_admin_predicate():
    """#58: a gate nothing calls is indistinguishable from one that does not
    work. `set_admin_fn` existing proves nothing — every test above passes with
    it never called in production, which is precisely the state that shipped
    the docstring's claim."""
    from tests.source_scan import handler_sources
    # Every file the handler class is made of: the wiring lives in
    # start_monitor, in the alert-loop mixin since the handler split, and a
    # scan of telegram_handler.py alone reads the move as the predicate
    # never being injected.
    src = "\n".join(p.read_text(encoding="utf-8") for p in handler_sources())
    assert "set_admin_fn(self._is_admin_id)" in src, (
        "the monitor is never told who is an admin, so every admin-only alert "
        "falls back to the configured operator chats alone")

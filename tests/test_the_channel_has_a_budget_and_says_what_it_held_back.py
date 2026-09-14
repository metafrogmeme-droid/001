"""The 5-minute dedup is PER KEY, so nothing bounded how many messages the
channel sent in an hour.

THE SECOND REPORT, 2026-09-14: "Still we have 20+ more anomaly messages last
hour this is to much." #107's scope and interval dials govern ONE of the
monitor's thirty check paths. The other twenty-nine share only
`DEDUP_COOLDOWN`, applied per `dedup_key` — and thirteen of the twenty key
sites mint one per symbol or per trade. Twelve an hour PER KEY, times the
position count. Nothing bounded the channel.

A blanket cap would be wrong, and anomaly_scope.py's own opening says why:
it "only trades a real warning for a quieter flood of irrelevant ones". So
the budget is SEVERITY-AWARE — CRITICAL is never budgeted — and what it holds
back is COUNTED and SAID on the next message through.

Driven, not scanned: the limiter is pure and takes its clock as a parameter
(`time.monotonic()` starts near zero on a fresh host, so "an hour ago" as
`monotonic() - 3600` is negative there); the monitor is built with `__new__`
the way the sibling suites build it, and `_should_send` / `_mark_sent` /
`_dispatch` are called for real.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core import anomaly_scope as sc
from bot.core.proactive_monitor import Alert, ProactiveMonitor

NOW = 1_000_000.0   # far from monotonic's zero, on purpose


# ── the pure limiter ────────────────────────────────────────────────────────

def test_an_empty_history_allows_and_records_nothing_on_its_own():
    allowed, fresh = sc.channel_budget_allows([], NOW, 3)
    assert allowed is True and fresh == []


def test_the_budget_is_exhausted_at_exactly_per_hour_and_not_before():
    sent = [NOW - 10, NOW - 20]
    assert sc.channel_budget_allows(sent, NOW, 3)[0] is True     # 2 of 3
    sent.append(NOW - 5)
    assert sc.channel_budget_allows(sent, NOW, 3)[0] is False    # 3 of 3


def test_the_window_boundary_frees_a_slot():
    sent = [NOW - 3600.0, NOW - 10]          # one exactly an hour old
    allowed, fresh = sc.channel_budget_allows(sent, NOW, 2)
    assert allowed is True, "an hour-old send no longer counts"
    assert fresh == [NOW - 10], "and it is pruned from what is persisted"


def test_the_limiter_only_decides_and_never_charges():
    """Charging is the sender's job AFTER the send. A decision that also spent
    the allowance would spend it on messages that then failed to send — the
    correction the black-swan card budget records having needed."""
    sent = [NOW - 10]
    _allowed, fresh = sc.channel_budget_allows(sent, NOW, 5)
    assert fresh == [NOW - 10], "nothing was appended by deciding"


def test_a_junk_per_hour_falls_back_to_the_default_not_to_unlimited():
    sent = [NOW - i for i in range(sc.DEFAULT_BUDGET_PER_HOUR)]
    assert sc.channel_budget_allows(sent, NOW, "banana")[0] is False
    assert sc.channel_budget_allows(sent, NOW, None)[0] is False


# ── who is budgeted ─────────────────────────────────────────────────────────

def test_critical_is_never_budgeted_and_everything_else_is():
    assert sc.is_budgeted("CRITICAL") is False
    assert sc.is_budgeted("critical") is False
    assert sc.is_budgeted("WARNING") is True
    assert sc.is_budgeted("INFO") is True


def test_an_unknown_severity_is_budgeted_because_that_is_the_safe_direction():
    # A word nobody recognises is not a reason to bypass the one control that
    # bounds the channel.
    for junk in ("", None, "URGENT", "SEVERE", 3):
        assert sc.is_budgeted(junk) is True, junk


# ── the dial ────────────────────────────────────────────────────────────────

def test_the_default_is_below_the_number_the_operator_called_too_many():
    assert sc.DEFAULT_BUDGET_PER_HOUR < 20
    assert sc.DEFAULT_BUDGET_PER_HOUR == 12   # one advisory every five minutes


def test_junk_budgets_are_refused_not_defaulted():
    for junk in (None, "", "lots", 0, -1, sc.MAX_BUDGET_PER_HOUR + 1):
        assert sc.normalise_budget(junk) is None, junk
    assert sc.normalise_budget("20") == 20
    assert sc.normalise_budget(sc.MIN_BUDGET_PER_HOUR) == sc.MIN_BUDGET_PER_HOUR


def test_the_parser_reads_the_budget_and_its_synonyms():
    assert sc.parse_setting(["budget", "20"]) == (None, None, 20, "")
    assert sc.parse_setting(["max", "5"])[2] == 5
    assert sc.parse_setting(["limit", "100"])[2] == 100


def test_the_card_prints_the_budget_and_names_the_command_that_sets_it():
    card = sc.settings_card({"scope": sc.SCOPE_HELD, "interval": 3600, "budget": 20},
                            held={"BNB/USDT"})
    assert "<code>20</code>" in card and "an hour" in card
    assert "critical alerts are never held" in card
    assert "/alerts budget" in card
    # A card with no budget stored prints the default rather than nothing.
    bare = sc.settings_card({"scope": sc.SCOPE_HELD, "interval": 3600}, held=set())
    assert f"<code>{sc.DEFAULT_BUDGET_PER_HOUR}</code>" in bare


# ── what is held back is said ───────────────────────────────────────────────

def test_the_note_is_empty_when_nothing_was_held():
    assert sc.held_back_note(0, 12) == ""
    assert sc.held_back_note(-3, 12) == ""


def test_the_note_says_the_count_the_dial_and_that_critical_was_never_subject():
    note = sc.held_back_note(7, 12)
    assert "7 advisory alerts" in note
    assert "12/hour" in note
    assert "critical alerts are never held" in note
    assert "1 advisory alert held" in sc.held_back_note(1, 12)


# ── the monitor, driven ─────────────────────────────────────────────────────

def _mon(budget=3, prefs_fn="ok", monkeypatch=None) -> ProactiveMonitor:
    """A monitor with only the state the chokepoint reads — the pattern every
    sibling suite uses. `prefs_fn`: "ok" wires a reader answering `budget`;
    "raises" wires one that raises; None wires nothing."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m._enabled_chats = {"111"}
    m._dedup_cache = {}
    if prefs_fn == "ok":
        m._anomaly_prefs_fn = lambda: {"scope": "held", "interval": 3600, "budget": budget}
    elif prefs_fn == "raises":
        def _boom():
            raise RuntimeError("store unreadable")
        m._anomaly_prefs_fn = _boom
    return m


def _alert(severity="WARNING", key="", n=0) -> Alert:
    return Alert(alert_type="SL_PROXIMITY", severity=severity,
                 title=f"t{n}", body=f"body {n}", dedup_key=key)


@pytest.fixture
def clock(monkeypatch):
    """Pin monotonic far from zero, and let a test advance it."""
    t = {"now": NOW}
    monkeypatch.setattr("bot.core.proactive_monitor.time.monotonic", lambda: t["now"])
    return t


def test_advisories_stop_at_the_budget_and_critical_does_not(clock):
    m = _mon(budget=2)
    for n in range(2):
        a = _alert("WARNING", n=n)
        assert m._should_send(a) is True
        m._mark_sent(a)
    assert m._should_send(_alert("WARNING", n=9)) is False, "third advisory held"
    assert m._should_send(_alert("INFO", n=10)) is False, "INFO shares the budget"
    assert m._should_send(_alert("CRITICAL", n=11)) is True, "critical is never held"


def test_the_budget_is_charged_on_what_was_SENT_not_on_what_was_decided(clock):
    m = _mon(budget=2)
    a = _alert("WARNING", n=1)
    # Three decisions, no sends: nothing is charged.
    for _ in range(3):
        assert m._should_send(a) is True
    assert len(getattr(m, "_channel_sent_at", [])) == 0
    m._mark_sent(a)
    assert len(m._channel_sent_at) == 1


def test_a_critical_send_does_not_spend_the_advisory_allowance(clock):
    m = _mon(budget=1)
    c = _alert("CRITICAL", n=1)
    assert m._should_send(c) is True
    m._mark_sent(c)
    assert m._should_send(_alert("WARNING", n=2)) is True, "the one advisory slot is still free"


def test_the_hour_rolls_over(clock):
    m = _mon(budget=1)
    a = _alert("WARNING", n=1)
    m._should_send(a)
    m._mark_sent(a)
    assert m._should_send(_alert("WARNING", n=2)) is False
    clock["now"] = NOW + 3601
    assert m._should_send(_alert("WARNING", n=3)) is True


def test_held_back_is_counted_and_said_on_the_next_message_through(clock):
    m = _mon(budget=1)
    a = _alert("WARNING", n=1)
    m._should_send(a)
    m._mark_sent(a)
    for n in range(3):
        assert m._should_send(_alert("WARNING", n=10 + n)) is False
    assert m._held_back == 3
    sent = []
    async def send_fn(chat_id, text, *rest):
        sent.append(text)
    c = _alert("CRITICAL", n=20)
    assert m._should_send(c) is True
    asyncio.run(m._dispatch(c, send_fn))
    assert len(sent) == 1
    assert "3 advisory alerts held back this hour" in sent[0]
    assert "critical alerts are never held" in sent[0]
    m._mark_sent(c)
    assert m._held_back == 0, "cleared once the message carrying it was sent"


def test_a_dispatch_that_fails_keeps_the_count_for_the_next_message(clock):
    """`_mark_sent` clears the count, `_dispatch` only reads it — so a send
    that raised has not told the operator anything and the count survives."""
    m = _mon(budget=1)
    a = _alert("WARNING", n=1)
    m._should_send(a)
    m._mark_sent(a)
    m._should_send(_alert("WARNING", n=2))
    assert m._held_back == 1
    async def bad_send(chat_id, text, *rest):
        raise RuntimeError("telegram down")
    # _dispatch swallows per-chat send errors; the point is _mark_sent is not
    # reached by the loop when dispatch raised, and the count is intact.
    asyncio.run(m._dispatch(_alert("CRITICAL", n=3), bad_send))
    assert m._held_back == 1


def test_no_note_rides_a_message_when_nothing_was_held(clock):
    m = _mon(budget=5)
    sent = []
    async def send_fn(chat_id, text, *rest):
        sent.append(text)
    asyncio.run(m._dispatch(_alert("WARNING", n=1), send_fn))
    assert "held back" not in sent[0]


def test_a_failing_prefs_read_falls_back_to_the_default_budget_not_to_unlimited(clock):
    m = _mon(prefs_fn="raises")
    assert m._anomaly_dials()["budget"] == sc.DEFAULT_BUDGET_PER_HOUR
    for n in range(sc.DEFAULT_BUDGET_PER_HOUR):
        a = _alert("INFO", n=n)
        assert m._should_send(a) is True
        m._mark_sent(a)
    assert m._should_send(_alert("INFO", n=99)) is False


def test_a_monitor_with_no_prefs_wired_uses_the_quiet_default(clock):
    m = _mon(prefs_fn=None)
    assert m._anomaly_dials() == {"scope": sc.SCOPE_HELD,
                                  "interval": sc.DEFAULT_INTERVAL_SEC,
                                  "budget": sc.DEFAULT_BUDGET_PER_HOUR}


def test_the_dedup_still_runs_first_and_a_dedup_refusal_is_not_a_hold(clock):
    m = _mon(budget=5)
    a = _alert("WARNING", key="k", n=1)
    assert m._should_send(a) is True
    m._mark_sent(a)
    assert m._should_send(a) is False, "the 5-minute per-key rule"
    assert getattr(m, "_held_back", 0) == 0, "a dedup refusal is a repeat, not a budget hold"


def test_no_enabled_chats_is_still_a_refusal_before_anything_else(clock):
    m = _mon(budget=5)
    m._enabled_chats = set()
    assert m._should_send(_alert("CRITICAL")) is False


# ── the arb path goes through the same gate ────────────────────────────────

def test_both_send_paths_reach_the_one_chokepoint():
    """The arb tracker sends outside `_check_all`. It must not have its own
    copy of the decision — a second gate is a second answer, and the flood
    would simply move there."""
    import ast
    import inspect
    src = inspect.getsource(ProactiveMonitor)
    tree = ast.parse(src)
    awaited_dispatch = 0
    gated = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and f.attr == "_dispatch":
                awaited_dispatch += 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "_should_send":
            gated += 1
    assert awaited_dispatch == 2, "the main loop and the arb tracker"
    assert gated == awaited_dispatch, "every dispatch sits behind _should_send"


# ── the store ───────────────────────────────────────────────────────────────

def _store(tmp_path):
    # `register` then `authorize`, the way the sibling suite creates a row.
    # The first draft called `add_user`, a method that does not exist — a
    # name remembered, not measured.
    from bot.utils.user_store import UserStore
    s = UserStore(path=tmp_path / "users.json")
    s.register("4242", "op")
    s.authorize("4242", "admin")
    return s


def test_the_store_defaults_the_budget_and_drops_a_corrupt_one(tmp_path):
    s = _store(tmp_path)
    assert s.anomaly_prefs(4242)["budget"] == sc.DEFAULT_BUDGET_PER_HOUR
    s._users["4242"]["anomaly_prefs"] = {"budget": "lots"}
    assert s.anomaly_prefs(4242)["budget"] == sc.DEFAULT_BUDGET_PER_HOUR
    s._users["4242"]["anomaly_prefs"] = {"budget": 0}
    assert s.anomaly_prefs(4242)["budget"] == sc.DEFAULT_BUDGET_PER_HOUR


def test_the_budget_is_set_without_clearing_the_other_two(tmp_path):
    s = _store(tmp_path)
    assert s.set_anomaly_prefs(4242, scope=sc.SCOPE_ALL, interval=7200)
    assert s.set_anomaly_prefs(4242, budget=30)
    got = s.anomaly_prefs(4242)
    assert got == {"scope": sc.SCOPE_ALL, "interval": 7200, "budget": 30}

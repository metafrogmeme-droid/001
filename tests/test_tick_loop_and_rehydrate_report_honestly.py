"""Two startup/tick-loop faults that nothing could see from a green suite.

1. ``_push_scan_summary_to_website`` ran INLINE on the event loop from
   ``_tick``. Its name says "push a summary", and it does — but first it calls
   ``_build_scan_payload``, which in live mode calls ``_fetch_live_exchange_data``:
   synchronous ccxt, a balance fetch plus a positions fetch plus a trade-history
   read, each with its own HTTP timeout. Every second that took was a second the
   event loop could not run the stop-loss re-arm, the Telegram poller, or the
   heartbeat the dashboard reads to decide the engine is alive. The
   ``sync_scan_in_background`` at the END of it was already off-thread, so the
   cheap half was covered and the expensive half was not.

2. ``_rehydrate_user_executors`` audited a NUMERATOR WITH NO DENOMINATOR, and
   said nothing at all on the worst outcome: the audit sat behind
   ``if self._user_executors:``, so a startup where every user failed to
   rehydrate wrote no line. A bot whose linked users all failed then looks
   exactly like a bot with no linked users — while their persisted LIVE
   positions sit unmonitored, with nothing re-arming their stops.
"""

from __future__ import annotations

import ast
import inspect
import logging
from types import SimpleNamespace

import pytest

import bot.core.engine as eng
from bot.core.engine import RuneClawEngine

# ── 1. the tick loop ─────────────────────────────────────────────────────
#
# WIRING, and asserted through the AST rather than by matching text: the
# property is "this call is awaited through a thread", which is a shape in the
# tree, and a comment quoting `to_thread` cannot satisfy it. Driving a whole
# `_tick` to observe the loop staying responsive would need the scanner, the
# risk engine and a venue stubbed to reach one call.


def _dedent(src: str) -> str:
    import textwrap
    return textwrap.dedent(src)


def _awaited_to_thread_targets(fn) -> set[str]:
    """Names passed as the first argument of an awaited ``asyncio.to_thread``."""
    tree = ast.parse(_dedent(inspect.getsource(fn)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        if isinstance(f, ast.Attribute) and f.attr == "to_thread" and call.args:
            target = call.args[0]
            if isinstance(target, ast.Attribute):
                found.add(target.attr)
            elif isinstance(target, ast.Name):
                found.add(target.id)
    return found


def _plain_call_names(fn) -> set[str]:
    """Method names called directly (``self.x(...)``), not through to_thread."""
    tree = ast.parse(_dedent(inspect.getsource(fn)))
    awaited = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if id(node) not in awaited:
                out.add(node.func.attr)
    return out


def test_the_scan_push_runs_on_a_worker_thread():
    assert "_push_scan_summary_to_website" in _awaited_to_thread_targets(RuneClawEngine._tick), (
        "_tick calls _push_scan_summary_to_website on the event loop. In live "
        "mode that reaches synchronous ccxt through _build_scan_payload -> "
        "_fetch_live_exchange_data, freezing every other coroutine for as long "
        "as the venue takes to answer.")


def test_the_scan_push_is_not_also_called_inline():
    # Belt and braces: adding the to_thread call while leaving the direct one
    # would satisfy the assertion above and change nothing.
    assert "_push_scan_summary_to_website" not in _plain_call_names(RuneClawEngine._tick)


def test_the_push_really_does_reach_synchronous_ccxt():
    """The premise, checked — not assumed.

    If `_build_scan_payload` stopped calling `_fetch_live_exchange_data`, the
    to_thread hop would be dead weight and this file would be pinning a cost
    nobody pays. Asserting the reason keeps the fix honest about why it exists.
    """
    from bot.skills import scan_skill
    payload_src = inspect.getsource(scan_skill._build_scan_payload)
    assert "_fetch_live_exchange_data()" in payload_src
    fetch_src = inspect.getsource(scan_skill._fetch_live_exchange_data)
    assert not inspect.iscoroutinefunction(scan_skill._fetch_live_exchange_data), (
        "the fetch became async — the to_thread hop in _tick should be "
        "revisited rather than left as cargo")
    assert "fetch_balance" in fetch_src or "fetch_positions" in fetch_src


# ── 2. the rehydrate ─────────────────────────────────────────────────────


@pytest.fixture
def rehydrate(monkeypatch):
    """A bare engine whose rehydrate can be driven with planted users."""
    audits: list[dict] = []

    def _fake_audit(log, message, **kw):
        audits.append({"message": message, **kw})

    monkeypatch.setattr(eng, "audit", _fake_audit)
    monkeypatch.setattr(eng, "CONFIG", SimpleNamespace(per_user_live_enabled=True))

    def _build(user_ids, failing=()):
        e = RuneClawEngine.__new__(RuneClawEngine)
        e._user_executors = {}

        import bot.core.exchange_credentials as ec
        monkeypatch.setattr(
            ec, "get_credential_store",
            lambda: SimpleNamespace(user_ids=lambda: list(user_ids)))

        def _executor_for(uid):
            if uid in failing:
                raise RuntimeError(f"no usable keys for {uid}")
            e._user_executors[uid] = object()

        e._executor_for = _executor_for
        e._rehydrate_user_executors()
        return audits

    return _build


def test_a_clean_rehydrate_reports_both_halves(rehydrate):
    audits = rehydrate(["1", "2", "3"])
    assert len(audits) == 1
    msg = audits[0]["message"]
    assert "3 of 3" in msg, f"a numerator with no denominator: {msg}"
    assert audits[0]["result"] == "OK"


def test_a_partial_failure_is_a_warning_that_names_the_consequence(rehydrate):
    audits = rehydrate(["1", "2", "3"], failing={"2"})
    assert len(audits) == 1
    a = audits[0]
    assert "2 of 3" in a["message"], a["message"]
    assert a["result"] == "WARNING"
    assert a["level"] == logging.WARNING
    assert "NOT being monitored" in a["message"], (
        "the audit must say what the failure MEANS — unmonitored live positions "
        f"— not just count them: {a['message']}")


def test_a_total_failure_is_not_silent(rehydrate):
    """THE CASE THAT AUDITED NOTHING.

    Every user failed, `self._user_executors` stayed empty, and the old
    `if self._user_executors:` guard skipped the audit entirely — so the worst
    outcome was the one that produced no record at all.
    """
    audits = rehydrate(["1", "2"], failing={"1", "2"})
    assert len(audits) == 1, "a startup where every rehydrate failed wrote no audit"
    assert "0 of 2" in audits[0]["message"]
    assert audits[0]["result"] == "WARNING"


def test_no_linked_users_writes_nothing(rehydrate):
    # Genuinely nothing to rehydrate is not an event. It must not be reported
    # as a success either — "Rehydrated 0 of 0" on every restart is noise that
    # buries the line above.
    assert rehydrate([]) == []


def test_rehydrate_is_a_no_op_when_per_user_live_is_off(monkeypatch):
    monkeypatch.setattr(eng, "CONFIG", SimpleNamespace(per_user_live_enabled=False))
    called = []
    monkeypatch.setattr(eng, "audit", lambda *a, **k: called.append(1))
    e = RuneClawEngine.__new__(RuneClawEngine)
    e._user_executors = {}
    e._rehydrate_user_executors()
    assert called == []


def test_an_unreadable_credential_store_still_says_so(monkeypatch):
    """The early return, pinned: it logs and gives up, and must not go on to
    audit a confident "0 of 0" about a store it could not open."""
    monkeypatch.setattr(eng, "CONFIG", SimpleNamespace(per_user_live_enabled=True))
    audits: list = []
    monkeypatch.setattr(eng, "audit", lambda *a, **k: audits.append(a))
    import bot.core.exchange_credentials as ec

    def _boom():
        raise RuntimeError("vault locked")

    monkeypatch.setattr(ec, "get_credential_store", _boom)
    e = RuneClawEngine.__new__(RuneClawEngine)
    e._user_executors = {}
    e._rehydrate_user_executors()
    assert audits == []

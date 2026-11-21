"""A force scan counts the orders it placed, says what it did not, and confirms nothing light.

Four defects, one loop (`RuneClawEngine._force_scan_locked`):

* `auto_confirmed += 1` ran for ANY string `confirm_trade` returned -- "Trade
  REJECTED by risk", "⏭️ Skipped", "Trade not found" -- so "Auto-confirmed: 3"
  could mean three refusals. It now counts placed orders only, through the
  one reading of a confirm's answer (`bot/core/confirm_result.py`), and the
  refused, unverified and errored ones get counts of their own.
* `except Exception: pass` around the confirm swallowed a raise with no audit
  at all; around the notice, the same. Both are audited by exception CLASS.
* The Latest Signal tap scans `lightweight=True` (technicals only: no order
  flow, no MTF, no basis) and the loop auto-confirmed what that produced, and
  so did the next tick's batch, which sweeps the same pending book. A light
  idea is now stamped (`analysis_depth`) and the one suppression reading both
  loops ask refuses it.
* A full force scan registered its ideas but never published them, so the
  outcome ledger and the website stream never heard of them while the
  proactive monitor still alerted them. They are published now; a light
  scan's are not, and the summary says why.

The tick and the force scan run ONE loop now (`_run_auto_confirm_pairs`),
selected by one batch reading (`_auto_confirm_batch`). Each still calls
`confirm_trade` itself, after its own gate, so the door-gate pins read the
door that chose the pair.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from bot.config import RUNTIME
from bot.core.engine import RuneClawEngine
from bot.utils.models import Direction, TradeIdea

pytestmark = pytest.mark.asyncio

_BOUND = ("_force_scan_locked", "_auto_confirm_gate_value", "_auto_confirm_suppressed",
          "_auto_confirm_batch", "_engine_pending_ids", "_register_engine_idea",
          "_run_auto_confirm_pairs")


def _idea(asset="ETH/USDT:USDT", confidence=0.95, **kw) -> TradeIdea:
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=confidence,
                     reasoning="planted", source="analyzer", timeframe="1h", **kw)


def _host(*, results, confirm, notify=None, lightweight_marker=None):
    """A stand-in carrying the REAL loop methods, and recording what it was handed."""
    engine = SimpleNamespace()
    engine.confirmed, engine.published = [], []

    async def _scan():
        return [SimpleNamespace(symbol=i.asset) for i in results]

    async def _batched(signals, lightweight=False):
        out = []
        for i in results:
            if lightweight and lightweight_marker is not None:
                i.analysis_depth = lightweight_marker
            out.append(i)
        return out

    async def _confirm(tid, user_id=""):
        engine.confirmed.append((tid, user_id))
        return await confirm(tid)

    engine._pending_ideas = {}
    engine._pending_atr = {}
    engine._pending_timing = {}
    engine._pending_turn = {}
    engine._pending_pyramid = {}
    engine._cooldown_until = 0.0
    engine._last_scan_signals = []
    engine.scanner = SimpleNamespace(scan=_scan)
    engine._transition = lambda *a, **k: None
    engine._analyze_signals_batched = _batched
    engine.confirm_trade = _confirm
    engine._auto_confirm_notify_callback = notify
    engine.analyzer = None
    engine._engine_idea_ids = set()
    engine._publish_engine_ideas = lambda ideas: engine.published.append([i.id for i in ideas])
    for name in _BOUND:
        setattr(engine, name, getattr(RuneClawEngine, name).__get__(engine))
    return engine


@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 0.85, raising=False)


def _ok(answer):
    async def confirm(_tid):
        return answer
    return confirm


async def test_a_placed_order_is_counted_and_a_refusal_is_not():
    a, b = _idea("ETH/USDT:USDT"), _idea("SOL/USDT:USDT")
    answers = {a.id: "✅ Opened LONG ETH", b.id: "Trade REJECTED by risk: DAILY_LOSS"}

    async def confirm(tid):
        return answers[tid]
    summary = await _host(results=[a, b], confirm=confirm)._force_scan_locked()
    assert summary["auto_confirmed"] == 1
    assert summary["auto_refused"] == 1
    assert summary["auto_errored"] == 0


async def test_an_existing_fake_answer_still_counts_as_placed():
    # Every older stand-in returns "ok"; `placed_nothing("ok")` is False.
    summary = await _host(results=[_idea()], confirm=_ok("ok"))._force_scan_locked()
    assert summary["auto_confirmed"] == 1


async def test_a_raise_is_audited_by_class_and_not_counted(monkeypatch):
    rows = []
    monkeypatch.setattr("bot.core.engine.audit", lambda *a, **k: rows.append(k))

    async def confirm(_tid):
        raise RuntimeError("apiKey=SECRET")
    summary = await _host(results=[_idea()], confirm=confirm)._force_scan_locked()
    assert summary["auto_confirmed"] == 0
    assert summary["auto_errored"] == 1
    err = [r for r in rows if r.get("action") == "auto_confirm" and r.get("result") == "ERROR"]
    assert len(err) == 1
    assert err[0]["data"]["error"] == "RuntimeError"
    assert "SECRET" not in repr(rows)


async def test_a_notice_that_raises_is_audited_not_swallowed(monkeypatch):
    rows = []
    monkeypatch.setattr("bot.core.engine.audit", lambda *a, **k: rows.append(k))

    async def notify(_idea, _result):
        raise ValueError("chat gone")
    summary = await _host(results=[_idea()], confirm=_ok("ok"), notify=notify)._force_scan_locked()
    assert summary["auto_confirmed"] == 1          # the order still counts
    bad = [r for r in rows if r.get("action") == "auto_confirm_notify"]
    assert len(bad) == 1 and bad[0]["data"]["error"] == "ValueError"


async def test_a_lightweight_scan_confirms_nothing_and_the_tick_does_not_either():
    eng = _host(results=[_idea()], confirm=_ok("ok"), lightweight_marker="lightweight")
    summary = await eng._force_scan_locked(lightweight=True)
    assert eng.confirmed == []
    assert summary["auto_confirmed"] == 0
    assert summary["auto_confirm_skipped"] == "lightweight"
    # The idea is still pending for its human...
    assert len(eng._engine_pending_ids()) == 1
    # ...and the tick's own batch, sweeping the same book, refuses it too.
    assert eng._auto_confirm_batch(0.85) == []


async def test_without_the_stamp_the_same_idea_would_be_confirmed():
    # The other arm: the batch refuses BECAUSE of the stamp, not for any other reason.
    eng = _host(results=[_idea()], confirm=_ok("ok"))
    await eng._force_scan_locked(lightweight=True)
    assert eng._auto_confirm_batch(0.85) != []


async def test_a_full_scan_publishes_its_ideas_once_and_a_light_one_says_why_not():
    full = _host(results=[_idea()], confirm=_ok("Trade REJECTED by risk: X"))
    s = await full._force_scan_locked()
    assert len(full.published) == 1 and len(full.published[0]) == 1
    assert s.get("unpublished_why") is None
    light = _host(results=[_idea()], confirm=_ok("ok"), lightweight_marker="lightweight")
    s = await light._force_scan_locked(lightweight=True)
    assert light.published == []
    assert s["unpublished_why"] == "lightweight analysis (technicals only)"


async def test_a_disabled_threshold_confirms_nothing(monkeypatch):
    # 1.0 means OFF (`auto_confirm_is_disabled`), and the force scan now reads
    # that through the tick's batch rather than a copy of it.
    monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 1.0, raising=False)
    eng = _host(results=[_idea(confidence=1.0)], confirm=_ok("ok"))
    summary = await eng._force_scan_locked()
    assert eng.confirmed == [] and summary["auto_confirmed"] == 0


async def test_both_loops_share_one_runner():
    import ast
    import inspect
    import textwrap
    for name in ("_tick", "_force_scan_locked"):
        tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(RuneClawEngine, name))))
        calls = {getattr(n.func, "attr", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "_run_auto_confirm_pairs" in calls, name
        assert "_auto_confirm_batch" in calls, name


async def test_the_runner_never_logs_an_exceptions_text(caplog):
    caplog.set_level(logging.DEBUG)

    async def confirm(_tid):
        raise RuntimeError("token=SECRET")
    await _host(results=[_idea()], confirm=confirm)._force_scan_locked()
    assert "SECRET" not in caplog.text

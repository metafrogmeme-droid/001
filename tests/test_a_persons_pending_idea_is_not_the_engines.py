"""A person's pending idea is theirs, and the engine's loops leave it alone.

`_pending_ideas` is one dict. The engine's autonomous scan writes its ideas
there, and so does every person: `/trade` and the web's propose route
(`register_manual_idea`), `/scan`, "analyze BTC", the drift re-offer. Nothing
said whose an entry was, so every loop that swept the dict treated all of it
as the engine's:

* **One person paused the engine for every account.** `_tick` returned early
  while ANYTHING was pending (C2-26), so a stranger's `/trade`, left
  unconfirmed, stopped the autonomous scan and its auto-confirm until the
  ticket expired.
* **The engine's dedup deleted a person's ticket.** A scan idea for BTC
  replaced whatever BTC entry was pending, so a trader's Confirm answered
  "not found" because the engine had scanned the same coin.
* **/forcescan destroyed every person's pending Confirm.** It cleared the
  whole dict before scanning.

**Fixing the first alone would have made the auto-confirm leak the ordinary
case.** While the skip stood, a person's idea reached the auto-confirm batch
only when it was registered during a scan (the race #422 drove). With the
skip narrowed, the batch sees every person's idea on every tick, and a `/scan`
idea carries a MEASURED confidence, so the stamp reading passes it: it would
have been executed under `user_id="auto"` on the operator's account. The batch
reads ownership first.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.core.engine import RuneClawEngine
from bot.risk.quality_ladder import auto_confirm_refusal
from bot.skills.manual_trade import build_manual_idea, register_manual_idea
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only


def _idea(asset="BTC/USDT:USDT", confidence=0.95, source="unknown"):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=60000.0,
                     stop_loss=59000.0, take_profit=63000.0,
                     confidence=confidence, reasoning="x", source=source)


def _host():
    """A stand-in `self` carrying the dict, the set and the real methods."""
    host = SimpleNamespace(analyzer=None, _pending_ideas={}, _pending_atr={},
                           _pending_pyramid={}, _pending_timing={},
                           _engine_idea_ids=set())
    for name in ("_auto_confirm_batch", "_auto_confirm_gate_value",
                 "_auto_confirm_suppressed", "_engine_pending_ids",
                 "_register_engine_idea"):
        setattr(host, name, getattr(RuneClawEngine, name).__get__(host))
    return host


# ── the tick ───────────────────────────────────────────────────────────────


def _tick(engine):
    engine._running = True
    engine.scanner.scan = AsyncMock(return_value=[])
    engine._check_open_positions = AsyncMock()
    # the scan summary syncs to the website on a daemon thread
    engine._push_scan_summary_to_website = lambda signals: None
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(engine._tick())
    finally:
        loop.close()


def test_a_persons_pending_ticket_does_not_pause_the_scan():
    engine = RuneClawEngine()
    register_manual_idea(engine, build_manual_idea(
        "LONG", "BTC", 60000.0, 59000.0, 63000.0))
    _tick(engine)
    engine.scanner.scan.assert_called_once()


def test_the_engines_own_pending_idea_still_does():
    """The control: C2-26 is narrowed, not removed."""
    engine = RuneClawEngine()
    engine._register_engine_idea(_idea())
    _tick(engine)
    engine.scanner.scan.assert_not_called()


# ── the auto-confirm batch ─────────────────────────────────────────────────


def test_a_persons_measured_idea_is_not_auto_confirmed():
    """The case the stamp reading cannot catch, and the reason ownership is
    read FIRST: a `/scan` idea's confidence was measured."""
    host = _host()
    theirs = _idea(source="scan_skill")
    ours = _idea(asset="ETH/USDT:USDT")
    host._pending_ideas[theirs.id] = theirs      # as scan_skill writes it
    host._register_engine_idea(ours)
    assert auto_confirm_refusal(theirs) is None, (
        "the premise: nothing about the idea itself refuses it")
    ids = [tid for tid, _ in host._auto_confirm_batch(0.85)]
    assert ours.id in ids, "the engine's own idea still auto-confirms"
    assert theirs.id not in ids


# ── the engine's dedup ─────────────────────────────────────────────────────


def test_the_engines_idea_does_not_replace_a_persons_ticket():
    host = _host()
    ticket = build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0)
    register_manual_idea(host, ticket)
    ours = _idea(asset=ticket.asset)
    host._register_engine_idea(ours)
    assert ticket.id in host._pending_ideas, (
        "the trader's Confirm would answer 'not found'")
    assert ours.id in host._pending_ideas


def test_it_still_replaces_its_own_idea_for_the_same_asset():
    host = _host()
    old, new = _idea(), _idea()
    host._register_engine_idea(old)
    host._pending_atr[old.id] = 1.0
    host._pending_pyramid[old.id] = True
    host._register_engine_idea(new)
    assert old.id not in host._pending_ideas
    assert old.id not in host._pending_atr
    assert old.id not in host._pending_pyramid
    assert host._engine_pending_ids() == {new.id}


def test_an_idea_that_left_the_book_is_no_longer_the_engines():
    """A confirm, a skip and the TTL sweep each pop the dict and none of them
    knows the set exists, so the set is pruned where it is read."""
    host = _host()
    ours = _idea()
    host._register_engine_idea(ours)
    host._pending_ideas.pop(ours.id)
    assert host._engine_pending_ids() == set()
    assert host._engine_idea_ids == set()


def test_the_engine_writes_the_book_only_through_its_registration():
    """The set is only as good as the writers that record into it, so the one
    place the engine assigns into `_pending_ideas` is the registration."""
    src = code_only(inspect.getsource(RuneClawEngine))
    tree = ast.parse(src)
    writers = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Subscript)
                            and isinstance(t.value, ast.Attribute)
                            and t.value.attr == "_pending_ideas"):
                        writers.append(fn.name)
    assert writers == ["_register_engine_idea"], writers


# ── /forcescan ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forcescan_keeps_a_ticket_registered_before_it():
    confirmed: list[str] = []
    engine = _host()
    ticket = build_manual_idea("LONG", "SOL", 150.0, 145.0, 165.0)
    register_manual_idea(engine, ticket)
    # A person's /scan idea: its confidence was measured, so only ownership
    # keeps it out of the loop (the stamped ticket above is refused twice).
    scanned = _idea(asset="AVAX/USDT:USDT", source="scan_skill")
    engine._pending_ideas[scanned.id] = scanned
    stale = _idea(asset="XRP/USDT:USDT", confidence=0.5)
    engine._register_engine_idea(stale)

    async def _scan():
        return [SimpleNamespace(symbol="ETH/USDT:USDT")]

    async def _batched(signals, lightweight=False):
        return [_idea(asset="ETH/USDT:USDT")]

    async def _confirm(tid, user_id=""):
        confirmed.append(tid)
        return "ok"

    engine._cooldown_until = 0.0
    engine._last_scan_signals = []
    engine.scanner = SimpleNamespace(scan=_scan)
    engine._transition = lambda *a, **k: None
    engine._analyze_signals_batched = _batched
    engine.confirm_trade = _confirm
    engine._auto_confirm_notify_callback = None
    engine._force_scan_locked = RuneClawEngine._force_scan_locked.__get__(engine)

    summary = await engine._force_scan_locked()

    assert ticket.id in engine._pending_ideas, (
        "the button destroyed a person's pending Confirm")
    assert ticket.id not in confirmed
    assert scanned.id in engine._pending_ideas
    assert scanned.id not in confirmed, (
        "a person's measured idea was executed on the operator account")
    assert stale.id not in engine._pending_ideas, (
        "the control: the engine's own stale idea is still cleared")
    assert summary["cleared_pending"] == 1
    assert len(confirmed) == 1 and confirmed[0] not in (
        ticket.id, scanned.id, stale.id)


# ── a person's analysis ────────────────────────────────────────────────────


class _Exchange:
    async def fetch_ticker(self, sym):
        return {"last": 100.0, "percentage": 1.0, "quoteVolume": 5e6}


def test_an_analysis_does_not_delete_the_engines_idea_for_the_same_asset():
    from bot.skills.skill_registry import AnalyzeAssetSkill

    host = _host()
    ours = _idea(asset="BTC/USDT")
    host._register_engine_idea(ours)
    theirs_before = _idea(asset="BTC/USDT", source="unknown")
    host._pending_ideas[theirs_before.id] = theirs_before   # an earlier analysis

    async def _get_exchange():
        return _Exchange()

    analysed = _idea(asset="BTC/USDT")

    async def _analyze_signal(sig, **kw):
        return analysed

    host.scanner = SimpleNamespace(_get_exchange=_get_exchange,
                                   _get_futures_exchange=_get_exchange)
    host._last_rejections = {}
    host._analyze_signal = _analyze_signal
    try:
        asyncio.run(AnalyzeAssetSkill().execute(host, symbol="BTC/USDT"))
    except Exception:
        pass    # the card past the registration is not what is measured
    assert analysed.id in host._pending_ideas
    assert ours.id in host._pending_ideas, (
        "a person asking about BTC deleted the engine's pending BTC idea")
    assert theirs_before.id not in host._pending_ideas, (
        "the control: an analysis still replaces an earlier analysis")

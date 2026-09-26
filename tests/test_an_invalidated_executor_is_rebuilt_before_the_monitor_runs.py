"""A user's executors are dropped on a key or control change, and rebuilt.

`invalidate_user_executor` pops every executor a user holds: on `/connect`, on
`/disconnect`, on the website's credential pull, and on EVERY website control
change (margin cap, pause, venue selection). It has to, so the next order is
built from the current keys. But the monitoring and reconciliation loops walk
`_user_executors` and nothing else (`_all_live_executors`), so the user's open
positions dropped out of them with it. Only the next trade, a card view (the
active venue) or a restart (every other venue) rebuilt one.

Driven before the fix: a user's book checked once, one website control change,
then three monitor passes checked it zero times, with no executor held for the
user. Stops resting on the venue still fire; the bot does not notice the close,
does not trail, time-exit or re-arm, and does not feed the breakers.

The next monitor pass now rebuilds what was dropped, the same rebuild a
restart does: the active venue's executor, and every other venue whose saved
book holds a position (`_rebind_invalidated_executors`).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor

CREDS = {"api_key": "uk", "api_secret": "us", "passphrase": "up"}


def _store(creds=CREDS):
    return type("S", (), {"get": staticmethod(lambda uid: creds)})()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
    original = engine_mod.CONFIG.per_user_live_enabled
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", True)

    async def _no_sync(_eng):
        return []

    monkeypatch.setattr(engine_mod, "sync_portfolio_with_exchange", _no_sync)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store())
    visits: list = []

    async def _check(self):
        visits.append(self.user_id)
        return []

    async def _none(self):
        return None

    async def _empty(self):
        return []

    monkeypatch.setattr(LiveExecutor, "check_positions", _check)
    monkeypatch.setattr(LiveExecutor, "reconcile_positions", _empty)
    monkeypatch.setattr(LiveExecutor, "verify_and_fix_sltp", _none)
    monkeypatch.setattr(LiveExecutor, "sync_positions_from_exchange", _none)
    try:
        yield visits
    finally:
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


def _pass(eng):
    """One run of the real monitor, with its notifiers stubbed."""
    eng._last_sltp_verify_ts = time.monotonic()
    for name in ("close", "fill", "sync"):
        setattr(eng, f"_{name}_notify_callback", AsyncMock())
    eng._owner_notify_callback = AsyncMock()
    asyncio.run(eng._check_open_positions())


class TestTheMonitorKeepsWatching:
    def test_a_dropped_executor_is_watched_again_on_the_next_pass(self, live):
        eng = RuneClawEngine()
        first = eng._executor_for("555")
        _pass(eng)
        assert live.count("555") == 1
        eng.invalidate_user_executor("555")
        assert "555" not in eng._user_executors
        _pass(eng)
        assert live.count("555") == 2, (
            "the user's open book dropped out of the monitor with the executor")
        rebuilt = eng._user_executors["555"]
        assert rebuilt is not first and rebuilt._credentials == CREDS

    def test_every_other_venue_with_a_book_is_rebuilt_too(self, live,
                                                         monkeypatch):
        eng = RuneClawEngine()
        eng._executor_for("555")
        asked: list = []
        monkeypatch.setattr(eng, "_rehydrate_other_venue_books",
                            lambda ids: asked.append(list(ids)) or [])
        eng.invalidate_user_executor("555")
        _pass(eng)
        assert asked == [["555"]]

    def test_nothing_is_rebuilt_for_a_user_nothing_invalidated(self, live,
                                                              monkeypatch):
        eng = RuneClawEngine()
        built: list = []
        monkeypatch.setattr(eng, "_executor_for",
                            lambda uid, *a, **k: built.append(uid))
        _pass(eng)
        assert built == []

    def test_a_rebuild_happens_once_per_invalidation(self, live, monkeypatch):
        eng = RuneClawEngine()
        built: list = []
        monkeypatch.setattr(eng, "_executor_for",
                            lambda uid, *a, **k: built.append(uid))
        eng.invalidate_user_executor("555")
        _pass(eng)
        _pass(eng)
        assert built == ["555"]


class TestWhatCouldNotBeRebuiltIsSaid:
    def test_a_rebuild_that_raises_is_retried_and_warned_once(
            self, live, monkeypatch, caplog):
        eng = RuneClawEngine()
        calls = {"n": 0}

        def _flaky(uid, *a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("store hiccup")
            return None

        monkeypatch.setattr(eng, "_executor_for", _flaky)
        eng.invalidate_user_executor("555")
        with caplog.at_level(logging.WARNING, logger=engine_mod.logger.name):
            _pass(eng)
            _pass(eng)
            assert "555" in eng._executors_to_rebind
            _pass(eng)
        assert calls["n"] == 3
        assert "555" not in eng._executors_to_rebind
        said = [r for r in caplog.records
                if "Rebuilding the executor for 555" in r.getMessage()]
        assert len(said) == 1, "a failing rebuild warned on every pass"
        assert "555" not in eng._rebind_warned

    def test_a_book_that_could_not_be_rebuilt_is_named(self, live,
                                                      monkeypatch):
        eng = RuneClawEngine()
        seen: list = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda *a, **k: seen.append((a, k)))
        monkeypatch.setattr(eng, "_executor_for", lambda uid, *a, **k: None)
        monkeypatch.setattr(eng, "_rehydrate_other_venue_books",
                            lambda ids: ["bybit/555"])
        eng.invalidate_user_executor("555")
        missing = eng._rebind_invalidated_executors()
        assert missing == ["bybit/555"]
        rows = [(a, k) for a, k in seen if k.get("action") == "per_user_rebind"]
        assert len(rows) == 1
        assert "bybit/555" in rows[0][0][1]
        assert rows[0][1]["result"] == "WARNING"

    def test_a_user_whose_active_venue_raised_is_not_named_as_a_book(
            self, live, monkeypatch):
        eng = RuneClawEngine()
        seen: list = []
        monkeypatch.setattr(engine_mod, "audit",
                            lambda *a, **k: seen.append(k.get("action")))

        def _raise(uid, *a, **k):
            raise RuntimeError("x")

        monkeypatch.setattr(eng, "_executor_for", _raise)
        eng.invalidate_user_executor("555")
        assert eng._rebind_invalidated_executors() == ["555"]
        assert "per_user_rebind" not in seen


class TestTheEdges:
    def test_with_per_user_live_off_the_queue_is_dropped(self, monkeypatch):
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live",
                            lambda self: True)
        eng = RuneClawEngine()
        built: list = []
        monkeypatch.setattr(eng, "_executor_for",
                            lambda uid, *a, **k: built.append(uid))
        eng.invalidate_user_executor("555")
        assert eng._rebind_invalidated_executors() == []
        assert built == [] and not eng._executors_to_rebind

    def test_an_engine_built_without_init_rebuilds_nothing(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        assert eng._rebind_invalidated_executors() == []

    def test_the_website_pull_can_queue_from_its_worker_thread(self, live):
        eng = RuneClawEngine()
        t = threading.Thread(target=eng.invalidate_user_executor, args=("555",))
        t.start()
        t.join()
        assert eng._executors_to_rebind == {"555"}

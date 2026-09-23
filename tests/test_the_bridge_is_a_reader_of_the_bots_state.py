"""The API bridge's engine is a copy of the bot's, and it acted as if it were the bot.

`api_bridge.py` is a SEPARATE process (`uvicorn api_bridge:app`, :8000) and its
lifespan builds its own `RuneClawEngine` over the bot's data directory. That
engine loads the operator's risk state once, at startup, and runs no trading
loop, so nothing ever refreshes it. Three things followed, each driven here
with two engines over one data directory -- which is all two processes share:

  * `/risk/halt` tripped the COPY's breaker, read the copy back, and answered
    "Circuit breaker tripped". The bot's breaker stayed closed and it kept
    accepting entries, and the bot's next ordinary save wrote its own
    `circuit_open: false` over the halt -- a restarted bot came up NOT halted.
  * Any save by the copy wrote the WHOLE operator state from its memory
    (`RuneClawEngine._save_combined_state`). The bot tripped its streak
    breaker; a paper close through the bridge saved; a restarted bot came up
    with the breaker closed and the streak at 0. `PortfolioTracker`'s
    revision guard did not cover it: that guards the portfolio's own file,
    and production saves through the combined one.
  * `/health` and `/risk/status` read the copy's breaker, so with the bot
    halted they said `circuit_breaker_active: false` and nothing blocking.

So the bridge's engine is a READER (`detach_state_persistence`): one writer,
the bot. Its breaker reads come from what the bot SAVED
(`bot.core.persisted_breaker`), with the time it saved it. The halt refuses
and names the door that works, and the two paper routes refuse, because once
the copy cannot save they would answer "confirmed" for positions nobody sees.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import secrets

import pytest

os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

from bot.core.engine import RuneClawEngine  # noqa: E402
from bot.core.persisted_breaker import (  # noqa: E402
    UNSAVED_GATES,
    blocked_by,
    read_persisted_breaker,
)
from bot.utils.models import Direction, TradeIdea  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path)
    # A paper close mirrors the book to the website on a thread; not this
    # suite's subject, and a thread a test starts reaching out is charged to it.
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda *a, **k: None)
    return tmp_path


def _saved(bot):
    return json.load(open(bot._combined_state_file))["risk"]


def _trip(bot):
    for _ in range(5):
        bot.risk.record_live_trade_result(-10.0)
    assert bot.risk.circuit_breaker_active and _saved(bot)["circuit_open"]


def _paper_round_trip(engine):
    idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=98.0, take_profit=106.0,
                     confidence=0.7, reasoning="bridge")
    t = engine.portfolio.open_position(idea, 50.0)
    engine.portfolio.close_position(t.trade_id, 101.0)


# ── one writer ───────────────────────────────────────────────────────────

class TestTheBridgeNeverWritesTheBotsState:
    def test_the_fixture_reaches_the_defect(self, data_dir):
        # Without the detach, the copy's save erases the bot's breaker. A
        # fixture that cannot produce the state it names measures nothing.
        bot, copy = RuneClawEngine(), RuneClawEngine()
        _trip(bot)
        _paper_round_trip(copy)
        assert _saved(bot)["circuit_open"] is False

    def test_a_reader_cannot_erase_a_tripped_breaker(self, data_dir):
        bot, bridge = RuneClawEngine(), RuneClawEngine()
        bridge.detach_state_persistence()
        _trip(bot)
        _paper_round_trip(bridge)
        bridge.risk.emergency_halt("a copy's halt")
        bridge.risk.record_live_trade_result(+50.0)
        risk = _saved(bot)
        assert risk["circuit_open"] is True
        assert risk["consecutive_losses"] == 5
        assert RuneClawEngine().risk.circuit_breaker_active is True

    def test_a_reader_does_not_fall_back_to_the_risk_file(self, data_dir):
        # `RiskEngine._save_state` writes its own file when the combined
        # saver RAISES. The reader returns, so there is no second door.
        bridge = RuneClawEngine()
        bridge.detach_state_persistence()
        before = sorted(p.name for p in (data_dir / "data").iterdir())
        bridge.risk.emergency_halt("a copy's halt")
        after = sorted(p.name for p in (data_dir / "data").iterdir())
        assert after == before

    def test_the_bot_is_still_the_writer(self, data_dir):
        bot, bridge = RuneClawEngine(), RuneClawEngine()
        bridge.detach_state_persistence()
        _trip(bot)
        assert _saved(bot)["circuit_open"] is True


# ── the reading ──────────────────────────────────────────────────────────

def _write(path, payload):
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


_RISK = {"circuit_open": True, "consecutive_losses": 5, "last_loss_time": None,
         "circuit_breaker_trips": 1, "circuit_trip_cause": "streak",
         "circuit_trip_day": ""}


class TestTheSavedBreakerReading:
    def test_absent(self, tmp_path):
        assert read_persisted_breaker(tmp_path / "nope.json") == {"state": "absent"}

    @pytest.mark.parametrize("payload", [
        "{not json", "[]", json.dumps({"risk": []}),
        json.dumps({"risk": {"consecutive_losses": 1}}),        # no circuit_open
        json.dumps({"risk": {**_RISK, "circuit_open": "false"}}),  # a spelling
    ], ids=["junk", "a-list", "risk-not-a-map", "no-circuit-open", "string-bool"])
    def test_unreadable_is_never_not_halted(self, tmp_path, payload):
        r = read_persisted_breaker(_write(tmp_path / "s.json", payload))
        assert r == {"state": "unreadable"}
        assert blocked_by(r) == ""

    def test_a_path_that_is_not_a_path_is_unreadable(self):
        assert read_persisted_breaker(None) == {"state": "unreadable"}

    def test_read(self, tmp_path):
        r = read_persisted_breaker(_write(tmp_path / "s.json", {
            "risk": _RISK, "written_at": "2026-09-23T17:00:00+00:00"}))
        assert r == {"state": "read", "circuit_open": True, "cause": "streak",
                     "consecutive_losses": 5,
                     "saved_at": "2026-09-23T17:00:00+00:00"}
        assert blocked_by(r) == "streak"

    def test_a_saved_at_that_is_not_text_is_not_a_time(self, tmp_path):
        r = read_persisted_breaker(_write(tmp_path / "s.json",
                                          {"risk": _RISK, "written_at": 12}))
        assert r["saved_at"] is None

    @pytest.mark.parametrize("risk,expect", [
        ({**_RISK, "circuit_trip_cause": ""}, "circuit"),
        ({**_RISK, "circuit_open": False}, ""),
    ], ids=["open-no-cause", "closed"])
    def test_blocked_by_speaks_the_bots_words(self, tmp_path, risk, expect):
        r = read_persisted_breaker(_write(tmp_path / "s.json", {"risk": risk}))
        assert blocked_by(r) == expect


# ── the routes ───────────────────────────────────────────────────────────

@pytest.fixture
def bridge_app(data_dir, monkeypatch):
    import api_bridge
    bot, copy = RuneClawEngine(), RuneClawEngine()
    copy.detach_state_persistence()
    monkeypatch.setattr(api_bridge, "engine", copy, raising=False)
    return api_bridge, bot, copy


def _body(res):
    if hasattr(res, "body"):
        return res.status_code, json.loads(res.body)
    return 200, res


class TestTheBridgeReportsTheBotsBreaker:
    def test_health_reports_the_breaker_the_bot_tripped(self, bridge_app):
        mod, bot, copy = bridge_app
        _trip(bot)
        assert copy.risk.circuit_breaker_active is False    # the stale copy
        body = asyncio.run(mod.health())
        assert body["circuit_breaker_read"] == "read"
        assert body["circuit_breaker_active"] is True
        assert body["trading_blocked_by"] == "streak"
        assert body["consecutive_losses"] == 5
        assert body["circuit_breaker_saved_at"]

    def test_a_clear_saved_breaker_is_reported_clear_and_never_complete(
            self, bridge_app):
        mod, bot, _ = bridge_app
        bot.risk.record_live_trade_result(-1.0)          # a save, breaker closed
        body = asyncio.run(mod.health())
        assert body["circuit_breaker_active"] is False
        assert body["trading_blocked_by"] == ""
        # What this process cannot read is said on every answer.
        assert body["trading_gate_unknown"] is True
        assert UNSAVED_GATES in body["trading_gate_scope"]

    @pytest.mark.parametrize("contents", [None, "{torn"], ids=["absent", "unreadable"])
    def test_no_saved_breaker_is_not_a_clear_one(self, bridge_app, contents):
        mod, bot, _ = bridge_app
        p = pathlib.Path(bot._combined_state_file)
        if p.exists():
            p.unlink()
        if contents is not None:
            p.write_text(contents)
        body = asyncio.run(mod.health())
        assert "circuit_breaker_active" not in body
        assert body["circuit_breaker_read"] == ("absent" if contents is None
                                                else "unreadable")
        assert body["trading_gate_unknown"] is True

    def test_health_no_longer_counts_the_copys_book(self, bridge_app):
        mod, _, _ = bridge_app
        assert "open_positions" not in asyncio.run(mod.health())

    def test_risk_status_reports_the_bots_breaker_and_none_of_the_copys(
            self, bridge_app):
        mod, bot, _ = bridge_app
        _trip(bot)
        body = asyncio.run(mod.risk_status(_token="t"))
        assert body["circuit_breaker_active"] is True
        assert body["consecutive_losses"] == 5
        for gone in ("warning_rate_breaker_active", "stats", "rejection_history"):
            assert gone not in body, gone


class TestTheBridgeCannotHaltTheBot:
    def test_the_halt_refuses_and_says_nothing_was_halted(self, bridge_app):
        mod, bot, copy = bridge_app
        status, body = _body(asyncio.run(mod.risk_halt(_token="t", _rl=None)))
        assert status == 501
        assert body["ok"] is False and body["halted"] is False
        assert body["closed_positions"] is False
        assert body["message"].startswith("Nothing was halted.")
        assert "/halt" in body["message"] and "/emergency_stop" in body["message"]

    def test_the_halt_does_not_trip_the_copy_either(self, bridge_app):
        # A copy's breaker tripped is what made the old answer read "tripped".
        mod, _, copy = bridge_app
        asyncio.run(mod.risk_halt(_token="t", _rl=None))
        assert copy.risk.circuit_breaker_active is False

    def test_the_halt_reports_the_bots_saved_breaker(self, bridge_app):
        mod, bot, _ = bridge_app
        _trip(bot)
        _, body = _body(asyncio.run(mod.risk_halt(_token="t", _rl=None)))
        assert body["circuit_breaker_active"] is True
        assert body["circuit_breaker_read"] == "read"

    def test_the_old_answer_was_a_copy_answering(self, data_dir):
        # The defect, driven on engines alone: the copy says tripped, the bot
        # is not, and the bot's next save erases what the copy wrote.
        bot, copy = RuneClawEngine(), RuneClawEngine()
        copy.risk.emergency_halt("Emergency halt from dashboard")
        assert copy.risk.circuit_breaker_active is True
        assert bot.risk.circuit_breaker_active is False
        bot.risk.record_live_trade_result(-1.0)
        assert _saved(bot)["circuit_open"] is False


class TestThePaperRoutesRefuse:
    def test_confirm_opens_nothing(self, bridge_app):
        mod, _, copy = bridge_app
        req = mod.ConfirmRequest(trade_id="T1", asset="BTC/USDT", direction="LONG",
                                 entry_price=100.0, stop_loss=98.0,
                                 take_profit=106.0)
        status, body = _body(asyncio.run(mod.confirm_trade(req, _token="t", _rl=None)))
        assert status == 410 and body["ok"] is False
        assert body["message"].startswith("Nothing was opened.")
        assert copy.portfolio.open_positions == []

    def test_close_closes_nothing(self, bridge_app):
        mod, _, _ = bridge_app
        status, body = _body(asyncio.run(
            mod.close_position("BTC/USDT", _token="t", _rl=None)))
        assert status == 410 and body["ok"] is False
        assert body["message"].startswith("Nothing was closed.")


def test_the_lifespan_makes_the_bridges_engine_a_reader(data_dir, monkeypatch):
    import api_bridge
    # The lifespan assigns the module global; restore it after.
    monkeypatch.setattr(api_bridge, "engine", api_bridge.engine, raising=False)

    async def _run():
        async with api_bridge.lifespan(api_bridge.app):
            return api_bridge.engine

    eng = asyncio.run(_run())
    assert getattr(eng, "_state_persistence_detached", False) is True
    # And it behaves as one: the lifespan's engine cannot write a breaker.
    eng.risk.emergency_halt("through the lifespan's engine")
    assert not os.path.exists(eng._combined_state_file)

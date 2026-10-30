"""The paper sleeve's rebalance, posted to the marketing groups as a paper call.

Off unless SLEEVE_CHANNEL_CALLS_ENABLED. Driven through the real `advance`,
the real engine hook bound to a recorder, and the real `start_monitor`.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.sleeve.paper import channel_call_text, state_file
from tests.test_the_sleeve_paper_book_places_nothing import R, make_closes, run_days


def _book_at_first_rebalance():
    """A record whose newest closed day is the first rebalance, BTC trending
    up so the regime allows a momentum book."""
    closes = make_closes(R + timedelta(days=3))
    state, _ = run_days(closes, R, R + timedelta(days=1))
    return closes, state


@pytest.fixture
def calls_flag():
    was = CONFIG.sleeve_channel_calls_enabled

    def _set(on: bool) -> None:
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", on)
    yield _set
    object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was)


# ── the text ──────────────────────────────────────────────────────────────

def test_a_rebalance_that_changes_the_book_is_a_call():
    _, state = _book_at_first_rebalance()
    assert state["rebalances"][-1]["day"] == state["last_day"]
    text = channel_call_text(state)
    assert text is not None
    assert f"At the {R.isoformat()} close" in text
    assert "Paper only: no order was placed" in text and "Not investment advice" in text
    assert "$" not in text, "a public room gets percent only"
    held = state["rebalances"][-1]["target"]
    assert held and all(sym in text for sym in held)


def test_the_next_day_without_a_rebalance_posts_nothing():
    closes, state = _book_at_first_rebalance()
    state, _ = run_days(closes, R + timedelta(days=2), R + timedelta(days=2), state=state)
    assert state["last_day"] != state["rebalances"][-1]["day"]
    assert channel_call_text(state) is None


def test_a_rebalance_that_trades_nothing_posts_nothing():
    _, state = _book_at_first_rebalance()
    state["rebalances"][-1]["turnover"] = 0.0
    assert channel_call_text(state) is None
    state["rebalances"][-1]["turnover"] = None
    assert channel_call_text(state) is None, "an unread turnover is not a trade"


def test_a_move_to_cash_says_all_cash():
    _, state = _book_at_first_rebalance()
    state["rebalances"][-1].update(target={}, turnover=1.0, reason="BTC is below its 150-day mean: cash")
    assert "all cash" in channel_call_text(state)


def test_an_unread_index_shows_no_figure():
    _, state = _book_at_first_rebalance()
    state["sleeve_index"] = None
    text = channel_call_text(state)
    assert "the index does not read" in text and "sleeve +" not in text and "sleeve -" not in text


# ── the engine hook ───────────────────────────────────────────────────────

def _engine_with(cb):
    eng = NS(_sleeve_call_callback=cb)
    eng._announce_sleeve_call = RuneClawEngine._announce_sleeve_call.__get__(eng)
    return eng


def _write(tmp_path, monkeypatch, state):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def test_the_call_is_handed_on_only_while_the_switch_is_on(tmp_path, monkeypatch, calls_flag):
    _, state = _book_at_first_rebalance()
    _write(tmp_path, monkeypatch, state)
    cb = AsyncMock()
    eng = _engine_with(cb)
    calls_flag(False)
    assert asyncio.run(eng._announce_sleeve_call()) is False
    cb.assert_not_awaited()
    calls_flag(True)
    assert asyncio.run(eng._announce_sleeve_call()) is True
    cb.assert_awaited_once_with(channel_call_text(state))


def test_an_unreadable_record_posts_nothing(tmp_path, monkeypatch, calls_flag):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    calls_flag(True)
    cb = AsyncMock()
    assert asyncio.run(_engine_with(cb)._announce_sleeve_call()) is False
    cb.assert_not_awaited()


def test_a_failed_post_is_reported_not_raised(tmp_path, monkeypatch, calls_flag):
    _, state = _book_at_first_rebalance()
    _write(tmp_path, monkeypatch, state)
    calls_flag(True)
    cb = AsyncMock(side_effect=RuntimeError("telegram down"))
    assert asyncio.run(_engine_with(cb)._announce_sleeve_call()) is False


def test_the_switch_ships_off():
    import inspect

    import bot.config as config
    src = inspect.getsource(config)
    assert '_env_bool("SLEEVE_CHANNEL_CALLS_ENABLED", False)' in src


# ── the wiring ────────────────────────────────────────────────────────────

def test_the_monitor_hands_the_engine_the_forwarders_poster():
    """Driven through the real `start_monitor`."""
    from tests.test_an_alert_is_in_the_transcript import ALICE, _started, _store, _Users
    fwd = NS(set_bot=lambda b: None, post_signal=AsyncMock(), post_trade_opened=AsyncMock(),
             post_trade_closed=AsyncMock(), post_sleeve_call=AsyncMock())
    w = _started(_store(), _Users([ALICE]), ALICE, forwarder=fwd)
    try:
        asyncio.run(w.engine.cbs["sleeve"]("call"))
    finally:
        w.restore()
    fwd.post_sleeve_call.assert_awaited_once_with("call")

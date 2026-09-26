"""A closed-trade record the bot could not read is not published as the whole.

`/api/bot/sync` REPLACES the agent's record: `app/routes/sync.js` deletes every
operator trade row and inserts whatever list the payload carries. So the list
the bot sends is published as the whole history, on the public track record,
the portfolio summary and the operator's dashboard.

When the closed-trade file will not parse, or holds a row the loader cannot
read, `LiveExecutor` says so (`closed_trades_read_failed`) and holds an empty
or partial list. `_sync_live_state_to_website` never asked, and it runs at
boot and on every open and close: driven, a file that would not parse was
pushed as `closed_trades: []`, and the website deleted every trade it held.

Every test here builds a REAL executor over a planted file, so the flag the
engine reads is the one the loader sets.
"""
from __future__ import annotations

import json
import logging

import pytest

import bot.utils.website_sync as ws
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor

UID = "9"
CREDS = {"api_key": "a", "api_secret": "b", "passphrase": "c"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def wire(monkeypatch):
    """Every request the sync would put on the wire, sent inline."""
    posts: list = []
    monkeypatch.setattr(
        ws, "_post",
        lambda path, data, **kw: posts.append((path, data)) or {"ok": True})
    monkeypatch.setattr(ws, "sync_in_background",
                        lambda *a: ws.sync_portfolio(*a))
    return posts


def _row(tid, opened="2026-09-20T00:00:00+00:00"):
    return {"trade_id": tid, "symbol": "BTC/USDT:USDT", "direction": "LONG",
            "entry_price": 100.0, "quantity": 1.0, "cost_usd": 20.0,
            "stop_loss": 95.0, "take_profit": 110.0, "leverage": 5,
            "close_price": 105.0, "pnl_usd": 5.0, "opened_at": opened,
            "closed_at": "2026-09-21T00:00:00+00:00", "venue": "bitget",
            "close_reason": "TP HIT"}


def _plant(state, text):
    (state / f"closed_trades_{UID}.json").write_text(text)


def _sync(ex):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = ex
    eng.resolve_display_equity_sync = lambda: (812.5, "live")
    eng._sync_live_state_to_website()
    return eng


def test_a_record_that_will_not_parse_is_not_pushed(state, wire):
    _plant(state, "{not json")
    ex = LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")
    assert ex.closed_trades_read_failed
    _sync(ex)
    assert wire == [], (
        "a record nobody could read was pushed, and the website replaces "
        f"the agent's whole history with it: {wire}")


def test_a_partially_read_record_is_not_pushed(state, wire):
    _plant(state, json.dumps([_row("T1"), _row("T2", opened="not a time"),
                              _row("T3")]))
    ex = LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")
    assert [p.trade_id for p in ex.closed_positions] == ["T1", "T3"]
    assert ex.closed_trades_read_failed
    _sync(ex)
    assert wire == [], "two of three closes were published as the record"


def test_a_readable_record_is_still_pushed(state, wire):
    _plant(state, json.dumps([_row("T1"), _row("T3")]))
    ex = LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")
    assert not ex.closed_trades_read_failed
    _sync(ex)
    assert len(wire) == 1
    path, body = wire[0]
    assert path == "/api/bot/sync"
    assert len(body["closed_trades"]) == 2
    assert body["equity"] == 812.5


def test_no_record_yet_is_a_real_empty_list(state, wire):
    """An absent file is a bot that has closed nothing -- a reading, and the
    one case where an empty list IS the whole record."""
    ex = LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")
    assert not ex.closed_trades_read_failed
    _sync(ex)
    assert len(wire) == 1 and wire[0][1]["closed_trades"] == []


def test_the_refusal_is_said_once_and_names_why(state, wire, caplog):
    _plant(state, "{not json")
    ex = LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")
    caplog.set_level(logging.WARNING, logger="bot.utils.website_sync")
    eng = _sync(ex)
    eng._sync_live_state_to_website()
    said = [r for r in caplog.records
            if "closed-trade record" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in said]
    msg = said[0].getMessage()
    assert said[0].levelno == logging.WARNING
    assert "not published" in msg and "keeps" in msg, msg
    assert wire == []


def test_a_second_unreadable_record_is_said_too(state, wire, caplog):
    """Once per FILE, not once per process: a second account's record going
    unreadable is a new fact, and silencing it behind the first would leave
    the operator reading one warning for two broken files."""
    for uid in ("9", "10"):
        (state / f"closed_trades_{uid}.json").write_text("{not json")
    caplog.set_level(logging.WARNING, logger="bot.utils.website_sync")
    for uid in ("9", "10"):
        _sync(LiveExecutor(user_id=uid, credentials=CREDS, venue="bitget"))
    said = [r for r in caplog.records
            if "closed-trade record" in r.getMessage()]
    assert len(said) == 2, [r.getMessage() for r in said]
    assert wire == []

"""The Daily Duel's own Telegram bot is its own process, and never the main bot's.

`bot/duel_bot.py` runs beside `bot.main` on a second BotFather token with no
engine and none of the main bot's state. Three things are held here, and each
is a thing a source scan could not see on its own:

1. It refuses to run on the main bot's token, or without one, with the exit
   status the unit treats as "do not restart" — and the line it prints names
   the variable, never the value.
2. Its handlers are driven: /duel renders the card and offers each round its
   own words, a tap records the call and answers the query, /link is private
   and posts the token with the bot secret, and an unreadable card is said to
   be unreadable rather than drawn empty.
3. It touches nothing of the main bot's: no engine, no SQLite store, no JSON
   user store, no PID file of the main bot's, no audit chain. A stale PID of
   its own is recognised only by the command line that wrote it.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot import duel_bot as db
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = code_only((ROOT / "bot" / "duel_bot.py").read_text(encoding="utf-8"))
UNIT = (ROOT / "scripts" / "systemd" / "runeclaw-duel.service").read_text(encoding="utf-8")
TOKEN = "123456:" + "A" * 30
OTHER = "654321:" + "B" * 30
SITE = "https://site.test"

CARD = {
    "day": "2026-10-19", "horizon_hours": 24, "open": 2,
    "rounds": [
        {"id": 1, "kind": "direction", "symbol": "BTCUSDT", "callable": True, "options": ["long", "short", "pass"]},
        {"id": 3, "kind": "range", "symbol": "BNBUSDT", "threshold_pct": 2, "callable": True,
         "options": ["break", "hold", "pass"]},
    ],
    "accuracy": {"pct": None, "scored": 0, "correct": 0, "unresolved": 1},
    "marks": {"marks": 0, "pending": 1, "beat_agent": 0},
    "streak": {"current": 2},
}


def _update(*, chat_type="private", data=None, user_id=777):
    msg = NS(reply_text=AsyncMock())
    query = NS(data=data, answer=AsyncMock(), message=msg) if data is not None else None
    return NS(effective_user=NS(id=user_id, username="p"),
              effective_chat=NS(id=user_id, type=chat_type),
              effective_message=msg, message=msg, callback_query=query)


def _ctx(*args):
    return NS(args=list(args), bot=NS(username="RuneclawDuelBot"))


def _texts(update) -> list[str]:
    return [c.args[0] for c in update.effective_message.reply_text.await_args_list]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ── configuration ──────────────────────────────────────────────────────────

def test_the_token_must_be_set_and_its_own_and_the_message_never_carries_it():
    assert db.token_problem({}) is not None
    assert "DUEL_BOT_TOKEN" in db.token_problem({"DUEL_BOT_TOKEN": "  "})
    same = db.token_problem({"DUEL_BOT_TOKEN": TOKEN, "TELEGRAM_BOT_TOKEN": TOKEN})
    assert same and "TELEGRAM_BOT_TOKEN" in same and "409" in same
    # Quoted as a hand-edited .env quotes it, it is still the same token.
    assert db.token_problem({"DUEL_BOT_TOKEN": f'"{TOKEN}"', "TELEGRAM_BOT_TOKEN": TOKEN})
    shape = db.token_problem({"DUEL_BOT_TOKEN": "not-a-token", "TELEGRAM_BOT_TOKEN": OTHER})
    assert shape and "shape" in shape
    assert db.token_problem({"DUEL_BOT_TOKEN": TOKEN, "TELEGRAM_BOT_TOKEN": OTHER}) is None
    assert db.token_problem({"DUEL_BOT_TOKEN": TOKEN}) is None
    for env in ({"DUEL_BOT_TOKEN": TOKEN, "TELEGRAM_BOT_TOKEN": TOKEN},
                {"DUEL_BOT_TOKEN": "not-a-token", "TELEGRAM_BOT_TOKEN": OTHER}):
        msg = db.token_problem(env) or ""
        assert TOKEN not in msg and "not-a-token" not in msg and OTHER not in msg, "a journal line carries the token"
    assert db.duel_token({"DUEL_BOT_TOKEN": f" '{TOKEN}' "}) == TOKEN


def test_the_bot_channel_secret_is_required():
    assert "BOT_SYNC_SECRET" in (db.channel_problem({}) or "")
    assert db.channel_problem({"BOT_SYNC_SECRET": "s" * 40}) is None


def test_exit_status_78_is_the_one_the_unit_does_not_restart():
    """One reading: the number the process exits with when it is not
    configured is the number the unit refuses to restart on."""
    assert db.EXIT_NOT_CONFIGURED == 78
    m = re.search(r"^RestartPreventExitStatus=(\d+)$", UNIT, re.M)
    assert m and int(m.group(1)) == db.EXIT_NOT_CONFIGURED, "the unit would loop on a missing token"
    assert re.search(r"^Restart=always$", UNIT, re.M)


def test_the_module_builds_no_engine_and_reads_none_of_the_main_bots_state():
    for forbidden in ("bot.core.engine", "RuneClawEngine", "bot.db", "get_user_by_chat_id",
                      "bot.skills.telegram_handler", "runeclaw.pid", "UserStore", "audit("):
        assert forbidden not in SRC, f"the duel bot reaches {forbidden!r}"
    # It reuses the duel's channel and card, which are the website's rules.
    assert "bot.utils.duel_pull" in SRC
    assert "bot.formatters.duel_card" in SRC
    # Its PID file is its own.
    assert "runeclaw-duel.pid" in SRC


def test_a_stale_instance_is_recognised_only_by_its_own_command_line(tmp_path):
    pidfile = tmp_path / "runeclaw-duel.pid"
    pidfile.write_text("4242")
    mine = lambda pid: b"/usr/bin/python3\x00-m\x00bot.duel_bot\x00"  # noqa: E731
    theirs = lambda pid: b"/usr/bin/python3\x00-m\x00bot.main\x00--mode\x00telegram\x00"  # noqa: E731
    assert db.stale_duel_pid(str(pidfile), alive=lambda pid: True, cmdline=mine) == 4242
    assert db.stale_duel_pid(str(pidfile), alive=lambda pid: True, cmdline=theirs) is None, \
        "a reused number that now belongs to the main bot must never be killed"
    assert db.stale_duel_pid(str(pidfile), alive=lambda pid: False, cmdline=mine) is None
    assert db.stale_duel_pid(str(tmp_path / "missing.pid")) is None
    pidfile.write_text("garbage")
    assert db.stale_duel_pid(str(pidfile)) is None


def test_health_answers_200_only_while_polling_and_not_stopping():
    assert db.health_body({"polling": True, "stopping": False})[0] == 200
    assert db.health_body({"polling": False, "stopping": False})[0] == 503
    assert db.health_body({"polling": True, "stopping": True})[0] == 503
    status, body = db.health_body({"polling": True, "stopping": False, "updates": 3})
    assert body["ok"] is True and body["updates_seen"] == 3 and body["service"] == "runeclaw-duel"


def test_the_limiter_counts_per_key_within_its_window():
    now = [0.0]
    rl = db.RateLimit(2, 60.0, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a")
    assert not rl.allow("a")
    assert rl.allow("b"), "another chat is not limited by the first"
    now[0] = 61.0
    assert rl.allow("a")


# ── the wiring ─────────────────────────────────────────────────────────────

def test_build_app_registers_the_commands_and_the_duel_callback():
    from telegram.ext import CallbackQueryHandler, CommandHandler, TypeHandler
    app = db.DuelBot(site_url=SITE).build_app(TOKEN)
    cmds = set()
    callbacks = []
    counters = []
    for group, handlers in app.handlers.items():
        for h in handlers:
            if isinstance(h, CommandHandler):
                cmds |= set(h.commands)
            elif isinstance(h, CallbackQueryHandler):
                callbacks.append((group, h))
            elif isinstance(h, TypeHandler):
                counters.append(group)
    assert cmds == {"start", "help", "duel", "me", "board", "link"}
    assert len(callbacks) == 1
    assert callbacks[0][1].pattern.pattern == r"^duel:"
    assert counters == [-1], "every update is counted ahead of the handlers"


def test_duel_renders_the_card_and_offers_each_round_its_own_words():
    calls = []
    bot = db.DuelBot(fetch_card=lambda tg: (calls.append(tg), (200, CARD))[1], site_url=SITE)
    u = _update()
    _run(bot.cmd_duel(u, _ctx()))
    assert calls == ["777"]
    text = _texts(u)[0]
    assert "BTCUSDT" in text and "BNBUSDT" in text and "Range" in text
    assert "Accuracy: —" in text and "0%" not in text
    markup = u.effective_message.reply_text.await_args_list[0].kwargs["reply_markup"]
    rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
    assert rows == [
        [("BTC LONG", "duel:1:long"), ("SHORT", "duel:1:short"), ("PASS", "duel:1:pass")],
        [("BNB BREAKS ±2%", "duel:3:break"), ("HOLDS ±2%", "duel:3:hold"), ("PASS", "duel:3:pass")],
    ]


def test_an_unlinked_player_is_told_to_link_and_an_unreadable_card_is_said_to_be():
    u = _update()
    unlinked = db.DuelBot(fetch_card=lambda tg: (404, {"error": "No linked web account"}), site_url=SITE)
    _run(unlinked.cmd_duel(u, _ctx()))
    assert "/link" in _texts(u)[0] and SITE in _texts(u)[0]
    u = _update()
    _run(db.DuelBot(fetch_card=lambda tg: (None, None), site_url=SITE).cmd_duel(u, _ctx()))
    assert "could not be read" in _texts(u)[0].lower()
    assert "no rounds" not in _texts(u)[0].lower()
    u = _update()
    down = db.DuelBot(fetch_card=lambda tg: (503, {"error": "The duel card is unavailable"}), site_url=SITE)
    _run(down.cmd_duel(u, _ctx()))
    assert "could not be read" in _texts(u)[0].lower()


def test_a_tap_records_the_call_answers_the_query_and_redraws_the_card():
    placed = []
    bot = db.DuelBot(
        fetch_card=lambda tg: (200, CARD),
        place_pick=lambda tg, rid, pick: (placed.append((tg, rid, pick)),
                                          (200, {"ok": True, "kind": "range", "pick": "hold", "symbol": "BNBUSDT",
                                                 "threshold_pct": 2, "entry_price": 600.0,
                                                 "resolves_at": "2026-10-20T12:00:00.000Z"}))[1],
        site_url=SITE)
    u = _update(data="duel:3:hold")
    _run(bot.on_duel_callback(u, _ctx()))
    assert placed == [("777", 3, "hold")]
    u.callback_query.answer.assert_awaited()
    texts = _texts(u)
    assert "Call recorded" in texts[0] and "HOLDS ±2%" in texts[0]
    assert "Daily Duel" in texts[1], "the card is drawn again after the call"
    # A refusal is the server's sentence, and nothing reads as recorded.
    u = _update(data="duel:3:hold")
    bot2 = db.DuelBot(fetch_card=lambda tg: (200, CARD), site_url=SITE,
                      place_pick=lambda tg, rid, pick: (409, {"error": "You have already called this round."}))
    _run(bot2.on_duel_callback(u, _ctx()))
    assert _texts(u)[0] == "You have already called this round."
    # A token that does not parse is refused, not guessed.
    u = _update(data="duel:x:maybe")
    seen = []
    _run(db.DuelBot(place_pick=lambda *a: (seen.append(a), (200, {}))[1], site_url=SITE).on_duel_callback(u, _ctx()))
    assert seen == [] and _texts(u) == []


def test_link_is_private_only_and_posts_the_token_with_the_bot_secret(monkeypatch):
    monkeypatch.setenv("BOT_SYNC_SECRET", "s" * 40)
    monkeypatch.setenv("WEBSITE_URL", SITE)
    # In a room: the notice, and no request.
    asked = []
    bot = db.DuelBot(site_url=SITE,
                     validate_link=lambda token, chat: (asked.append((token, chat)), (200, {"user_id": 1}))[1])
    u = _update(chat_type="supergroup")
    _run(bot.cmd_link(u, _ctx("tok123")))
    assert asked == []
    assert "🔒" in _texts(u)[0] and "/link" in _texts(u)[0]
    # No token: the prompt, with the site.
    u = _update()
    _run(bot.cmd_link(u, _ctx()))
    assert asked == [] and SITE in _texts(u)[0]
    # A token: posted for THIS chat, and the website's answer rendered.
    u = _update()
    _run(bot.cmd_link(u, _ctx("tok123")))
    assert asked == [("tok123", "777")]
    assert "Linked" in _texts(u)[0]
    # The request itself: the website's link endpoint, the secret in the header.
    req = db.link_request("tok123", "777", {"BOT_SYNC_SECRET": "s" * 40})
    assert req.full_url == f"{SITE}/api/auth/validate-token"
    assert req.get_method() == "POST"
    assert req.get_header("X-bot-secret") == "s" * 40
    assert json.loads(req.data.decode()) == {"token": "tok123", "chat_id": "777"}


@pytest.mark.parametrize("status,body,want", [
    (404, {"error": "invalid"}, "invalid or expired"),
    (409, {"error": "telegram_already_linked"}, "already linked"),
    (401, {"error": "unauthorized"}, "BOT_SYNC_SECRET"),
    (None, None, "Could not reach"),
    (500, None, "Could not validate"),
    (200, {"user_id": 9, "email": "p@example.com", "plan": "free"}, "Linked"),
])
def test_every_link_outcome_is_its_own_sentence_and_none_leaks_the_account(status, body, want):
    text = db.link_outcome_text(status, body, SITE)
    assert want in text, text
    assert "p@example.com" not in text


def test_me_and_board_read_counts_only():
    u = _update()
    _run(db.DuelBot(fetch_card=lambda tg: (200, CARD), site_url=SITE).cmd_me(u, _ctx()))
    me = _texts(u)[0]
    assert "Accuracy: —" in me and "1 still running" in me and "🔥 2" in me
    assert "$" not in me
    board = {"season": "2026-10", "rows": [{"rank": 1, "handle": "cap", "accuracy_pct": 62.5, "marks": 14}],
             "warming_up": 3, "min_calls": 5}
    u = _update()
    _run(db.DuelBot(fetch_board=lambda: (200, board), site_url=SITE).cmd_board(u, _ctx()))
    out = _texts(u)[0]
    assert "1. cap — 62.5% · 14 marks" in out and "3 more still warming up" in out
    u = _update()
    _run(db.DuelBot(fetch_board=lambda: (503, None), site_url=SITE).cmd_board(u, _ctx()))
    assert "could not be read" in _texts(u)[0]
    assert "Nobody" not in _texts(u)[0], "an unreadable board must not read as an empty one"


def test_commands_are_rate_limited_per_chat():
    now = [0.0]
    bot = db.DuelBot(fetch_card=lambda tg: (200, CARD), site_url=SITE, clock=lambda: now[0])
    bot.commands = db.RateLimit(1, 60.0, clock=lambda: now[0])
    u = _update()
    _run(bot.cmd_duel(u, _ctx()))
    _run(bot.cmd_duel(u, _ctx()))
    texts = _texts(u)
    assert "Daily Duel" in texts[0] and "Slow down" in texts[1]

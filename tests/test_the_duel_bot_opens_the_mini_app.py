"""The Duel bot opens the Daily Duel's Telegram Mini App, and only where it can.

The Mini App is the website's /tg (app/routes/tgapp.js), signed in with the
launch data Telegram signs (app/lib/tg_webapp.js). The bot's part is a
button, and three things are held here:

1. It is off unless DUEL_WEBAPP_URL names an https address: the website must
   also know the bot's id to check a launch, so the operator turns both on
   together, and Telegram opens nothing over plain http.
2. The button is a web_app button, sent in a private chat only: Telegram
   refuses one in a group, and the reply says where to open it instead.
3. /start mentions /app only when there is an app to open.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot import duel_bot as db

SITE = "https://site.test"
APP = "https://site.test/tg"


def _update(chat_type="private"):
    msg = NS(reply_text=AsyncMock())
    return NS(effective_user=NS(id=777, username="p"), effective_chat=NS(id=777, type=chat_type),
              effective_message=msg, message=msg, callback_query=None)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _sent(update):
    call = update.effective_message.reply_text.await_args_list[0]
    return call.args[0], call.kwargs.get("reply_markup")


@pytest.mark.parametrize("raw, want", [
    (APP, APP),
    ("  https://site.test/tg  ", APP),
    ("https://site.test:8443/tg?x=1", "https://site.test:8443/tg?x=1"),
    ("http://site.test/tg", None),
    ("https://site.test", None),
    ("javascript:alert(1)", None),
    ("https://site.test/tg\"><b>", None),
    ("", None),
])
def test_the_app_is_off_unless_an_https_address_is_set(raw, want):
    assert db.webapp_url({"DUEL_WEBAPP_URL": raw}) == want
    assert db.webapp_url({}) is None


def test_start_offers_the_app_in_a_private_chat_only(monkeypatch):
    monkeypatch.setenv("DUEL_WEBAPP_URL", APP)
    bot = db.DuelBot(site_url=SITE)
    u = _update()
    _run(bot.cmd_start(u, None))
    text, markup = _sent(u)
    assert "/app — the Duel as an app here in Telegram" in text
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == APP and button.callback_data is None and button.url is None
    g = _update("group")
    _run(bot.cmd_start(g, None))
    text, markup = _sent(g)
    assert markup is None, "Telegram refuses a web_app button in a group"


def test_without_an_app_start_says_nothing_of_one_and_app_says_so(monkeypatch):
    monkeypatch.delenv("DUEL_WEBAPP_URL", raising=False)
    bot = db.DuelBot(site_url=SITE)
    u = _update()
    _run(bot.cmd_start(u, None))
    text, markup = _sent(u)
    assert "/app" not in text and markup is None
    a = _update()
    _run(bot.cmd_app(a, None))
    text, markup = _sent(a)
    assert text == "The Duel app is not switched on here yet. /duel plays the same card." and markup is None


def test_app_sends_the_button_and_a_group_is_told_where_to_open_it(monkeypatch):
    monkeypatch.setenv("DUEL_WEBAPP_URL", APP)
    bot = db.DuelBot(site_url=SITE)
    u = _update()
    _run(bot.cmd_app(u, None))
    text, markup = _sent(u)
    assert "no wallet" in text and "nothing to buy" in text
    assert markup.inline_keyboard[0][0].web_app.url == APP
    g = _update("supergroup")
    _run(bot.cmd_app(g, None))
    text, markup = _sent(g)
    assert text == "Open the Duel app from a private chat with this bot." and markup is None

"""/me and the bridge's /auth/me print no balance nobody recorded.

Both read `user_portfolio`, and nothing writes a figure to it: its three
inserts write the column defaults, and `save_user_portfolio`'s one caller
(`UserContext.save_portfolio`) had no caller. So `/me` printed
`Equity $10000.00 · Open P&L $0.00 · Trades 0` for every linked account, and
`GET /auth/me` answered `equity: 10000.0`, a measured-looking account read
from nothing. Driven on a real database here, not on a stub: a stub that
answers the defaults would pass against the old code too.

The card now names the account's fields it does hold (email, plan, settings)
and says where the balance is read instead. The dead readers are deleted; the
table stays, because deployed databases hold it and the purge must reach it.
"""
from __future__ import annotations

import asyncio
import re
import types
from unittest import mock

import pytest

import bot.db.models as models
import bot.skills.user_middleware as um
from bot.utils.i18n import _STRINGS, SUPPORTED_LANGS, t


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "DB_PATH", tmp_path / "t.db")
    models.init_db()
    uid = models.create_user("ann@example.test", "correct horse battery")
    assert models.link_telegram(uid, "4242", "ann")
    return uid


def _me(lang="en") -> str:
    replies: list[str] = []

    class Msg:
        async def reply_text(self, text, **kw):
            replies.append(text)

    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=4242, type="private"),
        message=Msg(), effective_user=types.SimpleNamespace(id=4242))
    with mock.patch.object(um, "_user_lang", return_value=lang):
        asyncio.run(um.cmd_me(upd, types.SimpleNamespace(args=[])))
    assert len(replies) == 1, replies
    return replies[0]


def test_the_card_prints_no_balance_pnl_or_trade_count(db):
    card = _me()
    assert "ann@example.test" in card and "free" in card, card
    for gone in ("10000", "$0.00", "Equity", "P&amp;L", "Trades:"):
        assert gone not in card, (gone, card)


def test_the_card_says_where_the_account_is_read(db):
    card = _me()
    assert "not stored with this account" in card, card
    assert f"{um.REGISTER_URL}/dashboard" in card, card


def test_a_written_row_still_does_not_reach_the_card(db):
    # Even a figure somebody wrote into the table is not the account: the
    # table is not the book the bot trades. The card reads nothing from it.
    with models.get_db() as conn:
        conn.execute("UPDATE user_portfolio SET equity=1234.5, daily_pnl=-9 "
                     "WHERE user_id=?", (db,))
    card = _me()
    assert "1234" not in card and "-9" not in card, card


@pytest.mark.parametrize("lang", sorted(SUPPORTED_LANGS))
def test_every_language_takes_the_same_arguments_and_prints_no_figure(lang):
    text = t("me_account", lang, email="a@b.c", plan="pro", llm="x",
             notif="on", url="https://u.example")
    assert "https://u.example/dashboard" in text, (lang, text)
    assert "{" not in text and "$" not in text, (lang, text)


def test_no_translation_names_the_removed_fields():
    for lang, text in _STRINGS["me_account"].items():
        assert not re.search(r"\{(equity|pnl|trades)\}", text), lang


def test_the_dead_readers_are_gone():
    assert not hasattr(models, "get_user_portfolio")
    assert not hasattr(models, "save_user_portfolio")
    assert not hasattr(um.UserContext, "save_portfolio")
    assert not hasattr(um.UserContext, "equity")


def test_the_bridge_answers_no_equity(db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a" * 64)
    ar = pytest.importorskip("bot.api.auth_routes")
    info = ar._user_info(db)
    assert "equity" not in info, info
    assert info["email"] == "ann@example.test" and info["telegram_linked"] is True


def test_the_purge_still_reaches_the_table(tmp_path, monkeypatch):
    # A website-linked row: the purge refuses a bot-native account on purpose.
    monkeypatch.setattr(models, "DB_PATH", tmp_path / "p.db")
    models.init_db()
    um._ensure_local_user(77, "web@example.test", "free")
    assert "user_portfolio" in models.PURGE_TABLES
    models.purge_user_data(77)
    with models.get_db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM user_portfolio WHERE user_id=?",
                         (77,)).fetchone()[0]
    assert n == 0


def test_the_command_list_does_not_promise_a_portfolio():
    from bot.skills import command_catalog as cc
    row = dict(entry for _title, _audience, entries in cc.GROUPS for entry in entries)
    assert "portfolio" not in row["me"].lower(), row["me"]
    assert "投資組合" not in cc.DESC_ZH["me"], cc.DESC_ZH["me"]

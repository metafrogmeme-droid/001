"""The /connect success card reads PER_USER_LIVE_ENABLED, in three states.

The card used to say "not yet enabled" after every successful link, whatever
the flag held. Off must not say live trading is available. On names the
routing the flag turns on and still places no order. A missing attribute, a
raised read, and a value that is not a bool are none of those two sentences.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.config import CONFIG
from bot.core import exchange_credentials as ec
from bot.skills import account_commands
from bot.skills.account_commands import (
    per_user_live_connect_line,
    read_per_user_live_enabled,
)
from bot.skills.telegram_handler import TelegramHandler

_RAISE = object()


class _Store:
    def set_venue(self, tg_id, venue, fields):
        self.saved = (tg_id, venue, fields)

    def fingerprint(self, tg_id):
        return "bg_****1234"


class _Engine:
    def __init__(self):
        self.calls: list[str] = []

    def invalidate_user_executor(self, tg_id):
        self.calls.append("invalidate_user_executor")


class _Stub:
    def __init__(self):
        self.sent: list = []
        self.engine = _Engine()

    async def _guard(self, update, command="", ctx=None):
        return True

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update):
        return 4242


class _Ctx:
    def __init__(self, *args):
        self.args = list(args)


class _Msg:
    async def delete(self):
        return None


class _Chat:
    type = "private"


class _Update:
    message = _Msg()
    effective_chat = _Chat()


class _FlagConfig:
    """The real exchange block, and one planted flag.

    ``/connect`` reads ``CONFIG.exchange.sandbox`` before the card, so a
    stand-in that only has the flag would fail the path for the wrong reason.
    """

    def __init__(self, flag):
        self._flag = flag

    @property
    def exchange(self):
        return CONFIG.exchange

    @property
    def per_user_live_enabled(self):
        if self._flag is _RAISE:
            raise RuntimeError("unreadable")
        return self._flag


class _Bare:
    """No ``per_user_live_enabled`` attribute at all."""


def _connect(monkeypatch, flag):
    async def _fake_validate(venue, fields, sandbox=False):
        return True, "100.00 USDT free"

    async def _fake_scope(api_key, api_secret, passphrase, sandbox=False):
        return {"withdraw": "off", "ip_allowlist": []}

    monkeypatch.setattr(ec, "validate_venue_credentials", _fake_validate)
    monkeypatch.setattr(ec, "probe_bitget_key_scope", _fake_scope)
    monkeypatch.setattr(ec, "get_credential_store", lambda: _Store())
    monkeypatch.setattr(account_commands, "CONFIG", _FlagConfig(flag))

    stub = _Stub()
    asyncio.run(TelegramHandler._cmd_connect(
        stub, _Update(),
        _Ctx("bg_apikey_0123456789", "apisecret_0123456789", "passphrase1")))
    return stub.sent[-1], stub.engine


def test_flag_off_does_not_say_live_trading_is_available(monkeypatch):
    out, engine = _connect(monkeypatch, False)
    line = per_user_live_connect_line(False)
    assert line in out
    assert "account linked" in out
    assert "Per-user live trading is off" in out
    assert "does not open live trading" in out
    assert "places no order" in out
    assert "live trading is available" not in out
    assert "is on" not in out
    assert "could not be read" not in out
    assert "not yet enabled" not in out
    assert "you'll be notified" not in out
    assert "$" not in line
    assert engine.calls == ["invalidate_user_executor"]


def test_flag_on_names_own_key_routing_and_places_no_order(monkeypatch):
    out, engine = _connect(monkeypatch, True)
    line = per_user_live_connect_line(True)
    assert line in out
    assert "account linked" in out
    assert "Per-user live trading is on" in out
    assert "own linked keys" in out
    assert "shared operator account" in out
    assert "other live gates allow it" in out
    assert "places no order" in out
    assert "is off" not in out
    assert "could not be read" not in out
    assert "not yet enabled" not in out
    assert "live trading is available" not in out
    assert "$" not in line
    assert engine.calls == ["invalidate_user_executor"]


@pytest.mark.parametrize("flag", [_RAISE, None, 0, 1, "true", "false"])
def test_an_unreadable_flag_is_neither_on_nor_off(monkeypatch, flag):
    out, engine = _connect(monkeypatch, flag)
    line = per_user_live_connect_line(None)
    assert line in out
    assert "account linked" in out
    assert "could not be read" in out
    assert "does not call it on" in out
    assert "does not call it off" in out
    assert "places no order" in out
    assert "is on" not in out
    assert "is off" not in out
    assert "not yet enabled" not in out
    assert "live trading is available" not in out
    assert "unreadable" not in out
    assert "$" not in line
    assert engine.calls == ["invalidate_user_executor"]


def test_the_reader_keeps_a_bool_and_refuses_everything_else():
    assert read_per_user_live_enabled(_FlagConfig(True)) is True
    assert read_per_user_live_enabled(_FlagConfig(False)) is False
    assert read_per_user_live_enabled(_FlagConfig(None)) is None
    assert read_per_user_live_enabled(_FlagConfig(0)) is None
    assert read_per_user_live_enabled(_FlagConfig(1)) is None
    assert read_per_user_live_enabled(_FlagConfig("true")) is None
    assert read_per_user_live_enabled(_FlagConfig(_RAISE)) is None
    assert read_per_user_live_enabled(_Bare()) is None

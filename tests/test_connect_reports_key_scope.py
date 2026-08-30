"""`/connect` must tell you whether the key you just handed over can be drained.

WHY THIS FILE DRIVES THE COMMAND INSTEAD OF SCANNING IT

`bot/guardian/authority_preflight.py` sat in `tests/unreachable_baseline.txt`
with passing tests and no production caller. Wiring it is the point of this
change, and the repo's own lesson (#999: a card built inline, source-scanned,
shipped, rendered zero times) is that a scan cannot tell code that is PRESENT
from code that is REACHED. So these plant a key scope and read what the user is
actually sent.
"""
from __future__ import annotations

import asyncio

from bot.core import exchange_credentials as ec
from bot.skills.telegram_handler import TelegramHandler


class _Store:
    def set_venue(self, tg_id, venue, fields): self.saved = (tg_id, venue)
    def fingerprint(self, tg_id): return "bg_****1234"


class _Engine:
    def invalidate_user_executor(self, tg_id): pass


class _Stub:
    def __init__(self):
        self.sent: list = []
        self.engine = _Engine()

    async def _guard(self, update, command="", ctx=None): return True
    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)
    def _get_tg_id(self, update): return 4242


class _Ctx:
    def __init__(self, *args): self.args = list(args)


class _Msg:
    async def delete(self): pass


class _Chat:
    type = "private"


class _Update:
    message = _Msg()
    effective_chat = _Chat()


def _connect(monkeypatch, *, scope, validate_ok=True):
    """Drive /connect end-to-end with the network probes replaced."""
    async def _fake_validate(venue, fields, sandbox=False):
        return validate_ok, "100.00 USDT free"

    async def _fake_scope(api_key, api_secret, passphrase, sandbox=False):
        if isinstance(scope, Exception):
            raise scope
        return scope

    monkeypatch.setattr(ec, "validate_venue_credentials", _fake_validate)
    monkeypatch.setattr(ec, "probe_bitget_key_scope", _fake_scope)
    monkeypatch.setattr(ec, "get_credential_store", lambda: _Store())

    stub = _Stub()
    asyncio.run(TelegramHandler._cmd_connect(
        stub, _Update(), _Ctx("bg_apikey_0123456789", "apisecret_0123456789", "passphrase1")))
    return stub.sent[-1]


def test_a_key_that_can_withdraw_is_reported_loudly(monkeypatch):
    out = _connect(monkeypatch, scope={"withdraw": "on", "ip_allowlist": None})
    assert "account linked" in out
    assert "WITHDRAW" in out
    assert "trade-only" in out          # and says what to do about it


def test_a_trade_only_key_is_confirmed(monkeypatch):
    out = _connect(monkeypatch, scope={"withdraw": "off", "ip_allowlist": []})
    assert "cannot move funds out" in out


def test_unreadable_scope_never_renders_as_the_safe_case(monkeypatch):
    """The rule this whole module exists for, at the surface a user reads.

    An unreadable permission set must not arrive looking like a confirmed
    non-custodial key, and must not arrive as silence either — on this screen
    silence is read as "no warning, so it's fine".
    """
    out = _connect(monkeypatch, scope={"withdraw": "unknown", "ip_allowlist": None})
    assert "not readable" in out
    assert "cannot move funds out" not in out


def test_a_failing_scope_probe_does_not_break_connect(monkeypatch):
    """The scan is the safety feature; the scope read is evidence about it.

    A key that authenticates must still be linked when the scope endpoint is
    down — degraded to the honest "not readable" line, never to a failed
    /connect and never to a fabricated verdict.
    """
    out = _connect(monkeypatch, scope=RuntimeError("bitget 401"))
    assert "account linked" in out
    assert "not readable" in out


def test_rejected_credentials_are_never_stored_or_scored(monkeypatch):
    """A key we refused gets no custody verdict of any kind.

    The first draft of this test asserted only "Nothing was stored", and passed
    against credentials the FORMAT check rejected before the probe ever ran —
    the same sentence, a different door, and the scope path never exercised.
    Asserting the authentication-failure text pins which rejection happened.
    """
    out = _connect(monkeypatch, scope={"withdraw": "on"}, validate_ok=False)
    assert "Could not authenticate" in out
    assert "Nothing was stored" in out
    assert "WITHDRAW" not in out        # no scope claim about a key we refused
    assert "not readable" not in out    # nor the unreadable one


def test_a_non_bitget_venue_is_not_told_to_check_bitget(monkeypatch):
    """/connect links eight venues; only Bitget exposes key scope.

    The other seven fall through to the unreadable line, so that line must not
    name a venue the user never linked. The first draft said "Bitget did not
    tell us" on all of them.
    """
    from bot.guardian.authority_preflight import withdraw_notice
    unk = withdraw_notice("unknown")
    assert "Bitget" not in unk
    assert "this venue" in unk


def test_hyperliquid_links_without_a_bitget_scope_probe(monkeypatch):
    """A venue with no scope endpoint still links, and still says so honestly."""
    async def _fake_validate(venue, fields, sandbox=False):
        return True, "100.00 USDC"

    async def _boom(*a, **k):
        raise AssertionError("bitget scope probe must not run for hyperliquid")

    monkeypatch.setattr(ec, "validate_venue_credentials", _fake_validate)
    monkeypatch.setattr(ec, "probe_bitget_key_scope", _boom)
    monkeypatch.setattr(ec, "get_credential_store", lambda: _Store())

    stub = _Stub()
    asyncio.run(TelegramHandler._cmd_connect(
        stub, _Update(), _Ctx("hyperliquid",
                              "0x" + "a" * 40, "0x" + "b" * 64)))
    out = stub.sent[-1]
    assert "account linked" in out
    assert "not readable" in out
    assert "Bitget" not in out

"""`/exchange` names every linked venue and which one orders go to.

The card printed "Status: connected" and one fingerprint, and named no venue.
With Bitget and Bybit EU both linked (8 October) it read as one account, and
nothing on it said that Bitget is where orders go and Bybit EU is read for its
balance only. It now carries a "Linked:" line, each venue marked active and
balances-only where they are, and the key says whose it is.

Driven through the real `_cmd_exchange` on the real credential store.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cryptography.fernet import Fernet

from bot.core import exchange_credentials as ec
from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.skills.telegram_handler import TelegramHandler

BITGET = {"api_key": "BG" * 8, "api_secret": "BS" * 8, "passphrase": "pp"}
EU = {"api_key": "EU" * 8, "api_secret": "ES" * 8}
OKX = {"api_key": "OK" * 8, "api_secret": "OS" * 8, "passphrase": "pp"}


def _store(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    return ExchangeCredentialStore(creds_file=str(tmp_path / "creds.enc"),
                                   key_file=str(tmp_path / ".key"))


class _Host:
    def __init__(self):
        self.sent: list = []

    async def _guard(self, update, command="", ctx=None):
        return True

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update):
        return "7"


def _card(monkeypatch, store) -> str:
    monkeypatch.setattr(ec, "get_credential_store", lambda: store)
    h = _Host()
    asyncio.run(TelegramHandler._cmd_exchange(h, SimpleNamespace(), SimpleNamespace(args=[])))
    (out,) = h.sent
    return out


def _line(out: str, head: str) -> str:
    return next(ln for ln in out.splitlines() if ln.startswith(head))


def test_bitget_and_bybit_eu_are_both_named_and_told_apart(tmp_path, monkeypatch):
    s = _store(tmp_path)
    s.set_venue("7", "bitget", BITGET)
    s.set_venue("7", "bybiteu", EU)
    out = _card(monkeypatch, s)
    assert _line(out, "Linked:") == "Linked: Bitget (active) · Bybit EU (balances only)"
    key = _line(out, "Key:")
    assert key.endswith("(Bitget)") and "BG-" in key, key


def test_a_single_venue_is_named_and_marked_active(tmp_path, monkeypatch):
    s = _store(tmp_path)
    s.set_venue("7", "bitget", BITGET)
    out = _card(monkeypatch, s)
    assert _line(out, "Linked:") == "Linked: Bitget (active)"


def test_a_balances_only_venue_alone_is_both_active_and_balances_only(tmp_path, monkeypatch):
    s = _store(tmp_path)
    s.set_venue("7", "okx", OKX)
    out = _card(monkeypatch, s)
    assert _line(out, "Linked:") == "Linked: OKX (active, balances only)"
    assert _line(out, "Key:").endswith("(OKX)")

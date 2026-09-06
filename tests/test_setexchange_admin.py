"""
Admin /setexchange — the OPERATOR's exchange keys, into the vault.

Live incident: a wiped .env lost BITGET_PASSPHRASE, so the engine account failed
auth ("bitget requires 'password' credential") and live positions were
unprotected. /setexchange lets an admin re-supply the keys at runtime: validated
read-only, stored ENCRYPTED in the vault (survives future wipes), and the
operator exchange client rebuilt live.

BYBIT AND BINGX JOINED IT, and the reason is worth keeping here. The vault
MIRRORS .env; it never replaces it. So a venue whose only intake was .env kept
its operator key in the clear in that file for as long as the file existed, and
the operator's report read "that Bybit key remains in plaintext" — correctly.
With /setexchange bybit the key goes to the vault directly and the .env lines
can be deleted; the vault restores it on every boot.

The wiring pins are by source inspection, matching the existing telegram-
handler test style; the venue behaviour is DRIVEN through a bare host.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.skills.telegram_handler import TelegramHandler


def _src() -> str:
    return inspect.getsource(TelegramHandler._cmd_setexchange)


class TestSetExchangeHandler:
    def test_registered_command(self):
        cls_src = inspect.getsource(TelegramHandler)
        assert '("setexchange", self._cmd_setexchange)' in cls_src

    def test_admin_only(self):
        assert "_is_admin" in _src()

    def test_deletes_secret_message_first(self):
        src = _src()
        # Message deletion must come before the admin gate (keys never linger).
        assert "update.message.delete()" in src
        assert src.index("delete()") < src.index("_is_admin")

    def test_validates_read_only_before_storing(self):
        src = _src()
        # One validator for every venue — the per-venue probes live behind it.
        assert "validate_venue_credentials" in src
        # Store only happens after a successful validation branch.
        assert src.index("validate_venue_credentials(") < src.index("store_secrets(")

    def test_persists_all_three_bitget_secrets(self):
        src = _src()
        assert "BITGET_API_KEY" in src
        assert "BITGET_API_SECRET" in src
        assert "BITGET_PASSPHRASE" in src

    def test_bybit_and_bingx_have_a_vault_path(self):
        """The env names the vault stores and the CONFIG fields the engine
        reads, side by side in the handler, so they cannot drift apart."""
        src = _src()
        for name in ("BYBIT_API_KEY", "BYBIT_API_SECRET", "bybit_api_key", "bybit_api_secret",
                     "BINGX_API_KEY", "BINGX_API_SECRET", "bingx_api_key", "bingx_api_secret"):
            assert name in src, name

    def test_rebuilds_operator_exchange_live(self):
        src = _src()
        # Drops the cached operator client + invalidates balance cache so the
        # next call authenticates with the new creds — no restart.
        assert "_exchange = None" in src
        assert "_invalidate_live_balance_cache" in src


# ── driven: the Bybit path, end to end through a bare host ────────────────

def _host(sent, admin=True):
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, *a, **k):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: admin
    h.engine = SimpleNamespace(
        live_executor=SimpleNamespace(_exchange="stale-client"),
        _invalidate_live_balance_cache=lambda: None)
    return h


def _update(chat_type="private"):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type=chat_type),
        message=SimpleNamespace(delete=AsyncMock()), callback_query=None)


@pytest.fixture
def plumbing(monkeypatch):
    """Stub the venue probe, the vault and CONFIG; record what each was given.

    CONFIG is patched on the account mixin's module: since the handler split
    that is where /setexchange reads it, and a patch on the handler's copy of
    the name would leave the command reading the real config.
    """
    import bot.core.exchange_credentials as xc
    import bot.core.secrets_vault as sv
    import bot.skills.account_commands as th

    seen = {"validated": None, "stored": None}

    async def _validate(venue, fields, sandbox=False):
        seen["validated"] = (venue, dict(fields), sandbox)
        return True, "USDT 12.00 (free 12.00)"

    def _store(mapping):
        seen["stored"] = dict(mapping)
        return list(mapping)

    monkeypatch.setattr(xc, "validate_venue_credentials", _validate)
    monkeypatch.setattr(sv, "store_secrets", _store)
    cfg = SimpleNamespace(exchange=SimpleNamespace(
        sandbox=False, api_key="", api_secret="", passphrase="",
        bybit_api_key="", bybit_api_secret="", bingx_api_key="", bingx_api_secret=""))
    monkeypatch.setattr(th, "CONFIG", cfg)
    return seen, cfg


@pytest.mark.asyncio
async def test_setexchange_bybit_stores_the_bybit_names_and_patches_the_bybit_fields(plumbing):
    seen, cfg = plumbing
    sent = []
    h = _host(sent)
    update = _update()
    await h._cmd_setexchange(update, SimpleNamespace(args=["bybit", "k" * 12, "s" * 24]))

    update.message.delete.assert_awaited_once()
    assert seen["validated"] == ("bybit", {"api_key": "k" * 12, "api_secret": "s" * 24}, False)
    assert seen["stored"] == {"BYBIT_API_KEY": "k" * 12, "BYBIT_API_SECRET": "s" * 24}, (
        "the vault must get the names the engine's config reads back on boot")
    assert (cfg.exchange.bybit_api_key, cfg.exchange.bybit_api_secret) == ("k" * 12, "s" * 24)
    assert cfg.exchange.api_key == "", "the Bitget fields must not be touched by a Bybit set"
    assert h.engine.live_executor._exchange is None, "the cached client must be dropped"
    assert "Bybit credentials updated" in sent[-1]
    assert "delete those lines" in sent[-1], "the reply must say the .env copy can go"
    # The secret never reaches a reply.
    assert all("s" * 24 not in s and "k" * 12 not in s for s in sent)


@pytest.mark.asyncio
async def test_setexchange_without_a_venue_is_still_bitget(plumbing):
    seen, cfg = plumbing
    sent = []
    h = _host(sent)
    await h._cmd_setexchange(_update(), SimpleNamespace(args=["k" * 12, "s" * 24, "p" * 8]))
    assert seen["validated"][0] == "bitget"
    assert set(seen["stored"]) == {"BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE"}
    assert cfg.exchange.passphrase == "p" * 8


@pytest.mark.asyncio
async def test_a_failed_probe_stores_nothing(plumbing, monkeypatch):
    import bot.core.exchange_credentials as xc
    seen, cfg = plumbing

    async def _refuse(venue, fields, sandbox=False):
        return False, "invalid api key"

    monkeypatch.setattr(xc, "validate_venue_credentials", _refuse)
    sent = []
    h = _host(sent)
    await h._cmd_setexchange(_update(), SimpleNamespace(args=["bybit", "k" * 12, "s" * 24]))
    assert seen["stored"] is None
    assert cfg.exchange.bybit_api_key == ""
    assert "Nothing was changed" in sent[-1]


@pytest.mark.asyncio
async def test_the_usage_names_every_operator_venue(plumbing):
    sent = []
    h = _host(sent)
    await h._cmd_setexchange(_update(), SimpleNamespace(args=[]))
    assert "/setexchange bybit" in sent[-1] and "/setexchange bingx" in sent[-1]
    assert "/setexchange &lt;api_key&gt;" in sent[-1]


@pytest.mark.asyncio
async def test_a_group_chat_is_refused_after_the_message_is_deleted(plumbing):
    seen, _ = plumbing
    sent = []
    h = _host(sent)
    update = _update(chat_type="supergroup")
    await h._cmd_setexchange(update, SimpleNamespace(args=["bybit", "k" * 12, "s" * 24]))
    update.message.delete.assert_awaited_once()
    assert seen["stored"] is None
    assert "private chat" in sent[-1]

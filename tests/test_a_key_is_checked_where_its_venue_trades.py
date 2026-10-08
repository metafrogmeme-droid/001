"""A key is checked, and a balance read, in the environment its venue trades in.

Reported 8 October with two screenshots. `/setexchange bybit` answered
"Could not authenticate with Bybit" over Bybit's raw body,
`{"retCode":10003,"retMsg":"API key is invalid."...}`, and the website's Bybit
card sat on "applying connect…".

Bybit's 10003 reads in full "API key is invalid. Check whether the key and
domain are matched, there are 4 env: mainnet, testnet, mainnet-demo,
testnet-demo" (https://bybit-exchange.github.io/docs/v5/error). The key
checks behind /connect, /setexchange and the website's credential pull all
passed `sandbox=CONFIG.exchange.sandbox`, which is BITGET_SANDBOX, Bitget's
demo-trading flag. Bybit's and BingX's clients never read it, and
Hyperliquid's reads HYPERLIQUID_TESTNET. So under Bitget demo trading a
mainnet Bybit key was checked on api-testnet.bybit.com and refused with
exactly that 10003, while the client that would have traded it uses
api.bybit.com. A testnet key would have passed the check and then failed at
the first order.

`Venue.uses_sandbox` is now the one reading. The client asks it, and so do
the key check and the balance reader (`exchange_credentials.venue_sandbox`).
A Bybit refusal is said as an instruction, and a 10003 on mainnet is retried
on testnet and Demo Trading to name where the key belongs, as Bitget's 40099
already was.

Driven through the real `/setexchange`, `/connect`, the web pull's
validator and `balance_snapshot`, down to ccxt's own clients. Only
`fetch_balance` is replaced, by a stand-in that answers by the host the
client was pointed at.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import ccxt.async_support as ccxt_async
import pytest
from ccxt.base.errors import AuthenticationError, NetworkError

import bot.config as config_mod
from bot.core import exchange_credentials as ec
from bot.core.venues import get_venue, valid_venue_ids, venue_uses_sandbox

KEY, SECRET = "bybitkey0123456789", "bybitsecret0123456789abcdef0123456789"


def _cfg(sandbox: bool = False, hyperliquid_testnet: bool = False):
    return SimpleNamespace(sandbox=sandbox, hyperliquid_testnet=hyperliquid_testnet,
                           trade_mode="futures",
                           bybit_api_key="", bybit_api_secret="",
                           bingx_api_key="", bingx_api_secret="",
                           api_key="", api_secret="", passphrase="")


@pytest.fixture
def bitget_demo():
    """The RUNNING config with BITGET_SANDBOX on and HYPERLIQUID_TESTNET off.

    Set on the real (frozen) CONFIG.exchange and restored after, rather than
    swapping the module's CONFIG: a module imported mid-test reads CONFIG at
    import and would get the stand-in.
    """
    ex = config_mod.CONFIG.exchange
    saved = {k: getattr(ex, k) for k in ("sandbox", "hyperliquid_testnet")}
    object.__setattr__(ex, "sandbox", True)
    object.__setattr__(ex, "hyperliquid_testnet", False)
    try:
        yield ex
    finally:
        for k, v in saved.items():
            object.__setattr__(ex, k, v)


def _host_of(ex) -> str:
    """Which Bybit environment a ccxt client points at, from its own URLs."""
    api = ex.urls["api"]
    url = next(iter(api.values())) if isinstance(api, dict) else str(api)
    if "api-testnet." in url:
        return "testnet"
    if "api-demo." in url:
        return "demo"
    return "mainnet"


def _refusal(code: int) -> AuthenticationError:
    return AuthenticationError(
        'bybit {"retCode":%d,"retMsg":"refused","result":{},"retExtInfo":{},'
        '"time":1791449888572}' % code)


def _bybit(monkeypatch, answers: dict) -> list:
    """Bybit answering by host: ``answers[env]`` is "ok", a retCode, or an
    exception. An environment not named refuses with 10003, as Bybit does for
    a key from another environment."""
    seen: list = []

    async def fetch_balance(self, params=None):
        env = _host_of(self)
        seen.append(env)
        a = answers.get(env, 10003)
        if a == "ok":
            return {"USDT": {"free": 12.0, "used": 0.0, "total": 12.0}}
        if isinstance(a, BaseException):
            raise a
        raise _refusal(a)

    monkeypatch.setattr(ccxt_async.bybit, "fetch_balance", fetch_balance)
    return seen


def _in_sandbox(ex) -> bool:
    # Bitget's demo trading is a header ccxt keeps in options; the others
    # switch their URLs and say so on isSandboxModeEnabled.
    return bool(getattr(ex, "isSandboxModeEnabled", False) or ex.options.get("sandboxMode"))


_CREDS = {
    "bitget": {"api_key": "k" * 12, "api_secret": "s" * 24, "passphrase": "p" * 8},
    "bybit": {"api_key": KEY, "api_secret": SECRET},
    "bingx": {"api_key": "k" * 12, "api_secret": "s" * 24},
    "okx": {"api_key": "k" * 12, "api_secret": "s" * 24, "passphrase": "p" * 8},
    "gate": {"api_key": "k" * 12, "api_secret": "s" * 24},
    "kucoin": {"api_key": "k" * 12, "api_secret": "s" * 24, "passphrase": "p" * 8},
    # An agent key: not the wallet's own, so the adapter does not refuse it.
    "hyperliquid": {"wallet_address": "0x" + "2" * 40, "agent_private_key": "0x" + "1" * 64},
    "paradex": {"wallet_address": "0x" + "2" * 40, "agent_private_key": "0x" + "1" * 64},
}


# ── the one reading ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("vid", valid_venue_ids())
@pytest.mark.parametrize("sandbox,hl_testnet", [(False, False), (True, False),
                                                (False, True), (True, True)])
def test_the_client_and_the_key_check_agree_on_the_environment(vid, sandbox, hl_testnet):
    cfg = _cfg(sandbox=sandbox, hyperliquid_testnet=hl_testnet)
    ex = get_venue(vid).create_exchange(cfg, credentials=_CREDS[vid])
    assert _in_sandbox(ex) == venue_uses_sandbox(vid, cfg) == ec.venue_sandbox(vid, cfg), vid


def test_bitget_s_demo_flag_moves_bitget_and_not_bybit_bingx_or_hyperliquid():
    demo = _cfg(sandbox=True)
    assert ec.venue_sandbox("bitget", demo) is True
    assert ec.venue_sandbox("bybit", demo) is False
    assert ec.venue_sandbox("bingx", demo) is False
    assert ec.venue_sandbox("hyperliquid", demo) is False
    # Hyperliquid follows its own flag.
    assert ec.venue_sandbox("hyperliquid", _cfg(hyperliquid_testnet=True)) is True
    assert ec.venue_sandbox("bitget", _cfg(hyperliquid_testnet=True)) is False
    assert ec.venue_sandbox("no-such-venue", demo) is False


# ── the callers, under Bitget demo trading ───────────────────────────────────

def _setexchange_host(sent):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, *a, **k):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: True
    h.engine = SimpleNamespace(live_executor=SimpleNamespace(_exchange="stale"),
                               _invalidate_live_balance_cache=lambda: None)
    return h


def _update():
    return SimpleNamespace(effective_user=SimpleNamespace(id=1),
                           effective_chat=SimpleNamespace(id=1, type="private"),
                           message=SimpleNamespace(delete=AsyncMock()), callback_query=None)


@pytest.fixture
def operator(monkeypatch, bitget_demo):
    """/setexchange's own CONFIG and vault, recorded rather than written."""
    import bot.core.secrets_vault as sv
    import bot.skills.account_commands as ac
    stored: dict = {}
    monkeypatch.setattr(sv, "store_secrets", lambda m: stored.update(m) or list(m))
    # The handler hot-patches the operator fields on its CONFIG; give it a copy
    # to write to, so the running config keeps its own keys.
    monkeypatch.setattr(ac, "CONFIG", SimpleNamespace(exchange=_cfg(sandbox=True)))
    return stored


def test_setexchange_checks_a_bybit_key_on_mainnet_under_bitget_demo(monkeypatch, operator):
    seen = _bybit(monkeypatch, {"mainnet": "ok"})
    sent: list = []
    asyncio.run(_setexchange_host(sent)._cmd_setexchange(
        _update(), SimpleNamespace(args=["bybit", KEY, SECRET])))
    assert seen == ["mainnet"], "the key was checked off the host the Bybit client trades on"
    assert operator == {"BYBIT_API_KEY": KEY, "BYBIT_API_SECRET": SECRET}
    assert "Operator Bybit credentials updated" in sent[-1]


def test_setexchange_names_a_testnet_key_and_stores_nothing(monkeypatch, operator):
    seen = _bybit(monkeypatch, {"testnet": "ok"})
    sent: list = []
    asyncio.run(_setexchange_host(sent)._cmd_setexchange(
        _update(), SimpleNamespace(args=["bybit", KEY, SECRET])))
    assert seen == ["mainnet", "testnet"]
    assert operator == {}
    assert "Nothing was changed" in sent[-1]
    assert "Bybit testnet keys" in sent[-1]
    assert "retCode" not in sent[-1]


class _ConnectHost:
    def __init__(self):
        self.sent: list = []
        self.engine = SimpleNamespace(invalidate_user_executor=lambda tg: None)

    async def _guard(self, update, command="", ctx=None):
        return True

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update):
        return 4242


class _Store:
    saved = None

    def set_venue(self, tg_id, venue, fields):
        _Store.saved = (tg_id, venue)

    def fingerprint(self, tg_id):
        return "by_****6789"


def test_connect_checks_a_bybit_key_on_mainnet_under_bitget_demo(monkeypatch, bitget_demo):
    from bot.skills.telegram_handler import TelegramHandler
    seen = _bybit(monkeypatch, {"mainnet": "ok"})
    monkeypatch.setattr(ec, "get_credential_store", lambda: _Store())
    _Store.saved = None
    host = _ConnectHost()
    asyncio.run(TelegramHandler._cmd_connect(
        host, _update(), SimpleNamespace(args=["bybit", KEY, SECRET])))
    assert seen == ["mainnet"]
    assert _Store.saved == (4242, "bybit")
    assert "Bybit account linked" in host.sent[-1]


def test_the_web_pull_checks_a_bybit_key_on_mainnet_under_bitget_demo(monkeypatch, bitget_demo):
    from bot.utils.credential_pull import default_validator
    seen = _bybit(monkeypatch, {"mainnet": "ok"})
    assert default_validator({"venue": "bybit", "api_key": KEY, "api_secret": SECRET}) == (True, "")
    assert seen == ["mainnet"]
    # The other arm: a key Bybit's mainnet refuses is a verdict, said as one.
    seen.clear()
    _bybit(monkeypatch, {})
    verdict, why = default_validator({"venue": "bybit", "api_key": KEY, "api_secret": SECRET})
    assert verdict is False and "code 10003" in why and "retCode" not in why


def test_the_balance_reader_reads_where_each_venue_trades(monkeypatch, bitget_demo):
    seen: dict = {}

    async def bitget_fetch(self, params=None):
        seen["bitget"] = _in_sandbox(self)
        return {"USDT": {"free": 5.0, "used": 0.0}}

    monkeypatch.setattr(ccxt_async.bitget, "fetch_balance", bitget_fetch)
    bybit_seen = _bybit(monkeypatch, {"mainnet": "ok"})
    snap = asyncio.run(ec.balance_snapshot("bitget", _CREDS["bitget"]))
    assert snap["ok"] and seen["bitget"] is True, "a Bitget demo key read the live account"
    snap = asyncio.run(ec.balance_snapshot("bybit", _CREDS["bybit"]))
    assert snap["ok"] and bybit_seen == ["mainnet"]
    # An explicit environment is still honoured.
    asyncio.run(ec.balance_snapshot("bitget", _CREDS["bitget"], sandbox=False))
    assert seen["bitget"] is False


# ── Bybit's refusals, said as instructions ───────────────────────────────────

def _validate(monkeypatch, answers):
    seen = _bybit(monkeypatch, answers)
    ok, detail = asyncio.run(ec.validate_bybit_credentials(KEY, SECRET))
    return ok, detail, seen


def test_a_demo_trading_key_is_named(monkeypatch):
    ok, detail, seen = _validate(monkeypatch, {"demo": "ok"})
    assert ok is False
    assert seen == ["mainnet", "testnet", "demo"]
    assert detail == ec._BYBIT_DEMO_KEY


def test_a_key_no_environment_knows_is_told_what_10003_means(monkeypatch):
    ok, detail, seen = _validate(monkeypatch, {})
    assert ok is False
    assert seen == ["mainnet", "testnet", "demo"]
    assert "Bybit mainnet does not know this API key (code 10003)" in detail
    assert "Demo Trading" in detail and "regional" in detail
    assert "retCode" not in detail and "{" not in detail


@pytest.mark.parametrize("code,words", [
    (10004, "this API secret is not the key's"),
    (10005, "lacks permission"),
    (10010, "this server's IP"),
    (33004, "has expired"),
])
def test_each_refusal_says_what_to_fix_without_a_second_probe(monkeypatch, code, words):
    ok, detail, seen = _validate(monkeypatch, {"mainnet": code})
    assert ok is False
    assert seen == ["mainnet"], "only a 10003 asks the other environments"
    assert words in detail and f"code {code}" in detail


def test_a_refusal_bybit_has_no_sentence_for_keeps_the_venue_s_words(monkeypatch):
    ok, detail, _ = _validate(monkeypatch, {"mainnet": 10006})
    assert ok is False and "10006" in detail


def test_an_outage_is_not_read_as_a_refusal(monkeypatch):
    ok, detail, seen = _validate(monkeypatch, {"mainnet": NetworkError("bybit timed out")})
    assert ok is False and seen == ["mainnet"]
    assert detail.startswith("NetworkError")


def test_a_valid_key_passes_on_the_first_probe(monkeypatch):
    ok, detail, seen = _validate(monkeypatch, {"mainnet": "ok"})
    assert ok is True and seen == ["mainnet"]
    assert detail == "12.00 USDT free"


def test_every_sentence_fits_the_website_s_ack():
    """The web pull carries 180 characters of the reason to the card."""
    lines = [s.format(env="mainnet") for s in ec._BYBIT_REFUSALS.values()]
    lines += [ec._BYBIT_TESTNET_KEY, ec._BYBIT_DEMO_KEY]
    for s in lines:
        assert len(s) <= 180, (len(s), s)


def test_bingx_keeps_its_own_words(monkeypatch, bitget_demo):
    seen: list = []

    async def bingx_fetch(self, params=None):
        seen.append(_in_sandbox(self))
        raise AuthenticationError('bingx {"code":100413,"msg":"Incorrect apiKey"}')

    monkeypatch.setattr(ccxt_async.bingx, "fetch_balance", bingx_fetch)
    ok, detail = asyncio.run(ec.validate_venue_credentials("bingx", _CREDS["bingx"]))
    assert ok is False and "Incorrect apiKey" in detail
    assert seen == [False], "BingX's key check went to its demo host under BITGET_SANDBOX"


def test_the_key_check_s_own_default_follows_the_venue(monkeypatch, bitget_demo):
    """No caller argument: a Bitget key is checked in demo under BITGET_SANDBOX,
    a Hyperliquid key on mainnet until HYPERLIQUID_TESTNET says otherwise."""
    seen: dict = {}

    async def bitget_fetch(self, params=None):
        seen["bitget"] = _in_sandbox(self)
        return {"USDT": {"free": 5.0, "used": 0.0}}

    async def hl_fetch(self, params=None):
        seen.setdefault("hyperliquid", []).append(_in_sandbox(self))
        return {"USDC": {"free": 5.0, "used": 0.0, "total": 5.0}}

    monkeypatch.setattr(ccxt_async.bitget, "fetch_balance", bitget_fetch)
    monkeypatch.setattr(ccxt_async.hyperliquid, "fetch_balance", hl_fetch)
    ok, _ = asyncio.run(ec.validate_venue_credentials("bitget", _CREDS["bitget"]))
    assert ok and seen["bitget"] is True
    asyncio.run(ec.validate_venue_credentials("hyperliquid", _CREDS["hyperliquid"]))
    object.__setattr__(bitget_demo, "hyperliquid_testnet", True)
    asyncio.run(ec.validate_venue_credentials("hyperliquid", _CREDS["hyperliquid"]))
    assert seen["hyperliquid"] == [False, True], "Hyperliquid's check followed the Bitget flag"


def test_a_check_on_testnet_names_testnet_and_asks_nowhere_else(monkeypatch):
    seen = _bybit(monkeypatch, {})
    ok, detail = asyncio.run(ec.validate_bybit_credentials(KEY, SECRET, sandbox=True))
    assert ok is False and seen == ["testnet"]
    assert "Bybit testnet does not know this API key (code 10003)" in detail

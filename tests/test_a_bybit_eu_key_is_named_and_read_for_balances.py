"""A Bybit EU key is named as one, and Bybit EU is linked for balances.

Reported 8 October: `/setexchange bybit` answered "Bybit mainnet does not know
this API key (code 10003)" for the operator's key, and the key was a Bybit EU
key. Bybit EU (bybit.eu, api.bybit.eu) is Bybit's entity for the European
Economic Area. As of October 2026 it offers spot, spot margin and Earn, and no
perpetual futures, which are all this bot trades.

Two changes, both asked for:

  * a 10003 on bybit.com is retried on api.bybit.eu, purely to diagnose, and a
    key that answers there is named as a Bybit EU key, with what that means:
    there is nothing to fix in the key, the venue cannot take this bot's orders;
  * `bybiteu` is a venue that is linked and read and never traded, as OKX,
    Gate, KuCoin and Paradex are. Its balance is Bybit's own USD valuation of
    the wallet (`totalEquity`): a Bybit EU wallet's USDT line is not its
    balance.

And one found on the way. Linking a venue made it the ACTIVE one, and the
engine builds no executor for a balances-only venue, so a user who linked Bybit
EU beside Bitget would have stopped trading on Bitget, with nothing on the
connect card saying so. A balances-only venue no longer takes the slot.

Driven through ccxt's own Bybit client with only `fetch_balance` replaced by a
stand-in that answers by the host ccxt built, the real credential store, the
real `_executor_for`, and the real `/connect`, `/setexchange` and keyless scan
client. api.bybit.eu refuses this harness's region (CloudFront), so nothing
here has reached Bybit EU's servers.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import ccxt.async_support as ccxt_async
import pytest
from ccxt.base.errors import AuthenticationError, ExchangeNotAvailable
from cryptography.fernet import Fernet

import bot.core.market_scanner as ms
from bot.core import exchange_credentials as ec
from bot.core.engine import RuneClawEngine
from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.core.live_executor import LiveExecutor
from bot.core.venues import (
    PER_USER_EXECUTION_VENUES,
    get_venue,
    per_user_execution_refusal,
    valid_venue_ids,
)

KEY, SECRET = "eukey0123456789abcd", "eusecret0123456789abcdef0123456789ab"
EU = {"api_key": KEY, "api_secret": SECRET}

CREDS = {
    "bitget": {"api_key": "BG" * 8, "api_secret": "BS" * 8, "passphrase": "pp"},
    "bybit": {"api_key": "BY" * 8, "api_secret": "YS" * 8},
    "bybiteu": EU,
    "bingx": {"api_key": "BX" * 8, "api_secret": "XS" * 8},
    "hyperliquid": {"wallet_address": "0x" + "a" * 40, "agent_private_key": "0x" + "b" * 64},
    "okx": {"api_key": "OK" * 8, "api_secret": "OS" * 8, "passphrase": "pp"},
    "gate": {"api_key": "GA" * 8, "api_secret": "GS" * 8},
    "kucoin": {"api_key": "KC" * 8, "api_secret": "KS" * 8, "passphrase": "pp"},
    "paradex": {"wallet_address": "0x" + "c" * 40, "agent_private_key": "0x" + "d" * 64},
}

BALANCES_ONLY = sorted(set(valid_venue_ids()) - PER_USER_EXECUTION_VENUES)


def _reply(total_equity="1234.5678", *, usdt=None) -> dict:
    """A unified-account wallet-balance reply as ccxt hands it back: Bybit's
    raw body under `info`, and whatever coin lines it parsed."""
    row = {"accountType": "UNIFIED", "coin": [{"coin": "EUR", "equity": "1000"}]}
    if total_equity is not None:
        row["totalEquity"] = total_equity
    bal: dict = {"info": {"retCode": 0, "result": {"list": [row]}},
                 "EUR": {"free": 1000.0, "used": 0.0, "total": 1000.0}}
    if usdt is not None:
        bal["USDT"] = {"free": usdt, "used": 0.0, "total": usdt}
    return bal


def _host(ex) -> str:
    """Where a ccxt Bybit client's private calls go, as ccxt builds the URL."""
    return ex.implode_hostname(ex.urls["api"]["private"])


def _env(ex) -> str:
    host = _host(ex)
    return {"https://api.bybit.eu": "eu", "https://api.bybit.com": "mainnet",
            "https://api-testnet.bybit.com": "testnet",
            "https://api-demo.bybit.com": "demo"}.get(host, host)


def _bybit(monkeypatch, answers: dict) -> list:
    """Bybit answering by host: ``answers[env]`` is a reply dict, a retCode or
    an exception; a host not named refuses with 10003, as each Bybit does for
    a key from another. Records (env, defaultType, market types loaded)."""
    seen: list = []

    async def fetch_balance(self, params=None):
        env = _env(self)
        seen.append((env, self.options.get("defaultType"),
                     tuple((self.options.get("fetchMarkets") or {}).get("types") or ())))
        a = answers.get(env, 10003)
        if isinstance(a, dict):
            return a
        if isinstance(a, BaseException):
            raise a
        raise AuthenticationError(
            'bybit {"retCode":%d,"retMsg":"refused","result":{},"retExtInfo":{},'
            '"time":1791449888572}' % a)

    monkeypatch.setattr(ccxt_async.bybit, "fetch_balance", fetch_balance)
    return seen


def _envs(seen) -> list:
    return [s[0] for s in seen]


# ── option 2: a bybit.com 10003 asks Bybit EU ────────────────────────────────

def test_a_bybit_eu_key_is_named_on_a_bybit_com_10003(monkeypatch):
    seen = _bybit(monkeypatch, {"eu": _reply()})
    ok, detail = asyncio.run(ec.validate_bybit_credentials(KEY, SECRET))
    assert ok is False, "a Bybit EU key cannot trade this bot's perps; it is not accepted as Bybit"
    assert detail == ec._BYBIT_EU_KEY
    assert _envs(seen) == ["mainnet", "eu"], "testnet and demo are not asked once EU answers"
    for words in ("Bybit EU key", "spot trading only", "perpetual futures",
                  "Connect it as Bybit EU"):
        assert words in detail, words


def test_the_eu_probe_reads_spot_on_api_bybit_eu(monkeypatch):
    seen = _bybit(monkeypatch, {"eu": _reply()})
    asyncio.run(ec.validate_bybit_credentials(KEY, SECRET))
    assert seen[0] == ("mainnet", "swap", ("spot", "linear", "inverse", "option"))
    assert seen[1] == ("eu", "spot", ("spot",)), (
        "the EU probe must load spot markets alone: Bybit EU lists no linear market")


def test_an_eu_host_that_does_not_answer_is_not_read_as_an_eu_key(monkeypatch):
    # api.bybit.eu refuses some regions outright; that is not the key answering.
    seen = _bybit(monkeypatch, {"eu": ExchangeNotAvailable("403 Forbidden CloudFront")})
    ok, detail = asyncio.run(ec.validate_bybit_credentials(KEY, SECRET))
    assert ok is False
    assert _envs(seen) == ["mainnet", "eu", "testnet", "demo"]
    assert "Bybit mainnet does not know this API key (code 10003)" in detail
    assert "Bybit EU key" not in detail


def test_setexchange_names_the_eu_key_and_stores_nothing(monkeypatch):
    import bot.core.secrets_vault as sv
    import bot.skills.account_commands as ac
    from bot.skills.telegram_handler import TelegramHandler

    stored: dict = {}
    monkeypatch.setattr(sv, "store_secrets", lambda m: stored.update(m) or list(m))
    monkeypatch.setattr(ac, "CONFIG", SimpleNamespace(exchange=SimpleNamespace(
        sandbox=False, hyperliquid_testnet=False, trade_mode="futures",
        bybit_api_key="", bybit_api_secret="", bingx_api_key="", bingx_api_secret="",
        api_key="", api_secret="", passphrase="")))
    seen = _bybit(monkeypatch, {"eu": _reply()})
    sent: list = []
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, *a, **k):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: True
    h.engine = SimpleNamespace(live_executor=SimpleNamespace(_exchange="stale"),
                               _invalidate_live_balance_cache=lambda: None)

    async def _delete():
        return None

    update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                             effective_chat=SimpleNamespace(id=1, type="private"),
                             message=SimpleNamespace(delete=_delete), callback_query=None)
    asyncio.run(h._cmd_setexchange(update, SimpleNamespace(args=["bybit", KEY, SECRET])))
    assert _envs(seen) == ["mainnet", "eu"]
    assert stored == {}
    assert "Nothing was changed" in sent[-1]
    assert "This is a Bybit EU key" in sent[-1]
    assert "retCode" not in sent[-1]


# ── option 3: Bybit EU, linked and read, never traded ────────────────────────

def test_bybit_eu_is_a_venue_that_is_linked_and_never_traded():
    assert "bybiteu" in valid_venue_ids()
    assert "bybiteu" in ec.valid_venue_ids()
    assert ec._VENUE_FIELDS["bybiteu"] == ("api_key", "api_secret")
    assert "bybiteu" not in PER_USER_EXECUTION_VENUES
    why = per_user_execution_refusal("bybiteu")
    assert why == ("Bybit EU is linked for balances only. Bybit EU offers spot trading "
                   "only, not the perpetual futures this bot trades, so this bot places "
                   "no order there.")
    # Not the "not driven yet" sentence: driving adds no market the venue lacks.
    assert "has not been driven" not in why
    # And the other balances-only venues keep theirs.
    assert "has not been driven" in per_user_execution_refusal("okx")


def test_the_adapter_client_is_bybit_on_the_eu_host_reading_spot():
    v = get_venue("bybiteu")
    assert v.id == "bybiteu" and v.display_name == "Bybit EU"
    ex = v.create_exchange(None, credentials=EU)
    try:
        assert _host(ex) == "https://api.bybit.eu"
        assert ex.options["defaultType"] == "spot"
        assert ex.options["fetchMarkets"]["types"] == ["spot"]
    finally:
        asyncio.run(ex.close())
    # No operator keys: `/venue bybiteu` refuses, and says how to link instead.
    cfg = SimpleNamespace(sandbox=True, bybit_api_key="k" * 12, bybit_api_secret="s" * 12)
    assert v.has_operator_credentials(cfg) is False
    assert v.uses_sandbox(cfg) is False
    with pytest.raises(RuntimeError, match="/connect bybiteu"):
        v.create_exchange(cfg)


def test_the_key_check_reads_total_equity_on_the_eu_host_alone(monkeypatch):
    seen = _bybit(monkeypatch, {"eu": _reply()})
    ok, detail = asyncio.run(ec.validate_venue_credentials("bybiteu", dict(EU)))
    assert (ok, detail) == (True, "1234.57 USD total equity")
    assert seen == [("eu", "spot", ("spot",))]


@pytest.mark.parametrize("reply", [
    _reply(total_equity=None, usdt=0.0),   # a USDT line is not the wallet
    _reply(total_equity=""),               # Bybit's empty string
    {"info": {"result": {"list": []}}},    # no account row at all
    {"USDT": {"free": 5.0, "total": 5.0}},  # no raw body
])
def test_a_reply_with_no_total_equity_is_not_an_empty_account(monkeypatch, reply):
    _bybit(monkeypatch, {"eu": reply})
    ok, detail = asyncio.run(ec.validate_venue_credentials("bybiteu", dict(EU)))
    assert ok is True
    assert detail == "authenticated, but no readable total equity in Bybit EU's reply"
    snap = asyncio.run(ec.balance_snapshot("bybiteu", dict(EU)))
    assert snap["ok"] is True and snap["equity_usd"] is None
    assert "0.00" not in snap["detail"]


def test_zero_equity_is_a_reading():
    assert ec._bybit_total_equity(_reply(total_equity="0")) == 0.0
    assert ec._bybit_total_equity(_reply(total_equity="0")) is not None


@pytest.mark.parametrize("code,words", [
    (10003, "A key from bybit.com is not a Bybit EU key"),
    (10005, "Read permission is enough"),
    (10004, "this API secret is not the key's"),
    (10010, "this server's IP"),
    (33004, "has expired"),
])
def test_each_eu_refusal_says_what_to_fix_and_asks_nowhere_else(monkeypatch, code, words):
    seen = _bybit(monkeypatch, {"eu": code})
    ok, detail = asyncio.run(ec.validate_venue_credentials("bybiteu", dict(EU)))
    assert ok is False
    assert _envs(seen) == ["eu"], "Bybit EU has no testnet or demo to ask"
    assert words in detail and f"code {code}" in detail
    assert "retCode" not in detail


def test_an_eu_refusal_with_no_sentence_keeps_the_venue_s_words(monkeypatch):
    _bybit(monkeypatch, {"eu": 10006})
    ok, detail = asyncio.run(ec.validate_venue_credentials("bybiteu", dict(EU)))
    assert ok is False and "10006" in detail


def test_the_balance_reader_reads_bybit_eu_equity_in_usd(monkeypatch):
    seen = _bybit(monkeypatch, {"eu": _reply(usdt=3.0)})
    snap = asyncio.run(ec.balance_snapshot("bybiteu", dict(EU)))
    assert snap == {"ok": True, "venue": "bybiteu", "currency": "USD",
                    "equity_usd": 1234.57, "detail": "1234.57 USD total"}
    assert seen == [("eu", "spot", ("spot",))]
    # Bybit (bybit.com) is read as before: its USDT line, on its own host.
    seen.clear()
    _bybit(monkeypatch, {"mainnet": {"USDT": {"free": 7.0, "used": 1.0, "total": 8.0}}})
    snap = asyncio.run(ec.balance_snapshot("bybit", dict(EU)))
    assert snap["currency"] == "USDT" and snap["equity_usd"] == 8.0


def test_every_eu_sentence_fits_the_website_s_ack():
    for s in [ec._BYBIT_EU_KEY, *ec._BYBIT_EU_REFUSALS.values()]:
        assert len(s) <= 180, (len(s), s)


def test_the_paste_check_takes_a_bybit_eu_key():
    assert ec.basic_venue_format_ok("bybiteu", dict(EU)) is True
    assert ec.basic_venue_format_ok("bybiteu", {"api_key": "short", "api_secret": SECRET}) is False


def test_scan_bybiteu_reads_the_eu_host_s_spot_markets_keyless(monkeypatch):
    built: list = []

    class _Client:
        def __init__(self, opts):
            built.append(opts)

        async def close(self):
            pass

    monkeypatch.setattr(ms, "ccxt", SimpleNamespace(bybit=_Client))
    scanner = ms.MarketScanner()
    asyncio.run(scanner.venue_data_exchange("bybiteu"))
    asyncio.run(scanner.venue_data_exchange("bybit"))
    eu, com = built
    assert eu["hostname"] == "bybit.eu" and eu["options"]["defaultType"] == "spot"
    assert "apiKey" not in eu and "secret" not in eu
    # The other arm: bybit.com is still read on its own host, as perps.
    assert "hostname" not in com and com["options"] == {"defaultType": "swap"}
    asyncio.run(scanner.close())


# ── linking a balances-only venue does not stop trading elsewhere ────────────

def _store(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    return ExchangeCredentialStore(creds_file=str(tmp_path / "creds.enc"),
                                   key_file=str(tmp_path / ".key"))


@pytest.mark.parametrize("venue", BALANCES_ONLY)
def test_a_balances_only_venue_linked_beside_a_traded_one_does_not_take_the_slot(tmp_path, venue):
    s = _store(tmp_path)
    s.set_venue("u1", "bitget", CREDS["bitget"])
    s.set_venue("u1", venue, CREDS[venue])
    assert s.get_venue("u1") == "bitget"
    assert sorted(s.list_venues("u1")) == sorted(["bitget", venue])
    assert s.get_for_venue("u1", venue) == CREDS[venue], "the keys are stored all the same"


def test_a_traded_venue_still_takes_the_slot(tmp_path):
    s = _store(tmp_path)
    s.set_venue("u1", "bitget", CREDS["bitget"])
    s.set_venue("u1", "bybit", CREDS["bybit"])
    assert s.get_venue("u1") == "bybit"
    # Including from a balances-only venue that was the first one linked.
    s.set_venue("u2", "bybiteu", EU)
    assert s.get_venue("u2") == "bybiteu", "a first venue has nothing to take over"
    s.set_venue("u2", "bitget", CREDS["bitget"])
    assert s.get_venue("u2") == "bitget"


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()  # the shared operator executor, a sentinel
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    eng.risk = object()
    eng.slippage = None
    return eng


def test_the_engine_still_trades_bitget_after_bybit_eu_is_linked(tmp_path):
    s = _store(tmp_path)
    s.set_venue("u1", "bitget", CREDS["bitget"])
    s.set_venue("u1", "bybiteu", EU)
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=s), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: None):
        cfg.per_user_live_enabled = True
        ex = eng._executor_for("u1")
        assert isinstance(ex, LiveExecutor) and ex._venue.id == "bitget"
        # Why the slot matters: with Bybit EU active, there is no executor.
        s.set_active("u1", "bybiteu")
        eng._user_executors.clear()
        assert eng._executor_for("u1") is None
        assert "spot trading only" in eng.execution_refusal("u1")


# ── /connect bybiteu, through the real handler and the real check ────────────

class _Host:
    def __init__(self):
        self.sent: list = []
        self.invalidated: list = []
        self.engine = SimpleNamespace(invalidate_user_executor=self.invalidated.append)

    async def _guard(self, update, command="", ctx=None):
        return True

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update):
        return "4242"


def test_connect_bybiteu_links_it_for_balances_and_keeps_bitget_active(monkeypatch, tmp_path):
    from bot.skills.telegram_handler import TelegramHandler

    s = _store(tmp_path)
    s.set_venue("4242", "bitget", CREDS["bitget"])
    monkeypatch.setattr(ec, "get_credential_store", lambda: s)
    seen = _bybit(monkeypatch, {"eu": _reply()})

    async def _delete():
        return None

    update = SimpleNamespace(message=SimpleNamespace(delete=_delete),
                             effective_chat=SimpleNamespace(type="private"))
    host = _Host()
    asyncio.run(TelegramHandler._cmd_connect(host, update,
                                             SimpleNamespace(args=["bybiteu", KEY, SECRET])))
    out = host.sent[-1]
    assert "Bybit EU account linked" in out
    assert "1234.57 USD total equity" in out
    assert "spot trading only" in out and "places no order there" in out
    assert _envs(seen) == ["eu"]
    assert s.get_for_venue("4242", "bybiteu") == EU
    assert s.get_venue("4242") == "bitget", "linking Bybit EU took the trading slot"

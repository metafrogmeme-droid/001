"""A Bitget account in Classic mode was asked the unified API on every call.

Every Bitget client the bot built carried ``options["uta"] = True``. An
account in Classic mode refuses each unified call with code 40084 ("You are
in Classic Account mode, and the Unified Account API is not supported"), and
on such an account:

  * the startup credential check read the refusal as a rejected key, said
    "exchange auth FAILED" and halted new entries, on a key that read the
    classic API;
  * the balance behind every equity read, the scan payload's balance and the
    key check at ``/connect`` all failed the same way;
  * the position sync failed on every pass and fed the warning-rate breaker;
  * the reconcile sweep's read raised, was logged at DEBUG, and a row the
    venue no longer held stayed in the book for good.

The executor already had classic branches and already probed the account
type. That reading never reached the client's option, which is the fact that
routes every ccxt call.

What is pinned here, both arms each time: the venue's 40084 (read from the
reply's ``code`` field) switches that account to the classic v2 API and is
remembered per account; an account that never hears it is asked exactly what
it was asked before; 40084 is never a credential verdict and every other
refusal still is; a row the venue reports flat is cleared by reconcile, said
in the audit log, with no order sent.

No test here reaches a venue. The clients are ccxt's real Bitget clients with
the transport replaced, so the routing under ``uta`` is ccxt's own.
"""
from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from urllib.parse import urlparse

import ccxt
import ccxt.async_support as ccxt_async
import pytest

import bot.core.live_executor as le
import bot.main as main
from bot.config import CONFIG
from bot.core import bitget_account_mode as mode
from bot.core.close_lookup import client_is_uta
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue

BITGET = get_venue("bitget")

# Bitget's replies, as the venue writes them. The refusal's requestTime is
# chosen to spell an auth marker (40012) and a transport marker (429): a
# classifier that reads the text instead of the code field gets both wrong.
REFUSAL = ('{"code":"40084","msg":"You are in Classic Account mode, and the '
           'Unified Account API is not supported","requestTime":1794001242900,'
           '"data":null}')
BAD_PASSPHRASE = ('{"code":"40012","msg":"apikey/password is incorrect",'
                  '"requestTime":1794001242900,"data":null}')
WRONG_FAMILY_UNIFIED = ('{"code":"40085","msg":"You are in Unified Account mode",'
                        '"requestTime":1794001242900,"data":null}')
#: A healthy reply whose requestTime happens to contain the digits 40084.
HEALTHY_WITH_THE_DIGITS = '{"code":"00000","msg":"success","requestTime":1794008400000}'

CLASSIC_ACCOUNTS = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
                    "data": [{"marginCoin": "USDT", "locked": "0", "available": "90.5",
                              "crossedMaxAvailable": "90.5", "isolatedMaxAvailable": "90.5",
                              "maxTransferOut": "90.5", "accountEquity": "101.25",
                              "usdtEquity": "101.25", "btcEquity": "0.001",
                              "unrealizedPL": "1.25", "crossedRiskRate": "0"}]}
UNIFIED_ASSETS = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
                  "data": {"accountEquity": "147.46", "usdtEquity": "147.46",
                           "usdtUnrealisedPnl": "0", "unrealisedPnl": "0",
                           "assets": [{"coin": "USDT", "equity": "147.46",
                                       "balance": "147.46", "available": "147.46",
                                       "locked": "0", "debt": "0", "bonus": "0"}]}}
HOLD_MODE = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
             "data": {"marginCoin": "USDT", "holdMode": "one_way_mode",
                      "marginMode": "isolated", "posMode": "one_way_mode"}}

MARKET = dict(id="INJUSDT", symbol="INJ/USDT:USDT", base="INJ", quote="USDT",
              settle="USDT", baseId="INJ", quoteId="USDT", settleId="USDT", type="swap",
              spot=False, swap=True, contract=True, linear=True, inverse=False,
              contractSize=1, active=True, precision={"amount": 0.1, "price": 0.001},
              info={})


def _v2_row(total="2.0", leverage="5"):
    """An open position as ``/api/v2/mix/position/all-position`` writes it."""
    return {"marginCoin": "USDT", "symbol": "INJUSDT", "holdSide": "long",
            "openDelegateSize": "0", "marginSize": "4.0", "available": total,
            "locked": "0", "total": total, "leverage": leverage,
            "achievedProfits": "0", "openPriceAvg": "10.0", "marginMode": "isolated",
            "posMode": "one_way_mode", "unrealizedPL": "0", "liquidationPrice": "0",
            "keepMarginRate": "0.01", "markPrice": "10.0", "breakEvenPrice": "10.0",
            "totalFee": "0", "deductedFee": "0", "marginRatio": "0.01",
            "assetMode": "single", "uTime": "1", "autoMargin": "off", "cTime": "1"}


def _refuse(ex, url, body):
    """Raise the venue's refusal the way ccxt does: through the client's own
    error mapping, so the exception class and its text are ccxt's."""
    ex.handle_errors(400, "Bad Request", url, "GET", {}, body, json.loads(body),
                     None, None)
    raise AssertionError("ccxt did not raise on a refusal body")


class _Account:
    """One Bitget account behind a replaced transport.

    A Classic one refuses every ``/api/v3`` path with 40084 and answers
    ``/api/v2/mix``. A unified one answers ``/api/v3`` and FAILS THE TEST if
    the classic API is asked, which is the arm that says nothing changed for
    it. Any request that is not a GET fails the test: no path here may send
    an order. ``refuse_all`` answers every request with that body instead.
    """

    def __init__(self, *, classic, positions=(), refuse_all=None,
                 classic_refuses=None):
        self.classic = classic
        self.positions = list(positions)
        self.refuse_all = refuse_all
        self.classic_refuses = classic_refuses   # what /api/v2/mix answers with
        self.calls: list[tuple[str, str]] = []
        self.reloads: list[bool] = []
        self.reload_fails = False

    def paths(self):
        return [p for _, p in self.calls]

    def answer(self, ex, url, method):
        path = urlparse(url).path
        self.calls.append((method, path))
        if method != "GET":
            raise AssertionError(f"an order route was asked: {method} {path}")
        if self.refuse_all is not None:
            _refuse(ex, url, self.refuse_all)
        if path.startswith("/api/v3/"):
            if self.classic:
                _refuse(ex, url, REFUSAL)
            if path == "/api/v3/account/assets":
                return copy.deepcopy(UNIFIED_ASSETS)
            if path == "/api/v3/position/current-position":
                return {"code": "00000", "data": {"list": []}}
            raise AssertionError(f"unplanned unified path {path}")
        if not self.classic:
            raise AssertionError(f"a unified account was asked the classic API: {path}")
        if self.classic_refuses is not None:
            _refuse(ex, url, self.classic_refuses)
        if path == "/api/v2/mix/account/accounts":
            return copy.deepcopy(CLASSIC_ACCOUNTS)
        if path == "/api/v2/mix/account/account":
            return copy.deepcopy(HOLD_MODE)
        if path == "/api/v2/mix/position/all-position":
            return {"code": "00000", "msg": "success", "data": copy.deepcopy(self.positions)}
        raise AssertionError(f"unplanned classic path {path}")

    def attach(self, ex):
        async def fetch(url, method="GET", headers=None, body=None):
            return self.answer(ex, url, method)

        async def load_markets(reload=False, params={}):
            self.reloads.append(bool(reload))
            if reload and self.reload_fails:
                raise ccxt.NetworkError("bitget GET /api/v2/mix/market/contracts")
            return ex.markets

        ex.fetch = fetch
        ex.load_markets = load_markets
        return ex

    def attach_sync(self, ex):
        ex.fetch = lambda url, method="GET", headers=None, body=None: (
            self.answer(ex, url, method))
        ex.load_markets = lambda reload=False, params={}: ex.markets
        return ex


_CFG = SimpleNamespace(trade_mode="futures", sandbox=False, api_key="",
                       api_secret="", passphrase="")


def _built(account, key="k", *, markets=True):
    """A client from the VENUE's own builder, for the account with this key."""
    ex = BITGET.create_exchange(
        _CFG, {"api_key": key, "api_secret": "s", "passphrase": "p"})
    if markets:
        ex.set_markets([ex.safe_market_structure(dict(MARKET))])
    return account.attach(ex)


def _executor(tmp_path, exchange):
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget")
    e._exchange = exchange
    e._risk_engine = MagicMock()
    return e


def _tracked(e, *, leverage=10):
    pos = LivePosition(trade_id="T-ghost", symbol="INJ/USDT", direction="LONG",
                       entry_price=10.0, quantity=2.0, cost_usd=2.0, stop_loss=9.0,
                       take_profit=12.0, leverage=leverage, status="open")
    e._positions = {pos.trade_id: pos}
    return pos


@pytest.fixture
def audits(monkeypatch):
    calls: list = []

    def _fake_audit(log, msg, **kw):
        calls.append({"msg": msg, **kw})

    monkeypatch.setattr(le, "audit", _fake_audit)
    monkeypatch.setattr(main, "audit", _fake_audit)
    return calls


def _said(audits, action, result=None):
    return [c for c in audits if c.get("action") == action
            and (result is None or c.get("result") == result)]


# ── the refusal ──────────────────────────────────────────────────────────

class TestTheRefusalIsReadFromTheCodeField:

    def test_the_venues_40084_is_one(self):
        assert mode.is_classic_refusal("bitget " + REFUSAL)
        assert mode.is_classic_refusal(ccxt.ExchangeError("bitget " + REFUSAL))

    @pytest.mark.parametrize("err", [
        "bitget " + BAD_PASSPHRASE,
        "bitget " + WRONG_FAMILY_UNIFIED,
        "bitget " + HEALTHY_WITH_THE_DIGITS,
        "bitget GET https://api.bitget.com/api/v3/account/assets request timed out",
        "something nobody has seen before 40084",
        "",
        None,
    ])
    def test_nothing_else_is(self, err):
        assert not mode.is_classic_refusal(err)

    def test_the_digits_alone_are_in_the_fixture(self):
        # The fixture must be able to produce the mistake it guards against.
        assert "40084" in HEALTHY_WITH_THE_DIGITS
        assert "40012" in REFUSAL and "429" in REFUSAL


# ── the memory ───────────────────────────────────────────────────────────

class TestAnAccountIsRememberedPerKey:

    def test_an_unknown_account_is_built_unified_as_before(self):
        ex = _built(_Account(classic=False))
        assert mode.uta_option("k") is True
        assert ex.options["uta"] is True and client_is_uta(ex)

    def test_a_refused_account_is_classic_and_only_that_one(self):
        ex = _built(_Account(classic=True), "k")
        assert mode.note_classic_refusal(ex, ccxt.ExchangeError("bitget " + REFUSAL))
        assert ex.options["uta"] is False and not client_is_uta(ex)
        assert mode.is_classic("k") and mode.uta_option("k") is False
        assert not mode.is_classic("k2") and mode.uta_option("k2") is True
        # The next client for that key starts classic; another key does not.
        assert _built(_Account(classic=True), "k").options["uta"] is False
        assert _built(_Account(classic=False), "k2").options["uta"] is True

    def test_the_key_itself_is_not_kept(self):
        ex = _built(_Account(classic=True), "k")
        mode.note_classic_refusal(ex, "bitget " + REFUSAL)
        assert mode._classic_accounts and "k" not in mode._classic_accounts
        assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in mode._classic_accounts)

    def test_another_error_records_nothing(self):
        ex = _built(_Account(classic=False), "k")
        assert not mode.note_classic_refusal(ex, "bitget " + BAD_PASSPHRASE)
        assert ex.options["uta"] is True and not mode._classic_accounts

    @pytest.mark.parametrize("stand_in", [
        MagicMock(),
        SimpleNamespace(options={"uta": True}),              # no key to name it by
        SimpleNamespace(options=None, apiKey="k"),           # nothing to switch
        SimpleNamespace(options={"uta": True}, apiKey=""),
    ])
    def test_a_stand_in_is_neither_recorded_nor_written(self, stand_in):
        before = copy.deepcopy(getattr(stand_in, "options", None))
        assert not mode.note_classic_refusal(stand_in, "bitget " + REFUSAL)
        assert not mode._classic_accounts
        if isinstance(before, dict):
            assert stand_in.options == before


# ── the balance read ─────────────────────────────────────────────────────

class TestTheBalanceRead:

    async def test_a_classic_account_is_read_through_v2(self, tmp_path, audits):
        acct = _Account(classic=True)
        ex = _built(acct)
        e = _executor(tmp_path, ex)
        out = await e.fetch_balance()
        assert "error" not in out, out
        assert out["total"] == pytest.approx(101.25)
        assert out["equity_source"] == "usdtEquity", (
            "marked equity was labelled a wallet balance")
        assert out["free"] == pytest.approx(90.5)
        assert acct.paths() == ["/api/v3/account/assets", "/api/v2/mix/account/accounts"]
        assert not client_is_uta(ex) and e._is_uta is False
        assert mode.is_classic("k") and e._account_is_classic()
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1

        # Remembered: the second read asks the classic API only, and says nothing.
        again = await e.fetch_balance()
        assert again["total"] == pytest.approx(101.25)
        assert acct.paths()[2:] == ["/api/v2/mix/account/accounts"]
        assert len(_said(audits, "account_mode")) == 1

    async def test_a_unified_account_is_asked_what_it_was_asked_before(
            self, tmp_path, audits):
        acct = _Account(classic=False)   # fails the test on any classic path
        ex = _built(acct)
        e = _executor(tmp_path, ex)
        out = await e.fetch_balance()
        assert "error" not in out, out
        assert out["total"] == pytest.approx(147.46)
        assert out["equity_source"] == "usdtEquity"
        assert acct.paths() == ["/api/v3/account/assets"]
        assert client_is_uta(ex) and e._is_uta is None
        assert not mode._classic_accounts and not e._account_is_classic()
        assert not _said(audits, "account_mode")

    @pytest.mark.parametrize("body", [BAD_PASSPHRASE, WRONG_FAMILY_UNIFIED])
    async def test_any_other_refusal_is_still_unread(self, tmp_path, audits, body):
        acct = _Account(classic=False, refuse_all=body)
        ex = _built(acct)
        e = _executor(tmp_path, ex)
        out = await e.fetch_balance()
        assert out["error"] and out["total"] == 0
        assert json.loads(body)["code"] in out["error"]
        assert acct.paths() == ["/api/v3/account/assets"], "a rejected key was retried"
        assert client_is_uta(ex) and not mode._classic_accounts
        assert not _said(audits, "account_mode")

    async def test_a_timeout_is_still_unread(self, tmp_path):
        ex = _built(_Account(classic=False))

        async def _slow(params=None):
            raise ccxt.RequestTimeout("bitget GET /api/v3/account/assets")
        ex.privateUtaGetV3AccountAssets = _slow
        out = await _executor(tmp_path, ex).fetch_balance()
        assert out["error"] and client_is_uta(ex) and not mode._classic_accounts

    async def test_a_market_set_loaded_as_unified_is_reloaded_once(self, tmp_path):
        acct = _Account(classic=True)
        e = _executor(tmp_path, _built(acct, markets=True))
        await e.fetch_balance()
        await e.fetch_balance()
        assert acct.reloads.count(True) == 1

    async def test_no_market_set_means_nothing_to_reload(self, tmp_path):
        acct = _Account(classic=True)
        e = _executor(tmp_path, _built(acct, markets=False))
        out = await e.fetch_balance()
        assert "error" not in out and True not in acct.reloads

    async def test_a_reload_that_fails_does_not_undo_the_switch(self, tmp_path):
        acct = _Account(classic=True)
        acct.reload_fails = True
        ex = _built(acct, markets=True)
        out = await _executor(tmp_path, ex).fetch_balance()
        assert "error" not in out and out["total"] == pytest.approx(101.25)
        assert not client_is_uta(ex)

    async def test_two_reads_refused_at_once_say_it_once(self, tmp_path, audits):
        """Two calls already on the wire when the venue answers: both are
        refused, both may ask again, and the switch is said and done once."""
        acct = _Account(classic=True)
        ex = _built(acct, markets=True)
        e = _executor(tmp_path, ex)
        refusal = ccxt.ExchangeError("bitget " + REFUSAL)
        assert await e._switch_to_classic(ex, refusal) is True
        assert await e._switch_to_classic(ex, refusal) is True
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1
        assert acct.reloads.count(True) == 1

    async def test_the_switch_is_bitgets_alone(self, tmp_path, audits):
        ex = _built(_Account(classic=True))
        e = _executor(tmp_path, ex)
        e._venue = get_venue("bybit")
        assert await e._switch_to_classic(ex, ccxt.ExchangeError("bybit " + REFUSAL)) is False
        assert client_is_uta(ex) and not mode._classic_accounts
        # Even with that key's Bitget account marked, another venue's
        # executor does not read its positions through Bitget's classic API.
        assert mode.note_classic_refusal(ex, "bitget " + REFUSAL)
        assert e._account_is_classic() is False
        e._venue = BITGET
        assert e._account_is_classic() is True

    async def test_another_venues_list_shaped_reply_is_left_as_it_was(self, tmp_path):
        """The rows-as-`info` reading is Bitget's classic parse. A list from
        another venue is not known to carry account equity, and stays unread."""
        e = _executor(tmp_path, SimpleNamespace(
            options={}, fetch_balance=AsyncMock(return_value={
                "info": [{"equity": "9.0"}], "USDT": {"free": 3.0, "used": 0.0,
                                                      "total": 3.0}})))
        e._venue = get_venue("bybit")
        out = await e.fetch_balance()
        assert out["total"] == pytest.approx(3.0)
        assert out["equity_source"] == "wallet_total"


# ── the credential check ─────────────────────────────────────────────────

def _operator_cfg():
    p = patch("bot.main.CONFIG")
    m = p.start()
    m.simulation_mode = False
    m.live_trading_enabled = True
    m.per_user_live_enabled = True
    m.exchange.api_key = "k"
    m.exchange.api_secret = "s"
    m.exchange.passphrase = "p"
    m.exchange.sandbox = False
    m.telegram.chat_id = "999"
    m.telegram.admin_ids = ""
    return p


def _engine(executor):
    marks: list = []
    eng = Mock()
    eng.live_executor = executor
    eng.set_live_auth_status = (
        lambda ok, detail="", user_id="": marks.append((user_id, ok)))
    return eng, marks


class _Answers:
    def __init__(self, bal):
        self._bal = bal

    async def fetch_balance(self):
        return self._bal


def _unread(body):
    return {"error": "bitget " + body, "free": 0, "used": 0, "total": 0}


class TestItIsNotACredentialVerdict:

    def test_the_classifier_reads_the_code_not_the_text(self):
        text = "bitget " + REFUSAL
        assert main._is_account_mode_refusal(text)
        assert not main._is_auth_rejection(text), (
            "the requestTime spelled 40012 and the refusal was read as a bad key")
        assert not main._is_transport_failure(text), (
            "the requestTime spelled 429 and the refusal was read as a blip")

    @pytest.mark.parametrize("body", [BAD_PASSPHRASE])
    def test_a_real_rejection_is_still_one(self, body):
        text = "bitget " + body
        assert not main._is_account_mode_refusal(text)
        assert main._is_auth_rejection(text)
        assert not main._is_transport_failure(text)

    @pytest.mark.parametrize("text", [
        "bitget " + WRONG_FAMILY_UNIFIED.replace("1794001242900", "1794000000000"),
        "something nobody has seen before",
        "",
    ])
    def test_an_unknown_error_still_fails_closed(self, text):
        assert not main._is_account_mode_refusal(text)
        assert not main._is_transport_failure(text), "the caller halts on this"

    async def test_the_operator_check_marks_nothing_down_on_40084(self, audits):
        eng, marks = _engine(_Answers(_unread(REFUSAL)))
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        try:
            await main._credential_preflight(eng, bot)
        finally:
            p.stop()
        assert marks == [], "an account-mode answer latched live auth"
        assert _said(audits, "cred_preflight", "ACCOUNT_MODE")
        sent = bot.send_message.await_args.kwargs["text"]
        assert "Classic" in sent and "auth FAILED" not in sent
        assert "NOT halted" in sent

    @pytest.mark.parametrize("body", [BAD_PASSPHRASE, WRONG_FAMILY_UNIFIED])
    async def test_the_operator_check_still_halts_on_anything_else(self, audits, body):
        eng, marks = _engine(_Answers(_unread(body)))
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        try:
            await main._credential_preflight(eng, bot)
        finally:
            p.stop()
        assert marks == [("", False)]
        assert not _said(audits, "cred_preflight", "ACCOUNT_MODE")
        assert "auth FAILED" in bot.send_message.await_args.kwargs["text"]

    async def test_the_check_passes_on_a_classic_account(self, tmp_path, audits):
        """End to end: the real executor, the real balance read, 40084 first."""
        e = _executor(tmp_path, _built(_Account(classic=True)))
        eng, marks = _engine(e)
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        try:
            await main._credential_preflight(eng, bot)
        finally:
            p.stop()
        assert marks == [("", True)]
        assert _said(audits, "cred_preflight", "OK")
        assert not _said(audits, "cred_preflight", "FAIL")
        bot.send_message.assert_not_awaited()

    async def test_a_key_the_classic_api_rejects_still_halts(self, tmp_path, audits):
        """40084 does not prove the key. The classic read that follows it is
        the one that judges it, and a rejection there halts as it always did."""
        acct = _Account(classic=True, classic_refuses=BAD_PASSPHRASE)
        e = _executor(tmp_path, _built(acct))
        eng, marks = _engine(e)
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        try:
            await main._credential_preflight(eng, bot)
        finally:
            p.stop()
        assert acct.paths() == ["/api/v3/account/assets", "/api/v2/mix/account/accounts"]
        assert marks == [("", False)], "a rejected key passed behind an account-mode answer"
        assert "auth FAILED" in bot.send_message.await_args.kwargs["text"]
        assert not _said(audits, "cred_preflight", "OK")

    async def test_the_per_user_check_agrees(self, audits):
        operator = object()
        execs = {"classic": _Answers(_unread(REFUSAL)),
                 "rejected": _Answers(_unread(BAD_PASSPHRASE)),
                 "healthy": _Answers({"total": 100.0})}
        eng, marks = _engine(operator)
        eng._executor_for = lambda uid: execs.get(uid, operator)
        store = Mock()
        store.user_ids = lambda: list(execs)
        store.credential_state = lambda uid: "ok"
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        st = patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store)
        st.start()
        try:
            await main._per_user_credential_preflight(eng, bot)
        finally:
            st.stop()
            p.stop()
        assert ("rejected", False) in marks and ("healthy", True) in marks
        assert not [m for m in marks if m[0] == "classic"], (
            "a user was marked on an account-mode answer")
        assert _said(audits, "cred_preflight_users", "ACCOUNT_MODE")
        sent = " ".join(c.kwargs["text"] for c in bot.send_message.await_args_list)
        assert "rejected" in sent and "classic" not in sent, (
            "a Classic account's user was told to re-enter a key")


# ── the position sync ────────────────────────────────────────────────────

def _v3_reader(monkeypatch, result):
    seen: list = []

    def _read(*_a):
        seen.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(LiveExecutor, "_fetch_v3_positions_raw", staticmethod(_read))
    return seen


class TestThePositionSync:

    async def _classic(self, tmp_path, positions):
        acct = _Account(classic=True, positions=positions)
        e = _executor(tmp_path, _built(acct))
        e._save_positions = MagicMock()
        await e.fetch_balance()          # the venue says Classic here
        return acct, e

    async def test_a_classic_account_syncs_from_the_classic_rows(
            self, tmp_path, monkeypatch, audits):
        seen = _v3_reader(monkeypatch, AssertionError("the v3 channel was asked"))
        acct, e = await self._classic(tmp_path, [_v2_row(leverage="5")])
        pos = _tracked(e, leverage=10)
        await e.sync_positions_from_exchange()
        assert seen == []
        assert "/api/v2/mix/position/all-position" in acct.paths()
        assert pos.leverage == 5, "the classic row's leverage was not read"
        assert not _said(audits, "position_sync", "ERROR")
        e._risk_engine.record_warning.assert_not_called()

    async def test_an_empty_classic_book_is_said_and_left_to_reconcile(
            self, tmp_path, monkeypatch, audits):
        _v3_reader(monkeypatch, AssertionError("the v3 channel was asked"))
        _, e = await self._classic(tmp_path, [])
        pos = _tracked(e)
        await e.sync_positions_from_exchange()
        assert _said(audits, "position_sync", "EMPTY")
        assert not _said(audits, "position_sync", "ERROR")
        assert pos.status == "open" and "T-ghost" in e._positions, (
            "the sync removed a row; clearing is reconcile's")
        e._risk_engine.record_warning.assert_not_called()

    async def test_a_classic_read_that_fails_is_a_failed_sync(
            self, tmp_path, monkeypatch, audits):
        _v3_reader(monkeypatch, AssertionError("the v3 channel was asked"))
        acct, e = await self._classic(tmp_path, [_v2_row()])
        pos = _tracked(e, leverage=10)
        acct.refuse_all = BAD_PASSPHRASE
        await e.sync_positions_from_exchange()
        errs = _said(audits, "position_sync", "ERROR")
        assert errs and "classic v2 fetch raised" in errs[0]["msg"]
        assert "40012" in errs[0]["msg"]
        assert "apikey/password" not in errs[0]["msg"], "the venue's text reached the log"
        assert not _said(audits, "position_sync", "EMPTY")
        e._risk_engine.record_warning.assert_called_with("position_sync_fetch")
        assert pos.leverage == 10

    async def test_a_row_without_the_venues_document_is_not_an_empty_book(
            self, tmp_path, monkeypatch, audits):
        _v3_reader(monkeypatch, AssertionError("the v3 channel was asked"))
        _, e = await self._classic(tmp_path, [_v2_row()])
        _tracked(e)

        async def _parsed_only(symbols=None, params=None):
            return [{"symbol": "INJ/USDT:USDT", "contracts": 2.0}]
        e._exchange.fetch_positions = _parsed_only
        await e.sync_positions_from_exchange()
        assert _said(audits, "position_sync", "ERROR")
        assert not _said(audits, "position_sync", "EMPTY"), (
            "rows nobody could read were audited as an empty book")

    async def test_an_account_the_venue_never_refused_still_asks_v3(
            self, tmp_path, monkeypatch, audits):
        """`_is_uta = False` is the PROBE's reading. It moves no channel: only
        the venue's own 40084 does."""
        seen = _v3_reader(monkeypatch, [{"symbol": "INJUSDT", "leverage": "5",
                                         "total": "2.0"}])
        acct = _Account(classic=False)   # fails the test on any classic path
        e = _executor(tmp_path, _built(acct))
        e._save_positions = MagicMock()
        e._is_uta = False
        pos = _tracked(e, leverage=10)
        await e.sync_positions_from_exchange()
        assert seen == [1] and acct.calls == []
        assert pos.leverage == 5

    async def test_an_executor_with_no_client_has_heard_no_refusal(self, tmp_path):
        ex = _built(_Account(classic=True))
        mode.note_classic_refusal(ex, "bitget " + REFUSAL)
        e = _executor(tmp_path, None)
        assert e._account_is_classic() is False
        e._exchange = ex
        assert e._account_is_classic() is True


# ── reconcile, and the row the venue no longer holds ─────────────────────

CLOSE_DATA = {"close_price": 9.5, "pnl": -1.0, "fees": 0.01,
              "reason": "SL HIT (exchange)", "source": "bitget_position_history",
              "pnl_is_net": False}


def _reconciler(tmp_path, acct, *, close_data=None):
    e = _executor(tmp_path, _built(acct))
    e._save_closed_trades = MagicMock()
    e._fire_position_closed = MagicMock()
    e._fetch_bitget_close_data = AsyncMock(return_value=close_data)
    return e


class TestTheRowTheVenueNoLongerHolds:

    async def test_the_first_refused_read_switches_and_the_next_pass_clears_it(
            self, tmp_path, audits):
        acct = _Account(classic=True, positions=[])     # the venue holds nothing
        e = _reconciler(tmp_path, acct, close_data=dict(CLOSE_DATA))
        pos = _tracked(e)

        # Pass 1: the unified read is refused. Said, switched, row kept.
        first = await e.reconcile_positions()
        assert first == [] and pos.status == "open" and "T-ghost" in e._positions
        assert "/api/v3/position/current-position" in acct.paths()
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1
        assert not client_is_uta(e._exchange)
        assert not _said(audits, "reconcile_flat")

        # Pass 2: the classic API answers an empty book. Cleared, and said.
        seen = len(acct.calls)
        second = await e.reconcile_positions()
        assert acct.paths()[seen:] == ["/api/v2/mix/position/all-position"]
        assert pos.status == "closed" and "T-ghost" not in e._positions
        flat = _said(audits, "reconcile_flat", "VENUE_FLAT")
        assert len(flat) == 1 and flat[0]["data"]["trade_id"] == "T-ghost"
        assert "no order" in flat[0]["msg"]
        assert _said(audits, "reconcile_close", "CLOSED")
        assert second and second[0].startswith("RECONCILED")
        # No order was sent to do it: the transport fails on anything but a GET.
        assert {m for m, _ in acct.calls} == {"GET"}
        assert len(_said(audits, "account_mode")) == 1

    async def test_a_row_waiting_for_its_close_data_is_said_once(self, tmp_path, audits):
        acct = _Account(classic=True, positions=[])
        e = _reconciler(tmp_path, acct, close_data=None)
        pos = _tracked(e)
        with patch.object(le.asyncio, "sleep", AsyncMock()):
            for _ in range(3):
                await e.reconcile_positions()
        assert pos.status == "open", "no close data yet: deferred, not booked"
        assert len(_said(audits, "reconcile_flat", "VENUE_FLAT")) == 1

    @pytest.mark.parametrize("row,why", [
        (_v2_row(total="2.0"), "the venue still holds it"),
        (_v2_row(total=""), "its size could not be read"),
    ])
    async def test_a_row_the_venue_does_not_report_flat_is_kept(
            self, tmp_path, audits, row, why):
        acct = _Account(classic=True, positions=[row])
        e = _reconciler(tmp_path, acct, close_data=dict(CLOSE_DATA))
        pos = _tracked(e)
        await e.reconcile_positions()
        await e.reconcile_positions()
        assert pos.status == "open" and "T-ghost" in e._positions, why
        assert not _said(audits, "reconcile_flat") and not _said(audits, "reconcile_close")
        e._fetch_bitget_close_data.assert_not_awaited()

    async def test_a_unified_account_reconciles_through_v3_as_before(
            self, tmp_path, audits):
        acct = _Account(classic=False)   # fails the test on any classic path
        e = _reconciler(tmp_path, acct, close_data=dict(CLOSE_DATA))
        e._hedge_mode = False            # read already: no probe in this pass
        pos = _tracked(e)
        await e.reconcile_positions()
        assert acct.paths() == ["/api/v3/position/current-position"]
        assert pos.status == "closed" and client_is_uta(e._exchange)
        assert not mode._classic_accounts and not _said(audits, "account_mode")
        assert len(_said(audits, "reconcile_flat", "VENUE_FLAT")) == 1

    async def test_another_refusal_keeps_the_row_and_switches_nothing(
            self, tmp_path, audits):
        acct = _Account(classic=False, refuse_all=BAD_PASSPHRASE)
        e = _reconciler(tmp_path, acct, close_data=dict(CLOSE_DATA))
        e._hedge_mode = False
        pos = _tracked(e)
        await e.reconcile_positions()
        assert pos.status == "open" and client_is_uta(e._exchange)
        assert not mode._classic_accounts and not _said(audits, "account_mode")


# ── the other builders ───────────────────────────────────────────────────

@pytest.fixture
def operator_keys():
    saved = {n: getattr(CONFIG.exchange, n)
             for n in ("api_key", "api_secret", "passphrase", "sandbox")}
    for n, v in (("api_key", "k"), ("api_secret", "s"), ("passphrase", "p"),
                 ("sandbox", False)):
        object.__setattr__(CONFIG.exchange, n, v)
    yield
    for n, v in saved.items():
        object.__setattr__(CONFIG.exchange, n, v)


def _async_factory(monkeypatch, acct):
    """Every `ccxt.async_support.bitget(...)` is the real client over `acct`.
    Records the family each client was BUILT in."""
    real, built = ccxt_async.bitget, []

    def factory(cfg):
        ex = acct.attach(real(cfg))
        built.append(ex.options["uta"])
        return ex

    monkeypatch.setattr(ccxt_async, "bitget", factory)
    return built


class TestTheOtherBuildersAskToo:

    async def test_a_classic_key_passes_the_key_check(self, monkeypatch):
        import bot.core.exchange_credentials as ec
        acct = _Account(classic=True)
        built = _async_factory(monkeypatch, acct)
        ok, detail = await ec.validate_bitget_credentials("k", "s", "p", sandbox=False)
        assert ok is True and detail == "90.50 USDT free", detail
        assert acct.paths() == ["/api/v3/account/assets", "/api/v2/mix/account/accounts"]
        # The next check for that key starts on the classic API.
        ok, _ = await ec.validate_bitget_credentials("k", "s", "p", sandbox=False)
        assert ok is True and built == [True, False]
        assert acct.paths()[2:] == ["/api/v2/mix/account/accounts"]

    async def test_a_rejected_key_still_fails_the_key_check(self, monkeypatch):
        import bot.core.exchange_credentials as ec
        acct = _Account(classic=False, refuse_all=BAD_PASSPHRASE)
        built = _async_factory(monkeypatch, acct)
        ok, detail = await ec.validate_bitget_credentials("k", "s", "p", sandbox=False)
        assert ok is False and "40012" in detail
        assert built == [True] and len(acct.calls) == 1, "a rejected key was retried"
        assert not mode._classic_accounts

    async def test_a_unified_key_is_checked_as_before(self, monkeypatch):
        import bot.core.exchange_credentials as ec
        acct = _Account(classic=False)   # fails the test on any classic path
        built = _async_factory(monkeypatch, acct)
        ok, _ = await ec.validate_bitget_credentials("k", "s", "p", sandbox=False)
        assert ok is True and built == [True]
        assert acct.paths() == ["/api/v3/account/assets"]

    async def test_the_balance_card_reads_a_classic_account(self, monkeypatch):
        import bot.core.exchange_credentials as ec
        acct = _Account(classic=True)
        built = _async_factory(monkeypatch, acct)
        fields = {"api_key": "k", "api_secret": "s", "passphrase": "p"}
        snap = await ec.balance_snapshot("bitget", fields, sandbox=False)
        assert snap["ok"] is True and snap["equity_usd"] == pytest.approx(101.25)
        await ec.balance_snapshot("bitget", fields, sandbox=False)
        assert built == [True, False]

    def _scan(self, monkeypatch, tmp_path, acct):
        import bot.skills.scan_skill as ss
        real, built = ccxt.bitget, []

        def factory(cfg):
            ex = acct.attach_sync(real(cfg))
            built.append(ex.options["uta"])
            return ex

        monkeypatch.setattr(ccxt, "bitget", factory)
        monkeypatch.setattr(ss, "_closed_trades_file",
                            lambda: str(tmp_path / "closed_trades.json"))
        return ss, built

    def test_the_scan_payload_reads_a_classic_account(
            self, monkeypatch, tmp_path, operator_keys):
        acct = _Account(classic=True)
        ss, built = self._scan(monkeypatch, tmp_path, acct)
        first = ss._fetch_live_exchange_data()
        assert first["equity"] == pytest.approx(101.25), first
        assert first["open_count"] == 0
        assert acct.paths()[0] == "/api/v3/account/assets"
        seen = len(acct.calls)
        second = ss._fetch_live_exchange_data()
        assert second["equity"] == pytest.approx(101.25)
        assert built == [True, False]
        assert not [p for p in acct.paths()[seen:] if p.startswith("/api/v3/")]

    def test_the_scan_payload_reads_a_unified_account_as_before(
            self, monkeypatch, tmp_path, operator_keys):
        acct = _Account(classic=False)   # fails the test on any classic path
        ss, built = self._scan(monkeypatch, tmp_path, acct)
        out = ss._fetch_live_exchange_data()
        assert out["equity"] == pytest.approx(147.46), out
        assert built == [True] and not mode._classic_accounts
        assert all(p.startswith("/api/v3/") for p in acct.paths())


# ── the harness ──────────────────────────────────────────────────────────

class TestNoAccountLeaksIntoTheNextTest:

    def test_a_marks_an_account(self):
        ex = _built(_Account(classic=True), "k")
        assert mode.note_classic_refusal(ex, "bitget " + REFUSAL)
        assert mode.is_classic("k")

    def test_b_finds_none(self):
        assert not mode._classic_accounts, (
            "tests/conftest.py no longer empties the account modes")

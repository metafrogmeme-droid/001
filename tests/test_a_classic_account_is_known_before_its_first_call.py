"""The account's API family was learned by whichever call tripped over it.

The first production boot of the Classic-account fallback (17 November 2026)
showed three things the tests beside it had not driven:

  * The position sync audited ``Position sync FAILED: classic v2 fetch raised
    ... venue code 40084`` and the executor said "Classic" three seconds
    LATER. The account's mode is remembered per account, but each client was
    switched only by its own refusal. Another reader with the same key (the
    scan payload builds one every tick) had marked the account, so the sync
    took its classic branch on a client that still routed to the unified API.

  * Whichever call came first paid for the detection: a balance read, a
    reconcile read, or a sync that could not detect anything and audited an
    error instead.

  * The startup credential check said "exchange auth FAILED" over a balance
    read that had TIMED OUT. The driver's text for a timeout is only
    ``bitget GET <url>``, with no code and no word a classifier can place,
    and the class that names it was dropped on the way.

So: a freshly built Bitget client asks the venue once, with a time limit,
before it is handed to anyone, and every caller that arrives meanwhile waits
for that one answer. On every hand-out a client that disagrees with what is
known about its account is switched. And a network-class error names its
class, so a timeout reads as "could not reach the venue" and not as a
rejected key.

Both arms each time. No test here reaches a venue: the clients are ccxt's
real Bitget clients over a replaced transport.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, Mock
from urllib.parse import urlparse

import ccxt
import ccxt.async_support as ccxt_async
import pytest

import bot.core.live_executor as le
import bot.main as main
from bot.core import bitget_account_mode as mode
from bot.core.close_lookup import client_is_uta
from bot.core.live_executor import LiveExecutor, _balance_error_text
from tests.test_a_classic_bitget_account_is_read_through_v2 import (
    BAD_PASSPHRASE,
    CLOSE_DATA,
    MARKET,
    REFUSAL,
    _Account,
    _built,
    _engine,
    _executor,
    _operator_cfg,
    _said,
    _tracked,
    _v2_row,
    _v3_reader,
)

ASSETS = "/api/v3/account/assets"
V3_POSITIONS = "/api/v3/position/current-position"
V2_ACCOUNTS = "/api/v2/mix/account/accounts"
V2_ACCOUNT = "/api/v2/mix/account/account"
V2_POSITIONS = "/api/v2/mix/position/all-position"

USER = {"api_key": "k", "api_secret": "s", "passphrase": "p"}
TIMED_OUT = "bitget GET https://api.bitget.com/api/v3/account/assets"


@pytest.fixture
def audits(monkeypatch):
    calls: list = []

    def _fake_audit(log, msg, **kw):
        calls.append({"msg": msg, **kw})

    monkeypatch.setattr(le, "audit", _fake_audit)
    monkeypatch.setattr(main, "audit", _fake_audit)
    return calls


@pytest.fixture
def venue(monkeypatch):
    """Every client the venue's own builder makes is ccxt's real Bitget client
    over the given account. Returns the list of clients it built."""
    def install(acct):
        real, built = ccxt_async.bitget, []

        def factory(cfg):
            ex = acct.attach(real(cfg))
            ex.set_markets([ex.safe_market_structure(dict(MARKET))])
            built.append(ex)
            return ex

        monkeypatch.setattr(ccxt_async, "bitget", factory)
        return built
    return install


def _fresh(tmp_path):
    """An executor that has built no client yet."""
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget", user_id="u1",
                     credentials=dict(USER))
    e._risk_engine = MagicMock()
    e._save_positions = MagicMock()
    assert e._exchange is None
    return e


class _Silent(_Account):
    """A venue that takes the request and does not answer, for ``quiet``
    requests; after that it answers as the account it is."""

    def __init__(self, *, quiet=10**9, **kw):
        super().__init__(**kw)
        self.quiet = quiet

    def attach(self, ex):
        super().attach(ex)
        answering = ex.fetch

        async def fetch(url, method="GET", headers=None, body=None):
            if self.quiet > 0:
                self.quiet -= 1
                self.calls.append((method, urlparse(url).path))
                await asyncio.Event().wait()
            return await answering(url, method, headers, body)

        ex.fetch = fetch
        return ex


# ── the first call ───────────────────────────────────────────────────────────

class TestTheFamilyIsReadBeforeTheFirstCall:

    async def test_a_sync_that_runs_first_reads_the_classic_api(
            self, tmp_path, monkeypatch, audits, venue):
        """The production order: the sync before any balance read."""
        seen = _v3_reader(monkeypatch, AssertionError("the v3 position channel was asked"))
        acct = _Account(classic=True, positions=[_v2_row(leverage="5")])
        venue(acct)
        e = _fresh(tmp_path)
        pos = _tracked(e, leverage=10)
        await e.sync_positions_from_exchange()
        assert acct.paths() == [ASSETS, V2_POSITIONS], (
            "the family read goes out first, then the classic rows")
        assert seen == [] and pos.leverage == 5
        assert not _said(audits, "position_sync", "ERROR"), (
            "the venue naming the family was audited as a failed sync")
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1
        e._risk_engine.record_warning.assert_not_called()

    async def test_a_balance_read_that_runs_first_asks_v3_once(self, tmp_path, audits, venue):
        acct = _Account(classic=True)
        venue(acct)
        out = await _fresh(tmp_path).fetch_balance()
        assert "error" not in out and out["total"] == pytest.approx(101.25)
        assert acct.paths() == [ASSETS, V2_ACCOUNTS]

    async def test_a_reconcile_that_runs_first_clears_the_row_on_that_pass(
            self, tmp_path, audits, venue):
        acct = _Account(classic=True, positions=[])
        venue(acct)
        e = _fresh(tmp_path)
        e._save_positions = LiveExecutor._save_positions.__get__(e)
        e._save_closed_trades = MagicMock()
        e._fire_position_closed = MagicMock()
        e._fetch_bitget_close_data = AsyncMock(return_value=dict(CLOSE_DATA))
        pos = _tracked(e)
        await e.reconcile_positions()
        assert acct.paths() == [ASSETS, V2_ACCOUNT, V2_POSITIONS]
        assert V3_POSITIONS not in acct.paths(), "a unified read went out on a Classic account"
        assert pos.status == "closed", "the row waited a pass for a refusal to be heard"
        assert {m for m, _ in acct.calls} == {"GET"}

    async def test_callers_that_arrive_together_share_one_read(
            self, tmp_path, monkeypatch, audits, venue):
        _v3_reader(monkeypatch, AssertionError("the v3 position channel was asked"))
        acct = _Account(classic=True, positions=[_v2_row()])
        venue(acct)
        e = _fresh(tmp_path)
        _tracked(e)
        bal, _ = await asyncio.gather(e.fetch_balance(), e.sync_positions_from_exchange())
        assert "error" not in bal
        assert acct.paths().count(ASSETS) == 1, "each caller asked the family for itself"
        assert not [p for p in acct.paths() if p.startswith("/api/v3/") and p != ASSETS]
        assert not _said(audits, "position_sync", "ERROR")
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1

    async def test_a_unified_account_is_asked_once_more_and_nothing_else_changes(
            self, tmp_path, audits, venue):
        acct = _Account(classic=False)   # fails the test on any classic path
        built = venue(acct)
        e = _fresh(tmp_path)
        out = await e.fetch_balance()
        assert "error" not in out and out["total"] == pytest.approx(147.46)
        assert acct.paths() == [ASSETS, ASSETS], "the family read, then the read it always made"
        assert client_is_uta(built[0]) and e._is_uta is None
        assert not mode._classic_accounts and not _said(audits, "account_mode")

    async def test_one_client_is_built_and_asked_once(self, tmp_path, venue):
        acct = _Account(classic=True)
        built = venue(acct)
        e = _fresh(tmp_path)
        first = await e._get_exchange()
        again = await e._get_exchange()
        assert first is again and len(built) == 1
        assert acct.paths() == [ASSETS]


class TestAVenueThatDoesNotAnswer:

    async def test_the_first_use_is_bounded_and_decides_nothing(
            self, tmp_path, monkeypatch, audits, venue):
        monkeypatch.setattr(le, "ACCOUNT_FAMILY_READ_S", 0.05)
        acct = _Silent(classic=True)
        built = venue(acct)
        e = _fresh(tmp_path)
        t0 = time.monotonic()
        ex = await e._get_exchange()
        assert time.monotonic() - t0 < 1.0, "the first use waited on a venue that never answered"
        assert ex is built[0] and client_is_uta(ex), "an answer nobody got decided the family"
        assert not mode._classic_accounts and e._is_uta is None
        assert not _said(audits, "account_mode")
        # Not asked again by the hand-out: the next real read is what decides.
        await e._get_exchange()
        assert acct.paths() == [ASSETS]

    async def test_the_next_read_that_is_answered_still_decides(
            self, tmp_path, monkeypatch, audits, venue):
        monkeypatch.setattr(le, "ACCOUNT_FAMILY_READ_S", 0.05)
        acct = _Silent(classic=True, quiet=1)
        venue(acct)
        e = _fresh(tmp_path)
        await e._get_exchange()
        out = await e.fetch_balance()
        assert "error" not in out and out["total"] == pytest.approx(101.25)
        assert acct.paths() == [ASSETS, ASSETS, V2_ACCOUNTS]
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1


# ── the client agrees with what is known about its account ──────────────────

def _marked_by_another_reader(tmp_path, acct):
    """An executor whose client was built unified, on an account another
    client with the same key has since heard is Classic."""
    e = _executor(tmp_path, _built(acct))
    e._save_positions = MagicMock()
    other = _built(_Account(classic=True))
    assert mode.note_classic_refusal(other, "bitget " + REFUSAL)
    # The fixture is the production state: known Classic, client still unified.
    assert e._account_is_classic() and client_is_uta(e._exchange)
    return e


class TestTheClientAgreesWithWhatIsKnown:

    async def test_the_sync_is_not_sent_to_the_unified_api(
            self, tmp_path, monkeypatch, audits):
        _v3_reader(monkeypatch, AssertionError("the v3 position channel was asked"))
        acct = _Account(classic=True, positions=[_v2_row(leverage="5")])
        e = _marked_by_another_reader(tmp_path, acct)
        pos = _tracked(e, leverage=10)
        await e.sync_positions_from_exchange()
        assert acct.paths() == [V2_POSITIONS], "no call went to the unified API"
        assert pos.leverage == 5 and not client_is_uta(e._exchange)
        assert not _said(audits, "position_sync", "ERROR")
        assert len(_said(audits, "account_mode", "CLASSIC")) == 1, "the switch is still said"
        assert e._is_uta is False

    async def test_the_balance_is_not_sent_to_the_unified_api(self, tmp_path, audits):
        acct = _Account(classic=True)
        e = _marked_by_another_reader(tmp_path, acct)
        out = await e.fetch_balance()
        assert "error" not in out and acct.paths() == [V2_ACCOUNTS]

    async def test_a_market_set_loaded_as_unified_is_reloaded_at_the_hand_out(
            self, tmp_path):
        acct = _Account(classic=True)
        e = _marked_by_another_reader(tmp_path, acct)
        await e._get_exchange()
        await e._get_exchange()
        assert acct.reloads.count(True) == 1

    async def test_a_reload_that_never_finishes_does_not_hold_the_client(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(le, "MARKET_RELOAD_S", 0.05)
        acct = _Account(classic=True)
        e = _marked_by_another_reader(tmp_path, acct)

        async def never(reload=False, params={}):
            await asyncio.Event().wait()
        e._exchange.load_markets = never
        t0 = time.monotonic()
        ex = await e._get_exchange()
        assert time.monotonic() - t0 < 1.0 and not client_is_uta(ex)

    async def test_a_40084_inside_the_classic_read_is_not_a_failed_sync(
            self, tmp_path, monkeypatch, audits):
        """Should a still-unified client reach the classic read anyway, the
        refusal is the venue naming the family: switched, and asked once."""
        _v3_reader(monkeypatch, AssertionError("the v3 position channel was asked"))
        acct = _Account(classic=True, positions=[_v2_row(leverage="5")])
        e = _marked_by_another_reader(tmp_path, acct)
        e._get_exchange = AsyncMock(return_value=e._exchange)   # handed out as it is
        pos = _tracked(e, leverage=10)
        await e.sync_positions_from_exchange()
        assert acct.paths() == [V3_POSITIONS, V2_POSITIONS]
        assert pos.leverage == 5
        assert not _said(audits, "position_sync", "ERROR")
        e._risk_engine.record_warning.assert_not_called()

    async def test_another_refusal_inside_the_classic_read_is_still_a_failed_sync(
            self, tmp_path, monkeypatch, audits):
        _v3_reader(monkeypatch, AssertionError("the v3 position channel was asked"))
        acct = _Account(classic=True, positions=[_v2_row()])
        e = _marked_by_another_reader(tmp_path, acct)
        _tracked(e)
        await e._get_exchange()
        acct.refuse_all = BAD_PASSPHRASE
        await e.sync_positions_from_exchange()
        assert _said(audits, "position_sync", "ERROR")
        e._risk_engine.record_warning.assert_called_with("position_sync_fetch")

    async def test_another_accounts_client_is_left_alone(self, tmp_path, audits):
        acct = _Account(classic=False)   # fails the test on any classic path
        e = _executor(tmp_path, _built(acct, "k2"))
        assert mode.note_classic_refusal(_built(_Account(classic=True), "k"),
                                         "bitget " + REFUSAL)
        out = await e.fetch_balance()
        assert "error" not in out and acct.paths() == [ASSETS]
        assert client_is_uta(e._exchange) and not _said(audits, "account_mode")


# ── a timeout is not a rejected key ──────────────────────────────────────────

class TestANetworkErrorNamesItsClass:

    @pytest.mark.parametrize("exc,text", [
        (ccxt.RequestTimeout(TIMED_OUT), "RequestTimeout: " + TIMED_OUT),
        (ccxt.ExchangeNotAvailable(TIMED_OUT), "ExchangeNotAvailable: " + TIMED_OUT),
        (ccxt.NetworkError(TIMED_OUT), "NetworkError: " + TIMED_OUT),
        (ccxt.DDoSProtection(TIMED_OUT), "DDoSProtection: " + TIMED_OUT),
    ])
    def test_the_drivers_network_errors_are_prefixed(self, exc, text):
        assert _balance_error_text(exc) == text

    @pytest.mark.parametrize("exc,text", [
        # A clock or signature error is a NetworkError in the driver's tree
        # and is not one here: every signed call fails on it.
        (ccxt.InvalidNonce("bitget " + BAD_PASSPHRASE), "bitget " + BAD_PASSPHRASE),
        (ccxt.AuthenticationError("bitget " + BAD_PASSPHRASE), "bitget " + BAD_PASSPHRASE),
        (ccxt.ExchangeError("bitget " + REFUSAL), "bitget " + REFUSAL),
        (RuntimeError("40012 rejected"), "40012 rejected"),
        (TimeoutError(), "TimeoutError"),
        (ccxt.RequestTimeout(), "RequestTimeout"),
    ])
    def test_every_other_error_reads_as_it_did(self, exc, text):
        assert _balance_error_text(exc) == text


def _halts(text: str) -> bool:
    """The preflights' own decision: neither an account-mode answer nor a
    positively identified transport failure."""
    return not main._is_account_mode_refusal(text) and not main._is_transport_failure(text)


class TestATimeoutIsNotARejectedKey:

    def test_the_bare_driver_text_could_not_be_placed(self):
        # Why the class is named: this is the whole of what the driver says.
        assert _halts(TIMED_OUT)

    @pytest.mark.parametrize("name", [
        "RequestTimeout", "ExchangeNotAvailable", "NetworkError", "DDoSProtection",
        "RateLimitExceeded", "OnMaintenance"])
    def test_named_it_reads_as_transport(self, name):
        text = f"{name}: {TIMED_OUT}"
        assert main._is_transport_failure(text) and not main._is_auth_rejection(text)

    @pytest.mark.parametrize("text", [
        "bitget " + BAD_PASSPHRASE,
        "InvalidNonce: bitget request timestamp expired",
        "ExchangeNotAvailable: bitget GET https://api.bitget.com/api/v3/account/assets "
        "403 Forbidden",
        "ccxt.base.errors.ExchangeError",
        "something nobody has seen before",
        "",
    ])
    def test_a_rejection_and_an_unknown_error_still_halt(self, text):
        assert _halts(text), text

    async def _preflight(self, tmp_path, raised):
        ex = _built(_Account(classic=False))

        async def _assets(params=None):
            raise raised
        ex.privateUtaGetV3AccountAssets = _assets
        eng, marks = _engine(_executor(tmp_path, ex))
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        try:
            await main._credential_preflight(eng, bot)
        finally:
            p.stop()
        return marks, bot.send_message.await_args.kwargs["text"]

    async def test_a_timed_out_balance_is_reported_as_unreachable(self, tmp_path, audits):
        marks, sent = await self._preflight(tmp_path, ccxt.RequestTimeout(TIMED_OUT))
        assert marks == [], "a timeout latched live auth down"
        assert "auth FAILED" not in sent and "NOT halted" in sent
        assert _said(audits, "cred_preflight", "UNREACHABLE")

    async def test_a_lost_connection_is_reported_as_unreachable(self, tmp_path, audits):
        marks, sent = await self._preflight(tmp_path, ccxt.ExchangeNotAvailable(TIMED_OUT))
        assert marks == [] and "auth FAILED" not in sent

    @pytest.mark.parametrize("raised", [
        ccxt.AuthenticationError("bitget " + BAD_PASSPHRASE),
        ccxt.InvalidNonce("bitget request timestamp expired"),
        ccxt.ExchangeError("bitget something nobody has seen before"),
    ])
    async def test_a_rejection_still_halts(self, tmp_path, audits, raised):
        marks, sent = await self._preflight(tmp_path, raised)
        assert marks == [("", False)] and "auth FAILED" in sent

    async def test_a_classic_account_passes_from_a_cold_start(self, tmp_path, audits, venue):
        """End to end, from an executor that has built nothing."""
        acct = _Account(classic=True)
        venue(acct)
        eng, marks = _engine(_fresh(tmp_path))
        bot = Mock()
        bot.send_message = AsyncMock()
        p = _operator_cfg()
        try:
            await main._credential_preflight(eng, bot)
        finally:
            p.stop()
        assert marks == [("", True)] and _said(audits, "cred_preflight", "OK")
        assert acct.paths() == [ASSETS, V2_ACCOUNTS]
        bot.send_message.assert_not_awaited()

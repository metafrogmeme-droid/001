"""The emergency close asked the unified API on a Classic account.

`LiveExecutor._flash_close_position` is the close of last resort: it names no
size and no minimum, so it closes what the venue holds when a sized close has
been refused. It posted to ``/api/v3/trade/close-positions`` whatever the
account. A Bitget account in Classic mode refuses every unified call (code
40084), so on such an account the flash close failed, the caller reported
"CLOSE FAILED", and the position the sized close could not close stayed open.

On an account the venue has said is Classic it now posts the same close to
``/api/v2/mix/order/close-positions``. A unified account is asked exactly what
it was asked before. The classic reply has a different shape (what closed in
``successList``, what did not in ``failureList``), and it is spelled as the
unified one at the boundary so `order_state.flash_close_applied` stays the one
reader of "the close was applied".

Nothing here reaches a venue: the signed transport is replaced, and it fails
the test on any request it was not told to expect.
"""
from __future__ import annotations

import io
import json
import math
import urllib.error
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.core import bitget_account_mode as mode
from bot.core.bitget_v3_client import BitgetV3Client
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import classic_flash_close_reply, flash_close_applied

V3 = "/api/v3/trade/close-positions"
V2 = "/api/v2/mix/order/close-positions"

REFUSAL_40084 = ('bitget {"code":"40084","msg":"You are in Classic Account mode, and the '
                 'Unified Account API is not supported","requestTime":1794001242900,'
                 '"data":null}')

V3_APPLIED = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
              "data": {"list": [{"orderId": "F1", "clientOid": "c1"}]}}
V2_APPLIED = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
              "data": {"successList": [{"orderId": "F2", "clientOid": "c2",
                                        "symbol": "INJUSDT"}],
                       "failureList": [], "result": False}}
V2_ITEM_FAILED = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
                  "data": {"successList": [],
                           "failureList": [{"orderId": "", "clientOid": "",
                                            "symbol": "INJUSDT",
                                            "errorMsg": "No position to close",
                                            "errorCode": "22002"}],
                           "result": False}}
V2_NOTHING = {"code": "00000", "msg": "success", "requestTime": 1794001242900,
              "data": {"successList": [], "failureList": [], "result": False}}
NO_POSITION = {"code": "22002", "msg": "No position to close",
               "requestTime": 1794001242900, "data": None}
VENUE_ERROR = {"code": "40762", "msg": "The order amount exceeds the balance",
               "requestTime": 1794001242900, "data": None}


def _refused(body: dict) -> urllib.error.HTTPError:
    """The venue's HTTP 400 as urllib raises it, with its JSON body readable."""
    return urllib.error.HTTPError(
        "https://api.bitget.com", 400, "Bad Request", None,   # type: ignore[arg-type]
        io.BytesIO(json.dumps(body).encode()))


class _Wire:
    """Stands in for the signed transport. Answers each path from ``replies``
    and FAILS THE TEST on a path it was not given, which is how "no call to
    the other family" is asserted."""

    def __init__(self, monkeypatch, replies: dict):
        self.replies = replies
        self.calls: list[dict] = []
        wire = self

        def request(client, method, path, body_dict=None, timeout=10):
            wire.calls.append({"method": method, "path": path, "body": body_dict,
                               "key": client._api_key})
            if path not in wire.replies:
                raise AssertionError(f"an unplanned request went out: {method} {path}")
            reply = wire.replies[path]
            if isinstance(reply, BaseException):
                raise reply
            return json.loads(json.dumps(reply))

        monkeypatch.setattr(BitgetV3Client, "request", request)

    def paths(self):
        return [c["path"] for c in self.calls]


USER = {"api_key": "uk", "api_secret": "us", "passphrase": "up"}


def _executor(tmp_path, *, classic: bool, direction="LONG", credentials=None):
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget",
                     user_id="u1" if credentials else None, credentials=credentials)
    # The client in hand names the account. Only its key and options are read.
    key = (credentials or {}).get("api_key", "k")
    e._exchange = SimpleNamespace(apiKey=key, options={"uta": True})
    if classic:
        assert mode.note_classic_refusal(e._exchange, REFUSAL_40084)
    pos = LivePosition(trade_id="T-flash", symbol="INJ/USDT", direction=direction,
                       entry_price=10.0, quantity=2.0, cost_usd=4.0, stop_loss=9.0,
                       take_profit=12.0, leverage=5, status="open")
    e._positions = {pos.trade_id: pos}
    return e, pos


@pytest.fixture
def audits(monkeypatch):
    calls: list = []
    monkeypatch.setattr(le, "audit", lambda log, msg, **kw: calls.append({"msg": msg, **kw}))
    return calls


def _said(audits, action, result):
    return [c for c in audits if c.get("action") == action and c.get("result") == result]


# ── the reply, in the one reader's vocabulary ───────────────────────────────

class TestTheClassicReplyIsReadByTheOneReader:

    def test_a_close_that_applied_reads_applied(self):
        assert flash_close_applied(classic_flash_close_reply(V2_APPLIED)) is True

    def test_the_raw_classic_reply_was_never_readable(self):
        # Why the conversion exists: unconverted, an APPLIED classic close
        # reads as not applied, because the reader looks for `data.list`.
        assert flash_close_applied(V2_APPLIED) is False

    @pytest.mark.parametrize("reply,why", [
        (V2_ITEM_FAILED, "the venue named the close as failed"),
        (V2_NOTHING, "accepted, and nothing closed"),
        ({"code": "00000", "data": {"successList": [{"orderId": "F2"}],
                                    "failureList": [{"errorCode": "22002"}]}},
         "one closed and one did not"),
        ({"code": "00000", "data": {"successList": [{"orderId": "F2"}],
                                    "failureList": [{"errorMsg": "refused"}]}},
         "a failed item with no code is still failed"),
        ({"code": "00000", "data": {"successList": [{"orderId": "F2"}],
                                    "failureList": [{"errorCode": "00000"}]}},
         "a failed item cannot state success"),
        ({"code": "00000", "data": {"successList": ["F2"], "failureList": []}},
         "a success nobody could read as a record claims nothing"),
        ({"code": "00000", "data": {"successList": "F2"}}, "not a list"),
        ({"code": "00000", "data": None}, "no data"),
        (NO_POSITION, "refused outright"),
        (None, "no reply"),
    ])
    def test_everything_else_reads_not_applied(self, reply, why):
        assert flash_close_applied(classic_flash_close_reply(reply)) is False, why

    def test_a_failed_item_keeps_the_venues_code_and_words(self):
        out = classic_flash_close_reply(V2_ITEM_FAILED)
        assert out["data"]["list"] == [{
            "orderId": "", "clientOid": "", "symbol": "INJUSDT",
            "errorMsg": "No position to close", "errorCode": "22002",
            "code": "22002", "msg": "No position to close"}]

    def test_a_reply_that_was_not_accepted_is_handed_back_as_it_came(self):
        assert classic_flash_close_reply(NO_POSITION) is NO_POSITION
        assert classic_flash_close_reply(None) is None

    def test_the_input_is_not_written(self):
        before = json.dumps(V2_APPLIED, sort_keys=True)
        classic_flash_close_reply(V2_APPLIED)
        assert json.dumps(V2_APPLIED, sort_keys=True) == before


# ── a unified account: asked what it was always asked ───────────────────────

class TestAUnifiedAccount:

    @pytest.mark.parametrize("direction,side", [("LONG", "long"), ("SHORT", "short")])
    async def test_the_close_goes_to_v3_as_before(
            self, tmp_path, monkeypatch, audits, direction, side):
        wire = _Wire(monkeypatch, {V3: V3_APPLIED})
        e, pos = _executor(tmp_path, classic=False, direction=direction)
        out = await e._flash_close_position(pos)
        assert wire.calls == [{"method": "POST", "path": V3, "key": "",
                               "body": {"category": "USDT-FUTURES", "symbol": "INJUSDT",
                                        "posSide": side}}]
        assert out == V3_APPLIED, "the unified reply is returned as the venue wrote it"
        assert flash_close_applied(out) is True
        assert _said(audits, "flash_close_v3", "OK")
        assert not [c for c in audits if c.get("action") == "flash_close_v2"]

    async def test_no_position_is_not_a_close(self, tmp_path, monkeypatch, audits):
        wire = _Wire(monkeypatch, {V3: _refused(NO_POSITION)})
        e, pos = _executor(tmp_path, classic=False)
        out = await e._flash_close_position(pos)
        assert out is None and flash_close_applied(out) is False
        assert wire.paths() == [V3], "one request, and none to the other family"
        assert _said(audits, "flash_close_v3", "FAIL")
        assert pos.status == "open" and pos.quantity == 2.0

    async def test_an_accepted_close_that_closed_nothing_is_not_applied(
            self, tmp_path, monkeypatch):
        _Wire(monkeypatch, {V3: {"code": "00000", "data": {"list": []}}})
        e, pos = _executor(tmp_path, classic=False)
        assert flash_close_applied(await e._flash_close_position(pos)) is False

    async def test_a_refused_unified_close_is_not_retried_on_the_classic_api(
            self, tmp_path, monkeypatch):
        """No detection lives here. An account the venue has not said is
        Classic is asked the unified endpoint and nothing else."""
        wire = _Wire(monkeypatch, {V3: _refused(json.loads(REFUSAL_40084[len("bitget "):]))})
        e, pos = _executor(tmp_path, classic=False)
        assert await e._flash_close_position(pos) is None
        assert wire.paths() == [V3]
        assert not mode.is_classic("k")


# ── a Classic account: the same close, in its own family ────────────────────

class TestAClassicAccount:

    @pytest.mark.parametrize("direction,side", [("LONG", "long"), ("SHORT", "short")])
    async def test_the_close_goes_to_v2(self, tmp_path, monkeypatch, audits, direction, side):
        wire = _Wire(monkeypatch, {V2: V2_APPLIED})
        e, pos = _executor(tmp_path, classic=True, direction=direction)
        out = await e._flash_close_position(pos)
        assert wire.calls == [{"method": "POST", "path": V2, "key": "",
                               "body": {"productType": "USDT-FUTURES",
                                        "symbol": "INJUSDT", "holdSide": side}}]
        assert flash_close_applied(out) is True
        assert _said(audits, "flash_close_v2", "OK")
        assert not [c for c in audits if c.get("action") == "flash_close_v3"]

    async def test_the_request_can_only_close(self, tmp_path, monkeypatch):
        """No size, no price and no order side: it closes what the venue holds
        on this symbol and side, and names both so nothing else is touched."""
        wire = _Wire(monkeypatch, {V2: V2_APPLIED})
        e, pos = _executor(tmp_path, classic=True)
        await e._flash_close_position(pos)
        body = wire.calls[0]["body"]
        assert set(body) == {"productType", "symbol", "holdSide"}
        assert body["symbol"] and body["holdSide"] in ("long", "short")

    async def test_a_users_close_is_signed_with_the_users_keys(self, tmp_path, monkeypatch):
        wire = _Wire(monkeypatch, {V2: V2_APPLIED})
        e, pos = _executor(tmp_path, classic=True, credentials=USER)
        assert flash_close_applied(await e._flash_close_position(pos)) is True
        assert [c["key"] for c in wire.calls] == ["uk"], (
            "the flash close went out under another account's key")

    async def test_no_position_is_not_a_close(self, tmp_path, monkeypatch, audits):
        wire = _Wire(monkeypatch, {V2: _refused(NO_POSITION)})
        e, pos = _executor(tmp_path, classic=True)
        out = await e._flash_close_position(pos)
        assert out is None and flash_close_applied(out) is False
        assert wire.paths() == [V2], "one request, and none to the other family"
        fail = _said(audits, "flash_close_v2", "FAIL")
        assert fail and "22002" in fail[0]["msg"]
        assert pos.status == "open" and pos.quantity == 2.0

    @pytest.mark.parametrize("reply", [V2_ITEM_FAILED, V2_NOTHING])
    async def test_an_accepted_close_that_did_not_apply_is_not_applied(
            self, tmp_path, monkeypatch, reply):
        _Wire(monkeypatch, {V2: reply})
        e, pos = _executor(tmp_path, classic=True)
        out = await e._flash_close_position(pos)
        assert flash_close_applied(out) is False
        assert pos.status == "open"

    @pytest.mark.parametrize("error", [
        _refused(VENUE_ERROR),
        urllib.error.URLError("timed out"),
        urllib.error.HTTPError("https://api.bitget.com", 502, "Bad Gateway", None,  # type: ignore[arg-type]
                               io.BytesIO(b"<html>bad gateway</html>")),
    ])
    async def test_a_venue_error_is_a_failed_close(self, tmp_path, monkeypatch, audits, error):
        wire = _Wire(monkeypatch, {V2: error})
        e, pos = _executor(tmp_path, classic=True)
        out = await e._flash_close_position(pos)
        assert out is None and flash_close_applied(out) is False
        assert wire.paths() == [V2]
        assert not _said(audits, "flash_close_v2", "OK")
        assert pos.status == "open" and pos.quantity == 2.0 and "T-flash" in e._positions

    async def test_another_venue_is_never_asked(self, tmp_path, monkeypatch):
        from bot.core.venues import get_venue
        wire = _Wire(monkeypatch, {})
        e, pos = _executor(tmp_path, classic=True)
        e._venue = get_venue("bybit")
        assert await e._flash_close_position(pos) is None
        assert wire.calls == []


# ── driven: the real close, on a Classic account ────────────────────────────

REFUSAL_25231 = ('bitget {"code":"25231","msg":"Close quantity cannot exceed the held '
                 'position quantity","requestTime":1791733754258,"data":null}')


class _Book:
    """A venue whose sized close is refused for its size, as the caller of the
    flash close meets it."""

    def __init__(self):
        self.orders: list = []
        self.fetch_ticker = AsyncMock(return_value={"last": 9.9, "bid": 9.9, "ask": 9.9})
        self.fetch_open_orders = AsyncMock(return_value=[])
        self.cancel_order = AsyncMock(return_value={"status": "canceled"})

    async def fetch_positions(self, symbols=None, params=None):
        return []

    def amount_to_precision(self, symbol, amount):
        return str(math.floor(amount))

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.orders.append(amount)
        raise Exception(REFUSAL_25231)

    async def fetch_order(self, oid, symbol=None, params=None):
        return {"id": oid, "status": "closed", "filled": None}


class TestTheRealCloseOnAClassicAccount:

    def _driven(self, tmp_path, monkeypatch, replies):
        monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
        wire = _Wire(monkeypatch, replies)
        e, pos = _executor(tmp_path, classic=True)
        e._get_exchange = AsyncMock(return_value=_Book())
        e._is_uta = False
        e._fetch_bitget_close_data = AsyncMock(return_value=None)
        e._handle_already_closed_position = AsyncMock(
            return_value="✅ CLOSED LONG INJ/USDT (flash close)")
        return wire, e, pos

    async def test_a_size_refusal_is_flash_closed_through_v2_and_booked(
            self, tmp_path, monkeypatch):
        wire, e, _ = self._driven(tmp_path, monkeypatch, {V2: V2_APPLIED})
        msg = await e.close_position("T-flash", reason="manual")
        assert msg == "✅ CLOSED LONG INJ/USDT (flash close)", msg
        assert wire.paths() == [V2]
        assert e._handle_already_closed_position.await_args.kwargs == {
            "bot_reason": "manual", "bot_closed": True}

    async def test_a_flash_close_the_venue_refused_is_still_a_failed_close(
            self, tmp_path, monkeypatch):
        wire, e, pos = self._driven(tmp_path, monkeypatch, {V2: _refused(NO_POSITION)})
        msg = await e.close_position("T-flash", reason="manual")
        assert msg.startswith("CLOSE FAILED for T-flash: "), msg
        assert wire.paths() == [V2]
        e._handle_already_closed_position.assert_not_awaited()
        assert pos.status == "open" and "T-flash" in e._positions, "the record must survive"

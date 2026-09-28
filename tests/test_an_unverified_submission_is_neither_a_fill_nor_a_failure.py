"""An entry submission the venue never confirmed either way is the THIRD
outcome of an entry: not a fill, not a refusal, and never re-sent.

`_create_order_idempotent` sends the order with a client id, and when the
send raises it asks the venue's order lists whether the order landed. Driven
against the unfixed tree with the send raising a `RequestTimeout`, the open
list answering `[]` and the closed list RAISING:

    found=None  verified=True   <- one list read, reported as both
    execute() -> "EXECUTION FAILED ..."   audit: "never submitted"

A filled MARKET order is never in the open list, so the one list that could
hold it was the one that could not be read -- and the answer was a verified
absence. Every door then read EXECUTION FAILED as nothing placed: the Confirm
button said "❌", the web said `placed: false`, the chain sealed
EXECUTION_FAILED, the learner filed the idea as never opened, and the engine
left the idea pending for the next tap to SEND AGAIN -- over a fill that may
be sitting on the venue with no stop.

Three things now. The lookup confirms an absence only when EVERY list was
read (`_ORDER_LISTS`). A send whose outcome is unknown beside an unverified
absence raises `OrderOutcomeUnverified`, which `execute` records (per user
and per venue, beside the positions file), says as unknown (`ORDER
UNVERIFIED`, a token no door reads as either of the other two), and refuses a
re-send on that symbol while the record stands. And every positions pass asks
the venue by client id first: a fill is booked with the idea's own levels and
protected, a resting order is tracked, a confirmed absence frees the idea, a
list that still will not read is said once.

Nothing here is a scan standing in for behaviour: every claim is driven
through the real `execute`, the real `check_positions`, the real dispatcher,
the real gateway route, the real auto-confirm hook and the real engine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import ccxt
import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core.confirm_result import outcome_unverified, placed_nothing
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import (
    _ORDER_LISTS,
    ENTRY_ESTIMATED,
    EXECUTION_UNVERIFIED_TOKEN,
    LiveExecutor,
    LivePosition,
    OrderOutcomeUnverified,
    entry_is_estimated,
    execution_indicates_failure,
    execution_outcome_unverified,
    send_outcome_unknown,
)
from bot.learning.outcome_join import opened
from bot.utils.models import AgentState
from bot.web import user_gateway as ug
from tests.test_a_refusal_is_never_announced_as_a_trade import _tap
from tests.test_a_refused_web_confirm_is_not_a_confirmed_trade import (  # noqa: F401
    _confirm_audits,
    _Planted,
    audits,
)
from tests.test_a_seal_failure_does_not_unplace_a_trade import _confirm, _engine
from tests.test_an_estimated_entry_says_so import TICKER, Venue, _idea, _Slippage
from tests.test_an_unread_entry_is_an_unpriced_close import _no_sleep  # noqa: F401
from tests.test_the_scheduled_posts_say_whose_book_they_read import (  # noqa: F401
    OPERATOR,
    _auto,
    operator_chat,
)
from tests.test_web_gateway import AUTHED, HDRS, SECRET, FakeHandler, _propose, gateway_client

COID = LiveExecutor._client_oid("TI-EST")          # the idea `_idea()` builds
NOW_MS = int(time.time() * 1000)


class _Venue(Venue):
    """The estimated-entry venue with the send and the two order lists
    plantable: each list answers a list, or raises what it is handed."""

    def __init__(self, *, send=None, open_list=(), closed_list=(), **kw):
        super().__init__(**kw)
        self.send = send
        self.open_list = open_list
        self.closed_list = closed_list
        self.calls: list[str] = []
        self.sent_params: list[dict] = []

    @staticmethod
    def _answer(planted):
        if isinstance(planted, BaseException):
            raise planted
        return list(planted)

    async def create_order(self, symbol=None, type=None, side=None, amount=None,
                           price=None, params=None, **k):
        self.calls.append("create_order")
        self.sent_params.append(dict(params or {}))
        if self.send is not None:
            raise self.send
        return await super().create_order(symbol=symbol, type=type, side=side,
                                          amount=amount, price=price, params=params, **k)

    async def fetch_open_orders(self, *a, **k):
        self.calls.append("fetch_open_orders")
        return self._answer(self.open_list)

    async def fetch_closed_orders(self, *a, **k):
        self.calls.append("fetch_closed_orders")
        return self._answer(self.closed_list)

    async def fetch_ticker(self, symbol, *a, **k):
        self.calls.append("fetch_ticker")
        return await super().fetch_ticker(symbol, *a, **k)


def _order(**over):
    """A venue order carrying the idea's client id, filled unless told otherwise."""
    o = {"id": "o9", "status": "closed", "filled": 0.05, "amount": 0.05,
         "average": 4008.0, "price": None, "lastTradeTimestamp": NOW_MS,
         "info": {"clientOid": COID}}
    o.update(over)
    return o


def _timeout():
    return ccxt.RequestTimeout("bitget POST /order timed out")


@pytest.fixture(autouse=True)
def _no_override():
    RUNTIME.leverage_override = None
    yield
    RUNTIME.leverage_override = None


# The two imported fixtures are taken by NAME here rather than by a parameter
# that shadows the import (ruff reads that as a redefinition).
@pytest.fixture
def web_audit_rows(request):
    return request.getfixturevalue("audits")


@pytest.fixture
def op_chat(request):
    return request.getfixturevalue("operator_chat")


class _Harness:
    """The real executor over a planted venue, with the audits and warnings
    it writes captured, and the stops it places plantable."""

    def __init__(self, venue, tmp_path, *, sl_tp=None):
        self.venue = venue
        self.tmp_path = tmp_path
        self.audits: list[dict] = []
        self.warnings: list[str] = []
        self._sl_tp = sl_tp

    def _patches(self):
        async def _sl_tp_ok(*a, **k):
            return ("sl1", "tp1")

        async def _sync(*a, **k):
            return None

        return [
            patch.object(le, "audit", lambda log, msg, **kw: self.audits.append(
                {"message": msg, **kw})),
            patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None),
            patch.object(type(CONFIG), "is_live", return_value=True),
            patch.object(LiveExecutor, "_place_sl_tp", self._sl_tp or _sl_tp_ok),
            patch.object(LiveExecutor, "sync_positions_from_exchange", _sync),
            patch.object(LiveExecutor, "_post_fill_slippage_guard",
                         AsyncMock(return_value=(None, ""))),
        ]

    def build(self):
        with _Stack(self._patches()):
            ex = LiveExecutor(state_dir=str(self.tmp_path))
        ex._exchange = self.venue
        ex._hedge_mode = False
        ex._slippage_tracker = _Slippage()
        ex._record_warning = lambda key: self.warnings.append(key)
        return ex

    def execute(self, ex, idea=None, **kw):
        with _Stack(self._patches()):
            return asyncio.run(ex.execute(idea or _idea(), size_usd=200.0,
                                          order_type="market", **kw))

    def reconcile(self, ex):
        with _Stack(self._patches()):
            return asyncio.run(ex._reconcile_unverified_submissions(self.venue))

    def positions_pass(self, ex):
        with _Stack(self._patches()):
            return asyncio.run(ex.check_positions())

    def results(self, action="unverified_submission"):
        return [a.get("result") for a in self.audits if a.get("action") == action]


class _Stack:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        return False


def _record(ex):
    return json.loads(open(ex._unverified_file, encoding="utf-8").read())


# ── 1. the lookup confirms an absence only when every list was read ─────────

class TestTheLookupReadsEveryList:

    def _lookup(self, venue, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        return asyncio.run(ex._find_order_by_client_oid(venue, "ETH/USDT", COID))

    def test_the_closed_list_raising_is_not_a_verified_absence(self, tmp_path):
        found, verified = self._lookup(
            _Venue(open_list=[], closed_list=ccxt.NetworkError("x")), tmp_path)
        assert (found, verified) == (None, False), (
            "a filled market order is only ever in the closed list; the open "
            "list alone said nothing about it")

    def test_the_open_list_raising_is_not_a_verified_absence_either(self, tmp_path):
        found, verified = self._lookup(
            _Venue(open_list=ccxt.NetworkError("x"), closed_list=[]), tmp_path)
        assert (found, verified) == (None, False)

    def test_both_lists_read_and_empty_is_a_verified_absence(self, tmp_path):
        assert self._lookup(_Venue(open_list=[], closed_list=[]), tmp_path) == (None, True)

    def test_a_list_that_raised_does_not_hide_the_order_in_the_other(self, tmp_path):
        found, verified = self._lookup(
            _Venue(open_list=ccxt.NetworkError("x"), closed_list=[_order()]), tmp_path)
        assert verified is True and found["id"] == "o9"

    def test_a_client_without_one_fetcher_cannot_verify_an_absence(self, tmp_path):
        client = SimpleNamespace(fetch_open_orders=AsyncMock(return_value=[]))
        ex = LiveExecutor(state_dir=str(tmp_path))
        found, verified = asyncio.run(ex._find_order_by_client_oid(client, "ETH/USDT", COID))
        assert (found, verified) == (None, False), (
            "a list this client cannot read is a list nobody read")

    def test_the_lists_are_the_two_an_order_can_sit_in(self):
        assert _ORDER_LISTS == ("fetch_open_orders", "fetch_closed_orders")


class TestASendWhoseOutcomeIsUnknown:

    def test_a_bare_timeout_is_unknown(self):
        assert send_outcome_unknown(TimeoutError("deadline")) is True
        assert send_outcome_unknown(asyncio.TimeoutError()) is True

    def test_a_refusal_the_venue_answered_is_not(self):
        assert send_outcome_unknown(ccxt.InsufficientFunds("no")) is False


# ── 2. the idempotent create's three answers ─────────────────────────────────

class TestTheIdempotentCreate:

    def _create(self, venue, tmp_path):
        h = _Harness(venue, tmp_path)
        ex = h.build()

        async def go():
            return await ex._create_order_idempotent(
                venue, symbol="ETH/USDT:USDT", type="market", side="buy",
                amount=0.05, coid=COID)

        with _Stack(h._patches()):
            return h, asyncio.run(go())

    def test_unknown_send_and_an_unverified_absence_is_the_third_outcome(self, tmp_path):
        venue = _Venue(send=_timeout(), open_list=[], closed_list=ccxt.NetworkError("down"))
        with pytest.raises(OrderOutcomeUnverified) as info:
            self._create(venue, tmp_path)
        exc = info.value
        assert (exc.cause_class, exc.coid, exc.symbol, exc.side, exc.order_type,
                exc.amount, exc.price) == (
            "RequestTimeout", COID, "ETH/USDT:USDT", "buy", "market", 0.05, None)
        assert "timed out" in str(exc) and "order outcome unverified" in str(exc)
        assert isinstance(exc.__cause__, ccxt.RequestTimeout)
        assert venue.calls == ["create_order", "fetch_open_orders", "fetch_closed_orders"]

    def test_unknown_send_and_a_verified_absence_re_raises_the_cause(self, tmp_path):
        venue = _Venue(send=_timeout(), open_list=[], closed_list=[])
        with pytest.raises(ccxt.RequestTimeout):
            self._create(venue, tmp_path)

    def test_unknown_send_and_the_order_in_the_closed_list_is_the_order(self, tmp_path):
        venue = _Venue(send=_timeout(), open_list=[], closed_list=[_order()])
        h, order = self._create(venue, tmp_path)
        assert order["id"] == "o9"
        assert "RECOVERED_BY_COID" in h.results("live_execute")

    def test_a_refusal_the_venue_answered_re_raises_whatever_the_lists_read(self, tmp_path):
        venue = _Venue(send=ccxt.InsufficientFunds("no funds"),
                       open_list=ccxt.NetworkError("x"), closed_list=ccxt.NetworkError("x"))
        with pytest.raises(ccxt.InsufficientFunds):
            self._create(venue, tmp_path)

    def test_the_audit_names_the_cause_class_and_never_its_text(self, tmp_path):
        venue = _Venue(send=ccxt.RequestTimeout("SECRETVALUE?apiKey=k"),
                       open_list=[], closed_list=ccxt.NetworkError("down"))
        with pytest.raises(OrderOutcomeUnverified):
            h, _ = self._create(venue, tmp_path)
        # the harness is rebuilt inside _create; read its audits off the venue's owner
        # (pytest.raises swallowed the return), so assert on a fresh drive
        h = _Harness(venue, tmp_path)
        ex = h.build()

        async def go():
            return await ex._create_order_idempotent(
                venue, symbol="ETH/USDT:USDT", type="market", side="buy",
                amount=0.05, coid=COID)

        with _Stack(h._patches()), pytest.raises(OrderOutcomeUnverified):
            asyncio.run(go())
        rows = [a for a in h.audits if a.get("result") == "SUBMIT_UNVERIFIED"]
        assert len(rows) == 1
        assert rows[0]["data"]["cause"] == "RequestTimeout"
        assert "SECRETVALUE" not in json.dumps(rows[0])


# ── 3. execute: recorded, said as unknown, refused a re-send ────────────────

def _unverified_drive(tmp_path, **venue_kw):
    venue = _Venue(send=_timeout(), open_list=[],
                   closed_list=ccxt.NetworkError("orders endpoint down"), **venue_kw)
    h = _Harness(venue, tmp_path)
    ex = h.build()
    card = h.execute(ex)
    return h, ex, card


class TestExecuteAnswersTheThirdOutcome:

    def test_the_card_is_neither_a_fill_nor_a_failure(self, tmp_path):
        h, ex, card = _unverified_drive(tmp_path)
        assert card.startswith("⚠️ ORDER UNVERIFIED: ETH/USDT LONG")
        assert execution_outcome_unverified(card)
        assert outcome_unverified(card)
        assert not execution_indicates_failure(card), (
            "a failure token would make every door announce a refusal")
        assert not placed_nothing(card)
        assert "RequestTimeout" in card and COID in card
        assert "do not re-send it by hand" in card
        assert ex._positions == {}, "nothing is recorded as a position"

    def test_it_is_recorded_beside_the_positions_file_and_said(self, tmp_path):
        h, ex, card = _unverified_drive(tmp_path)
        rec = _record(ex)[COID]
        assert rec["trade_id"] == "TI-EST" and rec["symbol"] == "ETH/USDT"
        assert rec["order_symbol"] == "ETH/USDT:USDT" and rec["side"] == "buy"
        assert rec["direction"] == "LONG" and rec["order_type"] == "market"
        assert rec["stop_loss"] == TICKER * 0.98 and rec["take_profit"] == TICKER * 1.06
        # the size the order was sized at, after the per-trade cap, not the ask
        assert rec["size_usd"] == min(200.0, le.MICRO_MAX_POSITION_USD) and rec["leverage"] >= 1
        assert rec["pre_order_price"] == TICKER and rec["cause"] == "RequestTimeout"
        assert rec["amount"] > 0 and rec["at"]
        assert h.results() == ["RECORDED"]
        assert "SUBMIT_UNVERIFIED" in h.results("live_execute")
        assert h.warnings == ["submit_unverified"]

    def test_the_same_idea_is_refused_a_re_send_while_the_record_stands(self, tmp_path):
        h, ex, _ = _unverified_drive(tmp_path)
        sends = h.venue.calls.count("create_order")
        again = h.execute(ex)
        assert again.startswith("EXECUTION BLOCKED:") and "this idea" in again
        assert execution_indicates_failure(again), "a refusal, read as one"
        assert h.venue.calls.count("create_order") == sends, "nothing was re-sent"
        assert "BLOCKED_UNVERIFIED_STANDING" in h.results("live_execute")

    def test_another_idea_on_the_same_symbol_is_refused_too(self, tmp_path):
        h, ex, _ = _unverified_drive(tmp_path)
        other = _idea()
        other.id = "TI-OTHER"
        sends = h.venue.calls.count("create_order")
        again = h.execute(ex, other)
        assert again.startswith("EXECUTION BLOCKED:") and "ETH/USDT" in again
        assert h.venue.calls.count("create_order") == sends

    def test_a_different_symbol_is_not_refused_by_it(self, tmp_path):
        h, ex, _ = _unverified_drive(tmp_path)
        assert ex._unverified_standing("BTC/USDT", "rcOTHER") is None

    def test_the_record_survives_a_restart_and_is_said_on_load(self, tmp_path):
        h, ex, _ = _unverified_drive(tmp_path)
        h2 = _Harness(h.venue, tmp_path)
        again = h2.build()
        assert COID in again._unverified_submissions
        assert h2.results() == ["LOADED"]
        assert again._unverified_standing("ETH/USDT", "rcOTHER") is not None


class TestAnUnreadableRecordRefusesEveryEntry:

    def _unreadable(self, tmp_path, body="{not json"):
        path = tmp_path / "unverified_submissions.json"
        path.write_text(body, encoding="utf-8")
        h = _Harness(_Venue(), tmp_path)
        ex = h.build()
        return h, ex, path

    def test_it_is_said_by_name_and_refuses_by_name(self, tmp_path):
        h, ex, path = self._unreadable(tmp_path)
        assert ex._unverified_file_unreadable == "JSONDecodeError"
        assert h.results() == ["RECORD_UNREADABLE"]
        why = ex._unverified_standing("BTC/USDT", "rcANY")
        assert "could not be read" in why and "JSONDecodeError" in why
        card = h.execute(ex)
        assert card.startswith("EXECUTION BLOCKED:") and "could not be read" in card
        assert h.venue.calls == [], "no order went out"

    def test_it_is_never_written_over(self, tmp_path):
        h, ex, path = self._unreadable(tmp_path)
        ex._unverified_submissions["rcX"] = {"coid": "rcX", "symbol": "X/USDT"}
        ex._write_unverified_submissions()
        ex._drop_unverified_submission("rcX")
        assert path.read_text(encoding="utf-8") == "{not json"

    def test_the_wrong_shape_is_unreadable_and_an_empty_file_is_fresh(self, tmp_path):
        h, ex, _ = self._unreadable(tmp_path, body="[]")
        assert ex._unverified_file_unreadable is not None
        h2, ex2, _ = self._unreadable(tmp_path, body="")
        assert ex2._unverified_file_unreadable is None and ex2._unverified_submissions == {}


# ── 4. the positions pass asks the venue by client id ──────────────────────

def _standing(tmp_path, *, sl_tp=None, **venue_kw):
    """An executor with the submission on record, and the venue now answering
    what it is told; the send is not asked again."""
    h, ex, _ = _unverified_drive(tmp_path)
    venue = _Venue(**venue_kw)
    h2 = _Harness(venue, tmp_path, sl_tp=sl_tp)
    ex._exchange = venue
    ex._record_warning = lambda key: h2.warnings.append(key)
    return h2, ex


class TestAFillFoundIsBookedWithTheIdeasLevels:

    def test_the_positions_pass_asks_first_and_books_the_fill(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order()])
        assert ex._positions == {}
        msgs = h.positions_pass(ex)
        pos = ex._positions["TI-EST"]
        assert (pos.symbol, pos.direction, pos.status) == ("ETH/USDT", "LONG", "open")
        assert pos.entry_price == 4008.0 and getattr(pos, "entry_source") == "order"
        assert pos.quantity == 0.05
        assert (pos.stop_loss, pos.take_profit) == (TICKER * 0.98, TICKER * 1.06)
        assert (pos.sl_order_id, pos.tp_order_id) == ("sl1", "tp1")
        assert getattr(pos, "unprotected", False) is False
        assert pos.leverage >= 1 and pos.cost_usd > 0
        assert getattr(pos, "filled_at", None) is not None
        assert COID not in ex._unverified_submissions and COID not in _record(ex)
        assert h.results() == ["RECOVERED_FILL"]
        assert h.venue.calls.index("fetch_closed_orders") < h.venue.calls.index("fetch_ticker"), (
            "the submission is asked about before the book is priced")
        assert len(msgs) == 1 and msgs[0].startswith("RECOVERED FILL: ETH/USDT LONG")
        assert "$4,008.0000" in msgs[0] and "stop and target placed" in msgs[0]
        assert RuneClawEngine._is_fill_message(msgs[0]), "told as an OPEN"
        assert not RuneClawEngine._is_sync_message(msgs[0])

    def test_a_fill_stating_no_price_is_booked_at_the_pre_order_ticker_marked(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order(average=None, price=None)])
        msgs = h.reconcile(ex)
        pos = ex._positions["TI-EST"]
        assert pos.entry_price == TICKER and entry_is_estimated(pos)
        assert getattr(pos, "entry_source") == ENTRY_ESTIMATED
        assert "ESTIMATED from the pre-order ticker" in msgs[0]
        assert "fill_price_estimated" in [a.get("action") for a in h.audits]

    def test_the_fill_is_on_disk_before_the_stops_are_asked_for(self, tmp_path):
        """A stop placement can raise out of the process; the booking must not
        depend on it (a restart re-reads the positions file)."""
        seen: list = []

        async def _sl_tp(self_, *a, **k):
            seen.append(open(self_._positions_file, encoding="utf-8").read())
            return ("sl1", "tp1")

        h, ex = _standing(tmp_path, sl_tp=_sl_tp, closed_list=[_order()])
        h.reconcile(ex)
        assert seen and "TI-EST" in seen[0], "the stop was asked for before the fill was saved"

    def test_a_stop_refused_twice_leaves_the_fill_booked_and_unprotected(self, tmp_path):
        attempts = []

        async def _refuse(*a, **k):
            attempts.append(1)
            raise ccxt.ExchangeError("stop refused")

        h, ex = _standing(tmp_path, sl_tp=_refuse, closed_list=[_order()])
        msgs = h.reconcile(ex)
        pos = ex._positions["TI-EST"]
        assert len(attempts) == 2
        assert pos.sl_order_id is None and getattr(pos, "unprotected") is True
        assert "NO STOP could be placed" in msgs[0] and "UNPROTECTED" in msgs[0]
        assert h.results() == ["RECOVERED_UNPROTECTED", "RECOVERED_FILL"]
        assert "sltp_recovered" in h.warnings
        assert COID not in ex._unverified_submissions, "booked, whatever the stop did"


class TestARestingOrderIsTracked:

    def test_an_open_order_becomes_a_pending_fill(self, tmp_path):
        h, ex = _standing(tmp_path, open_list=[_order(status="open", filled=0.0,
                                                       average=None, price=3990.0)])
        msgs = h.reconcile(ex)
        pos = ex._positions["TI-EST"]
        assert (pos.status, pos.order_type, pos.limit_order_id) == ("pending_fill", "limit", "o9")
        assert pos.entry_price == 3990.0 and pos.quantity == 0.05
        assert (pos.stop_loss, pos.take_profit) == (TICKER * 0.98, TICKER * 1.06)
        assert h.results() == ["RECOVERED_RESTING"]
        assert COID not in _record(ex)
        assert msgs[0].startswith("SUBMISSION: ETH/USDT LONG") and "resting" in msgs[0]
        assert RuneClawEngine._is_sync_message(msgs[0]), "information, nothing closed"
        assert not RuneClawEngine._is_fill_message(msgs[0])


class TestAnAbsenceFreesTheIdea:

    def test_both_lists_read_and_empty_drops_the_record(self, tmp_path):
        h, ex = _standing(tmp_path, open_list=[], closed_list=[])
        msgs = h.reconcile(ex)
        assert ex._unverified_submissions == {} and COID not in _record(ex)
        assert h.results() == ["NEVER_LANDED"]
        assert msgs[0].startswith("SUBMISSION:") and "never landed" in msgs[0]
        assert "may be sent again" in msgs[0]
        assert ex._unverified_standing("ETH/USDT", COID) is None, "the idea is free"
        assert ex._positions == {}

    def test_a_cancelled_order_that_filled_nothing_never_opened(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order(status="canceled", filled=0.0)])
        msgs = h.reconcile(ex)
        assert ex._unverified_submissions == {}
        assert h.results() == ["NEVER_FILLED"]
        assert "canceled with nothing filled" in msgs[0]
        assert ex._positions == {}

    def test_a_cancelled_order_that_filled_something_is_a_fill(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order(status="canceled", filled=0.02)])
        h.reconcile(ex)
        assert ex._positions["TI-EST"].quantity == 0.02
        assert h.results() == ["RECOVERED_FILL"]


class TestAListThatStillWillNotReadIsKeptAndSaidOnce:

    def test_kept_said_once_and_still_standing(self, tmp_path, caplog):
        h, ex = _standing(tmp_path, open_list=[], closed_list=ccxt.NetworkError("still down"))
        with caplog.at_level(logging.WARNING, logger=le.logger.name):
            assert h.reconcile(ex) == []
            assert h.reconcile(ex) == []
        said = [r for r in caplog.records if "still UNVERIFIED" in r.getMessage()]
        assert len(said) == 1, "said once per record, not once per pass"
        assert COID in ex._unverified_submissions and _record(ex)[COID]["said"] is True
        assert h.results() == []
        assert ex._unverified_standing("ETH/USDT", COID) is not None
        assert ex._positions == {}

    def test_a_found_order_whose_state_cannot_be_read_is_kept(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order(status=None, filled=None)])
        assert h.reconcile(ex) == []
        assert COID in ex._unverified_submissions
        assert _record(ex)[COID]["said_unread"] is True
        assert ex._positions == {}


class TestAFillAlreadyTrackedIsUpgraded:

    def test_an_adopted_row_gets_the_ideas_levels_and_a_stop(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order()])
        adopted = LivePosition(trade_id="TI-adopted-ETH-1", symbol="ETH/USDT",
                               direction="LONG", entry_price=4008.0, quantity=0.05,
                               cost_usd=40.0, stop_loss=0.0, take_profit=0.0,
                               leverage=5, is_spot=False, status="open")
        setattr(adopted, "origin", "adopted")
        ex._positions[adopted.trade_id] = adopted
        msgs = h.reconcile(ex)
        assert list(ex._positions) == [adopted.trade_id], "no second row for one fill"
        assert (adopted.stop_loss, adopted.take_profit) == (TICKER * 0.98, TICKER * 1.06)
        assert getattr(adopted, "sl_tp_source") == "recovered_idea"
        assert (adopted.sl_order_id, adopted.tp_order_id) == ("sl1", "tp1")
        assert h.results() == ["RECOVERED_INTO_TRACKED"]
        assert COID not in ex._unverified_submissions
        assert msgs[0].startswith("SUBMISSION:") and "already tracked" in msgs[0]
        assert "stop, target, stop order placed" in msgs[0]
        assert RuneClawEngine._is_sync_message(msgs[0])

    def test_a_tracked_row_with_its_own_levels_keeps_them(self, tmp_path):
        h, ex = _standing(tmp_path, closed_list=[_order()])
        own = LivePosition(trade_id="TI-own", symbol="ETH/USDT", direction="LONG",
                           entry_price=4008.0, quantity=0.05, cost_usd=40.0,
                           stop_loss=3950.0, take_profit=4100.0, leverage=5,
                           is_spot=False, status="open", sl_order_id="s-own",
                           tp_order_id="t-own")
        ex._positions[own.trade_id] = own
        msgs = h.reconcile(ex)
        assert (own.stop_loss, own.take_profit) == (3950.0, 4100.0)
        assert own.sl_order_id == "s-own"
        assert "its record needed nothing" in msgs[0]


# ── 5. the emergency record marks an estimated entry ───────────────────────

class TestTheEmergencyRecordMarksItsEntry:

    def _crash_after_the_order(self, tmp_path, venue):
        h = _Harness(venue, tmp_path)
        ex = h.build()

        def _boom(*a, **k):
            raise RuntimeError("card renderer crashed")

        with patch.object(LiveExecutor, "_entry_filled_card", _boom):
            h.execute(ex)
        return h, ex._positions["TI-EST"]

    def test_a_stated_average_is_the_entry_and_its_source(self, tmp_path):
        h, pos = self._crash_after_the_order(tmp_path, _Venue(order_average=4008.0))
        assert "CREATED" in h.results("emergency_position")
        assert pos.entry_price == 4008.0 and getattr(pos, "entry_source") == "order"
        assert not entry_is_estimated(pos)

    def test_nothing_stated_is_the_ticker_marked_as_an_estimate(self, tmp_path):
        h, pos = self._crash_after_the_order(tmp_path, _Venue())
        assert "CREATED" in h.results("emergency_position")
        assert pos.entry_price == TICKER and entry_is_estimated(pos)
        assert "ESTIMATED" in h.results("fill_price_estimated")


# ── 6. every door reads the third outcome as neither of the other two ──────

UNVERIFIED_CARD = (
    "⚠️ ORDER UNVERIFIED: BTC/USDT LONG — the venue raised RequestTimeout "
    "while the order was being sent, and its order lists could not be read after it, "
    "so whether the order landed is unknown. Nothing was recorded as a position and "
    "nothing was re-sent. The next positions check asks the venue by client id (rcX)."
)


def test_the_planted_card_is_the_executors_own_shape(tmp_path):
    """The door drives below plant a card; this pins that the plant is what
    `execute` answers, so the doors are driven on the executor's vocabulary."""
    _, _, card = _unverified_drive(tmp_path)
    assert card.split(":")[0] == UNVERIFIED_CARD.split(":")[0]
    assert EXECUTION_UNVERIFIED_TOKEN in UNVERIFIED_CARD and outcome_unverified(UNVERIFIED_CARD)


class TestTheTelegramConfirmButton:

    def test_it_says_unverified_and_posts_nothing(self):
        replies, posts, _ = _tap(UNVERIFIED_CARD)
        assert replies[-1].startswith("⚠️")
        assert "Order outcome unverified" in replies[-1]
        assert "nothing re-sent" in replies[-1]
        assert not replies[-1].startswith("✅") and "❌" not in replies[-1][:4]
        assert posts == [], "an order the venue never confirmed was posted as a TRADE OPENED"


class TestTheWebConfirm:

    async def test_placed_is_null_the_audit_says_unverified_and_the_idea_stays(
            self, monkeypatch, web_audit_rows):
        monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
        engine = _Planted(UNVERIFIED_CARD, drops_idea=False)
        async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
            tid = await _propose(c)
            r = await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                             headers=HDRS)
            body = await r.json()
            assert r.status == 200
            assert "placed" in body and body["placed"] is None
            assert body["result_html"] == UNVERIFIED_CARD
            assert [row.result for row in _confirm_audits(web_audit_rows)] == ["UNVERIFIED"]
            assert tid in c.server.app["proposers"], "the idea is still the proposer's"


class TestTheAutoConfirmNotice:

    def test_it_is_neither_confirmed_nor_nothing_placed(self, monkeypatch, op_chat):
        w = _auto(monkeypatch, op_chat, UNVERIFIED_CARD)
        card = w.bot.to(OPERATOR)[0][0]
        assert "AUTO-CONFIRM: OUTCOME UNVERIFIED" in card
        assert "AUTO-CONFIRMED TRADE" not in card and "NOTHING PLACED" not in card
        assert "confirmed it neither way" in card
        assert "whether the order landed is not yet known" in card
        assert [k for _, k, _ in w.recorded] == ["AUTO_CONFIRM_UNVERIFIED"]


class TestTheEngineSealsTheThirdWord:

    def test_sealed_and_learned_as_unverified_and_the_idea_stays_pending(self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine.live_executor.execute = AsyncMock(return_value=UNVERIFIED_CARD)
        result = _confirm(engine, idea)
        assert result.startswith(UNVERIFIED_CARD)
        entries = [e for e in engine.audit_chain.get_entries()
                   if e.payload.get("decision_id") == idea.id]
        assert entries and entries[-1].payload["outcome"] == "EXECUTION_UNVERIFIED"
        kw = engine.learning.log_decision.call_args.kwargs
        assert kw["decision"] == "EXECUTION_UNVERIFIED"
        assert idea.id in engine._pending_ideas, "a submission the venue may hold is not popped"
        assert engine.state == AgentState.IDLE

    def test_the_learner_reads_it_as_not_yet_known(self):
        assert opened("EXECUTION_UNVERIFIED") is None
        assert opened("EXECUTION_FAILED") is False

    def test_the_explainer_has_a_verb_for_it(self):
        from bot.guardian.explain_fill import explain
        text = json.dumps(explain({"idea": {"asset": "BTC/USDT", "direction": "LONG"},
                                   "outcome": "EXECUTION_UNVERIFIED"}))
        assert "never confirmed either way" in text


class TestTheSentenceIsTranslated:

    def test_fourteen_of_fourteen(self):
        from bot.utils.i18n import _STRINGS, SUPPORTED_LANGS
        entry = _STRINGS["trade_outcome_unverified"]
        assert len(SUPPORTED_LANGS) == 14
        for code in SUPPORTED_LANGS:
            assert isinstance(entry.get(code), str) and entry[code].strip(), code

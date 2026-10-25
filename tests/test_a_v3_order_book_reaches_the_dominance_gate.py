"""Bitget's v2 book refuses the R-prefix equities, and the gate saw no book.

``OrderFlowAnalyzer.analyze`` asks ccxt for the spot book. For an ordinary
pair that is ``GET /api/v2/spot/market/orderbook`` and it answers. For the
R-prefix tokenized equities (unified ``RFSLY/USDT``, market id
``RFSLYUSDT``, base coin ``rFSLY`` — the same family as ``RAAPL/USDT``)
Bitget answers that URL with HTTP 400:

    {"code":"40502","msg":"Please upgrade to access the V3 API."}

ccxt raises, the exception used to land only in ``notes``, ``"book"`` never
entered ``components_ok``, and ``check_bid_dominance`` fail-closed with
"order book data unavailable" on a book the venue does publish. The same
limit on ``/api/v3/market/orderbook?category=SPOT`` (ccxt
``params={"uta": True}``) returns it: 25 levels for RAAPL, an empty book
for RFSLY when nothing is resting. An empty book is a book the venue
answered, and the gate refuses it on its depth. A book that still cannot
be read stays fail-closed, and the refusal is an audit line that names
the symbol and the error.

Checked live against ccxt 4.5.56 on 2026-10-25: v2 raised that body for
RFSLY/USDT and RAAPL/USDT, v3 returned RAAPL's 25 levels and RFSLY's
empty book, and BTC/USDT still answered on v2.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from bot.core import order_flow as of
from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig

V2_REFUSAL = (
    'bitget {"code":"40502","msg":"Please upgrade to access the V3 API.",'
    '"data":null}'
)

# Bid notional 100*40 + 99*10 = 4990; ask 101*10 + 102*5 = 1520.
# 4990/1520 is about 3.28, over the 1.2 gate.
_DEEP = {
    "bids": [[100.0, 40.0], [99.0, 10.0]],
    "asks": [[101.0, 10.0], [102.0, 5.0]],
}
# 1000 against 1000. Under 1.2, so a book that arrived must still be able
# to reject. A loosened gate would pass this.
_BALANCED = {
    "bids": [[100.0, 10.0]],
    "asks": [[101.0, 1000.0 / 101.0]],
}
_EMPTY = {"bids": [], "asks": []}


class _V2Refusal(Exception):
    pass


class _Venue:
    """v2 raises, or returns; v3 is a second answer, keyed on uta=True."""

    def __init__(self, v2, v3=None):
        self._v2 = v2
        self._v3 = v3
        self.calls: list[dict] = []

    async def fetch_order_book(self, symbol, limit=None, params=None):
        self.calls.append({"symbol": symbol, "limit": limit, "params": params})
        if params and params.get("uta"):
            if isinstance(self._v3, BaseException):
                raise self._v3
            if self._v3 is None:
                raise AssertionError("v3 book was not supposed to be read")
            return self._v3
        if isinstance(self._v2, BaseException):
            raise self._v2
        return self._v2

    async def fetch_trades(self, symbol, limit=None):
        return []

    async def fetch_funding_rate(self, symbol):
        raise RuntimeError("spot symbol")

    async def fetch_open_interest(self, symbol):
        raise RuntimeError("no oi")


class _NoParams:
    """A double whose book method does not accept params, as several do."""

    def __init__(self):
        self.calls = 0

    async def fetch_order_book(self, symbol, limit=None):
        self.calls += 1
        raise _V2Refusal(V2_REFUSAL)

    async def fetch_trades(self, symbol, limit=None):
        return []

    async def fetch_funding_rate(self, symbol):
        raise RuntimeError("spot symbol")

    async def fetch_open_interest(self, symbol):
        raise RuntimeError("no oi")


def _analyzer():
    return OrderFlowAnalyzer(OrderFlowConfig(
        book_depth_levels=25,
        dominance_required_ratio=1.2,
        cross_venue_funding=False,
    ))


def _read(venue, symbol, monkeypatch):
    seen: list[dict] = []

    def _audit(channel, message, **kw):
        seen.append({"message": message, **kw})

    monkeypatch.setattr(of, "audit", _audit)
    logging.disable(logging.CRITICAL)
    try:
        sig = asyncio.run(_analyzer().analyze(venue, symbol))
    finally:
        logging.disable(logging.NOTSET)
    return sig, seen


def _book_failures(seen):
    return [row for row in seen if row.get("result") == "UNAVAILABLE"]


class TestTheV3BookReachesTheGate:
    def test_a_v2_refusal_reads_the_v3_book_and_the_gate_can_pass(self, monkeypatch):
        venue = _Venue(_V2Refusal(V2_REFUSAL), _DEEP)
        sig, seen = _read(venue, "RAAPL/USDT", monkeypatch)
        assert [c["symbol"] for c in venue.calls] == ["RAAPL/USDT", "RAAPL/USDT"]
        assert venue.calls[0]["params"] is None
        assert venue.calls[0]["limit"] == 25
        assert venue.calls[1]["params"] == {"uta": True}
        assert venue.calls[1]["limit"] == 25
        assert "book" in sig.components_ok
        assert "order book read from the v3 endpoint" in sig.notes
        gate = _analyzer().check_bid_dominance(sig, "LONG")
        assert gate["passed"] is True
        assert gate["bid_ask_ratio"] >= 1.2
        assert _book_failures(seen) == []

    def test_a_v3_book_under_the_ratio_still_fails_the_gate(self, monkeypatch):
        # The fallback delivers the book. It does not move the ratio.
        venue = _Venue(_V2Refusal(V2_REFUSAL), _BALANCED)
        sig, seen = _read(venue, "RAAPL/USDT", monkeypatch)
        assert "book" in sig.components_ok
        gate = _analyzer().check_bid_dominance(sig, "LONG")
        assert gate["passed"] is False
        assert "1.2" in gate["reason"]
        assert "unavailable" not in gate["reason"]
        assert _book_failures(seen) == []

    def test_an_empty_v3_book_is_measured_and_still_refused(self, monkeypatch):
        # RFSLY's v3 book was empty at the time of the report: no resting
        # bid or ask. That is a book, and the gate refuses it on depth.
        venue = _Venue(_V2Refusal(V2_REFUSAL), _EMPTY)
        sig, seen = _read(venue, "RFSLY/USDT", monkeypatch)
        assert "book" in sig.components_ok
        assert "empty order book" in sig.notes
        gate = _analyzer().check_bid_dominance(sig, "LONG")
        assert gate["passed"] is False
        assert gate["reason"] != "order book data unavailable (fail-closed)"
        assert "ratio" in gate["reason"]
        assert _book_failures(seen) == []

    def test_a_book_neither_endpoint_returns_stays_fail_closed(self, monkeypatch):
        venue = _Venue(_V2Refusal(V2_REFUSAL), RuntimeError("v3 order book timed out"))
        sig, seen = _read(venue, "RFSLY/USDT", monkeypatch)
        assert "book" not in sig.components_ok
        assert len(venue.calls) == 2
        gate = _analyzer().check_bid_dominance(sig, "LONG")
        assert gate["passed"] is False
        assert gate["reason"] == "order book data unavailable (fail-closed)"
        assert len(_book_failures(seen)) == 1
        line = _book_failures(seen)[0]
        assert line["message"].startswith("order book fetch failed for RFSLY/USDT:")
        assert "v3 order book timed out" in line["message"]
        assert line["data"]["symbol"] == "RFSLY/USDT"
        assert line["level"] == logging.WARNING

    def test_a_timeout_is_not_a_second_call(self, monkeypatch):
        venue = _Venue(TimeoutError("order book timed out"))
        sig, seen = _read(venue, "BTC/USDT", monkeypatch)
        assert len(venue.calls) == 1
        assert venue.calls[0]["params"] is None
        assert "book" not in sig.components_ok
        gate = _analyzer().check_bid_dominance(sig, "LONG")
        assert gate["reason"] == "order book data unavailable (fail-closed)"
        assert len(_book_failures(seen)) == 1
        assert "BTC/USDT" in _book_failures(seen)[0]["message"]
        assert "order book timed out" in _book_failures(seen)[0]["message"]

    def test_a_v2_book_is_not_read_twice(self, monkeypatch):
        venue = _Venue(_DEEP)
        sig, seen = _read(venue, "BTC/USDT", monkeypatch)
        assert len(venue.calls) == 1
        assert "book" in sig.components_ok
        assert _book_failures(seen) == []
        assert _analyzer().check_bid_dominance(sig, "LONG")["passed"] is True

    def test_a_stand_in_that_cannot_take_params_still_fail_closes(self, monkeypatch):
        venue = _NoParams()
        sig, seen = _read(venue, "RFSLY/USDT", monkeypatch)
        assert venue.calls == 1
        assert "book" not in sig.components_ok
        assert _analyzer().check_bid_dominance(sig, "LONG")["passed"] is False
        assert "40502" in _book_failures(seen)[0]["message"]
        assert "RFSLY/USDT" in _book_failures(seen)[0]["message"]


class TestTheRefusalIsTheV3Sentence:
    def test_the_live_body_is_the_signal(self):
        assert of.order_book_moved_to_v3(_V2Refusal(V2_REFUSAL)) is True

    @pytest.mark.parametrize("text", [
        "TimeoutError: order book timed out",
        "bitget {\"code\":\"40502\",\"msg\":\"If it is a copy user, you must pass the copy to whom\"}",
        "BadSymbol: bitget does not have market symbol RFSLY/USDT",
    ])
    def test_anything_else_is_not_a_second_call(self, text):
        assert of.order_book_moved_to_v3(RuntimeError(text)) is False

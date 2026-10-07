"""The active venue's data client is never handed to a second event loop.

`_get_venue_data_exchange` cached one ccxt client and both the scan lane and
the engine loop read the active venue's market data through it. A ccxt
session is bound to the loop that opened it, and the second loop's read
raised ("Timeout context manager should be used inside a task"). The client
now follows the futures client's rule: one per loop. A /venue switch still
drops every loop's client for the old venue.
"""
import asyncio
from types import SimpleNamespace

import bot.core.market_scanner as ms
from bot.core.market_scanner import MarketScanner


class _Client:
    def __init__(self, venue, opts):
        self.venue, self.closed = venue, False

    async def close(self):
        self.closed = True


def _scanner(monkeypatch, venue_box):
    monkeypatch.setattr("bot.core.venues.get_venue",
                        lambda vid=None: SimpleNamespace(id=venue_box[0]))
    for vid in ("bybit", "okx"):
        monkeypatch.setattr(ms.ccxt, vid, lambda opts, _v=vid: _Client(_v, opts), raising=False)
    s = MarketScanner.__new__(MarketScanner)
    s._loop_clients = {}
    s._venue_data_exchange = None
    s._venue_data_exchange_id = None
    return s


def _on(loop, s):
    return loop.run_until_complete(s._get_venue_data_exchange())


def test_each_loop_gets_its_own_client_and_keeps_it(monkeypatch):
    s = _scanner(monkeypatch, ["bybit"])
    one, two = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        a, b = _on(one, s), _on(two, s)
        assert a is not b
        assert _on(one, s) is a and _on(two, s) is b
    finally:
        one.close()
        two.close()


def test_a_venue_switch_drops_every_loops_old_client(monkeypatch):
    box = ["bybit"]
    s = _scanner(monkeypatch, box)
    one, two = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        old_a, old_b = _on(one, s), _on(two, s)
        box[0] = "okx"
        new_a = _on(one, s)
        assert new_a.venue == "okx" and old_a.closed and old_b.closed
        assert all(not k[1].startswith("venue:bybit") for k in s._loop_clients)
    finally:
        one.close()
        two.close()


def test_bitget_has_no_separate_data_client(monkeypatch):
    s = _scanner(monkeypatch, ["bitget"])
    loop = asyncio.new_event_loop()
    try:
        assert _on(loop, s) is None
    finally:
        loop.close()

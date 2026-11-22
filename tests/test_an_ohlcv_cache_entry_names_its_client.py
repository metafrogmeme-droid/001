"""Two clients' candles are two series: the OHLCV cache key names who served them.

`RuneClawEngine._cached_ohlcv` keyed ``{symbol}:{timeframe}:{limit}``, so
any two clients asked for the same symbol shared one entry. The spot and the
perp Bitget clients are both ``id == "bitget"`` and differ only in
``defaultType``; and `/venue` switches the data client without clearing the
cache, so the previous venue's candles were served as the new venue's for up
to the TTL. The key is now ``{venue}/{market}|{symbol}:{timeframe}:{limit}``
(`_ohlcv_cache_key`), and the chat draft -- the one reader of the key's
shape -- reads it.
"""
from __future__ import annotations

import asyncio
import time

from bot.core.engine import RuneClawEngine, _ohlcv_cache_key
from bot.nlp.chat_draft import market_for


def _rows(close: float, n: int = 20) -> list:
    """``n`` CLOSED 1h bars ending an hour ago, the last closing at ``close``
    (enough for the chat draft's ATR)."""
    hour = 3_600_000
    last_open = (int(time.time() * 1000) // hour) * hour - hour
    return [[last_open - (n - 1 - i) * hour, close, close + 1, close - 1, close, 10.0]
            for i in range(n)]


class _Client:
    def __init__(self, venue: str, market: str, close: float):
        self.id = venue
        self.options = {"defaultType": market}
        self.close = close
        self.asked = 0

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        self.asked += 1
        return _rows(self.close)


class _Host:
    """The real cache method and the real forming-bar drop it applies; the
    health feed it reports to is recorded, not needed."""
    _cached_ohlcv = RuneClawEngine._cached_ohlcv
    _drop_forming_candle = RuneClawEngine._drop_forming_candle

    def _record_exchange_read(self, *a, **k):
        self.reads.append(a)

    def __init__(self):
        self._ohlcv_cache: dict = {}
        self.reads: list = []


def _read(host, client, symbol="BTC/USDT"):
    return asyncio.run(host._cached_ohlcv(client, symbol, "1h", limit=100, ttl=900))


def test_the_spot_and_the_perp_client_get_their_own_rows():
    host = _Host()
    spot, perp = _Client("bitget", "spot", 100.0), _Client("bitget", "swap", 200.0)
    assert _read(host, spot)[-1][4] == 100.0
    assert _read(host, perp)[-1][4] == 200.0, "the perp was served the spot's candles"
    assert (spot.asked, perp.asked) == (1, 1)
    # ...and each is still cached under its own key.
    assert _read(host, spot)[-1][4] == 100.0 and spot.asked == 1


def test_a_venue_switch_does_not_serve_the_old_venues_candles():
    host = _Host()
    assert _read(host, _Client("bitget", "swap", 100.0))[-1][4] == 100.0
    assert _read(host, _Client("hyperliquid", "swap", 300.0))[-1][4] == 300.0


def test_the_same_client_is_still_served_from_the_cache():
    # The other arm: the key did not become per-call.
    host = _Host()
    c = _Client("bitget", "swap", 100.0)
    _read(host, c)
    _read(host, c)
    assert c.asked == 1


def test_the_key_names_the_venue_the_market_and_the_series():
    assert _ohlcv_cache_key(_Client("bitget", "swap", 1.0), "BTC/USDT", "1h", 100) == \
        "bitget/swap|BTC/USDT:1h:100"


def test_a_client_with_no_id_is_named_by_its_class_not_left_blank():
    class Bare:
        async def fetch_ohlcv(self, *_a, **_k):
            return []
    assert _ohlcv_cache_key(Bare(), "BTC/USDT", "1h", 100) == "Bare/-|BTC/USDT:1h:100"


def test_the_chat_draft_reads_the_new_key_and_still_the_old():
    rows = _rows(150.0)
    new = type("E", (), {})()
    new._ohlcv_cache = {"bitget/swap|BTC/USDT:USDT:1h:100": (5.0, rows, 900.0)}
    assert market_for(new, "BTC")["price"] == 150.0
    old = type("E", (), {})()
    old._ohlcv_cache = {"BTC/USDT:1h:100": (5.0, rows, 900.0)}
    assert market_for(old, "BTC")["price"] == 150.0
    other = type("E", (), {})()
    other._ohlcv_cache = {"bitget/swap|ETH/USDT:1h:100": (5.0, rows, 900.0)}
    assert market_for(other, "BTC")["read_state"] == "unread"

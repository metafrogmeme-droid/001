"""A chat-drafted ticket is priced from the analyzer's own candle leg.

`market_for` took whichever `BTC/USDT…` cache entry was stamped last, from
the 15m, 1h, 4h and 1d legs the engine caches for one symbol. The entry was
that leg's last closed close (a day old on the 1d), and the stop and target
were that leg's ATR times multiples tuned for the 1h. A manual limit ticket
then skips the confirm's drift check, so the stale entry rode to the order.
"""
import inspect

from bot.core.engine import RuneClawEngine
from bot.nlp import chat_draft


def _rows(start, step, n=30):
    rows, price = [], start
    for i in range(n):
        price += step
        rows.append([1_700_000_000_000 + i * 3_600_000, price - step, price + abs(step) * 2,
                     price - abs(step) * 2, price, 10])
    return rows


class _Engine:
    def __init__(self):
        self._ohlcv_cache = {}


def test_the_1h_leg_is_read_even_when_another_leg_is_newer():
    e = _Engine()
    h1 = _rows(100.0, 0.5)
    d1 = _rows(50.0, 5.0)        # a different price and a ten-times ATR
    e._ohlcv_cache["BTC/USDT:1h:100"] = (10.0, h1, 900.0)
    e._ohlcv_cache["BTC/USDT:1d:200"] = (99.0, d1, 21600.0)       # stamped last
    e._ohlcv_cache["BTC/USDT:15m:48"] = (98.0, _rows(70.0, 0.1), 60.0)
    read = chat_draft.market_for(e, "BTC/USDT")
    assert read["read_state"] == "read"
    assert read["price"] == h1[-1][4]
    assert read["atr"] == chat_draft._atr(h1)


def test_no_1h_leg_is_unread_not_another_leg():
    e = _Engine()
    e._ohlcv_cache["BTC/USDT:1d:200"] = (99.0, _rows(50.0, 5.0), 21600.0)
    e._ohlcv_cache["BTC/USDT:4h:200"] = (98.0, _rows(60.0, 1.0), 3600.0)
    assert chat_draft.market_for(e, "BTC/USDT")["read_state"] == "unread"


def test_the_perp_spelling_of_the_symbol_still_matches():
    e = _Engine()
    h1 = _rows(100.0, 0.5)
    e._ohlcv_cache["BTC/USDT:USDT:1h:100"] = (5.0, h1, 900.0)
    assert chat_draft.market_for(e, "BTC/USDT")["price"] == h1[-1][4]


def test_the_leg_is_the_analyzers_own_timeframe():
    default = inspect.signature(RuneClawEngine._analyze_signal).parameters["timeframe"].default
    assert chat_draft.ANALYZER_TIMEFRAME == default

"""A key check never reads a missing balance as an empty account.

The card `/connect` answers with carries the key check's own detail as its
"Balance:" line ("Balance: 52.56 USDT free", 8 October). All five probes read
it as ``float(bal["USDT"]["free"] or 0.0)`` and fell back to 0.0 on a missing
entry or an unparseable value. So a key whose account holds USDC, which a Bybit
or Bitget unified account can, was linked with "Balance: 0.00 USDT free": a
measurement of an empty account made from a line the reply never carried.

`_free_detail` is the one reading now, through `read_money_field`: a real 0 is
"0.00 USDT free", and absent or unreadable says the venue answered without a
readable figure.

Driven through each venue's own validator and ccxt's own client class, with
only `fetch_balance` replaced.
"""
from __future__ import annotations

import asyncio

import ccxt.async_support as ccxt_async
import pytest

from bot.core import exchange_credentials as ec

AGENT = {"wallet_address": "0x" + "2" * 40, "agent_private_key": "0x" + "1" * 64}
KEYS = {"api_key": "k" * 12, "api_secret": "s" * 24, "passphrase": "p" * 8}

# venue, ccxt class, the coin its check reads, its fields
VENUES = [
    ("bitget", "bitget", "USDT", KEYS),
    ("bybit", "bybit", "USDT", {"api_key": KEYS["api_key"], "api_secret": KEYS["api_secret"]}),
    ("bingx", "bingx", "USDT", {"api_key": KEYS["api_key"], "api_secret": KEYS["api_secret"]}),
    ("okx", "okx", "USDT", KEYS),
    ("gate", "gate", "USDT", {"api_key": KEYS["api_key"], "api_secret": KEYS["api_secret"]}),
    ("kucoin", "kucoinfutures", "USDT", KEYS),
    ("hyperliquid", "hyperliquid", "USDC", AGENT),
    ("paradex", "paradex", "USDC", AGENT),
]


def _check(monkeypatch, venue, cls, reply):
    async def fetch_balance(self, params=None):
        return reply

    monkeypatch.setattr(getattr(ccxt_async, cls), "fetch_balance", fetch_balance)
    return asyncio.run(ec.validate_venue_credentials(venue, dict(VENUES_BY[venue][3]), sandbox=False))


VENUES_BY = {v[0]: v for v in VENUES}


@pytest.mark.parametrize("venue,cls,coin,_fields", VENUES)
@pytest.mark.parametrize("reply", [
    {"EUR": {"free": 5.0, "total": 5.0}},          # the coin is not in the reply
    "COIN_FREE_NONE",                               # the line is there, its free is null
    "COIN_FREE_JUNK",                               # and unparseable
    {},                                             # nothing at all
])
def test_a_missing_or_unreadable_balance_is_not_zero(monkeypatch, venue, cls, coin, _fields, reply):
    if reply == "COIN_FREE_NONE":
        reply = {coin: {"free": None, "total": 3.0}}
    elif reply == "COIN_FREE_JUNK":
        reply = {coin: {"free": "n/a"}}
    ok, detail = _check(monkeypatch, venue, cls, reply)
    assert ok is True, "the venue answered: the key authenticates"
    assert detail.startswith(f"authenticated, but no readable {coin} balance in the reply"), detail
    assert "0.00" not in detail


@pytest.mark.parametrize("venue,cls,coin,_fields", VENUES)
def test_a_real_zero_and_a_real_figure_are_readings(monkeypatch, venue, cls, coin, _fields):
    ok, detail = _check(monkeypatch, venue, cls, {coin: {"free": 0.0, "used": 0.0, "total": 0.0}})
    assert ok is True and detail.startswith(f"0.00 {coin} free"), detail
    ok, detail = _check(monkeypatch, venue, cls, {coin: {"free": "52.56", "total": 60.0}})
    assert ok is True and detail.startswith(f"52.56 {coin} free"), detail


def test_the_reading_itself():
    assert ec._free_detail({"USDT": {"free": 0}}, "USDT") == "0.00 USDT free"
    assert ec._free_detail({"USDT": {"free": 1.005}}, "USDT") == "1.00 USDT free"
    for bal in (None, [], "x", {"USDT": None}, {"USDT": {"free": float("nan")}},
                {"USDT": {"free": True}}):
        assert ec._free_detail(bal, "USDT").startswith("authenticated, but no readable"), bal

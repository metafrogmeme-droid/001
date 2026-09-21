"""HIP-3 coins keep their dex, and a master key never builds a client.

``hyperliquid_coin`` is what ``HyperliquidVenue.swap_symbol`` reads. An
unreadable coin falls through to the inherited map rather than raising —
scans hand symbols this reading declines — and it never strips ``xyz:``
onto the main dex. ``hyperliquid_key_role`` reuses ``check_signing_key``.
Rejected and master refuse before ccxt is constructed. Unconfirmed (no
address could be derived) still constructs, and the role is stored on
the client so the check has a reader.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.core.venues import (
    HyperliquidVenue,
    hyperliquid_coin,
    hyperliquid_key_role,
)

HL = HyperliquidVenue()
_KEY = "0x" + "11" * 32
_OTHER = "0x" + "ab" * 32


@pytest.mark.parametrize("symbol,coin", [
    ("UNI/USDT", "UNI"),
    ("UNI/USDT:USDT", "UNI"),
    ("BTC/USDC:USDC", "BTC"),
    ("1000PEPE/USDT", "1000PEPE"),
    ("xyz:TSLA", "xyz:TSLA"),
    ("xyz:TSLA/USDT", "xyz:TSLA"),
    ("XYZ:tsla", "xyz:TSLA"),
    ("xyz:TSLA/USDC:USDC", "xyz:TSLA"),
])
def test_a_readable_coin_keeps_its_dex(symbol, coin):
    assert hyperliquid_coin(symbol) == coin
    assert HL.swap_symbol(symbol) == f"{coin}/USDC:USDC"
    assert HL.order_symbol(symbol) == f"{coin}/USDC:USDC"


@pytest.mark.parametrize("symbol", ["", "   ", "xyz:", ":TSLA", "xyz:TSLA:EXTRA",
                                     "xyz:TSLA EXTRA", None, 3])
def test_an_unreadable_coin_is_none_and_is_not_stripped(symbol):
    assert hyperliquid_coin(symbol) is None
    if not isinstance(symbol, str) or not symbol.strip():
        return
    mapped = HL.swap_symbol(symbol)
    assert mapped != "TSLA/USDC:USDC"
    assert not mapped.startswith("TSLA/")


def test_the_pinned_usdt_mapping_does_not_move():
    assert HL.swap_symbol("UNI/USDT") == "UNI/USDC:USDC"
    assert HL.swap_symbol("BTC/USDC:USDC") == "BTC/USDC:USDC"


def _cfg(key: str, wallet: str = "0xabc"):
    return SimpleNamespace(
        hyperliquid_wallet_address=wallet,
        hyperliquid_private_key=key,
        hyperliquid_testnet=False,
        trade_mode="futures",
    )


def test_a_rejected_key_never_builds_a_client():
    key = "0xnot-a-key"
    with pytest.raises(RuntimeError) as caught:
        HL.create_exchange(_cfg(key))
    message = str(caught.value)
    assert key not in message
    assert "not-a-key" not in message


def test_the_master_wallet_key_is_refused(monkeypatch):
    monkeypatch.setattr(
        "bot.web.web3_signer.check_signing_key",
        lambda raw: {"ok": True, "address": "0xABC", "reason": ""},
    )
    assert hyperliquid_key_role(_OTHER, "0xabc") == "master"
    with pytest.raises(RuntimeError) as caught:
        HL.create_exchange(_cfg(_OTHER, wallet="0xabc"))
    message = str(caught.value)
    assert _OTHER not in message
    assert _OTHER[2:] not in message


def test_an_agent_key_is_recorded_on_the_client(monkeypatch):
    monkeypatch.setattr(
        "bot.web.web3_signer.check_signing_key",
        lambda raw: {"ok": True, "address": "0x1111111111111111111111111111111111111111",
                     "reason": ""},
    )
    assert hyperliquid_key_role(_KEY, "0xabc") == "agent"
    ex = HL.create_exchange(_cfg(_KEY))
    try:
        assert ex.options["runeclaw_key_role"] == "agent"
        assert ex.walletAddress == "0xabc"
    finally:
        asyncio.run(ex.close())


def test_a_well_formed_key_with_no_address_is_unconfirmed(monkeypatch):
    monkeypatch.setattr("bot.web.web3_signer._signing_lib", lambda: None)
    assert hyperliquid_key_role(_KEY, "0xabc") == "unconfirmed"


@pytest.mark.asyncio
async def test_the_reference_price_reads_the_ticker_as_money(tmp_path):
    from bot.core.live_executor import LiveExecutor

    ex = LiveExecutor(
        user_id="1",
        credentials={"wallet_address": "0xabc", "agent_private_key": "k"},
        venue="hyperliquid",
        state_dir=str(tmp_path),
    )
    assert ex._venue.market_order_needs_price is True

    class Book:
        def __init__(self, ticker):
            self.ticker = ticker
            self.symbol = None

        async def fetch_ticker(self, symbol):
            self.symbol = symbol
            return self.ticker

    zero = Book({"last": "0", "close": "100"})
    assert await ex._venue_market_price(zero, "BTC/USDT") is None
    assert zero.symbol == "BTC/USDC:USDC"

    blank = Book({"last": "", "close": "10.5"})
    assert await ex._venue_market_price(blank, "ETH/USDT") == pytest.approx(10.5)

    spelled = Book({"last": "$1,234.50", "close": "1"})
    assert await ex._venue_market_price(spelled, "xyz:TSLA") == pytest.approx(1234.5)
    assert spelled.symbol == "xyz:TSLA/USDC:USDC"

    percent = Book({"last": "1.2%", "close": "9"})
    assert await ex._venue_market_price(percent, "BTC/USDT") is None

    missing = Book({"last": None, "close": None})
    assert await ex._venue_market_price(missing, "BTC/USDT") is None

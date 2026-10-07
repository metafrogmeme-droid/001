"""A symbol the research dossier cannot be asked for is said as such.

`fetch_research` strips "usdt", "比特币" or "???" to nothing and returns None
without sending a request. The door read that None as every other None: "The
web app isn't reachable (or your account isn't linked) ... /link your
account". The channel was never asked, and /link changes nothing. The door
asks the same reading the pull does (`research_base`) and says the symbol
could not be read, before any channel.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.skills.telegram_handler import TelegramHandler
from bot.utils import web_data_pull as wdp


class _Host:
    research_card_text = TelegramHandler.research_card_text

    def __init__(self):
        self.asked: list = []

    async def _web_card_text(self, name, surface, telegram_id="", params=None,
                             unlinked=None, keep_markup=False):
        self.asked.append((name, surface, params))
        return "DOSSIER"


def _card(symbol, surface="telegram"):
    host = _Host()
    out = asyncio.run(host.research_card_text(symbol, surface=surface))
    return out, host.asked


@pytest.mark.parametrize("symbol", ["usdt", "比特币", "???", "/USDT"])
def test_a_symbol_with_no_base_is_named_and_no_channel_is_asked(symbol):
    out, asked = _card(symbol)
    assert asked == []
    assert "is not a coin symbol the research dossier can read" in out
    assert "<code>/research PENDLE</code>" in out
    assert "/link" not in out and "reachable" not in out and "did not answer" not in out


def test_the_web_surface_names_words_not_a_slash_command():
    out, asked = _card("usdt", surface="web")
    assert asked == []
    assert "<code>research PENDLE</code>" in out and "/research" not in out


def test_the_symbol_is_escaped_where_it_is_repeated():
    out, asked = _card("<>!!")
    assert asked == [] and "<i>&lt;&gt;!!</i>" in out


@pytest.mark.parametrize("symbol, base", [
    ("pendle", "PENDLE"), ("SOL/USDT", "SOL"), ("btcusdt", "BTC"), ("  eth ", "ETH"),
])
def test_a_readable_symbol_asks_the_channel(symbol, base):
    out, asked = _card(symbol)
    assert out == "DOSSIER"
    assert asked == [("research", "telegram", {"symbol": symbol})]
    assert wdp.research_base(symbol) == base


@pytest.mark.parametrize("symbol", ["usdt", "比特币", "???", "", None, 5])
def test_the_pull_reads_the_same_way(symbol, monkeypatch):
    sent: list = []
    monkeypatch.setattr(wdp, "SYNC_SECRET", "s")
    monkeypatch.setattr(wdp, "fetch_web_card", lambda *a, **k: sent.append((a, k)) or {})
    assert wdp.research_base(symbol) is None
    assert wdp.fetch_research(symbol) is None and sent == []

"""The radar doors take the phrasings their cards claim.

#479–#481 moved the RWA, airdrop and venue radars off the website's own
chat shortcuts onto the shared doors. Each move left a phrasing behind:

- `_EDU` declines every sentence that opens "how is/are", so "how are rwa
  tokens doing" reached the model and "how is rwa doing vs btc" a BTC chart.
  The website shortcut had caught both.
- The venue rule wrote its words with single spaces, so "best  venue for
  BTC" reached the model. The Node pattern it replaced took any whitespace.
- The venue reader took the first two-to-ten letters after "for" or "to",
  so the card answered "No cross-venue funding data for GO" (from "to go
  long btc"), for ING (from "for longing eth") and for TRADE.
- The test that pinned "can you farm airdrops for me?" to the guided-only
  card was deleted with the shortcut and not replaced.
- `/etf` fetched its card for itself and reported a fetch that ran out of
  its budget as a channel that did not answer. `/rwa` did the same until it
  moved onto `_web_card_text`; that seam is pinned in the intercept test.

Driven: the router's own `classify_rules`, the reader both doors call, and
the `/etf` command.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import bot.utils.web_data_pull as wdp
from bot.nlp.intent_router import IntentRouter, is_not_a_ticker
from bot.nlp.web_card_args import venue_base
from bot.skills.telegram_handler import TelegramHandler


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def route(router, text: str) -> str:
    x = router.classify_rules(text)
    if x.is_social:
        return "SOCIAL"
    if not x.skill:
        return "MODEL"
    return f"ASK:{x.skill}" if x.confidence < 1.0 else x.skill


# ── RWA: how the sector stands now is the radar's question ───────────────────

@pytest.mark.parametrize("text", [
    "how are rwa tokens doing", "how is rwa doing vs btc", "how is rwa doing",
    "how are rwas doing", "how is the rwa sector doing", "how are tokenized treasuries doing",
    "how are real world assets performing", "so how is rwa looking", "how's rwa doing",
    "hows rwa doing", "rwa radar", "rwa vs btc",
])
def test_a_question_about_how_rwa_stands_reaches_the_radar(router, text):
    assert route(router, text) == "rwa", text


@pytest.mark.parametrize("text, expected", [
    # What RWA is stays the model's, the rule `_EDU` exists for.
    ("what is rwa", "MODEL"), ("what are real world assets", "MODEL"),
    ("how does rwa work", "MODEL"), ("how do rwas work", "MODEL"),
    ("how is rwa different from defi", "MODEL"), ("how are rwa tokens issued", "MODEL"),
    # The chart keeps "how is <asset> doing".
    ("how is btc doing", "analyze_asset"), ("hows btc doing", "analyze_asset"),
    ("how is eth looking", "analyze_asset"),
])
def test_what_rwa_is_and_how_an_asset_is_doing_stay_where_they_were(router, text, expected):
    assert route(router, text) == expected, text


# ── airdrops: a farming ask gets the guided-only card ────────────────────────

@pytest.mark.parametrize("text", [
    "can you farm airdrops for me?", "farm airdrops", "farming airdrops please",
    "airdrop radar",
])
def test_a_farming_ask_reaches_the_airdrops_door(router, text):
    """The card on that door carries the guided-only stance
    (`app/test/airdrops.test.js` pins the card). A rule registered above it
    that took the farming phrasing would answer it with something else."""
    assert route(router, text) == "airdrops", text


# ── venue router: any whitespace, and the asset is an asset ──────────────────

@pytest.mark.parametrize("text", [
    "best venue for BTC", "best  venue for BTC", "best\tvenue for BTC",
    "cheapest   exchange to short eth", "best venues for sol", "venue  router",
    "cheapest  funding", "whats the best exchange to trade on",
])
def test_the_venue_door_takes_any_whitespace(router, text):
    assert route(router, text) == "venue_router", text


@pytest.mark.parametrize("text, base", [
    ("best venue for BTC", "BTC"), ("best  venue  for  BTC", "BTC"),
    ("best venue to go long btc", "BTC"), ("best venue for longing eth", "ETH"),
    ("best exchange for shorting doge", "DOGE"), ("best venue to buy btc", "BTC"),
    ("best venue to trade btc", "BTC"), ("best venue for the btc trade", "BTC"),
    ("best venue for my eth", "ETH"), ("cheapest exchange to short ethusdt", "ETH"),
    ("best venue to be long sol", "SOL"), ("cheapest exchange for $pendle", "PENDLE"),
    ("best venues for sol", "SOL"),
])
def test_the_venue_reader_reads_the_asset_past_the_verb(text, base):
    assert venue_base(text) == base


@pytest.mark.parametrize("text", [
    "whats the best exchange to trade on", "best venue for trading", "best venue for a long",
    "best venue for perps", "best venue for funding", "best venue to trade on",
    # "on eth" is past the slot. The top five, never a wrong asset.
    "best venue for going long on eth",
    "best venue", "venue router", "cheapest funding", "",
])
def test_a_venue_ask_that_names_no_asset_is_the_top_five(text):
    assert venue_base(text) == ""


@pytest.mark.parametrize("word", ["the", "my", "on", "trading", "trade", "position", "it"])
def test_the_venue_reader_asks_the_routers_list_of_words_that_are_not_tickers(word):
    """One list: a word the router never reads as an asset is not one here."""
    assert is_not_a_ticker(word)
    assert venue_base(f"best venue for {word}") == ""


def test_a_real_ticker_is_not_on_the_list():
    for word in ("btc", "ETH", "pendle", "natgas"):
        assert not is_not_a_ticker(word), word
    assert not is_not_a_ticker("")


# ── /etf: a slow card is a wait ──────────────────────────────────────────────

def _etf_host():
    return NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
              _send_photo=AsyncMock(return_value=True),
              _link_hint=TelegramHandler._link_hint,
              _timeout_hint=TelegramHandler._timeout_hint)


@pytest.mark.asyncio
async def test_an_etf_card_still_rendering_is_named_as_a_wait(monkeypatch):
    monkeypatch.setattr(wdp, "fetch_web_card",
                        lambda name, *a, **k: {"reply_html": None, "timed_out": 15.0})
    h = _etf_host()
    await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
    sent = h._send.await_args.args[1]
    assert sent == TelegramHandler._timeout_hint("telegram", 15.0)
    assert "15 s" in sent and "/link" not in sent
    assert h._send_photo.await_count == 0


@pytest.mark.asyncio
async def test_an_etf_channel_that_did_not_answer_still_names_the_channel(monkeypatch):
    monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: None)
    h = _etf_host()
    await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
    assert h._send.await_args.args[1] == TelegramHandler._link_hint("telegram")

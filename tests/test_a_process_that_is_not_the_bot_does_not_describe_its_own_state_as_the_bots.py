"""A chat turn in a process that is not the bot does not describe that process as the bot.

`api_bridge.py` builds its own `RuneClawEngine`, detaches it
(`detach_state_persistence`) and answers `/chat` through the one chat brain.
That engine runs no tick, starts no price feed and queues no idea, and the
prompt it built said "PENDING TRADE IDEAS: none queued by the bot right now.
The bot is not about to place anything on its own." It also stated the
breaker and the entry gate from its own copy, and called its unstarted feed
"not returning fresh ticks". Each of those is this process's state, read as
the bot's. On a reader engine each section now says it was not read from the
bot, and the bot's own prompt is unchanged.
"""
from __future__ import annotations

import pytest

from bot.skills import telegram_handler as th


@pytest.fixture
def prompts():
    from bot.core.engine import RuneClawEngine
    from bot.nlp.chat_facade import headless_handler

    def build(detached: bool) -> str:
        engine = RuneClawEngine()
        if detached:
            engine.detach_state_persistence()   # what api_bridge's lifespan does
        handler = headless_handler(engine)
        # `ask` appends the question before the model runs; the engine-state
        # line is part of the context block that turn opens.
        handler.conversations.append("12345", "user", "can I trade right now?")
        return handler._build_chat_system_prompt("12345", surface="api")

    return build


def test_the_readers_empty_queue_is_not_the_bots(prompts):
    reader, bot = prompts(True), prompts(False)
    assert th.NOT_THE_BOT_IDEAS in reader
    assert "none queued by the bot right now" not in reader
    assert "none queued by the bot right now" in bot
    assert th.NOT_THE_BOT_IDEAS not in bot


def test_the_readers_breaker_and_gate_are_not_the_bots(prompts):
    reader, bot = prompts(True), prompts(False)
    assert th.NOT_THE_BOT_GATE in reader
    assert "CB=" not in reader
    assert "an arm made in the bot at runtime is not visible here" in reader
    assert "CB=" in bot and th.NOT_THE_BOT_GATE not in bot


def test_the_readers_unstarted_feed_is_not_the_bots_feed_down(prompts):
    reader, bot = prompts(True), prompts(False)
    assert th.NOT_THE_BOT_FEED in reader
    assert "not returning fresh ticks" not in reader
    assert "not returning fresh ticks" in bot
    assert th.NOT_THE_BOT_FEED not in bot


def test_the_readers_book_is_named_as_a_copy(prompts):
    reader, bot = prompts(True), prompts(False)
    assert th.NOT_THE_BOT_BOOK in reader
    assert th.NOT_THE_BOT_BOOK not in bot


def test_a_stand_in_engine_is_not_read_as_a_reader():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    assert th._not_the_bot(MagicMock()) is False
    assert th._not_the_bot(SimpleNamespace()) is False
    assert th._not_the_bot(SimpleNamespace(_state_persistence_detached=True)) is True


def test_a_price_a_reader_did_read_is_still_printed():
    # The reader's sentence is for a feed it never ran, not a filter on prices:
    # a tick it holds is a measurement of the market wherever it was read.
    from types import SimpleNamespace
    tick = SimpleNamespace(last=61234.5, change_pct_24h=0.0)
    h = SimpleNamespace(
        engine=SimpleNamespace(_state_persistence_detached=True,
                               ws_feed=SimpleNamespace(get_snapshot=lambda **_k: {"BTC/USDT": tick})),
        CHAT_TICKER_MAX_AGE_SEC=90, CHAT_TICKER_LEAD=("BTC/USDT",), CHAT_TICKER_MAX=8)
    block = th.TelegramHandler._live_ticker_block(h)
    assert "BTC/USDT  $61,234.50" in block
    assert th.NOT_THE_BOT_FEED not in block

""""close my ETH" reached a model with no tool, no door and no rule.

`intent_router` had no rule for close / exit / flatten / sell-my-position
(only `halt` was a routed dangerous intent), so the message fell through to
the chat model on both surfaces. The model holds read-only tools; the LIVE
`_CHAT_SYSTEM_PROMPT` carried no statement that it cannot place, modify or
close trades (the PUBLIC prompt has always said so); and the only fabrication
guard, `bot/nlp/fabricated_tool_calls.py`, strips the `[skill] result:` block
shape — a prose "Done, I've closed your ETH position" is not that shape and
passed untouched. No test pinned that a reply never narrates an action.

THE DOORS, read before this was designed: the `Close` button is on the
per-position detail card (`pos_close_<trade_id>:<uid>`, owner-checked,
closing on an explicit tap); `/liveclose` is `@guard("admin")`, takes a TRADE
ID and closes with NO confirmation — the wrong door for free text; the web
gateway has no live close route at all. So `close_position` is a ROUTED
intent, like `help` and `status`, not a registered skill: Telegram answers
with the positions card and a sentence naming the button, the web names the
Telegram door, and neither ever dispatches a close.

THE RED HERRINGS, planted below: advice about closing ("should I close my
BTC?") must still reach the model — it is a question, not a request; a
question about CLOSED trades must still reach the journal; and the notice
must say nothing was closed, because a routed request answered with a list
reads as "it did not understand me".
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from bot.nlp.intent_router import IntentRouter, symbol_mentioned
from bot.skills import telegram_handler as th
from bot.skills.chat_runtime import _CHAT_CANNOT_ACT_RULE, close_intent_notice
from bot.skills.skill_permissions import permission_for
from bot.skills.skill_registry import build_default_registry
from tests.source_scan import code_only
from tests.test_free_text_obeys_the_role_gate import OPERATOR, TRADER, _handler, _update

CLOSE = [
    "close my ETH position", "close ETH", "Close my btc trade now", "exit my BTC long",
    "flatten everything", "get out of SOL", "sell my ETH position",
    "please close my btc trade", "I want to close my position", "close it",
    "can you close my SOL short", "unwind the ETH trade",
]
NOT_CLOSE = [
    "should I close my BTC?", "how do I close a trade?", "is it time to exit ETH?",
    "show my closed trades", "what's my open position", "my positions",
    "when should I close a scalp", "close price of BTC yesterday",
]


# ── the router ─────────────────────────────────────────────────────────────

class TestTheRouter:
    @pytest.mark.parametrize("text", CLOSE)
    def test_an_imperative_close_routes_to_the_intent(self, text):
        r = IntentRouter().classify_rules(text)
        assert r.matched and r.skill == "close_position" and not r.is_social, (text, r)

    @pytest.mark.parametrize("text", NOT_CLOSE)
    def test_advice_and_history_do_not(self, text):
        """RED HERRING. A question about closing is the model's to answer; a
        question about CLOSED trades is the journal's."""
        r = IntentRouter().classify_rules(text)
        assert r.skill != "close_position", (text, r)

    def test_a_short_close_is_not_swallowed_by_the_social_gate(self):
        r = IntentRouter().classify_rules("close it")
        assert not r.is_social and r.skill == "close_position"

    def test_the_symbol_is_read_when_named(self):
        assert symbol_mentioned("close my ETH position") == "ETH/USDT"
        assert symbol_mentioned("close my position") is None

    def test_it_is_a_routed_intent_not_a_skill(self):
        """Documented on purpose: nothing may ever `dispatch("close_position")`.
        Unregistered and unmapped, so every generic path fails closed."""
        assert build_default_registry().get("close_position") is None
        assert permission_for("close_position") is None


# ── Telegram: the positions card, never a close ────────────────────────────

@pytest.fixture
def bot(tmp_path):
    h = _handler(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
    h._cmd_open_positions = AsyncMock()
    h._cmd_liveclose = AsyncMock()
    h.engine.live_executor = NS(close_position=AsyncMock(), open_positions=[])
    yield h
    patch.stopall()


class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", CLOSE)
    async def test_a_close_request_meets_the_card_and_closes_nothing(self, bot, text):
        await bot._handle_message(_update(TRADER, text), None)
        assert bot._cmd_open_positions.await_count == 1, text
        bot.engine.live_executor.close_position.assert_not_awaited()
        bot._cmd_liveclose.assert_not_awaited()
        assert bot.registry.executed == [] and bot.registry.dispatched == []
        notice = "\n".join(bot.sent)
        assert "tap <b>Close</b>" in notice and "Nothing has been closed" in notice

    @pytest.mark.asyncio
    async def test_the_symbol_is_named_on_the_card(self, bot):
        await bot._handle_message(_update(TRADER, "close my ETH position"), None)
        assert "ETH/USDT" in "\n".join(bot.sent)

    @pytest.mark.asyncio
    async def test_an_operator_is_routed_the_same_way(self, bot):
        await bot._handle_message(_update(OPERATOR, "close ETH"), None)
        assert bot._cmd_open_positions.await_count == 1
        bot.engine.live_executor.close_position.assert_not_awaited()


# ── the web: the door, never the model ─────────────────────────────────────

def test_the_web_answers_with_the_door_before_any_alias_or_skill():
    from pathlib import Path
    src = code_only(Path("bot/web/user_gateway.py").read_text())
    # The branch became a membership test when cancel and modify joined it
    # (`ACT_INTENTS`, chat_runtime); close is still the first name in it.
    from bot.skills.chat_runtime import ACT_INTENTS
    assert "close_position" in ACT_INTENTS
    branch = src.index("intent.skill in ACT_INTENTS")
    assert branch < src.index("_INTENT_ALIASES = {")


class TestTheNotice:
    @pytest.mark.parametrize("surface", ["telegram", "web"])
    @pytest.mark.parametrize("symbol", [None, "ETH/USDT"])
    def test_it_names_the_door_and_claims_no_action(self, surface, symbol):
        out = close_intent_notice(symbol, surface=surface)
        assert "tap <b>Close</b>" in out
        assert "Nothing has been closed" in out
        assert "I closed" not in out and "I've closed" not in out
        if symbol:
            assert symbol in out

    def test_the_web_notice_names_where_the_door_is(self):
        out = close_intent_notice(None, surface="web")
        assert "Telegram" in out and "/liveclose TRADE_ID" in out


# ── the prompt: the boundary the live surface never stated ─────────────────

def test_the_live_prompt_states_it_cannot_act():
    assert _CHAT_CANNOT_ACT_RULE in th.TelegramHandler._CHAT_SYSTEM_PROMPT
    assert "cannot place, modify, cancel or close trades or orders" in _CHAT_CANNOT_ACT_RULE
    assert "unless a tool result in THIS turn says so" in _CHAT_CANNOT_ACT_RULE


def test_the_rule_names_a_door_for_opening_as_well_as_closing():
    """The first draft named only the close door, so "buy ETH" would have been
    sent to the positions card's Close button. Both doors, and never the
    admin-only one."""
    assert "/trade" in _CHAT_CANNOT_ACT_RULE and "Confirm" in _CHAT_CANNOT_ACT_RULE
    assert "Trade this" in _CHAT_CANNOT_ACT_RULE
    assert "tap Close" in _CHAT_CANNOT_ACT_RULE
    assert "/liveclose" not in _CHAT_CANNOT_ACT_RULE


@pytest.mark.asyncio
async def test_the_legacy_sell_command_names_the_same_door(bot):
    """/sell is guarded "trade" — a trader's command — and told the trader to
    run /liveclose, which is admin-only. Same door as the notice now."""
    await bot._cmd_sell(_update(TRADER, "/sell"), None)
    text = "\n".join(bot.sent)
    assert "/liveclose" not in text
    assert "/positions" in text and "<b>Close</b>" in text


def test_the_public_prompt_keeps_its_own_boundary():
    assert "canNOT place, propose, size or modify any trade" in th.TelegramHandler._PUBLIC_CHAT_SYSTEM_PROMPT

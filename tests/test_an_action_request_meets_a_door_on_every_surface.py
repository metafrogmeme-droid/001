"""Seven realistic messages got a confident wrong card, and four of them were
requests to ACT.

Run the router over what a trader actually types and read the table:

  "set stop loss at 2900"        -> get_portfolio   ("loss" hit the bare
  "change my take profit"        -> get_portfolio    Portfolio-keyword rule)
  "cancel my order"              -> get_orders      (a card, no sentence)
  "why was my trade rejected"    -> get_portfolio   (the generic "my trades?"
  "why did my trade close"       -> get_portfolio    rule precedes `whynot`)
  "what's the status of my ETH trade" -> status     (the ENGINE card, off the
  "order status"                 -> status           bare word "status")

The first three are the "close my ETH" shape one action over: a request to
act answered with a read card and no sentence, so the reader either takes
"it did not understand me" or — worse — reads the card as confirmation that
the stop was set. The next two are rule ORDER: the `whynot` rule's own
comment says it "MUST be registered before the broader no-trade rule" and
nobody noticed the portfolio rule fifty lines above it. The last two are the
bare `status|dashboard` alternative firing on any sentence containing the
word.

THE DOORS, read before this was designed: a pending order's Cancel button is
on the positions card (`_cmd_open_positions`, `pos_close_<trade_id>`, which
`close_position` turns into `cancel_order` for a `pending_fill`); the web's
`/api/trade/cancel` withdraws an UNCONFIRMED manual proposal, not a resting
order; and NOTHING modifies a stop or target on an open position — no
command, no button, no executor method — so the honest notice says so rather
than naming a door that does not exist.

THE RED HERRINGS: advice and questions about stops, orders and rejections
still reach the model ("where should I set my stop?", "did my order get
cancelled?", "why is my pnl negative" keeps its portfolio card); bare
"status" and "what is the bot status?" keep the engine card.

THE FOURTH KIND, found while building the idle-yield door: "stake my usdc"
reached no rule — three words, no trading word, GREETED — and "stake my eth"
reached a model that holds no staking tool. The website's idle-yield
intercept reads "stake my …" as a yield question; here /stake and /unstake
move the OPERATOR's funds behind a Confirm card and are admin-only. So a
staking request is a routed action: the notice says whose door it is and
that nothing moved, the operator's own plan card follows for an admin
(it moves nothing until Confirm), and nothing follows for anyone else —
never the positions card, which is not this request's door.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from bot.nlp.intent_router import IntentRouter, symbol_mentioned
from bot.nlp.skill_memory import routed_answer_memory
from bot.skills.chat_runtime import (
    _CHAT_CANNOT_ACT_RULE,
    ACT_INTENTS,
    act_intent_notice,
    close_intent_notice,
    stake_verb,
)
from bot.skills.skill_permissions import permission_for
from bot.skills.skill_registry import build_default_registry
from tests.source_scan import code_only
from tests.test_free_text_obeys_the_role_gate import OPERATOR, TRADER, _handler, _update
from tests.test_the_web_intercept_phrasings_reach_the_same_read_on_telegram import _assistant, _turn, _web

MODIFY = [
    "set stop loss at 2900", "change my take profit", "move my stop to 3000",
    "set my ETH stop at 2900", "tighten the stop on BTC", "update my tp to 3500",
    "please move my SL to breakeven", "raise the target on SOL", "trail my stop",
]
CANCEL = [
    "cancel my order", "cancel the ETH limit", "cancel my BTC order", "pull the limit order",
    "cancel all my orders", "remove the pending order", "can you cancel my SOL limit",
    "cancel it",
]
STAKE = [
    "stake my usdc", "stake my eth", "stake 500 usdt", "unstake my usdc", "redeem my earn",
    "restake my eth on eigenlayer", "can you stake my stables", "stake it all",
    "lock my usdt for 30 days", "stake my usdc please", "please unstake everything",
]
# A question about staking, a bare word, and the website's own idle-yield
# phrasing keep their destinations: none is a request to move funds.
STAKE_NOT = {
    "should i stake eth": None, "what is staking": None, "how do i unstake": None,
    "stake": None, "staking options": None, "stake my claim": None,
    "put my idle cash to work": "idle_yield",
}
# Questions and advice keep their old destination — a question about a stop is
# not a request to move one.
STILL_MODEL_OR_READ = {
    "where should I set my stop?": None,
    "what's my stop on ETH?": None,
    "how does the trailing stop work": None,
    # questions about orders keep the orders card they always had — what
    # matters here is that neither is an ACTION
    "did my order get cancelled?": "get_orders",
    "should I cancel my order?": "get_orders",
    "why did my trade close": None,          # no rule; the model has the close reason
    "why is my pnl negative": "get_portfolio",
    # "status" mid-sentence about something that is NOT the engine: only the
    # anchoring keeps these off the engine card (the book rules above do not
    # claim them), so they are what a reverted anchor fails on
    "what's the status of the ETH market": None,
    "give me a status update on BTC": None,
    "any status on the SOL setup?": None,
    "is the status quo bullish": None,
    "my positions": "get_portfolio",
    "my orders": "get_orders",
}
RE_ROUTED = {
    # `(reject|skip|filter)\b` was the bare verb only, so the sentence the
    # whynot rule's own comment is about never reached it
    "why was my trade rejected": "whynot",
    "why was my trade rejected?": "whynot",
    "why did it skip ETH": "whynot",
    "why did the bot skip my trade": "whynot",
    "what's the status of my ETH trade": "get_portfolio",
    "status of my position": "get_portfolio",
    "order status": "get_orders",
    "status of my orders": "get_orders",
    # unchanged, pinned so the anchored form did not lose them
    "status": "status",
    "dashboard": "status",
    "what's the status": "status",
    "what is the bot status?": "status",
    "show me the dashboard": "status",
    "is the bot running": "status",
}


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


# ── the router ─────────────────────────────────────────────────────────────

class TestTheRouter:
    @pytest.mark.parametrize("text", MODIFY)
    def test_a_stop_or_target_change_is_a_routed_action(self, router, text):
        i = router.classify_rules(text)
        assert i.skill == "modify_position" and i.confidence >= 0.8, (text, i.skill)

    @pytest.mark.parametrize("text", CANCEL)
    def test_a_cancel_is_a_routed_action(self, router, text):
        i = router.classify_rules(text)
        assert i.skill == "cancel_order" and i.confidence >= 0.8, (text, i.skill)

    @pytest.mark.parametrize("text,skill", list(STILL_MODEL_OR_READ.items()))
    def test_questions_and_advice_keep_their_destination(self, router, text, skill):
        i = router.classify_rules(text)
        assert i.skill not in ACT_INTENTS, (text, i.skill)
        if skill is None:
            assert not i.skill, (text, i.skill)
        else:
            assert i.skill == skill, (text, i.skill)

    @pytest.mark.parametrize("text,skill", list(RE_ROUTED.items()))
    def test_the_wrong_cards_are_re_routed(self, router, text, skill):
        i = router.classify_rules(text)
        assert i.skill == skill and i.confidence >= 0.8, (text, i.skill)

    def test_the_symbol_travels(self):
        assert symbol_mentioned("cancel the ETH limit") == "ETH/USDT"
        assert symbol_mentioned("set my ETH stop at 2900") == "ETH/USDT"

    def test_the_action_intents_are_routed_not_skills(self):
        reg = build_default_registry()
        for name in ACT_INTENTS:
            assert reg.get(name) is None, name
            assert permission_for(name) is None, name

    @pytest.mark.parametrize("text", STAKE)
    def test_a_stake_or_redeem_request_is_a_routed_action(self, router, text):
        i = router.classify_rules(text)
        assert i.skill == "stake_request" and i.confidence == 1.0, (text, i.skill)

    @pytest.mark.parametrize("text,skill", list(STAKE_NOT.items()), ids=list(STAKE_NOT))
    def test_a_staking_question_or_a_bare_word_is_not(self, router, text, skill):
        i = router.classify_rules(text)
        got = i.skill if i.confidence >= 0.8 else None
        assert got != "stake_request", (text, got)
        assert got == skill, (text, got)

    def test_the_verb_is_read_from_the_words(self):
        assert stake_verb("stake my usdc") == "stake"
        assert stake_verb("unstake my usdc") == "unstake"
        assert stake_verb("redeem my earn") == "unstake"
        assert stake_verb("restake my eth") == "stake"


# ── Telegram: the positions card, never an action ──────────────────────────

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
    h._cmd_orders = AsyncMock()
    h._cmd_stake = AsyncMock()
    h._cmd_unstake = AsyncMock()
    h.engine.live_executor = NS(close_position=AsyncMock(), cancel_order=AsyncMock(),
                                open_positions=[])
    yield h
    patch.stopall()


class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", CANCEL)
    async def test_a_cancel_meets_the_card_and_cancels_nothing(self, bot, text):
        await bot._handle_message(_update(TRADER, text), None)
        assert bot._cmd_open_positions.await_count == 1, text
        bot._cmd_orders.assert_not_awaited()
        bot.engine.live_executor.close_position.assert_not_awaited()
        bot.engine.live_executor.cancel_order.assert_not_awaited()
        assert bot.registry.executed == [] and bot.registry.dispatched == []
        notice = "\n".join(bot.sent)
        assert "<b>Cancel</b>" in notice and "Nothing has been cancelled" in notice

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", MODIFY)
    async def test_a_stop_change_meets_the_card_and_changes_nothing(self, bot, text):
        await bot._handle_message(_update(TRADER, text), None)
        assert bot._cmd_open_positions.await_count == 1, text
        assert bot.registry.executed == [] and bot.registry.dispatched == []
        notice = "\n".join(bot.sent)
        assert "no command" in notice and "Nothing has been changed" in notice
        # The one door that must NOT be named: it does not exist.
        assert "/setstop" not in notice and "/sl" not in notice

    @pytest.mark.asyncio
    async def test_the_symbol_is_named(self, bot):
        await bot._handle_message(_update(TRADER, "cancel the ETH limit"), None)
        assert "ETH/USDT" in "\n".join(bot.sent)

    @pytest.mark.asyncio
    async def test_an_operator_is_routed_the_same_way(self, bot):
        await bot._handle_message(_update(OPERATOR, "cancel my order"), None)
        assert bot._cmd_open_positions.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["stake my usdc", "unstake my usdc"])
    async def test_a_traders_stake_request_meets_the_notice_and_no_card(self, bot, text):
        # The door is the operator's; a trader is told so, and no card follows
        # — not the positions card (the wrong door) and not a plan card that
        # would only refuse them under a notice that already said so.
        await bot._handle_message(_update(TRADER, text), None)
        bot._cmd_stake.assert_not_awaited()
        bot._cmd_unstake.assert_not_awaited()
        bot._cmd_open_positions.assert_not_awaited()
        assert bot.registry.executed == [] and bot.registry.dispatched == []
        assert bot.sent[-1] == act_intent_notice("stake", None, "telegram", verb=stake_verb(text))
        assert "Nothing has been staked or redeemed" in bot.sent[-1]
        assert "put my idle cash to work" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_the_operators_stake_request_gets_their_own_plan_card(self, bot):
        await bot._handle_message(_update(OPERATOR, "stake my usdc"), None)
        assert bot._cmd_stake.await_count == 1
        bot._cmd_unstake.assert_not_awaited()
        bot._cmd_open_positions.assert_not_awaited()
        assert "/stake" in bot.sent[-1] and "Confirm" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_the_operators_redeem_request_gets_the_unstake_card(self, bot):
        await bot._handle_message(_update(OPERATOR, "redeem my earn"), None)
        assert bot._cmd_unstake.await_count == 1
        bot._cmd_stake.assert_not_awaited()
        assert "/unstake" in bot.sent[-1]


# ── the web: the door, never the model ─────────────────────────────────────

def test_the_web_answers_every_action_intent_before_any_alias_or_skill():
    from pathlib import Path
    src = code_only(Path("bot/web/user_gateway.py").read_text())
    branch = src.index("intent.skill in ACT_INTENTS")
    # NOT `"_INTENT_ALIASES = {"`. That map is one derivation from
    # `skill_doors` now — it was three copies answering three ways — so the
    # literal is gone and only the assignment is left. The claim here was
    # always about ORDER, and the assignment is what it orders against.
    assert branch < src.index("_INTENT_ALIASES =")
    assert "act_intent_notice(" in src


@pytest.mark.parametrize("text", ["stake my usdc", "unstake my usdc"])
def test_the_web_answers_a_stake_request_with_the_notice_and_records(monkeypatch, text):
    ug, h = _web(monkeypatch)
    resp, body = _turn(ug, h, text)
    assert resp.status == 200
    assert body["intent"] == "stake_request"
    assert body["reply_html"] == act_intent_notice("stake", None, "web", verb=stake_verb(text))
    assert _assistant(h) == routed_answer_memory("stake_request", body["reply_html"])


# ── the notice ─────────────────────────────────────────────────────────────

class TestTheNotice:
    def test_close_still_reads_the_same(self):
        assert close_intent_notice("ETH/USDT") == act_intent_notice("close", "ETH/USDT")
        assert close_intent_notice(None, "web") == act_intent_notice("close", None, "web")

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_cancel_names_the_button_and_claims_no_action(self, surface):
        n = act_intent_notice("cancel", "ETH/USDT", surface)
        assert "<b>Cancel</b>" in n and "Nothing has been cancelled" in n
        assert "ETH/USDT" in n

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_modify_says_no_door_exists_and_claims_no_action(self, surface):
        n = act_intent_notice("modify", None, surface)
        assert "no command" in n and "Nothing has been changed" in n

    def test_the_web_notice_names_where_the_door_is(self):
        assert "Telegram" in act_intent_notice("cancel", None, "web")

    def test_an_unknown_kind_is_refused_not_narrated(self):
        with pytest.raises(KeyError):
            act_intent_notice("liquidate_everything")

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    @pytest.mark.parametrize("verb", ["stake", "unstake"])
    def test_stake_names_the_operators_door_and_claims_nothing_moved(self, surface, verb):
        n = act_intent_notice("stake", None, surface, verb=verb)
        assert f"<code>/{verb}</code>" in n and "admin-only" in n and "Confirm" in n
        assert "Nothing has been staked or redeemed" in n
        # The caller's OWN idle assets have a read, and it is not a move.
        assert "put my idle cash to work" in n and "never a move" in n
        assert ("Telegram" in n) == (surface == "web")
        assert "positions card" not in n
        assert act_intent_notice("stake", None, surface, verb=verb, also_asked=True).endswith("I will.")


# ── the prompt ─────────────────────────────────────────────────────────────

def test_the_rule_covers_cancel_and_says_a_stop_cannot_be_changed():
    assert "cancel" in _CHAT_CANNOT_ACT_RULE
    assert "cannot be changed" in _CHAT_CANNOT_ACT_RULE
    # The claims, not substrings the rest of the sentence carries anyway: a
    # mutation that dropped the rule's opening verbs survived a pin on
    # "stake" because "/stake" further along still matched it.
    assert "You cannot stake, unstake or move funds into or out of Earn" in _CHAT_CANNOT_ACT_RULE
    assert "/stake and /unstake in Telegram are the operator's, admin-only" in _CHAT_CANNOT_ACT_RULE
    assert "moves nothing until Confirm is tapped" in _CHAT_CANNOT_ACT_RULE
    assert "Never say funds were staked or redeemed" in _CHAT_CANNOT_ACT_RULE

"""A request to OPEN a trade meets a door, on both surfaces.

Driven over 54 ordinary phrasings BEFORE this rule existed, six came back
with a CONFIDENT WRONG CARD: "place a limit order on pendle", "place a limit
order" and "put in a limit order for btc" reached `get_orders` — the card
that LISTS resting orders, answering a request to PLACE one — and "open a
position in sol", "add to my eth position" and "double my eth position"
reached `get_portfolio`. A bare "long"/"short"/"buy"/"sell" was GREETED: one
word, no symbol, no trading word, so the social gate answered a request to
move money with "hey!". Everything else reached a chat model that holds
read-only tools and would narrate a placement.

THREE SOURCE COMMENTS ALREADY NAMED THE RULE. `manual_trade.py` twice and
`telegram_handler.py` once said that what the full grammar declines "is the
router's `place_order` rule's, which answers with this grammar as the door".
No such rule existed — the `/vault` hint shape inside a code comment.

The corpus is the test: a table of phrases a trader really types, each with
the destination it must reach, because the rule that answers is decided by
ORDER and order is invisible from any one rule.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.nlp.intent_router import (
    PLACE_ORDER,
    IntentRouter,
    _is_social_message,
    place_target,
)
from bot.skills.chat_runtime import (
    _TRADE_GRAMMAR,
    ACT_INTENTS,
    ACT_KIND,
    act_intent_notice,
)
from bot.skills.manual_trade import looks_like_manual_trade, parse_manual_trade

ROUTER = IntentRouter()

#: Every ordinary way of asking to open a trade. Each one reached the orders
#: card, the positions card, the greeter or the model before this rule.
OPENS = [
    # the filed cases: a PLACE request answered with the LISTING
    "place a limit order on pendle", "place a limit order",
    "put in a limit order for btc", "Place limit pendle", "place an order",
    "place a buy order on eth", "place a stop order on btc",
    # the filed cases: a request to OPEN answered with the positions card
    "open a position in sol", "add to my eth position", "double my eth position",
    # directional, every wrapping
    "open a long on eth", "open a short on btc", "enter a long on eth",
    "go long eth", "go short btc", "take a long on sol", "put on a long eth",
    "i want to long eth", "let's short btc", "open a 10x long on eth",
    "open a trade on sol",
    # bare directional with an asset — the web's old private regex
    "buy eth", "sell btc", "short btc", "long eth", "long btc please",
    # size, price and kind
    "buy 100 of eth", "buy me 0.5 eth", "can you buy me some eth",
    "market buy eth", "limit buy eth at 3000", "set a limit buy for eth at 3000",
    "buy the dip on eth",
    # into / grow
    "get me into eth", "scale into btc", "size into eth",
    # symbol first
    "eth long", "btc short",
    # the bare verb, nothing named
    "long", "short", "buy", "sell",
]

#: Phrases another rule must keep. A place rule that steals any of these is
#: the `get_orders` defect in a new place.
KEEPS = {
    "close my long": "close_position",
    "close my eth": "close_position",
    "sell my eth position": "close_position",
    "cancel my order": "cancel_order",
    "set stop loss at 2900": "modify_position",
    "stake my usdc": "stake_request",
    "halt the bot": "halt",
    "should i long eth": "analyze_asset",
    "analyze eth": "analyze_asset",
    "why did you long eth": "trade_postmortem",
    "open positions": "get_portfolio",
    "my positions": "get_portfolio",
    "open orders": "get_orders",
    "show open orders": "get_orders",
    "scan the market": "scan_market",
    "4h scan": "scan_swing",
    # a compound keeps the close door, which answers both halves' question
    "buy eth and close my btc": "close_position",
    "close all positions and halt": "emergency_stop",
}

#: Questions, opinions and REPORTS. Each one is the model's — a request to
#: act is the WHOLE message or it is not one.
DECOYS = [
    "is it a good time to buy eth", "what is a limit order",
    "how do i place a trade", "did you buy eth", "do you think eth is a buy",
    "are we long or short", "show me my longs", "eth is a buy here",
    "the bot went long eth", "should i buy eth?", "is eth a buy?",
    "how do limit orders work", "long term view", "short term trend",
    # ordinary English that shares the rule's verbs
    "open the dashboard", "get my balance", "get the chart", "take profit",
    "open my portfolio", "get me the news", "take a look at eth",
    "open the app", "enter the market", "i sell my car", "double check that",
    "get in touch", "buy in bulk",
    # THE OBJECT NOUNS AND THE ASSET GUARD decide exactly these. Without
    # them each is a request to open a trade in an asset called `balance`,
    # `dashboard`, `wallet` or `account`; the first draft's decoys were all
    # rejected by the verb/preposition structure instead, so the mutation
    # that inverted the guard changed no verdict in the whole corpus.
    "balance long", "position long", "dashboard long", "wallet long",
    "portfolio long", "the long", "market long", "chart long",
    "buy into the dashboard", "scale into balance", "get me into margin",
    "add to my wallet position", "double my account trade",
    # ...and these are what the FIRST line of that addition decides — ordinary
    # English idioms whose second word would otherwise be read as the asset.
    # Measured: dropping that line alone changes 76 verdicts, dropping the
    # nouns changes 208, and the first draft's decoys only ever exercised the
    # second — which is why the mutation on the first line survived a round.
    "buy back", "buy out", "buy up", "sell off", "long down",
]

#: A CASUAL LEAD is informality, and informality goes to the door — the same
#: reading the halt slice made, with no dispatch behind it here. Each of
#: these was GREETED: `_SOCIAL_CHAT`'s `^bro`, the three-word rule, and
#: `_THANKS_PATTERNS` (an unanchored search) which ate a trailing thanks
#: after a COMMA because the tail required whitespace.
CASUAL_OPENS = ["bro buy eth", "lol long eth", "thanks, buy eth",
                "buy eth, thanks", "ok buy eth", "bro long", "yo short btc",
                "mate, buy eth now"]

#: ...and the same words with no request behind them stay small talk.
STILL_SOCIAL = ["thanks", "thanks bro", "lol ok", "hey", "hi there", "ty",
                "cheers", "thanks for the scan", "thanks mate"]


class TestEveryWayOfAskingReachesTheDoor:
    @pytest.mark.parametrize("text", OPENS)
    def test_it_routes_to_place_order(self, text):
        res = ROUTER.classify_rules(text)
        assert res.skill == "place_order", f"{text!r} -> {res.skill!r}"
        assert res.confidence >= 0.8, text

    @pytest.mark.parametrize("text", CASUAL_OPENS)
    def test_a_casual_lead_still_reaches_the_door(self, text):
        assert not _is_social_message(text), text
        assert ROUTER.classify_rules(text).skill == "place_order", text

    @pytest.mark.parametrize("text", STILL_SOCIAL)
    def test_the_same_words_with_no_request_stay_small_talk(self, text):
        assert _is_social_message(text), text
        assert ROUTER.classify_rules(text).skill != "place_order", text

    @pytest.mark.parametrize("text", ["long", "short", "buy", "sell"])
    def test_a_bare_verb_is_not_small_talk(self, text):
        """One word, no symbol, no trading word — greeted before this rule.
        The social gate consults the anchored action rules first, so the fix
        is the rule being anchored rather than a word added to a list."""
        assert not _is_social_message(text)
        assert ROUTER.classify_rules(text).skill == "place_order"

    @pytest.mark.parametrize("text,want", sorted(KEEPS.items()))
    def test_another_rules_words_stay_its_own(self, text, want):
        assert ROUTER.classify_rules(text).skill == want, text

    @pytest.mark.parametrize("text", DECOYS)
    def test_a_question_or_a_report_is_not_a_request(self, text):
        assert ROUTER.classify_rules(text).skill != "place_order", text

    def test_the_full_grammar_is_still_the_grammar(self):
        """Both handlers consult `looks_like_manual_trade` BEFORE the router,
        so a line with levels proposes rather than meeting a door."""
        assert looks_like_manual_trade("buy SOL 71 sl 70 tp 76") is not None


class TestTheDoorItNamesIsOneThatWorks:
    def test_the_grammar_the_notice_prints_parses(self):
        """The `/trade` help's own example was one the parser REJECTS. A card
        that names a format is claiming typing it works, so it is DRIVEN."""
        body = looks_like_manual_trade(_TRADE_GRAMMAR)
        assert body is not None, _TRADE_GRAMMAR
        parsed = parse_manual_trade(body)
        assert isinstance(parsed, tuple), parsed

    def test_the_prompt_rule_and_the_notice_name_one_grammar(self):
        """A second copy of a door is a second answer the day one moves."""
        from bot.skills.chat_runtime import _CHAT_CANNOT_ACT_RULE
        assert _TRADE_GRAMMAR in _CHAT_CANNOT_ACT_RULE
        assert _TRADE_GRAMMAR in act_intent_notice("place", None, "telegram")
        assert _TRADE_GRAMMAR in act_intent_notice("place", None, "web")

    def test_it_is_the_fifth_act_intent(self):
        assert "place_order" in ACT_INTENTS
        assert ACT_KIND["place_order"] == "place"

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_every_notice_says_nothing_was_placed(self, surface):
        for follows in (True, False):
            out = act_intent_notice("place", "ETH/USDT", surface,
                                    setup_follows=follows)
            assert "Nothing has been placed." in out

    def test_a_web_caller_is_never_told_a_slash_command(self):
        """`/trade` is a door painted on a wall for a web caller.

        The markup is stripped FIRST: `</code>` and `</b>` both match a
        naive slash-command pattern, so the first draft of this assertion
        accused a notice that was telling the truth."""
        out = act_intent_notice("place", "ETH/USDT", "web")
        visible = re.sub(r"</?[a-z]+>", " ", out)
        assert not re.search(r"(?<!\w)/[a-z]{2,}(?:_[a-z]+)*", visible), visible

    def test_the_card_sentence_is_written_only_when_the_card_came(self):
        """A notice promising a card that failed is the `/vault` hint shape
        one turn long."""
        assert "is below" in act_intent_notice("place", "ETH/USDT", "telegram",
                                               setup_follows=True)
        assert "is below" not in act_intent_notice("place", "ETH/USDT",
                                                   "telegram")


class TestTheTargetIsOneReadingForBothSurfaces:
    def test_one_asset_named_is_the_target(self):
        assert place_target("buy eth") == "ETH/USDT"
        assert place_target("open a position in sol") == "SOL/USDT"

    def test_two_assets_named_is_not_one_asset_asked_about(self):
        """Taking the first would answer half the message with a card."""
        assert place_target("buy eth and btc") is None

    def test_no_asset_named_answers_none(self):
        assert place_target("long") is None
        assert place_target("place a limit order") is None

    def test_an_unresolvable_name_answers_none(self):
        """PENDLE is not one of the 49 known symbols and was filled live.
        The DOOR still shows; no setup is named for an asset nobody read."""
        assert place_target("place a limit order on pendle") is None
        assert ROUTER.classify_rules(
            "place a limit order on pendle").skill == "place_order"

    def test_the_web_has_no_private_copy_of_the_reading(self):
        """It had one — `^(?:paper\\s+)?(?:long|short|buy|sell)\\s+...$` — so
        three phrasings got a setup on the web and a tool-less model on
        Telegram. A second copy of a gate is a second answer."""
        import inspect

        from bot.web import user_gateway as ug
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(ug._chat_turn))
        assert "long|short|buy|sell" not in src
        assert "place_target" in src


class TestTheAssetTheProductTradesCanReachTheDoor:
    """`_KNOWN_SYMBOLS` holds 49 names; the bot filled PENDLE, NATGAS, TRUMP
    and RAVE live on 2026-09-15, none of them on it. An object restricted to
    that list would refuse the door to the assets the product trades."""

    @pytest.mark.parametrize("sym", ["pendle", "natgas", "trump", "rave", "wif"])
    def test_a_symbol_outside_the_known_list_still_opens_the_door(self, sym):
        assert PLACE_ORDER.match(f"buy {sym}"), sym
        assert ROUTER.classify_rules(f"go long {sym}").skill == "place_order"


class TestTheSetupReadIsThreeValued:
    """`read`, `failed` and `absent` are different facts and only a read may
    put the card sentence on the door."""

    def _handler(self, skill, denial=None):
        from bot.skills.telegram_handler import TelegramHandler
        h = TelegramHandler.__new__(TelegramHandler)
        h.registry = SimpleNamespace(get=lambda n: skill)
        h.engine = SimpleNamespace(pending_ideas=[])
        h.users = SimpleNamespace(permission_denial=lambda uid, perm: denial)
        return h

    @pytest.mark.asyncio
    async def test_a_read_is_a_read(self):
        h = self._handler(SimpleNamespace(execute=AsyncMock(return_value="card")))
        text, idea, state = await h._place_setup("7", "ETH/USDT")
        assert (text, idea, state) == ("card", None, "read")

    @pytest.mark.asyncio
    async def test_a_raise_is_a_failure_not_an_absence(self):
        boom = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("venue")))
        h = self._handler(boom)
        assert (await h._place_setup("7", "ETH/USDT"))[2] == "failed"

    @pytest.mark.asyncio
    async def test_an_empty_answer_is_a_failure_too(self):
        h = self._handler(SimpleNamespace(execute=AsyncMock(return_value="")))
        assert (await h._place_setup("7", "ETH/USDT"))[2] == "failed"

    @pytest.mark.asyncio
    async def test_no_analyzer_registered_attempted_nothing(self):
        assert (await self._handler(None)._place_setup("7", "ETH/USDT"))[2] == "absent"

    @pytest.mark.asyncio
    async def test_a_denied_role_is_denied_and_the_skill_never_runs(self):
        """This arm calls the skill directly, skipping the role check every
        other free-text route to `analyze_asset` goes through."""
        skill = SimpleNamespace(execute=AsyncMock(return_value="card"))
        h = self._handler(skill, denial="role")
        assert (await h._place_setup("7", "ETH/USDT"))[2] == "denied"
        assert skill.execute.await_count == 0


class TestTheDoorIsNotThePositionsCard:
    def test_the_telegram_arm_does_not_show_positions(self):
        """The positions card is the CLOSE door. Showing it for a request to
        OPEN is the confident wrong card this rule exists to end — the same
        argument the `stake` arm already makes in as many words."""
        import ast
        import inspect
        import textwrap

        from bot.skills.telegram_handler import TelegramHandler
        from tests.source_scan import code_only
        src = code_only(textwrap.dedent(
            inspect.getsource(TelegramHandler._handle_message)))
        tree = ast.parse(src)
        arms = [n for n in ast.walk(tree)
                if isinstance(n, ast.If)
                and "_kind == 'place'" in ast.unparse(n.test)]
        assert len(arms) == 1, "the place arm moved; re-anchor this guard"
        body = ast.unparse(arms[0])
        assert "_cmd_open_positions" not in body
        assert "_send_idea_with_door" in body


# ── Driven, on Telegram ─────────────────────────────────────────────────────
# The four tests above prove `_place_setup`'s states and the notice's words
# separately; these drive the ARM, because the mutation that hands the card to
# a caller with no sentence tying it to the door, and the one that drops the
# failed read from the model's record, both survive every assertion that does
# not run the branch.

from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot  # noqa: E402
from tests.test_free_text_obeys_the_role_gate import OPERATOR, VIEWER, _update  # noqa: E402


@pytest.fixture(name="bot")
def _bot(tmp_path):
    """The halt suite's handler through its own fixture function, so there is
    no second copy of the stub to drift."""
    yield from _halt_bot.__wrapped__(tmp_path)


def _idea(sym="ETH/USDT"):
    from bot.utils.models import Direction, TradeIdea
    return TradeIdea(asset=sym, direction=Direction.LONG, entry_price=3000.0,
                     stop_loss=2900.0, take_profit=3300.0, confidence=0.8,
                     reasoning="x")


@pytest.mark.asyncio
async def test_a_named_asset_gets_the_door_then_the_setup(bot):
    idea = _idea()
    skill = SimpleNamespace(execute=AsyncMock(return_value="SETUP CARD"))
    bot.registry = SimpleNamespace(get=lambda n: skill if n == "analyze_asset" else None)
    bot.engine.pending_ideas = []

    async def _exec(*a, **kw):
        bot.engine.pending_ideas = [idea]
        return "SETUP CARD"

    skill.execute = AsyncMock(side_effect=_exec)
    bot._send_idea_with_door = AsyncMock()
    await bot._handle_message(_update(OPERATOR, "buy eth"), None)

    said = " ".join(str(t) for t in bot.sent)
    assert "Nothing has been placed." in said
    # the sentence that ties the card to the door — only written because the
    # read succeeded
    assert "is below" in said
    assert bot._send_idea_with_door.await_count == 1
    assert bot._send_idea_with_door.await_args.args[2] is idea
    # the skill was asked for the ROUTER's symbol, not a bare token
    assert skill.execute.await_args.kwargs["symbol"] == "ETH/USDT"


@pytest.mark.asyncio
async def test_a_failed_setup_read_leaves_the_door_and_reaches_the_model(bot):
    bot.registry = SimpleNamespace(get=lambda n: SimpleNamespace(
        execute=AsyncMock(side_effect=RuntimeError("venue down"))))
    bot.engine.pending_ideas = []
    bot._send_idea_with_door = AsyncMock()
    recorded = []
    bot._remember_routed = lambda uid, q, skill, ans: recorded.append((skill, ans))
    await bot._handle_message(_update(OPERATOR, "buy eth"), None)

    said = " ".join(str(t) for t in bot.sent)
    assert "Nothing has been placed." in said
    assert "is below" not in said, "a card that failed must not be announced"
    assert bot._send_idea_with_door.await_count == 0
    assert recorded and recorded[-1][0] == "place_order"
    assert "[analyze_asset] FAILED" in recorded[-1][1]


@pytest.mark.asyncio
async def test_a_role_that_may_not_read_still_gets_the_door(bot):
    """THE DOOR IS UNGATED AND THE READ IS NOT. This arm calls the skill
    directly, which skips the role check every other free-text route to
    `analyze_asset` goes through — without it a viewer who typed "buy eth"
    would be handed a read their role forbids at /analyze."""
    skill = SimpleNamespace(execute=AsyncMock(return_value="SETUP CARD"))
    bot.registry = SimpleNamespace(get=lambda n: skill)
    bot.engine.pending_ideas = []
    recorded = []
    bot._remember_routed = lambda uid, q, skill_, ans: recorded.append((skill_, ans))
    await bot._handle_message(_update(VIEWER, "buy eth"), None)

    said = " ".join(str(t) for t in bot.sent)
    assert "Nothing has been placed." in said, "the door is not the gated read"
    assert "is below" not in said
    assert skill.execute.await_count == 0, "the gated read ran for a denied role"
    assert recorded and "analyze_asset" in recorded[-1][1]


@pytest.mark.asyncio
async def test_no_asset_named_is_the_door_alone(bot):
    skill = SimpleNamespace(execute=AsyncMock(return_value="SETUP CARD"))
    bot.registry = SimpleNamespace(get=lambda n: skill)
    bot.engine.pending_ideas = []
    await bot._handle_message(_update(OPERATOR, "long"), None)
    said = " ".join(str(t) for t in bot.sent)
    assert "Nothing has been placed." in said
    assert skill.execute.await_count == 0, "a setup was read for an asset nobody named"


@pytest.mark.asyncio
async def test_two_assets_named_is_the_door_alone(bot):
    """`symbol_mentioned` answers the FIRST (ETH); `place_target` answers
    None. A branch that re-derives the target its own way is
    indistinguishable from this one on every SINGLE-asset fixture, so the
    phrase has to be one that routes AND names two — a bare "buy eth and
    btc" is claimed by no rule at all."""
    skill = SimpleNamespace(execute=AsyncMock(return_value="SETUP CARD"))
    bot.registry = SimpleNamespace(get=lambda n: skill)
    bot.engine.pending_ideas = []
    await bot._handle_message(
        _update(OPERATOR, "place a limit order on eth and btc"), None)
    said = " ".join(str(t) for t in bot.sent)
    assert "Nothing has been placed." in said
    assert skill.execute.await_count == 0

"""An education opener is not an education question.

`what is a limit order` asks about the CONCEPT and belongs to the model.
`what are my limit orders` asks for the caller's own listing, in question
form. They open identically, so the OPENER is not the reading — what the
sentence asks ABOUT is, and the possessive is where that is written.

The router's education lookahead read only the opener, and declined both.
Driven over the possessive form of every row it guards, 27 of 28 missed
their own read:

    what are my open orders · what are my pending orders
    what are my limit orders · what is my order status
    what are my airdrops · what are my nfts · what is my defi health
    what is my idle cash doing · what are my rwas
        -> no rule at all, so a chat model

    what is my total balance · what is my total equity
    what is my balance across all exchanges
        -> get_portfolio, the SINGLE-ACCOUNT card, for a question whose own
           words say "across all exchanges"
    what are my defi positions · what are my aave positions
        -> get_portfolio, the EXCHANGE positions card, for an on-chain ask

That is the `get_orders` lesson this repo already records one noun over,
where a request to PLACE a limit order was answered with the card that
LISTS the resting ones.

AND THE EXCLUSION WAS NEVER AN ABSTENTION — IT IS A HAND-OFF. A lookahead
narrows only the rule that carries it, and the two widest rules in the file
sit below every user of this one and carried none: the bare Portfolio
keyword rule (`portfolio|balance|equity|pnl|profit|loss|p&l`) and the
typo-tolerant positions rule. So a sentence declined above did not reach
the model; it fell to whichever of those shared a word with it. Driven, TEN
education questions reached the POSITIONS CARD at confidence 1.0 —

    what is a stop loss · how does a stop loss work · what is pnl
    what is a position · what is equity · what is a portfolio
    how does pnl work · what is a balance · what is unrealized pnl
    how is equity calculated · what is p&l

— on the gate written to send education to the model. `what is profit
factor` was the only escape, and only because somebody hand-wrote
`profit(?! factor)` there for an unrelated reason: a per-WORD exclusion
standing in for a per-SENTENCE one.

AND THE CARD PROMISED A HALF THE ORDERS VOCABULARY COULD NOT HEAR. The
capability card's `get_orders` row says "your resting limit orders and
stop/take-profit triggers, as the exchange reports them", and the chat
tool's description promises the same triggers. The earlier fix reordered
the rules so that SENTENCE stopped reaching the positions card — it works
because the sentence contains the words `limit orders`. Ask for the half it
names second and nothing claimed it: `my stop orders`, `my tp orders`,
`do i have any stop orders` and `my triggers` reached NOTHING, while `my
take profit orders`, `my stop loss orders` and `my stop and take profit
orders` reached `get_portfolio` at 1.0 — down the very path that rule's own
comment describes. Fixed for the phrase that was measured, not for the
class it belongs to.

These tests DRIVE the router. There is no assertion here about the shape of
any regex: a rule that kept a private copy of the old lookahead declines its
own possessive form, and a rule that lost the reading answers education with
a card, so both mutations die on the corpus rather than on a scan. That is
also the only honest way to prove ONE definition — five copies of this
lookahead agreed with each other on every fixture, which is what a
byte-identical copy looks like from outside.
"""
from __future__ import annotations

import pytest

from bot.nlp.intent_router import IntentRouter


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def route(router, text: str) -> str:
    """The destination, in the sibling corpora's vocabulary."""
    x = router.classify_rules(text)
    if x.is_social:
        return "SOCIAL"
    if not x.skill:
        return "MODEL"
    return f"ASK:{x.skill}" if x.confidence < 1.0 else x.skill


# ── a possessive names the caller's own book ─────────────────────────────────
#
# One row per rule the lookahead guards, in the question form that used to be
# declined. A rule holding a private copy of the old lookahead fails here.

MY_OWN_BOOK = [
    # the orders listing — the row this slice is named for
    ("what are my open orders", "get_orders"),
    ("what are my pending orders", "get_orders"),
    ("what are my limit orders", "get_orders"),
    ("what are my resting orders", "get_orders"),
    ("what are my orders", "get_orders"),
    ("what are my active orders", "get_orders"),
    ("what is my order status", "get_orders"),
    # the possessive is anywhere in the sentence, not adjacent to the verb
    ("what is the status of my orders", "get_orders"),
    # a shared desk says "our"; it is the only other word in the escape, and
    # an alternative no corpus row drives is a claim there is a check
    ("what are our open orders", "get_orders"),
    ("what are my open limit orders", "get_orders"),
    ("how do my orders look", "get_orders"),
    ("what is pending on my account", "get_orders"),
    # the cross-venue read, whose card the single-account one cannot stand in for
    ("what is my net worth", "networth"),
    ("what is my total balance", "networth"),
    ("what is my total equity", "networth"),
    ("what are my total holdings", "networth"),
    ("what is my balance across all exchanges", "networth"),
    # the website-mirrored reads
    ("what are my airdrops", "airdrops"),
    ("what are my testnets", "airdrops"),
    ("what are my nft floor prices", "nft"),
    ("what is my defi health", "defi"),
    ("what are my defi positions", "defi"),
    ("what is my health factor", "defi"),
    ("what are my aave positions", "defi"),
    ("what is my idle cash doing", "idle_yield"),
    ("what are my idle stables", "idle_yield"),
    ("what are my rwas", "rwa"),
    # the two lookahead-free catchers this slice gave the reading to: their
    # possessive forms must be UNCHANGED by it
    ("what is my balance", "get_portfolio"),
    ("what is my pnl", "get_portfolio"),
    ("what is my portfolio", "get_portfolio"),
    ("what are my positions", "get_portfolio"),
    ("what are my open positions", "get_portfolio"),
    ("what is my p&l", "get_portfolio"),
]


@pytest.mark.parametrize("text,want", MY_OWN_BOOK)
def test_a_possessive_question_reaches_its_own_read(router, text, want):
    assert route(router, text) == want, (
        f"{text!r} names the caller's own book and must reach {want}"
    )


# ── the concept belongs to the model ─────────────────────────────────────────
#
# Every one of these is an education question, and a card here is a confident
# wrong answer to a question. The second block is the ten that reached the
# POSITIONS CARD at confidence 1.0 before this slice.

EDUCATION = [
    # the rows the lookahead has always guarded
    "what is a limit order",
    "how do limit orders work",
    "what are limit orders",
    "what is an order book",
    "what is a stop order",
    "how do orders work",
    "what are stop orders",
    # the trigger vocabulary this slice added, in its concept form
    "what is a trigger",
    "what are triggers",
    # AND AS A VERB, which is the only input that measures the qualifier on
    # that alternative. The two forms above are declined by the lookahead
    # whatever the alternative says, so dropping `(?:my|our|open|resting|
    # pending|active)` from it survived the first mutation round against
    # them — a mutation that changes no verdict is the corpus reporting a
    # gap, not the code. "what triggers…" does not open `what is/are/do/
    # does`, so the rule is live and only the qualifier declines it.
    "what triggers a margin call",
    "what triggers a liquidation",
    "what triggers the stop",
    "what is defi",
    "how does defi work",
    "what is rwa",
    "what are real world assets",
    "what is an airdrop",
    "how do airdrops work",
    "what is an nft",
    "what is a health factor",
    "what is yield farming",
    "what is a price alert",
    "what is net worth",
    "what is the spot market",
    "what are meme coins",
    # the ten that fell through to the positions card
    "what is a stop loss",
    "how does a stop loss work",
    "what is pnl",
    "how does pnl work",
    "what is unrealized pnl",
    "what is a position",
    "what is equity",
    "how is equity calculated",
    "what is a portfolio",
    "what is a balance",
    "what is p&l",
    # BEHIND A CONVERSATIONAL LEAD. The lookahead is `^`-anchored — it has
    # to be, or it would decline a question mid-sentence — and driven against
    # the pre-fix router, TEN of these eleven reached a card: `ok so what is
    # a stop loss`, `actually what is a position`, `well what is equity` and
    # `btw what is a position` to the POSITIONS card, `so what is defi` to
    # the DeFi card, `anyway what is rwa` to the RWA one, `just what is an
    # airdrop` to the airdrop radar, and both `limit order` forms to the
    # ORDERS listing. Only `then what is a stop order` reached the model,
    # and only because `stop order` had no vocabulary until this slice.
    "and what is a limit order",
    "ok so what is a stop loss",
    "but what is pnl",
    "so what is defi",
    "actually what is a position",
    "well what is equity",
    "just what is an airdrop",
    "anyway what is rwa",
    "btw what is a position",
    "then what is a stop order",
    "ok, what is a limit order",
]


@pytest.mark.parametrize("text", EDUCATION)
def test_a_question_about_the_concept_reaches_the_model(router, text):
    assert route(router, text) == "MODEL", (
        f"{text!r} asks about the concept — a card is a confident wrong answer"
    )


# ── the card's own second half ───────────────────────────────────────────────

TRIGGERS = [
    "my stop orders",
    "show my stop orders",
    "what are my stop orders",
    "my take profit orders",
    "my stop loss orders",
    "my stop and take profit orders",
    "my tp orders",
    "my sl orders",
    "my triggers",
    "do i have any stop orders",
    "any stop orders on eth",
]


@pytest.mark.parametrize("text", TRIGGERS)
def test_the_triggers_the_card_promises_reach_the_listing(router, text):
    assert route(router, text) == "get_orders", (
        f"{text!r} is the half the capability card names second"
    )


def test_the_card_and_the_tool_both_promise_those_triggers(router):
    """The vocabulary above is not invented here — two surfaces promise it.

    A card that names a capability is claiming asking for it does something,
    and this reads the promise out of the surfaces that make it rather than
    restating the words.
    """
    from bot.nlp.chat_tools import CHAT_TOOLS
    from bot.skills.skill_permissions import SKILL_SAYS

    card = SKILL_SAYS["get_orders"].lower()
    assert "stop" in card and "profit" in card, card

    tool = next((t for t in CHAT_TOOLS if t.name == "get_orders"), None)
    assert tool is not None, "get_orders is a chat tool"
    said = tool.description.lower()
    assert "stop-loss" in said and "take-" in said, said


# ── nothing was taken from a rule that already answered ──────────────────────
#
# A request to ACT keeps its door: `modify_position` and `cancel_order` are
# registered six hundred lines above the orders rule, and the widened trigger
# vocabulary must not reach past them.

NOT_STOLEN = [
    ("set stop loss at 2900", "modify_position"),
    ("change my take profit", "modify_position"),
    ("move my stop loss to 2900", "modify_position"),
    ("tighten my stop", "modify_position"),
    ("cancel my order", "cancel_order"),
    ("close my eth", "close_position"),
    # a bare possessive stop is a question about ONE position's protection,
    # and the positions card is what carries a position's stop level
    ("where is my stop loss", "get_portfolio"),
    ("my stop loss is too tight", "get_portfolio"),
    # the plain forms of every row above, which never needed the opener
    ("my open orders", "get_orders"),
    ("open orders", "get_orders"),
    ("show my orders", "get_orders"),
    ("my net worth", "networth"),
    ("rwa radar", "rwa"),
    ("this week's letter", "letter"),
    ("airdrop radar", "airdrops"),
    ("my defi positions", "defi"),
    ("my positions", "get_portfolio"),
    # a profit factor is a statistic no card prints, and its exclusion is a
    # different reason from this one — it stays with the model
    ("what is profit factor", "MODEL"),
    ("my profit factor", "MODEL"),
    # A NON-LEAD WORD BEFORE THE OPENER IS PART OF THE SENTENCE. The lead is
    # a fixed conversational vocabulary, never "any word": each of these
    # names its own read before asking, and a lead that took any word would
    # eat the name and decline the card the caller asked for.
    ("airdrops what is the schedule", "airdrops"),
    ("nft radar what is trending", "nft"),
    ("meme radar what is hot", "meme_radar"),
]


@pytest.mark.parametrize("text,want", NOT_STOLEN)
def test_the_rules_that_already_answered_still_answer(router, text, want):
    assert route(router, text) == want


# ── the misses, measured and recorded rather than patched around ─────────────

def test_a_comparison_naming_the_callers_own_order_gets_the_listing(router):
    """The known cost of reading the possessive as the discriminator.

    An education question that names the caller's OWN order reaches the
    listing. That is the reading the orders rule's own comment already takes
    for "should I cancel my order?" — the decision is the caller's and the
    listing is what it is made from. Recorded here so the next reader finds
    the decision rather than the symptom.
    """
    text = "what is the difference between my limit order and a stop order"
    assert route(router, text) == "get_orders"


def test_a_definite_object_with_no_possessive_is_still_declined(router):
    """The other known miss, and it is the letter's.

    "this week's letter" reaches the letter; "what is this week's letter"
    does not, because the possessive is the whole discriminator and there is
    none. Widening it to demonstratives was considered and refused: "what is
    the spot market" is education with a definite article, so definiteness
    does not separate the two, and a rule that cannot be stated in one
    sentence is a rule nobody can check. The row's three other phrasings
    reach it.
    """
    assert route(router, "this week's letter") == "letter"
    assert route(router, "weekly letter") == "letter"
    assert route(router, "agent letter") == "letter"
    assert route(router, "what is this week's letter") == "MODEL"

"""A greeting lead is informality, and what follows it decides.

`_GREETING_PATTERNS` is `^`-anchored — it is a LEAD, not a message — and the
social gate returned True for everything it led. Driven, **19 of 19** ordinary
greeting-led reads were answered "hey!":

    hey what is my balance · hi what are my positions · yo what is my pnl
    hello how is my portfolio doing · gm what is my equity
    hey what are my open orders · hi my open orders · yo show my orders
    hey what is my net worth · hi how much am i worth
    hey what is my defi health · hey my wallet on base
    hi am i overexposed · hey analyze btc · hi can you look at eth

The last two are the sharpest: a chart request that NAMES its symbol, greeted.

That is `HALT_SOCIAL_LEAD`'s lesson, which `CLAUDE.md` records one gate up as
*"a social lead on a whole-message action is INFORMALITY, and informality goes
to the door"*. The fix reached the ACTION rules only — `_ANCHORED_ACTION_RULES`
is consulted above the greeting line — and every READ rule was still behind it.
The education slice measured this and deliberately left it, because widening
`_EDU_LEAD` to greetings would have left the gate untouched while hiding what
it does.

THE LEAD IS STRIPPED AND THE REMAINDER IS ASKED THE SAME QUESTION, ONCE.
Recursing rather than consulting the rule table is the narrow choice: most of
`_INTENT_RULES` is unanchored, so asking it here would let a rule matching
INSIDE a pleasantry acquit real small talk — the shape this file records for
the orders rule ("take-profit" claimed by a bare `profit`) and for the halt
rule both. What the remainder is, the message is.

AND ONE MISS WAS NOT THE LEAD'S. `hey what is my risk` still reached the
model after the gate was fixed, because `what is my risk` does too, with no
lead at all — a `check_risk` alternation gap, not a greeting one. `my risk
level` and `my exposure` reach the card; bare `my risk` and `what is my risk`
reached nothing. That is the possessive-question family the education slice
closed, one rule short, so it is closed here with the decoy that makes it
delicate: `my risk reward` is an R:R question this product prints no card
for, and a bare `my risk` alternative would have taken it.
"""
from __future__ import annotations

import pytest

from bot.nlp.intent_router import IntentRouter


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


# ── a greeting lead on a real read ───────────────────────────────────────────

LED = [
    ("hey what is my balance", "get_portfolio"),
    ("hi what are my positions", "get_portfolio"),
    ("yo what is my pnl", "get_portfolio"),
    ("hello how is my portfolio doing", "get_portfolio"),
    ("gm what is my equity", "get_portfolio"),
    ("hey show me my positions", "get_portfolio"),
    ("good morning what is my pnl", "get_portfolio"),
    ("hey what are my open orders", "get_orders"),
    ("hi my open orders", "get_orders"),
    ("yo show my orders", "get_orders"),
    ("hey do i have any pending orders", "get_orders"),
    ("hey what is my net worth", "networth"),
    ("hi how much am i worth", "networth"),
    ("hey what is my defi health", "defi"),
    ("hey my wallet on base", "wallet"),
    ("hey what is my risk", "check_risk"),
    ("hi am i overexposed", "check_risk"),
    # a chart request that NAMES its symbol
    ("hey analyze btc", "analyze_asset"),
    ("hi can you look at eth", "analyze_asset"),
]

#: The same, with the punctuation a person actually types between the
#: pleasantry and the ask. These are what the `lstrip` is for; without it the
#: remainder opens with a comma and is a different sentence.
PUNCTUATED = [
    ("hey, what is my balance", "get_portfolio"),
    ("hi - what are my positions", "get_portfolio"),
    ("yo: my open orders", "get_orders"),
    ("hey! what is my pnl", "get_portfolio"),
    ("good morning, what is my equity", "get_portfolio"),
    ("hey...what is my balance", "get_portfolio"),
]


@pytest.mark.parametrize("text,want", PUNCTUATED)
def test_the_punctuation_between_them_is_not_the_message(router, text, want):
    assert route(router, text) == want


@pytest.mark.parametrize("text,want", LED)
def test_a_greeting_lead_does_not_greet_a_real_read(router, text, want):
    assert route(router, text) == want, (
        f"{text!r} is a {want} ask behind a pleasantry, not small talk"
    )


# ── and the message without its lead reaches the same place ──────────────────
#
# The lead is the only difference, so the pair is the proof: a gate that
# stripped the lead and then answered something ELSE would pass the table
# above and fail here.

@pytest.mark.parametrize("text,want", LED)
def test_the_lead_is_the_only_difference(router, text, want):
    bare = text.split(" ", 1)[1]
    if text.startswith(("good morning", "good afternoon", "good evening")):
        bare = text.split(" ", 2)[2]
    assert route(router, bare) == want, (
        f"{bare!r} must reach {want} with or without the pleasantry"
    )


# ── what is still small talk ─────────────────────────────────────────────────
#
# The gate's whole job. A greeting whose remainder is itself social, or which
# has no remainder at all, is a greeting.

SOCIAL = [
    # nothing after the lead
    "hey", "hi", "hello", "yo", "gm", "sup", "good morning", "hiya", "howdy",
    # a remainder that is itself social
    "hey there", "hey how are you", "hi how's it going", "yo what's up",
    # WITH the punctuation. This is the row that measures the `lstrip`: keep
    # the comma and the remainder is ", how are you", whose leading token
    # pushes it past the three-word gate and out of small talk entirely.
    "hey, how are you", "hey, thanks",
    "hello there friend", "hey mate", "hi bot",
    # not greetings at all, and unaffected
    "thanks", "thanks bro", "ty", "cheers", "much appreciated", "thank you",
    "lol ok", "ok cool", "nice one", "bye", "see ya", "good night",
    # a greeting that is ALL punctuation after the lead has no remainder
    "hey,", "hey!", "hey - ",
]


@pytest.mark.parametrize("text", SOCIAL)
def test_small_talk_is_still_small_talk(router, text):
    assert route(router, text) == "SOCIAL", f"{text!r} is small talk"


# ── the check_risk gap the lead was hiding ───────────────────────────────────

RISK = ["my risk", "what is my risk", "whats my risk", "my risk level",
        "my exposure", "check my risk", "am i overexposed", "risk check"]


@pytest.mark.parametrize("text", RISK)
def test_the_callers_own_risk_reaches_the_risk_card(router, text):
    assert route(router, text) == "check_risk"


NOT_RISK = [
    # an R:R question, which this product prints no card for
    ("my risk reward", "MODEL"),
    ("what is my risk reward on this", "MODEL"),
    ("my risk rr", "MODEL"),
    # education about the concept — the education slice's subject, still held
    ("what is risk", "MODEL"),
    ("what is a risk", "MODEL"),
    # recorded boundaries that must not move
    ("event risk on eth", "check_event_risk"),
    ("risk on eth", "stance_aggressive"),
]


@pytest.mark.parametrize("text,want", NOT_RISK)
def test_the_risk_alternative_takes_nothing_that_was_not_its(router, text, want):
    assert route(router, text) == want


@pytest.mark.parametrize("text", ["my risk:reward", "my risk/reward", "my r:r"])
def test_a_punctuated_compound_is_still_greeted(router, text):
    """MEASURED AND FILED, not blessed — and two steps from this subject.

    These are GREETED today, and neither the lead nor the risk alternative
    put them there. The short-message gate matches WHITESPACE-SPLIT words
    against `trading_words`, so the single token `risk:reward` is not the
    word `risk` and nothing in the set matches; `my risk reward`, three
    words, is not social for exactly that reason. It is a tokenization gap
    in the gate's vocabulary check, not a greeting gap and not a rule gap,
    so it is recorded here rather than fixed inside a slice about the lead.

    This asserts what the product does TODAY. A fix will trip it, which is
    the point: the next reader arrives at this note rather than at a silent
    behaviour change.
    """
    assert route(router, text) == "SOCIAL"


# ── the action rules keep the behaviour they were given ──────────────────────
#
# `_ANCHORED_ACTION_RULES` is consulted ABOVE the greeting line, so a casual
# fleet halt still reaches its door rather than the greeter. Pinned here
# because the greeting branch now sits between that check and the rest, and a
# careless reorder would put the recursion first.

ACTIONS = [
    ("stop the bot", "halt"),
    ("halt the bot", "halt"),
    ("close my eth", "close_position"),
    ("cancel my order", "cancel_order"),
    # THE ROW THAT MEASURES `_ANCHORED_ACTION_RULES` STILL BEING ABOVE THIS.
    # A trailing pleasantry is what that check exists for — `_THANKS_PATTERNS`
    # is unanchored, so without it "halt the bot, thanks" is small talk, which
    # is the defect the halt slice fixed. The greeting recursion does not
    # cover it: there is no greeting LEAD here to strip.
    ("halt the bot, thanks", "halt"),
    ("stop trading, ty", "halt"),
]


@pytest.mark.parametrize("text,want", ACTIONS)
def test_an_action_still_reaches_its_door(router, text, want):
    assert route(router, text) == want


def test_the_greeting_branch_sits_below_the_action_rules(router):
    """A greeting-led ACTION is the halt suite's recorded case, not this one.

    `HALT_SOCIAL_LEAD` routes "bro stop the bot" to the ambiguous DOOR rather
    than dispatching it, and that decision is owned one gate up. This only
    pins that the greeting branch did not swallow it.
    """
    assert route(router, "hey stop the bot") != "SOCIAL"

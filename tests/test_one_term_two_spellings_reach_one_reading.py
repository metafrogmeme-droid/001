"""One term, two spellings, two destinations.

A SEPARATOR IS NOT A WORD BOUNDARY THE SAME WAY IN EVERY READER, and three
readers of one message disagreed about it. Driven over fourteen pairs of the
SAME trading term spelled two ways, EIGHT answered two different ways, and the
direction was arbitrary::

    win rate       -> the positions card    win-rate       -> "hey!"
    profit factor  -> the model             profit-factor  -> the positions card
    max drawdown   -> the model             max-drawdown   -> "hey!"
    api keys       -> the model             api-keys       -> "hey!"
    risk reward    -> the model             risk:reward    -> "hey!"
    my drawdown    -> the risk card         drawdown%      -> "hey!"
    win loss       -> the positions card    win/loss       -> "which coin?"

Three causes, each a different tokenization of one string.

**THE SOCIAL GATE** splits on WHITESPACE and matches whole tokens against
`trading_words`, which is written in WORDS — so every compound a trader writes
with an internal ``:``, ``/``, ``-`` or ``%`` arrived as one token the set had
never heard of, and the check that decides whether a short message is small
talk was not consulted at all. Eighteen ordinary short trading terms were
answered "hey!" — `my sl/tp`, `api-keys`, `max-drawdown`, `r:r ratio` among
them. `_vocabulary_forms` is the reading: the whole token FIRST and its parts
after it, because the set holds entries that are themselves compounds (`p&l`,
`walk-forward`) and splitting alone would lose them. It adds spellings and
removes none, which is the only direction that cannot send a message that
reached a read to the greeter instead.

**THE RULES** match with ``\\b``, which treats ``-`` as a boundary. The bare
Portfolio keyword rule carries a hand-written per-WORD exclusion,
``profit(?! factor)``, spelled with a SPACE — so ``\\bprofit\\b`` matched
inside `profit-factor` while the ` factor` the lookahead spells never followed
it: `profit factor` reached the model and `profit-factor` reached the POSITIONS
CARD at confidence 1.0, the statistic no card prints, let through by the
exclusion written to keep it off. The account-status rule spelled
``win ?rate``, so `win-rate` was nobody's. And the same keyword rule knew
`p&l` and not `p/l`.

**THE BARE-TICKER RULE** read ``[A-Za-z]{2,10}/[A-Za-z]{2,10}`` — ANY two
English words joined by a slash — while `_extract_symbol` resolves the slash
form for ``/USDT`` alone. So the rule CLAIMED the message, the extractor
answered nothing, and the caller was asked **"which coin do you want me to
look at?"**: `buy/sell`, `risk/reward`, `win/loss`, `long/short`, `fear/greed`,
`call/put`, `boom/bust`, `sl/tp` and `risk/return`, each a clarification
offered for a question that named no coin — the `scan the 15m` shape arriving
through a separator. That rule's own docstring promises "a one-word message
that is not a symbol is left exactly where it was", which was false of that
alternative. A pair is a base PRICED IN something, so the quote side is
`_QUOTE_WORDS` and nothing else.

WHAT THIS SUITE CLAIMS is not that any given term reaches any given card —
those readings belong to the rules and are pinned by their own suites. It
claims the SAME TERM REACHES ONE READING however it is spelled, that none of
the eighteen is greeted, that none of the nine is asked which coin, and that a
real pair is still a ticker.
"""
from __future__ import annotations

import pytest

from bot.nlp.intent_router import IntentRouter, _vocabulary_forms


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


# ── the claim ────────────────────────────────────────────────────────────────
#
# One term, two spellings. The destination is whatever the rules decide; what
# is asserted is that BOTH spellings reach the SAME one. Deriving the expected
# destination from the rules would be a guard that cannot see the rules drift,
# so each row states it — and a row whose reading is deliberately re-argued
# later fails here rather than changing in silence.
PAIRS = [
    # was: card / "hey!"
    ("win rate", "win-rate", "get_portfolio"),
    ("hit rate", "hit-rate", "get_portfolio"),
    # was: model / card — the exclusion's own spelling
    ("profit factor", "profit-factor", "MODEL"),
    # was: model / "hey!"
    ("max drawdown", "max-drawdown", "MODEL"),
    ("api keys", "api-keys", "MODEL"),
    ("risk reward", "risk:reward", "MODEL"),
    ("my risk reward", "my risk:reward", "MODEL"),
    ("my sl tp", "my sl/tp", "MODEL"),
    ("4h 1d", "4h/1d", "MODEL"),
    ("15m 1h", "15m/1h", "MODEL"),
    # was: card / "which coin?"
    ("win loss", "win/loss", "get_portfolio"),
    # already agreed, and must keep agreeing: a `\b` that treats `-` as a
    # boundary is what makes these two the same today, so a narrowing of the
    # keyword rule would show up here.
    ("stop loss", "stop-loss", "get_portfolio"),
    ("take profit", "take-profit", "get_portfolio"),
]


@pytest.mark.parametrize("spaced,joined,want", PAIRS)
def test_the_same_term_spelled_two_ways_reaches_one_reading(router, spaced, joined, want):
    assert route(router, spaced) == want, f"{spaced!r} moved"
    assert route(router, joined) == want, f"{joined!r} does not agree with {spaced!r}"


# ── the eighteen that were answered "hey!" ───────────────────────────────────
#
# Every one of these is a trading term a person types, and none is small talk
# by any reading. They are listed as they were driven so the count in the
# docstring is checkable, and the assertion is only that the greeter is not
# the answer — where each goes is the rules' business.
WAS_GREETED = [
    "my risk:reward", "my r:r", "risk:reward", "risk-reward", "r:r ratio",
    "rr ratio", "my sl/tp", "sl-tp", "win-rate", "w/l", "p/l", "my p/l",
    "max-drawdown", "drawdown%", "api-keys", "api-key", "4h/1d", "15m/1h",
]


@pytest.mark.parametrize("text", WAS_GREETED)
def test_a_punctuated_trading_term_is_not_small_talk(router, text):
    assert route(router, text) != "SOCIAL"


def test_the_eighteen_are_eighteen():
    """The docstring's count, pinned — a measurement you remember is not one."""
    assert len(WAS_GREETED) == 18
    assert len(set(WAS_GREETED)) == 18


# ── the nine that were asked which coin ──────────────────────────────────────
#
# `ASK:analyze_asset` is the router's "I think you want a chart and I cannot
# tell of what" — honest for a message that named an asset it could not
# resolve, and a confident wrong door for two English words with a slash
# between them.
WAS_ASKED_WHICH_COIN = [
    "risk/reward", "win/loss", "buy/sell", "long/short", "fear/greed",
    "risk/return", "sl/tp", "call/put", "boom/bust",
]


@pytest.mark.parametrize("text", WAS_ASKED_WHICH_COIN)
def test_two_english_words_with_a_slash_are_not_a_ticker(router, text):
    assert route(router, text) != "ASK:analyze_asset"


# ── and a real pair is still a ticker ────────────────────────────────────────
#
# The narrowing is the whole risk in this slice: a quote list that is missing
# a currency turns a ticker into small talk, which is the direction that
# costs. `wif/usdt` is the rule's own docstring example.
REAL_PAIRS = ["wif/usdt", "btc/usdt", "eth/usd", "sol/usdc", "BTC/USDT",
              "doge/busd", "arb/dai", "pepe/usdc"]


@pytest.mark.parametrize("text", REAL_PAIRS)
def test_a_pair_quoted_in_a_currency_is_still_a_ticker(router, text):
    assert route(router, text) == "analyze_asset"


def test_a_cross_pair_naming_two_known_assets_still_asks_which(router):
    """RECORDED, not changed. `eth/btc` names TWO assets the router knows, and
    a needs-symbol rule that finds more than one asks WHICH — the reading
    `CLAUDE.md` states as "two assets named is not one asset asked about".
    It answered `ASK:analyze_asset` before this slice and answers it after;
    pinned so the quote list cannot be read as having changed it."""
    assert route(router, "eth/btc") == "ASK:analyze_asset"


# ── the tokenizer ────────────────────────────────────────────────────────────

def test_the_whole_token_comes_first_and_survives():
    """`p&l` IS the vocabulary entry, and no part of it is. A reading that
    split and did not try the whole would lose the entry it was written for —
    which is why the whole is first and the parts follow."""
    assert _vocabulary_forms("p&l")[0] == "p&l"
    assert _vocabulary_forms("P&L")[0] == "p&l"
    assert "p&l" in _vocabulary_forms("p&l")


@pytest.mark.parametrize("token,want", [
    ("risk:reward", ("risk:reward", "risk", "reward")),
    ("walk-forward", ("walk-forward", "walk", "forward")),
    ("drawdown%", ("drawdown%", "drawdown")),
    ("4h/1d", ("4h/1d", "4h", "1d")),
    ("r:r", ("r:r", "r")),          # deduped: one distinct part, not two
    ("plain", ("plain",)),          # nothing to split
    ("what's", ("what's", "what", "s")),
])
def test_the_forms_are_the_whole_then_its_parts(token, want):
    assert _vocabulary_forms(token) == want


# ── the decoys, which are the load-bearing half ──────────────────────────────
#
# Reading more spellings per token can only make the gate LESS likely to
# answer "hey!", so the cost lands entirely here: a social message whose
# punctuation hides a trading word would stop being small talk. Driven over
# thirty ordinary pleasantries, none did.
SOCIAL = [
    "r u there", "r u ok", "how's it going", "what's up", "ok, thanks",
    "hey!", "well-done", "u ok m8", "lol :)", "brb", "good/bad", "yes/no",
    "he/she", "24/7", "and/or", "pros/cons", "hi there", "thanks!", "gm",
    "wagmi", "ty", "np", "o/", "hey :)", "see ya", "later!", "cheers",
    "no worries", "all good", "hey, how are you",
]


@pytest.mark.parametrize("text", SOCIAL)
def test_small_talk_is_still_small_talk(router, text):
    assert route(router, text) == "SOCIAL"


def test_the_bare_letters_are_not_in_the_vocabulary(router):
    """`r:r`, `r/r`, `rr` and `w/l` are WHOLE-token entries, which is the form
    tried first, and the single letters are deliberately absent: a one-letter
    entry would acquit "r u there", which is the one decoy that makes the
    shorthand delicate. Driven both ways here, because a vocabulary that
    carried `r` would pass every other test in this file."""
    assert route(router, "r u there") == "SOCIAL"
    assert route(router, "w") == "SOCIAL"
    assert route(router, "my r:r") == "MODEL"
    assert route(router, "w/l") == "MODEL"


def test_a_pleasantry_whose_part_is_a_trading_word_costs_the_greeting(router):
    """MEASURED AND STATED, not hidden. `top` is a trading word (the scanner's
    "top movers"), so `top-notch` now has a part in the vocabulary and reaches
    the model rather than the greeter. That is the price of reading the parts,
    it is one row in thirty, and it falls on the safe side: a model that
    answers conversationally, not a confident wrong card."""
    assert route(router, "top-notch") == "MODEL"


def test_the_rows_that_still_differ_differ_by_something_else(router):
    """RECORDED, not fixed, because none of these is one term spelled two ways.

    Re-driven over the same fourteen-row probe after the fix, three rows
    still answer differently, and the separator is not what separates them:

      * `my p l` is SOCIAL where `my p/l` reaches the card. ``p l`` is not a
        spelling of anything — two bare letters, kept out of the vocabulary
        for the reason ``r`` is kept out.
      * `my drawdown` reaches the risk card where `drawdown%` reaches the
        model. That is a POSSESSIVE difference and the risk rule's own
        alternation makes it deliberately; this slice has no argument
        against it.
      * `my r r` is SOCIAL where `my r:r` reaches the model — the first case
        again.

    Stated as a test rather than as prose so the claim is driven: if any of
    the three converges later, this fails and the note is re-read.
    """
    assert route(router, "my p l") == "SOCIAL"
    assert route(router, "my p/l") == "get_portfolio"
    assert route(router, "my drawdown") == "check_risk"
    assert route(router, "drawdown%") == "MODEL"
    assert route(router, "my r r") == "SOCIAL"
    assert route(router, "my r:r") == "MODEL"

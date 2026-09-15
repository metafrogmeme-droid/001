"""The universe scanner's own verb, and the ladders it does not run.

Driven before anything was written, over every ordinary way of asking the
scanner for a timeframe. The mode rules accept the request as the WHOLE
message ("4h setups") or as an explicit multi-word form ("4h scan"), and both
have always worked — but every spelling that starts with the VERB failed, in
one of two ways:

    scan 4h · scan 15m · scan 1h · scan 5m · scan 30m · scan intraday
    scan for swings · scan for setups · scan 4 hour · scan 1 hour
        -> no rule at all, so a chat model that holds no scan tool

    scan the 4h · scan the 15m · scan on 15m · scan in 15m · scan at 4h
    scan using 4h · scan swings · scan scalps · scan hourly · scan daily
        -> analyze_asset with NO symbol: "which coin do you want me to look
           at?", asked of a request for the whole universe

`_MODE_LEAD` already accepts run / do / show me / give me / find me / got any.
The one verb it did not accept is the word the product calls the thing.

Two more came out of the same drive.

A market scan that NAMES a ladder was answered by the movers table, which
carries no timeframe at all — "scan the market on 4h" and "market scan on 4h"
both dropped the 4h with nothing on the card saying so.

And `1d`. The LADDER CARD runs three modes (`ProScanSkill.MODE_CFG`: 5m, 15m
and 4h), and `weekly` is recorded in the router as naming none of them and
staying with the model. `1d` is the same shape in the spelling a chart uses,
and it went somewhere else entirely: "1d scan", "1D scan" and "d1 scan"
reached `analyze_asset` with no symbol, because `_names_a_non_asset` reads
runs of two or more LETTERS and `1d` is a digit and one letter — invisible, so
the message was read as naming no object at all. Typed alone, "1d" and "1w"
were GREETED, while "daily" and "weekly" were not. One timeframe, two
spellings, three destinations.

They agree on the model now, which is better than asking which coin and is
still not the door. THE FIRST DRAFT OF THIS FILE SAID `1d` NAMES "a ladder
this scanner does not run", and that is false of the PRODUCT: `MODE_CFG` is
the ladder CARD's table, while the full-universe sweep reads
`candles.SUPPORTED_TIMEFRAMES` — 5m, 15m, 1h, 4h, 1d — so `/deepscan 1d`
really is a daily sweep of the whole universe. `test_the_scan_timeframes_are_
read_from_the_skills_own_tables` pins both tables for that reason. Routing a
typed `1d` to the deep scan at the timeframe it names is filed, not done, and
so is the one the same reading turned up next door: `_INTRADAY_TF` folds `1h`,
`2h`, `30m` and `hourly` into the intraday ladder, whose card is headed 15M.
"""
from __future__ import annotations

import re

import pytest

from bot.nlp.intent_router import (
    _SWEEP_TRIGGER,
    _TIMEFRAME_TOKEN,
    IntentRouter,
    _names_a_non_asset,
)
from bot.nlp.skill_doors import dispatch_kwargs, dispatches_to


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def route(router, text: str) -> str:
    """The destination, in the vocabulary the sibling corpus uses.

    `ASK:<skill>` is the partial match — the rule claimed the message and the
    surface asks which coin. For a sweep request that is the wrong answer and
    naming it separately is the whole point: `analyze_asset` and
    `ASK:analyze_asset` are different events.
    """
    x = router.classify_rules(text)
    if x.is_social:
        return "SOCIAL"
    if not x.skill:
        return "MODEL"
    return f"ASK:{x.skill}" if x.confidence < 1.0 else x.skill


# ── the verb-first ask, every ladder ─────────────────────────────────────────

VERB_FIRST = [
    # the scalp ladder
    ("scan 5m", "scan_scalp"),
    ("scan the 5m", "scan_scalp"),
    ("scan scalps", "scan_scalp"),
    ("scan for scalps", "scan_scalp"),
    ("scan 5 min", "scan_scalp"),
    # the intraday ladder
    ("scan 15m", "scan_intraday"),
    ("scan the 15m", "scan_intraday"),
    ("scan 1h", "scan_intraday"),
    ("scan 2h", "scan_intraday"),
    ("scan 30m", "scan_intraday"),
    ("scan on 15m", "scan_intraday"),
    ("scan in 15m", "scan_intraday"),
    ("scan intraday", "scan_intraday"),
    ("scan hourly", "scan_intraday"),
    ("scan daily", "scan_intraday"),
    ("scan the daily", "scan_intraday"),
    ("scan 1 hour", "scan_intraday"),
    ("scan for 15m setups", "scan_intraday"),
    ("scan for setups", "scan_intraday"),
    # "setups, for me" — the possessive before the scan NOUN, which is not
    # the possessive before a MODE word (see the decoys). It had been
    # answered "which coin?", because `setups` is in `_FILLER` so nothing
    # was left over for `_names_a_non_asset` to object with.
    ("scan my setups", "scan_intraday"),
    # the swing ladder
    ("scan 4h", "scan_swing"),
    ("scan the 4h", "scan_swing"),
    ("scan at 4h", "scan_swing"),
    ("scan using 4h", "scan_swing"),
    ("scan on the 4h", "scan_swing"),
    ("scan swings", "scan_swing"),
    ("scan the swing", "scan_swing"),
    ("scan for swings", "scan_swing"),
    ("scan for swing setups", "scan_swing"),
    ("scan 4 hour", "scan_swing"),
    ("scan four hour", "scan_swing"),
    ("scan 4h setups", "scan_swing"),
    ("scan the 4h setups", "scan_swing"),
    # the politeness lead, and the scanner's other verbs
    ("please scan 4h", "scan_swing"),
    ("can you scan the 15m", "scan_intraday"),
    ("screen 4h", "scan_swing"),
    ("sweep the 15m", "scan_intraday"),
    # a market scan that names a ladder is a ladder request
    ("scan the market on 4h", "scan_swing"),
    ("scan the market on the 4h", "scan_swing"),
    ("market scan on 4h", "scan_swing"),
    ("market scan on the 15m", "scan_intraday"),
    ("run a market scan on 5m", "scan_scalp"),
]

#: Spellings that were already right and must stay right — the whole-message
#: form and the explicit multi-word one. A fix to the verb-first lead that
#: moved any of these would be trading one drift for another.
ALREADY_RIGHT = [
    ("4h", "scan_swing"), ("15m", "scan_intraday"), ("5m", "scan_scalp"),
    ("1h", "scan_intraday"), ("30m", "scan_intraday"), ("2h", "scan_intraday"),
    ("4h setups", "scan_swing"), ("15m setups", "scan_intraday"),
    ("5m setups", "scan_scalp"), ("1h ideas", "scan_intraday"),
    ("4h scan", "scan_swing"), ("1h scan", "scan_intraday"),
    ("15m scan", "scan_intraday"), ("5m scan", "scan_scalp"),
    ("daily scan", "scan_intraday"), ("daily setups", "scan_intraday"),
    ("show me 4h setups", "scan_swing"), ("run the 4h", "scan_swing"),
    ("swing scan", "scan_swing"), ("scalp scan", "scan_scalp"),
    ("intraday scan", "scan_intraday"),
    # the general scans, which name no ladder
    ("scan", "scan_market"), ("market scan", "scan_market"),
    ("run a scan", "scan_market"), ("quick scan", "scan_market"),
    ("top movers", "scan_market"), ("scan the market", "scan_market"),
    ("deep scan", "scan_deep"), ("scan everything", "scan_deep"),
    ("scan all", "scan_deep"), ("full scan", "scan_full"),
]

#: A sweep the LADDER CARD has no mode for. `weekly` was already recorded as
#: the model's; these are the same request in every other spelling, and the
#: point of the row is that they all agree now rather than splitting three
#: ways. It is not the destination these deserve — `/deepscan 1d` is a real
#: daily sweep of the universe — but it is an honest one, and routing them
#: there needs a dispatch row per timeframe, which is filed rather than done.
UNRUN_LADDER = [
    ("1d", "MODEL"), ("1w", "MODEL"), ("d1", "MODEL"), ("w1", "MODEL"),
    ("1d scan", "MODEL"), ("1D scan", "MODEL"), ("d1 scan", "MODEL"),
    ("1w scan", "MODEL"), ("weekly scan", "MODEL"), ("monthly scan", "MODEL"),
    ("scan 1d", "MODEL"), ("scan 1w", "MODEL"), ("scan the 1d", "MODEL"),
    ("1d setups", "MODEL"), ("1d ideas", "MODEL"),
    ("weekly", "MODEL"), ("monthly", "MODEL"),
]

#: Phrases that carry the scan verb, or a timeframe, and are NOT a sweep of
#: the universe on a ladder. Each one is a destination the fix had to leave
#: exactly where it was.
DECOYS = [
    # the object is an account, not the universe. A possessive before a MODE
    # word names the CALLER'S OWN book, which is why `my` is in the scan-noun
    # lead and not in the mode-word one: "scan my swings" is a claim about
    # this caller's swing trades and must not be answered with a market
    # ladder. The wallet and portfolio rows are not what excludes `my` — the
    # lead has to reach a mode word and neither of those is one — and a first
    # draft of the comment said they were, which the mutation round refused.
    ("scan my wallet", "wallet"),
    ("scan my portfolio", "get_portfolio"),
    ("scan my trades", "get_portfolio"),
    ("scan my positions", "get_portfolio"),
    ("scan my swings", "MODEL"),
    ("scan my scalps", "MODEL"),
    ("scan my 4h", "MODEL"),
    # the object is one asset — a timeframe beside it is a modifier
    ("scan eth", "analyze_asset"),
    ("scan btc", "analyze_asset"),
    ("scan eth on the 4h", "analyze_asset"),
    ("deep scan eth", "analyze_asset"),
    ("eth 15m setup", "analyze_asset"),
    ("btc 4h structure", "analyze_asset"),
    # the object is not an asset at all
    ("scan the docs", "MODEL"),
    ("scan the news", "MODEL"),
    ("scan for new listings", "MODEL"),
    # a market scan naming a ladder the scanner does not run keeps the market
    # card. "scan the market" IS the request there and the daily is a
    # qualifier nothing can honour; the 4h case is different only because a
    # correct answer exists to be dropped.
    ("scan the market on 1d", "scan_market"),
    ("market scan on the 1d", "scan_market"),
    # two ladders named is not one ladder asked for. The scanner runs one
    # mode per card, so this reaches the model rather than picking one and
    # printing a card that looks like an answer to the question that was
    # asked. Asking WHICH — the reading a symbol-needing rule already does
    # for two assets — is filed, not done.
    ("scan 4h and 15m", "MODEL"),
    # education about a timeframe is the model's. The last of these is what
    # the verb-first lead's `^` anchor is for: a rule that matched the verb
    # anywhere would read a question ABOUT scanning as a request to scan, the
    # shape the halt rule records ("a rule matching inside a sentence routes
    # the sentence's subject as the command").
    ("how do i read a 4h chart", "MODEL"),
    ("how do i scan the 4h", "MODEL"),
    ("can you explain how to scan 15m", "MODEL"),
    ("what is a 15m candle", "MODEL"),
    ("how does the scanner work", "MODEL"),
    ("i want to learn swing trading", "MODEL"),
    # a bare analysis verb with a timeframe still asks WHICH COIN: the caller
    # wants a chart and has named the timeframe, not the subject.
    ("analyze 4h", "ASK:analyze_asset"),
    ("analyse 15m", "ASK:analyze_asset"),
    ("analyze", "ASK:analyze_asset"),
    ("check the setup", "ASK:analyze_asset"),
    ("where is liquidity", "ASK:analyze_asset"),
]


class TestTheVerbFirstAsk:
    @pytest.mark.parametrize("text, expected", VERB_FIRST)
    def test_it_reaches_the_ladder_it_named(self, router, text, expected):
        assert route(router, text) == expected, text

    @pytest.mark.parametrize("text, expected", ALREADY_RIGHT)
    def test_the_spellings_that_worked_still_work(self, router, text, expected):
        assert route(router, text) == expected, text

    @pytest.mark.parametrize("text, expected", DECOYS)
    def test_a_decoy_keeps_its_destination(self, router, text, expected):
        assert route(router, text) == expected, text

    def test_no_verb_first_ask_is_answered_by_asking_which_coin(self, router):
        """The expensive half. A sweep request that reaches `analyze_asset`
        with no symbol is answered "which coin?", which is a question about
        one asset asked of a request for the universe — a confident wrong
        door, not a clarification."""
        asked = [t for t, _ in VERB_FIRST + UNRUN_LADDER
                 if route(router, t).startswith("ASK:")]
        assert asked == [], asked

    def test_the_ladder_the_words_name_is_the_mode_that_runs(self, router):
        """The intent's own name anchors the mode, through the one dispatch
        table — a retarget that carried only the name would render a card
        headed with a different timeframe and nothing would raise."""
        for text, expected in VERB_FIRST:
            mode = expected.removeprefix("scan_")
            intent = router.classify_rules(text).skill
            assert intent == expected, text
            assert dispatches_to(intent) == "pro_scan", text
            assert dispatch_kwargs(intent) == {"mode": mode}, text


class TestTheLadderItDoesNotRun:
    @pytest.mark.parametrize("text, expected", UNRUN_LADDER)
    def test_every_spelling_of_it_has_one_destination(self, router, text, expected):
        assert route(router, text) == expected, text

    def test_it_is_not_greeted(self, router):
        """`daily` and `weekly` were trading words and `1d`/`1w` were not, so
        the chart spelling was small talk and the English one was not. The
        gate consults the rules before it decides, so a word a rule claims
        needs no entry — these are the ones no rule claims."""
        for text, _ in UNRUN_LADDER:
            assert route(router, text) != "SOCIAL", text

    def test_the_scan_timeframes_are_read_from_the_skills_own_tables(self):
        """TWO tables, and reading one as the other is what made the first
        draft of this file wrong.

        `MODE_CFG` is the LADDER CARD's — three modes, each with an entry, a
        stop and a target — so there is no fourth row to alias a daily
        request to, and answering one with the 4h card would be the confident
        wrong answer this router records about the orders card.

        `SUPPORTED_TIMEFRAMES` is the FULL-UNIVERSE SWEEP's, and it holds 1d:
        `/deepscan 1d` is a real daily sweep and `/deepscan all` does 5m to 1d
        in one pass. So "a ladder this scanner does not run" was true of one
        skill and false of the product, and the sentence is pinned here
        against both tables rather than written out anywhere.
        """
        from bot.skills.skill_registry import ProScanSkill
        from bot.utils.candles import SUPPORTED_TIMEFRAMES

        assert set(ProScanSkill.MODE_CFG) == {"scalp", "intraday", "swing"}
        assert [ProScanSkill.MODE_CFG[m]["timeframe"]
                for m in ("scalp", "intraday", "swing")] == ["5m", "15m", "4h"]
        assert "1d" in SUPPORTED_TIMEFRAMES, "the deep sweep really does run a daily"
        assert "1w" not in SUPPORTED_TIMEFRAMES and "1M" not in SUPPORTED_TIMEFRAMES, (
            "weekly and monthly are run by neither, which is why they keep the "
            "destination the router already recorded for them")


class TestTheReadingUnderIt:
    """`_names_a_non_asset` drove the whole `1d` half, so it is driven here."""

    def test_a_timeframe_is_an_object_when_the_trigger_is_a_sweep(self):
        m = re.search(r"scan", "1d scan")
        assert _names_a_non_asset("1d scan", m) is True
        m = re.search(r"scan", "d1 scan")
        assert _names_a_non_asset("d1 scan", m) is True

    def test_and_a_modifier_when_the_trigger_is_an_analysis(self):
        """"analyze 4h" must still ask which coin. Reading the timeframe as
        an object there would send a caller who wants a chart to a model
        instead of to the question that gets them one."""
        m = re.search(r"analyze", "analyze 4h")
        assert _names_a_non_asset("analyze 4h", m) is False
        m = re.search(r"analyze", "analyze 15m")
        assert _names_a_non_asset("analyze 15m", m) is False

    def test_a_word_left_over_is_still_a_non_asset_object(self):
        """The reading that was already there, unchanged."""
        m = re.search(r"look at", "look at the docs")
        assert _names_a_non_asset("look at the docs", m) is True
        m = re.search(r"analyze", "analyze")
        assert _names_a_non_asset("analyze", m) is False

    @pytest.mark.parametrize("text", ["1d", "1w", "4h", "15m", "5m", "1 d",
                                      "d1", "w1", "4H", "15M", "3d"])
    def test_the_timeframe_token_reads_every_chart_spelling(self, text):
        assert _TIMEFRAME_TOKEN.search(text), text

    @pytest.mark.parametrize("text", ["63000", "5 trades", "m5x", "a1b",
                                      "0x1d4f", "120m2", ""])
    def test_and_nothing_that_is_not_one(self, text):
        assert not _TIMEFRAME_TOKEN.search(text), text

    def test_the_sweep_trigger_is_read_from_the_match(self):
        """Read from the trigger the rule matched rather than kept as a
        second list of rule names, so a scan rule added later is classified
        by what it says."""
        assert _SWEEP_TRIGGER.search("scan") and _SWEEP_TRIGGER.search("scaaannn")
        assert _SWEEP_TRIGGER.search("Deep Scan") and _SWEEP_TRIGGER.search("sweep")
        assert not _SWEEP_TRIGGER.search("analyze")
        assert not _SWEEP_TRIGGER.search("look at")

"""A table of what a trader types, and where each phrase is answered.

A corpus of 153 realistic messages was run through `classify_rules` and 56
were misrouted. This file is the half of them that is about ONE ASSET — the
largest family, and the one where the wrong route is a model with no chart
answering about a chart.

  give me a full analysis of BTC   -> the chat model     (no analysis tool)
  technical analysis of sol        -> the chat model
  deep dive on eth                 -> the chat model
  chart for doge                   -> the chat model
  is BTC bullish                   -> the chat model
  $HYPE                            -> the SOCIAL gate, answered as small talk
  look at the link I sent          -> analyze_asset LINK/USDT, confidence 1.0
  analyze btc and eth              -> analyze_asset BTC/USDT, the ETH dropped
  close my ETH and scan the market -> scan_market, the CLOSE dropped
  explain rsi                      -> "what coin do you want me to look at?"

The route IS the grounding: a read that reaches `analyze_asset` is built from
live indicators, and one that reaches the chat model is fiction in the shape
of a measurement. Every `current` above was recorded by running the router,
not remembered.

Red herrings run through the table: "how does the scan work" and "explain
support and resistance" are education and belong to the model; "btc.d" is
dominance, not a spot chart; "hype analysis" names a lowercase word nobody
lists; "gm" and "thanks bro" stay social; "close the gap and move on" is an
idiom, not a close.
"""
from __future__ import annotations

import pytest

from bot.nlp.intent_router import (
    AMBIGUOUS_TICKER_WORDS,
    IntentRouter,
    _extract_symbol,
    needs_live_market_data,
    symbols_named,
)
from bot.skills.chat_runtime import act_intent_notice


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def route(router, text: str) -> str:
    """The vocabulary the corpus is written in: a skill name, ASK (the router
    matched and could not name one asset), SOCIAL, or MODEL."""
    x = router.classify_rules(text)
    if x.is_social:
        return "SOCIAL"
    if not x.skill:
        return "MODEL"
    return f"ASK:{x.skill}" if x.confidence < 1.0 else x.skill


# ── the corpus ──────────────────────────────────────────────────────────────

ONE_ASSET = [
    # (message, route, symbol the route must carry)
    ("give me a full analysis of BTC", "analyze_asset", "BTC/USDT"),
    ("run a full analysis on bitcoin", "analyze_asset", "BTC/USDT"),
    ("technical analysis of sol", "analyze_asset", "SOL/USDT"),
    ("deep dive on eth", "analyze_asset", "ETH/USDT"),
    ("full analysis btc", "analyze_asset", "BTC/USDT"),
    ("full breakdown of sol please", "analyze_asset", "SOL/USDT"),
    # "dive"/"write-up" with no "deep" in front: the of/on/for rule is the
    # only one that reads these, which a mutation deleting it proved.
    ("write-up on eth", "analyze_asset", "ETH/USDT"),
    ("dive on sol", "analyze_asset", "SOL/USDT"),
    ("can u do a TA on avax", "analyze_asset", "AVAX/USDT"),
    ("chart for doge", "analyze_asset", "DOGE/USDT"),
    ("doge chart", "analyze_asset", "DOGE/USDT"),
    ("eth/usdt 1h chart pls", "analyze_asset", "ETH/USDT"),
    ("explain the btc chart to me", "analyze_asset", "BTC/USDT"),
    ("btc 4h structure", "analyze_asset", "BTC/USDT"),
    ("eth 15m setup", "analyze_asset", "ETH/USDT"),
    ("is BTC bullish", "analyze_asset", "BTC/USDT"),
    ("pepe looking good?", "analyze_asset", "PEPE/USDT"),
    ("what do you think of sui", "analyze_asset", "SUI/USDT"),
    ("whats the play on wif", "analyze_asset", "WIF/USDT"),
    ("why is sol pumping", "analyze_asset", "SOL/USDT"),
    ("what is the rsi on btc", "analyze_asset", "BTC/USDT"),
    ("eth on bybit", "analyze_asset", "ETH/USDT"),
    ("btc on hyperliquid", "analyze_asset", "BTC/USDT"),
    ("$HYPE", "analyze_asset", "HYPE/USDT"),
    ("$PEPE", "analyze_asset", "PEPE/USDT"),
    ("wif/usdt", "analyze_asset", "WIF/USDT"),
    ("BTC", "analyze_asset", "BTC/USDT"),
    # already routed before this slice — the table records them so a rule
    # added for the rows above cannot quietly take them somewhere else
    ("analyse ethereum pls", "analyze_asset", "ETH/USDT"),
    ("chainlink analysis", "analyze_asset", "LINK/USDT"),
    ("hows link looking", "analyze_asset", "LINK/USDT"),
    ("analyze BTC", "analyze_asset", "BTC/USDT"),
    ("scan ETH", "analyze_asset", "ETH/USDT"),
]

NOT_ONE_ASSET = [
    # (message, route) — the red herrings
    ("how does the scan work", "MODEL"),
    ("explain support and resistance", "MODEL"),
    ("what is rsi", "MODEL"),
    ("explain rsi", "MODEL"),
    ("define liquidity", "MODEL"),
    ("explain elliott waves", "MODEL"),
    ("teach me about rsi", "MODEL"),
    ("btc.d", "MODEL"),
    ("look at the link I sent", "MODEL"),
    ("check out the near term", "MODEL"),
    ("hype analysis", "MODEL"),
    ("look at the docs", "MODEL"),
    ("check out my portfolio", "get_portfolio"),
    ("chart analysis", "ASK:analyze_asset"),
    ("gm", "SOCIAL"),
    ("thanks bro", "SOCIAL"),
    ("lol ok", "SOCIAL"),
    ("you there?", "SOCIAL"),
]

TWO_ASSETS = ["analyze btc and eth", "scan btc and sol", "sol or avax which is stronger",
              "btc vs eth", "compare btc and eth", "analyze btc, eth and sol"]

COMPOUND_ACTIONS = ["close my ETH and scan the market", "scan the market and close my eth",
                    "close eth then scan", "flatten everything and tell me my pnl",
                    "exit BTC now and show pnl"]


class TestTheTable:
    @pytest.mark.parametrize("text, skill, symbol", ONE_ASSET)
    def test_one_named_asset_reaches_the_analyzer(self, router, text, skill, symbol):
        x = router.classify_rules(text)
        assert route(router, text) == skill, text
        assert x.kwargs.get("symbol") == symbol, text

    @pytest.mark.parametrize("text, expected", NOT_ONE_ASSET)
    def test_what_is_not_an_asset_read_is_left_alone(self, router, text, expected):
        assert route(router, text) == expected, text

    def test_no_row_of_the_table_is_answered_by_a_model_with_no_chart(self, router):
        # The whole point, stated once over the whole corpus rather than per row.
        assert [t for t, *_ in ONE_ASSET if route(router, t) == "MODEL"] == []


class TestTwoAssets:
    @pytest.mark.parametrize("text", TWO_ASSETS)
    def test_two_assets_are_asked_about_never_half_answered(self, router, text):
        x = router.classify_rules(text)
        assert x.skill == "analyze_asset" and x.confidence == 0.5, text
        assert not x.kwargs.get("symbol"), text
        assert "names 2 assets" in x.explanation or "names 3 assets" in x.explanation, x.explanation

    def test_the_reading_lists_every_asset_in_order(self):
        assert symbols_named("analyze btc and eth") == ["BTC/USDT", "ETH/USDT"]
        assert symbols_named("analyze btc, eth and sol") == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        # RED HERRING: the same asset twice is ONE asset.
        assert symbols_named("btc entry and btc stop") == ["BTC/USDT"]
        assert symbols_named("bitcoin vs BTC/USDT") == ["BTC/USDT"]
        assert symbols_named("") == [] and symbols_named("hello there") == []

    def test_one_asset_still_carries_its_symbol(self, router):
        x = router.classify_rules("analyze btc")
        assert x.confidence == 1.0 and x.kwargs["symbol"] == "BTC/USDT"


class TestTheSymbolReading:
    def test_a_dollar_prefixed_token_is_a_ticker_by_its_spelling(self):
        # It required membership of the known list, so "$HYPE" resolved to
        # nothing and the social gate answered a ticker as small talk.
        assert _extract_symbol("$HYPE") == "HYPE/USDT"
        assert _extract_symbol("thoughts on $TIA") == "TIA/USDT"
        assert _extract_symbol("$BTC") == "BTC/USDT"

    @pytest.mark.parametrize("text, expected", [
        ("look at the link I sent", None),
        ("check out the near term", None),
        ("analyze the link", None),
        ("hows link looking", "LINK/USDT"),
        ("link analysis", "LINK/USDT"),
        ("analyze the LINK", "LINK/USDT"),
        ("$link chart", "LINK/USDT"),
        ("the link/usdt pair", "LINK/USDT"),
        ("near is my biggest position", "NEAR/USDT"),
        # A pair spelling the explicit /USDT branch does not cover: the
        # word scan has to let it through on the slash alone.
        ("the link/usd pair", "LINK/USDT"),
    ])
    def test_a_ticker_that_is_also_an_english_word(self, text, expected):
        assert _extract_symbol(text) == expected, text
        assert symbols_named(text) == ([expected] if expected else []), text

    def test_the_ambiguous_list_is_tickers_the_router_knows(self):
        from bot.nlp.intent_router import _KNOWN_SYMBOLS
        assert AMBIGUOUS_TICKER_WORDS <= _KNOWN_SYMBOLS
        assert "btc" not in AMBIGUOUS_TICKER_WORDS and "eth" not in AMBIGUOUS_TICKER_WORDS


class TestAnActionIsNeverDropped:
    @pytest.mark.parametrize("text", COMPOUND_ACTIONS)
    def test_a_close_joined_to_a_read_answers_the_close(self, router, text):
        x = router.classify_rules(text)
        assert x.skill == "close_position", text
        assert x.kwargs.get("also_asked") is True, text

    def test_the_notice_says_the_other_ask_did_not_run(self):
        for surface in ("telegram", "web"):
            out = act_intent_notice("close", "ETH/USDT", surface=surface, also_asked=True)
            assert "has not been run" in out and "Nothing has been closed" in out
            plain = act_intent_notice("close", "ETH/USDT", surface=surface)
            assert "has not been run" not in plain
        # every kind carries it, including the one with no door at all
        assert "has not been run" in act_intent_notice("modify", None, also_asked=True)
        assert "has not been run" in act_intent_notice("cancel", None, also_asked=True)

    def test_a_lone_close_carries_no_such_claim(self, router):
        x = router.classify_rules("close my eth")
        assert x.skill == "close_position" and not x.kwargs.get("also_asked")

    @pytest.mark.parametrize("text", ["close the gap and move on", "close the app and restart",
                                      "i closed my eth and made 5%"])
    def test_an_idiom_is_not_a_close(self, router, text):
        assert router.classify_rules(text).skill != "close_position", text

    def test_a_halt_joined_to_a_flatten_is_the_door_that_does_both(self, router):
        # /emergency_stop halts AND flattens; the close notice points at the
        # positions card and says nothing about the halt.
        assert router.classify_rules("close all positions and halt").skill == "emergency_stop"
        assert router.classify_rules("halt and close everything").skill == "emergency_stop"


class TestTheDoorsAReadQuestionMustNotOpen:
    """Three doors a read-only question was opening, or not opening at all."""

    @pytest.mark.parametrize("text", ["event risk on eth", "macro risk on sol",
                                      "whats the risk on this trade", "any risk on this setup",
                                      # the DEFENSIVE half has the same shape and the same
                                      # cost one direction over: a read question that
                                      # proposes changing how the agent sizes.
                                      "how do i take risk off the table",
                                      "what takes risk off this position"])
    def test_the_noun_risk_on_is_not_the_stance_risk_on(self, router, text):
        # The stance card PROPOSES trading more aggressively. A read-only
        # question about macro exposure opened it at confidence 1.0.
        assert router.classify_rules(text).skill not in ("stance_aggressive", "stance_defensive"), text

    @pytest.mark.parametrize("text, skill", [
        ("risk on", "stance_aggressive"), ("go risk on", "stance_aggressive"),
        ("risk off", "stance_defensive"), ("switch to risk off", "stance_defensive"),
        ("be more aggressive", "stance_aggressive"), ("reduce the risk", "stance_defensive"),
    ])
    def test_the_stance_itself_still_routes(self, router, text, skill):
        assert router.classify_rules(text).skill == skill, text

    @pytest.mark.parametrize("text", ["remove my stop loss", "delete my stop", "cancel my stop loss",
                                      "take off my sl", "get rid of my take profit",
                                      "turn off the stop", "drop my tp"])
    def test_removing_protection_is_a_modification_with_a_door(self, router, text):
        # It fell to the bare Portfolio keyword rule and came back as the
        # positions card with no sentence — a request to take protection OFF
        # an open position, answered as though it were a request to look.
        assert router.classify_rules(text).skill == "modify_position", text

    @pytest.mark.parametrize("text", ["cancel my order", "cancel all orders", "kill my limit"])
    def test_cancelling_an_order_is_still_the_order_door(self, router, text):
        assert router.classify_rules(text).skill == "cancel_order", text

    @pytest.mark.parametrize("text", ["emergency", "panic", "emergency!", "panic now"])
    def test_a_bare_cry_for_help_meets_the_door(self, router, text):
        # Answered with the door and dispatched nowhere, exactly like a bare
        # "stop"; before this they were greeted as small talk.
        assert router.classify_rules(text).skill == "halt_ambiguous", text

    @pytest.mark.parametrize("text, skill", [("emergency stop", "emergency_stop"),
                                             ("stop", "halt_ambiguous"), ("halt", "halt")])
    def test_the_phrases_around_it_are_unchanged(self, router, text, skill):
        assert router.classify_rules(text).skill == skill, text

    def test_a_panic_that_is_not_a_request_is_left_alone(self, router):
        # RED HERRING: the word inside a sentence is a word.
        assert router.classify_rules("a panic attack about eth").skill != "halt_ambiguous"


class TestTheSocialGate:
    @pytest.mark.parametrize("text", ["win rate", "sharpe ratio", "profit factor", "biggest loser",
                                      "fees this month", "cpi tomorrow?", "15m setups", "api keys",
                                      "connect bitget", "what is rsi", "my drawdown", "open orders"])
    def test_a_short_trading_question_is_not_small_talk(self, router, text):
        assert route(router, text) != "SOCIAL", text

    def test_the_gate_reads_the_analysis_vocabulary_itself(self):
        """Not a copy of it. A term the analysis rules know and the gate does
        not is a three-word chart question answered with a greeting, and it
        is invisible from either side alone — so there is one list."""
        from bot.nlp import intent_router as ir
        assert set(ir._ANALYSIS_WORDS) <= {"elliott", "wave", "waves"} | set(ir._ANALYSIS_WORDS)
        for word in ("elliott", "rsi", "vwap", "structure", "momentum"):
            assert word in ir._ANALYSIS_WORDS
            assert not ir._is_social_message(f"{word} please"), word
        # the phrases stay patterns, and the pattern is built from both
        assert "fair\\s?value\\s?gaps?" in ir._ANALYSIS_TERMS
        assert all(w in ir._ANALYSIS_TERMS for w in ir._ANALYSIS_WORDS)

    @pytest.mark.parametrize("text", ["gm", "hey", "thanks", "thank you", "lol", "ok cool",
                                      "bye", "good morning", "you there?", "how are you"])
    def test_small_talk_is_still_small_talk(self, router, text):
        assert route(router, text) == "SOCIAL", text


class TestThePublicSurface:
    @pytest.mark.parametrize("text", [t for t, *_ in ONE_ASSET])
    def test_every_asset_read_is_gated_on_the_surface_with_no_feed(self, text):
        # `needs_live_market_data` reads the router, so a rule that routes a
        # phrasing to the analyzer also closes the public gate on it — the
        # visitor gets a refusal that names the reason instead of a model
        # answering about a chart nobody fetched.
        assert needs_live_market_data(text), text

    @pytest.mark.parametrize("text", ["what is rsi", "explain support and resistance",
                                      "how does the scan work"])
    def test_education_is_still_answerable_for_a_visitor(self, text):
        assert not needs_live_market_data(text), text

"""Symbol-first scan phrasing must reach the real analyzer, not freeform chat.

The web widget's quick action sends "BTC scan" — symbol BEFORE the verb. The
router had a rule for "scan BTC" and nothing for the reversed shape, so the
message matched no rule at all and fell through to the chat LLM, which
confidently published a complete "BTC/USDT Scan — Full Analysis" around an
invented price ($93.06, observed live on the site widget on 2026-08-23). The
same fall-through produced the fabricated elliott-wave read on Telegram; both
surfaces share ``classify_rules``.

THE ROUTE IS THE GROUNDING. A scan that reaches ``analyze_asset`` is built
from live indicators. A scan that reaches the chat model is fiction in the
format of a measurement, and the two are indistinguishable to the reader —
which is the same shape as every other defect this repo's guard tests exist
to prevent.

``_extract_symbol`` was already correct: it resolved BTC/USDT from "BTC scan"
the whole time. Only the routing rule was missing, so the fix is a rule that
ASKS it rather than a pattern that re-decides what a ticker looks like.

The red herring these tests are built around is the leading word. "portfolio
scan", "wallet scan" and "technical analysis" are the same SHAPE as "BTC
scan", and a rule that reads the first word as a symbol turns each of them
into an analysis of PORTFOLIO/USDT, WALLET/USDT, TECHNICAL/USDT — which is
the fabrication defect one level up, dressed as its own fix.
"""

import pytest

from bot.nlp.intent_router import (
    IntentRouter,
    _extract_symbol,
    needs_live_market_data,
)


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


class TestSymbolFirstScanRouting:
    def test_symbol_before_scan_routes_to_analyzer(self, router):
        for text in ["BTC scan", "btc scan", "ETH scan", "sol scan"]:
            result = router.classify_rules(text)
            assert result.matched, f"'{text}' must match a rule, got fall-through"
            assert result.skill == "analyze_asset", (
                f"'{text}' must route to analyze_asset (live indicators), "
                f"got {result.skill}")
            # Confidence 1.0 with the symbol resolved — a 0.5 "which asset?"
            # would mean the rule matched but the ticker did not land, and the
            # user would be asked to name the coin they just named.
            assert result.confidence == 1.0, result.explanation
            assert result.kwargs.get("symbol") == _extract_symbol(text)

    def test_symbol_before_analysis_routes_to_analyzer(self, router):
        for text in ["BTC analysis", "eth analysis"]:
            result = router.classify_rules(text)
            assert result.matched and result.skill == "analyze_asset", \
                f"'{text}' -> {result.skill if result.matched else 'NO MATCH'}"
            assert result.kwargs.get("symbol")

    def test_an_unlisted_ticker_still_routes_in_both_orders(self, router):
        # HYPE is not in _KNOWN_SYMBOLS. The extractor admits it because it is
        # ALL-CAPS next to a command word, and "analysis" had to be added to
        # that command-word set for the symbol-first order to work — verb-first
        # already did.
        for text in ["scan HYPE", "HYPE scan", "HYPE analysis"]:
            result = router.classify_rules(text)
            assert result.matched and result.skill == "analyze_asset", text
            assert result.kwargs.get("symbol") == "HYPE/USDT", text

    def test_verb_first_still_routes(self, router):
        result = router.classify_rules("scan BTC")
        assert result.matched and result.skill == "analyze_asset"

    def test_mode_scans_keep_their_routes(self, router):
        # RED HERRING: these END in "scan" but the leading word is a scan MODE,
        # not a symbol. They are registered ahead of the symbol-first rule and
        # must keep winning.
        for text, want in [
            ("deep scan", "scan_deep"),
            ("full scan", "scan_full"),
            ("quick scan", "scan_scalp"),
            ("market scan", "scan_market"),
            ("swing scan", "scan_swing"),
            ("intraday scan", "scan_intraday"),
            ("scan", "scan_market"),
            ("scan the market", "scan_market"),
        ]:
            result = router.classify_rules(text)
            assert result.matched and result.skill == want, (
                f"'{text}' must stay on {want}, got "
                f"{result.skill if result.matched else 'NO MATCH'}")

    def test_a_non_asset_leading_word_is_not_read_as_a_ticker(self, router):
        # THE REGRESSION THIS RULE IS EASIEST TO CAUSE. Each of these is
        # "<word> scan/analysis" — identical in shape to "BTC scan" — and none
        # of the words is a ticker. The first draft of this fix used an
        # exclusion list in the pattern and turned every one of them into
        # analyze_asset; "portfolio scan" stopped reaching get_portfolio.
        #
        # A denylist cannot win this: the excluded set is every English noun
        # and the admitted set is tickers. The rule defers to _extract_symbol
        # instead, and leaves the leading word outside its own match so
        # _names_a_non_asset can rule on it.
        assert router.classify_rules("portfolio scan").skill == "get_portfolio"
        for text in ["wallet scan", "token scan", "security scan", "whale scan",
                     "news scan", "sector scan", "airdrop scan",
                     "technical analysis", "risk analysis", "sentiment analysis"]:
            result = router.classify_rules(text)
            assert not result.matched or result.skill != "analyze_asset", (
                f"'{text}' routed to analyze_asset — the leading word was read "
                f"as a ticker")
            assert not result.kwargs.get("symbol"), (
                f"'{text}' resolved a symbol nobody named: "
                f"{result.kwargs.get('symbol')}")

    def test_education_question_not_hijacked(self, router):
        # Questions ABOUT scanning, not requests FOR one.
        for text in ["how does the scan work", "what is a scan",
                     "explain how scans work"]:
            result = router.classify_rules(text)
            assert (not result.matched) or result.skill != "analyze_asset", \
                f"'{text}' wrongly routed to analyze_asset"

    def test_a_trailing_scan_word_does_not_capture_another_intent(self, router):
        # The rule is anchored to the end of the message, so it sees these —
        # and must still decline, because the leftover words name something
        # else. Without that, any sentence mentioning a coin and ending in
        # "scan" would become an asset analysis.
        result = router.classify_rules("close my BTC position after the scan")
        assert not result.matched or result.skill != "analyze_asset", \
            f"a close request became an analysis: {result.skill}"

    def test_missing_asset_asks_rather_than_inventing_one(self, router):
        # "chart analysis" leaves only filler behind, so the honest answer is
        # to ask WHICH asset — confidence 0.5, no symbol. Never a ticker built
        # out of the word "chart".
        for text in ["chart analysis", "analysis"]:
            result = router.classify_rules(text)
            if result.matched:
                assert result.confidence == 0.5, text
                assert not result.kwargs.get("symbol"), (
                    f"'{text}' invented {result.kwargs.get('symbol')}")


class TestPublicLiveDataGate:
    """The anonymous path refuses live-market asks BEFORE the model runs.

    The public system prompt already forbade stating live prices and the model
    was watched ignoring it. An instruction is advisory, so the gateway now
    returns early instead — and the predicate is exercised here directly
    rather than through a copy of its pattern. A test that re-declares the
    regex passes while the real one drifts, which is the failure mode this
    file was written about in the first place.
    """

    def test_scan_shapes_are_gated(self):
        for text in ["BTC scan", "scan BTC", "eth analysis", "analyze SOL",
                     "HYPE scan", "deep scan", "market scan", "ETH levels"]:
            assert needs_live_market_data(text), f"'{text}' must hit the gate"

    def test_price_asks_are_gated(self):
        # The router does NOT route these — measured, not assumed. A gate
        # built only on classify_rules would have let every one through.
        for text in ["current price of bitcoin", "price of ETH", "btc price",
                     "what is bitcoin worth", "how much is ETH"]:
            assert needs_live_market_data(text), f"'{text}' must hit the gate"
            assert not IntentRouter().classify_rules(text).matched, (
                f"'{text}' now routes — fold it into _LIVE_MARKET_SKILLS and "
                f"drop it from the price branch")

    def test_education_stays_answerable_for_visitors(self):
        # Gating these would make the public chat useless: none of them needs
        # a number.
        for text in ["what is a stop loss", "how does funding work",
                     "explain leverage", "what is RUNECLAW",
                     "how does the scan work", "what is a scan",
                     "how do i sign up", "what fees do you charge"]:
            assert not needs_live_market_data(text), f"'{text}' must NOT be gated"

    def test_the_gate_runs_before_the_model(self):
        # Ordering is the whole property: after the LLM call it is decoration.
        import inspect
        from bot.web import user_gateway
        src = inspect.getsource(user_gateway.handle_public_chat)
        assert "needs_live_market_data" in src, (
            "handle_public_chat lost its structural gate — the public path is "
            "back to trusting the model's instructions about prices")
        assert src.index("needs_live_market_data") < src.index("_llm_chat"), (
            "the gate must run BEFORE the LLM call")

    def test_the_gate_and_the_router_cannot_drift_apart(self):
        # One source, so a new route is a new refusal for free. If the gate
        # ever grows its own pattern for a shape the router already knows,
        # this is where that shows up.
        from bot.nlp.intent_router import _LIVE_MARKET_SKILLS
        r = IntentRouter()
        for text in ["BTC scan", "eth analysis", "scan BTC", "deep scan"]:
            result = r.classify_rules(text)
            assert result.skill in _LIVE_MARKET_SKILLS
            assert needs_live_market_data(text)

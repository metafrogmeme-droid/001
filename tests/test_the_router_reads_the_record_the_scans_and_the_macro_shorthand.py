"""The second half of the 153-phrase corpus: the record, the scans, the macro
shorthand and the order phrasings.

The first half (`test_the_router_reads_what_a_trader_types.py`) was about ONE
ASSET. These four families were filed, not fixed, and each had the same
shape: a card that answers the question exists, is registered and is
permissioned, and the phrasing a trader actually types reached nothing —
the social gate, or a model with no tool — or reached a DIFFERENT card:

  15m setups                     -> the chat model        (pro_scan's ladder exists)
  cpi tomorrow?                  -> small talk            (macro_calendar exists)
  is macro cutting size          -> the chat model        (macro_brief exists)
  do i have any pending limits   -> the chat model        (get_orders exists)
  hows my pnl looking            -> "which coin?"         (the chart rule's "how's … looking")
  am i close to the daily loss limit -> the positions card (the keyword rule's `loss`)
  show me my trade history       -> the positions card    (`my trades`, fifty lines above the journal's rule)
  whats the average win rate for swing trading -> a real 4h ProScan, behind the paywall

Every `expected` below was decided against the card that would answer, not
against the phrase: a profit factor or a Sharpe over the caller's record is
printed by no surface and stays with the model; "how am i doing this week"
names a window no card filters to; "weekly scan" names a ladder this scanner
does not run; "quick" is how fast the answer is wanted, not a five-minute
timeframe; "should I cancel my order?" keeps the listing because the decision
is the caller's and the listing is what it is made from. Education and
opinion — "what is a limit order", "what does cpi mean", "is fomc priced in",
"rate cut odds" — stay with the model. Every `current` in the header was
recorded by running the router, and the tables were re-driven before this
file was written, because the first draft of the first half wrote a number it
could not reproduce.
"""
from __future__ import annotations

import pytest

from bot.nlp.intent_router import (
    JOURNAL_SUPERLATIVE_COUNT,
    IntentRouter,
    journal_count,
    routed_skill_names,
)
from bot.nlp.skill_doors import dispatch_kwargs, dispatches_to


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


# ── the record ───────────────────────────────────────────────────────────────

RECORD = [
    ("whats my win rate", "get_portfolio"),
    ("win rate", "get_portfolio"),
    ("what percent of my trades win", "get_portfolio"),
    ("whats my pnl", "get_portfolio"),
    ("hows my pnl looking", "get_portfolio"),
    ("am i up or down today", "get_portfolio"),
    ("whats my current unrealized pnl", "get_portfolio"),
    ("hows my eth position doing", "get_portfolio"),
    ("show me my trade history", "trade_journal"),
    ("my last 10 trades", "trade_journal"),
    ("recent trades", "trade_journal"),
    ("what was my worst loss", "trade_journal"),
    ("my biggest loser", "trade_journal"),
    ("hows my drawdown", "check_risk"),
    ("whats my max drawdown", "check_risk"),
    ("whats my exposure", "check_risk"),
    ("how much can i lose before the bot halts", "check_risk"),
    ("am i close to the daily loss limit", "check_risk"),
    ("am i overexposed", "check_risk"),
    ("review my last trade", "trade_postmortem"),
    ("break down my BTC loss", "trade_postmortem"),
    ("why did i lose so much on eth", "trade_postmortem"),
    ("how much did we make on the eth trade", "trade_postmortem"),
    ("how much am i spending on the llm", "costs"),
    ("why no trade today", "whynot"),
    ("biggest losers today", "scan_market"),
    ("how is eth doing", "analyze_asset"),
    # what no card answers, and what is education
    ("my profit factor", "MODEL"),
    ("whats my sharpe", "MODEL"),
    ("how am i doing this week", "MODEL"),
    ("how much did i make yesterday", "MODEL"),
    ("whats the win rate of this strategy in general", "MODEL"),
    ("what is a good profit factor", "MODEL"),
    ("whats the average win rate for swing trading", "MODEL"),
    ("performance", "MODEL"),
]

# ── the scans ────────────────────────────────────────────────────────────────

SCANS = [
    ("15m setups", "scan_intraday"),
    ("any 30m setups", "scan_intraday"),
    ("1h scan", "scan_intraday"),
    ("2h scan", "scan_intraday"),
    ("daily setups", "scan_intraday"),
    ("hourly ideas", "scan_intraday"),
    ("intraday plays", "scan_intraday"),
    ("intraday scan", "scan_intraday"),
    ("give me 3 setups", "scan_intraday"),
    ("best setups right now", "scan_intraday"),
    ("anything worth trading today", "scan_intraday"),
    ("what setups do you see", "scan_intraday"),
    ("show me setups", "scan_intraday"),
    ("4h setups", "scan_swing"),
    ("swing ideas", "scan_swing"),
    ("any good swings", "scan_swing"),
    ("swing scan", "scan_swing"),
    ("5m scan", "scan_scalp"),
    ("any scalps?", "scan_scalp"),
    ("scalp scan", "scan_scalp"),
    ("quick scan", "scan_market"),
    ("top movers", "scan_market"),
    ("top gainers", "scan_market"),
    ("scan", "scan_market"),
    ("run a scan", "scan_market"),
    ("scann the market", "scan_market"),
    ("market scan", "scan_market"),
    ("scan everything", "scan_deep"),
    ("deep scan", "scan_deep"),
    ("full scan with patterns", "scan_full"),
    ("full scan", "scan_full"),
    # The verb-first spelling of a ladder. Kept in THIS table as well as in
    # its own suite because destination is decided by rule ORDER, which is
    # invisible from any one rule: "scan the 15m" reached `analyze_asset`
    # with no symbol until the mode rules learned the scanner's own verb, and
    # nothing but a table of phrases shows that.
    # (tests/test_the_scanner_takes_its_own_verb.py is the full family.)
    ("scan 4h", "scan_swing"),
    ("scan the 15m", "scan_intraday"),
    ("scan for scalps", "scan_scalp"),
    ("scan the market on 4h", "scan_swing"),
    ("1d scan", "MODEL"),
    ("1h chart of btc", "analyze_asset"),
    ("scan btc", "analyze_asset"),
    ("deep scan eth", "analyze_asset"),
    ("eth 15m setup", "analyze_asset"),
    ("scan my portfolio", "get_portfolio"),
    ("is the scanner still running", "status"),
    # education, and not a scan at all
    ("weekly scan", "MODEL"),
    ("how does the scanner work", "MODEL"),
    # Pinned to the model while Telegram had no wallet read; the wallet mirror
    # is a command now and the website answers these words with its wallet
    # card, so "scan my wallet" is the wallet card here too — still not a scan.
    ("scan my wallet", "wallet"),
    ("scan the docs", "MODEL"),
]

# ── the macro shorthand ──────────────────────────────────────────────────────

MACRO = [
    ("cpi tomorrow?", "macro_calendar"),
    ("when is fomc", "macro_calendar"),
    ("nfp this friday", "macro_calendar"),
    ("any news events today", "macro_calendar"),
    ("whats on the calendar this week", "macro_calendar"),
    ("fed decision", "macro_calendar"),
    ("economic calendar", "macro_calendar"),
    ("whens the next fed meeting", "macro_calendar"),
    ("next macro event", "macro_calendar"),
    ("pce print", "macro_calendar"),
    ("ppi tomorrow", "macro_calendar"),
    ("jobs report", "macro_calendar"),
    ("unemployment numbers friday", "macro_calendar"),
    ("fomc minutes tonight?", "macro_calendar"),
    ("did cpi come out yet", "macro_calendar"),
    ("cpi data", "macro_calendar"),
    ("macro", "macro_calendar"),
    ("is macro cutting size", "macro_brief"),
    ("is macro cutting my size right now", "macro_brief"),
    ("is there a blackout window", "macro_brief"),
    ("is macro blocking entries rn", "macro_brief"),
    ("whats the macro risk state", "macro_brief"),
    ("macro brief", "macro_brief"),
    ("how much size am i allowed with fomc coming", "macro_brief"),
    ("should i sit out before cpi", "macro_brief"),
    ("hold off til after fomc?", "macro_brief"),
    ("is the bot pausing for the fed meeting", "macro_brief"),
    ("event risk on eth", "check_event_risk"),
    ("any macro risk for sol today", "check_event_risk"),
    ("is it safe to trade btc with cpi coming", "check_event_risk"),
    ("why is the bot not trading, is it macro", "whynot"),
    ("why isnt the bot trading is it the fed", "whynot"),
    ("why no trade today cpi?", "whynot"),
    ("scan CPIX", "analyze_asset"),
    ("should i long eth before cpi", "analyze_asset"),
    # education and opinion
    ("what does cpi mean", "MODEL"),
    ("explain what nfp is", "MODEL"),
    ("fed up with this market", "MODEL"),
    ("cpi is a lagging indicator right", "MODEL"),
    ("how does the macro gate work", "MODEL"),
    ("is fomc priced in", "MODEL"),
    ("rate cut odds", "MODEL"),
]

# ── the orders ───────────────────────────────────────────────────────────────

ORDERS = [
    ("do i have any pending limits", "get_orders"),
    ("did my btc order fill", "get_orders"),
    ("any resting orders on eth", "get_orders"),
    ("is my limit still open", "get_orders"),
    ("any open limits on sol", "get_orders"),
    ("whats pending", "get_orders"),
    ("should i cancel my order or let it run", "get_orders"),
    ("cancel all orders", "cancel_order"),
    ("cancel my order", "cancel_order"),
    ("pull my eth limit", "cancel_order"),
    ("kill that order", "cancel_order"),
    ("remove my stop loss", "modify_position"),
    ("walk me through my last trade", "trade_postmortem"),
    ("run a backtest on sol", "run_backtest"),
    ("backtest btc last month", "run_backtest"),
    ("run a backtest", "run_backtest"),
    # education, and the research surface that is not wired on purpose
    ("what is a limit order", "MODEL"),
    ("how do limit orders work", "MODEL"),
    ("order of operations", "MODEL"),
    ("in order to trade well", "MODEL"),
    ("walk forward test", "MODEL"),
    ("run a walk-forward", "MODEL"),
    ("can you run a walk forward validation", "MODEL"),
    ("what is a walk forward test", "MODEL"),
    ("optimize my strategy", "MODEL"),
    ("optimise the params", "MODEL"),
    ("show me the token optimizer stats", "MODEL"),
    ("how does backtesting work", "MODEL"),
]


class TestTheTables:
    @pytest.mark.parametrize("text, expected", RECORD + SCANS + MACRO + ORDERS,
                             ids=[t for t, _ in RECORD + SCANS + MACRO + ORDERS])
    def test_each_phrase_reaches_the_card_that_answers_it(self, router, text, expected):
        assert route(router, text) == expected, text

    def test_nothing_in_the_tables_is_greeted(self, router):
        """The social gate called "15m setups", "cpi tomorrow?" and "jobs
        report" small talk. A phrase a trader types about the product is
        never a greeting, whether or not a rule answers it."""
        for text, _ in RECORD + SCANS + MACRO + ORDERS:
            assert route(router, text) != "SOCIAL", text

    def test_every_card_the_tables_name_is_a_registered_router_skill(self):
        named = {s for _, s in RECORD + SCANS + MACRO + ORDERS if s != "MODEL"}
        assert named <= routed_skill_names(), named - routed_skill_names()


class TestWhatRidesWithTheRoute:
    def test_the_journal_carries_the_count_the_question_named(self, router):
        assert router.classify_rules("my last 10 trades").kwargs == {"count": 10}
        assert router.classify_rules("show me my last 25 closes").kwargs == {"count": 25}
        assert router.classify_rules("recent trades").kwargs == {}
        # A superlative needs a window wide enough to hold it: the card ranks
        # nothing, it prints the last N with their P&L under a header that
        # says N.
        assert router.classify_rules("what was my worst loss").kwargs == {"count": JOURNAL_SUPERLATIVE_COUNT}
        assert router.classify_rules("my biggest loser").kwargs == {"count": JOURNAL_SUPERLATIVE_COUNT}

    def test_the_count_reader_alone(self):
        assert journal_count("my last 10 trades") == 10
        assert journal_count("last 3 closes") == 3
        assert journal_count("the last 999 trades") == 200, "bounded"
        assert journal_count("last 0 trades") == 1, "bounded below"
        assert journal_count("best trade") == JOURNAL_SUPERLATIVE_COUNT
        assert journal_count("recent trades") is None
        assert journal_count("") is None and journal_count(None) is None

    def test_the_post_mortem_and_the_event_risk_carry_the_asset(self, router):
        assert router.classify_rules("why did i lose so much on eth").kwargs == {"symbol": "ETH/USDT"}
        assert router.classify_rules("how much did we make on the eth trade").kwargs == {"symbol": "ETH/USDT"}
        assert router.classify_rules("event risk on eth").kwargs == {"symbol": "ETH/USDT"}
        assert router.classify_rules("is it safe to trade btc with cpi coming").kwargs == {"symbol": "BTC/USDT"}
        assert router.classify_rules("deep scan eth").kwargs == {"symbol": "ETH/USDT"}

    def test_a_timeframe_names_the_ladder_and_the_ladder_names_its_mode(self, router):
        """The intent's OWN NAME anchors the mode (`scan_<mode>` runs
        `mode=<mode>`), so a phrase that says 4h reaches the swing ladder and
        one that says 15m the intraday one — through the one dispatch table."""
        for text, mode in (("4h setups", "swing"), ("15m setups", "intraday"),
                           ("hourly ideas", "intraday"), ("5m scan", "scalp"),
                           ("any scalps?", "scalp"), ("daily setups", "intraday")):
            intent = router.classify_rules(text).skill
            assert intent == f"scan_{mode}", text
            assert dispatches_to(intent) == "pro_scan" and dispatch_kwargs(intent) == {"mode": mode}, text


class TestTheDecoys:
    """Phrases that CONTAIN a routed word and are not the request."""

    @pytest.mark.parametrize("text, expected", [
        # the bare mode words no longer match inside a sentence
        ("i want to learn swing trading", "MODEL"),
        ("scalping is too stressful for me", "MODEL"),
        ("whats the average win rate for swing trading", "MODEL"),
        # a timeframe beside an asset is a read of the asset
        ("eth 15m setup", "analyze_asset"),
        ("btc 4h structure", "analyze_asset"),
        # "quick"/"fast" are not timeframes
        ("quick scan", "scan_market"),
        ("fast scan", "scan_market"),
        # the education shapes of the orders and macro vocabularies
        ("what is a limit order", "MODEL"),
        ("what does cpi mean", "MODEL"),
        ("how does the macro gate work", "MODEL"),
        # the model's opinions
        ("is fomc priced in", "MODEL"),
        ("rate cut odds", "MODEL"),
        # a profit FACTOR is printed by no card; a profit is the book's
        ("my profit factor", "MODEL"),
        ("my profit today", "get_portfolio"),
    ])
    def test_a_decoy_keeps_its_destination(self, router, text, expected):
        assert route(router, text) == expected, text

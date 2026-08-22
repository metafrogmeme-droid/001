"""Free text that names an asset must reach the chart, or say it did not.

Two defects, found by driving the router over realistic phrasings rather than
by reading it.

ONE — SYMBOL-FIRST PHRASING NEVER MATCHED. Every analyze rule assumed the verb
comes first ("analyze BTC", "scan ETH"), and those already worked. People type
the asset first at least as often, and none of it matched:

    BTC elliott waves · eth elliott wave count · BTC rsi · ETH macd
    BTC fibonacci · SOL setup · check BTC · what about BTC

Eight of eighteen realistic phrasings, all of them symbol-first, falling
through to the tool-less chat model — which answers anyway, about a chart it
has not read.

TWO — AND THIS IS THE WORSE ONE. `_handle_message` did

    skill = self.registry.get(intent.skill)
    if skill:
        ...dispatch...
        return

with NO else. A confident intent whose skill is not registered fell past every
branch below it and into the AI chat fallback at the bottom of the function.
Typing "status" does exactly that: it classifies at confidence 1.0, `status` is
not a registered skill, and the reply is a language model's impression of
whether the engine is running. "help" is the same.

That is the house rule at its plainest — an unavailable tool is not a
measurement — and it was reached by the two words a new user is most likely to
type first.

The fix has three parts and this file covers all three: the symbol-first rules,
help/status wired to the commands that already existed, and an honest notice
for anything else the router names but cannot run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from bot.formatters.onboarding import skill_unavailable_notice
from bot.nlp.intent_router import IntentRouter
from bot.nlp.skill_memory import skill_failure_memory, skill_unavailable_memory
from bot.skills.skill_registry import build_default_registry

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


def _skill(router, text):
    i = router.classify_rules(text)
    return i.skill, i.confidence


# ── 1. Routing ────────────────────────────────────────────────────────────

SYMBOL_FIRST = [
    "BTC elliott waves", "eth elliott wave count", "SOL elliott",
    "btc wave analysis", "BTC rsi", "ETH macd", "BTC fibonacci",
    "SOL setup", "BTC support", "ETH levels", "SOL trend", "BTC/USDT rsi",
    "btc ta", "ETH order blocks", "SOL liquidity", "BTC breakout",
]
VERB_FIRST = [
    "scan BTC", "analyze ETH", "analyse SOL", "look at ETH",
    "scan solana", "analyze bitcoin",
]
BARE_ENQUIRY = ["check BTC", "what about BTC", "thoughts on SOL", "how about ETH"]


@pytest.mark.parametrize("text", SYMBOL_FIRST + VERB_FIRST + BARE_ENQUIRY)
def test_an_asset_request_routes_to_the_chart(router, text):
    skill, conf = _skill(router, text)
    assert skill == "analyze_asset", f"{text!r} routed to {skill!r}, not the chart"
    assert conf >= 0.8, (
        f"{text!r} matched at {conf}, below the 0.8 the handler dispatches on — "
        "it would ask 'which coin?' instead of answering")


# Each of these is a real thing someone types, and NONE of them is a request to
# read a chart. The stop list in the bare-enquiry rule exists because the first
# draft answered "how about tomorrow" with "which asset do you want?".
NOT_AN_ASSET = [
    "how about tomorrow", "what about today", "check the market",
    "thoughts on it", "what about everything", "how about now",
    "what about this", "anything on news", "what about you",
    "check my portfolio", "my positions", "scan the market",
    "hello there", "thanks a lot", "stop trading",
    "what is elliott wave theory",
]


@pytest.mark.parametrize("text", NOT_AN_ASSET)
def test_ordinary_english_is_not_an_asset_request(router, text):
    skill, _ = _skill(router, text)
    assert skill != "analyze_asset", (
        f"{text!r} was read as a request to analyse an asset. A rule that "
        "matches too much is not a better rule — it turns every stray sentence "
        "into 'which coin do you want me to look at?'")


def test_the_scan_and_market_rules_still_win_where_they_should(router):
    """The new rules are anchored so they cannot shadow the existing ones."""
    assert _skill(router, "scan the market")[0] == "scan_market"
    assert _skill(router, "what's moving")[0] == "scan_market"
    assert _skill(router, "my portfolio")[0] == "get_portfolio"


# ── 2. Every skill the router can name is reachable ───────────────────────

def _emitted_skills() -> set[str]:
    """Skills the rule table can produce, read from the calls themselves.

    AST, not a regex: the first attempt at this used one and found 3 of 24,
    which would have reported the gap below as already closed.
    """
    tree = ast.parse((REPO / "bot" / "nlp" / "intent_router.py").read_text())
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_rule"
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)):
            out.add(node.args[1].value)
    return out


def test_the_extractor_sees_the_rule_table():
    # A checker that finds nothing reports perfect coverage.
    emitted = _emitted_skills()
    assert len(emitted) >= 20, f"only {len(emitted)} skills parsed out of the rule table"
    assert {"analyze_asset", "scan_market", "get_portfolio"} <= emitted


def test_no_intent_can_fall_through_to_the_chat_model():
    """Registered, or handled by name in the free-text branch. Never neither.

    This is the guard for defect two. It is a source check on purpose: the
    question is whether every branch EXISTS, and no unit test can drive a
    Telegram update through a 13k-line handler for 24 skills.
    """
    registry = build_default_registry()
    handler = (REPO / "bot" / "skills" / "telegram_handler.py").read_text()

    # The handler ALSO dispatches by prefix — `intent.skill.startswith("stance_")`
    # sends all three stances to _propose_stance. The first draft of this check
    # matched literal names only and reported those three unreachable, which is
    # the accusation-manufacturing failure this repo names outright. Read the
    # prefixes out of the handler rather than exempting the names.
    prefixes = re.findall(r'intent\.skill\.startswith\(\s*["\']([\w_]+)["\']', handler)
    assert prefixes, "no prefix dispatch found — the handler changed shape"

    unreachable = []
    for skill in sorted(_emitted_skills()):
        if registry.get(skill) is not None:
            continue
        # Handled by name in the handler: an `intent.skill == "x"` branch or a
        # membership dict like `scan_modes`…
        if f'"{skill}"' in handler:
            continue
        # …or by one of the prefix branches above.
        if any(skill.startswith(p) for p in prefixes):
            continue
        unreachable.append(skill)

    assert unreachable == [], (
        "These intents classify with confidence but reach no code: "
        f"{unreachable}. Before the else-branch existed they fell into the AI "
        "chat fallback, which has no tools and answers anyway.")


# ── 3. What the user is told when it cannot run ───────────────────────────

def test_the_notice_says_understood_and_not_run():
    out = skill_unavailable_notice("analyze_asset")
    low = out.lower()
    assert "understood" in low, "collapsing this into 'I didn't get that' is a lie"
    assert "not available" in low
    # The point of the whole change: no answer is manufactured.
    assert "not going to guess" in low


def test_the_notice_only_suggests_a_command_that_exists():
    assert "/analyze BTC" in skill_unavailable_notice("analyze_asset")
    # An intent with no mapped alternative gets NO suggestion rather than an
    # invented one — a wrong command is worse than none.
    out = skill_unavailable_notice("some_skill_nobody_wired")
    assert "Closest thing" not in out
    assert "/" not in out.replace("</b>", "").replace("<b>", "")


def test_unavailable_and_failed_are_different_memories():
    """A tool that errored was reached; one that is absent never was.

    Recording them identically invites a later turn to suggest a retry for a
    thing that does not exist.
    """
    a = skill_unavailable_memory("status")
    b = skill_failure_memory("status")
    assert a != b
    assert "UNAVAILABLE" in a and "FAILED" in b
    for m in (a, b):
        assert "Nothing was measured" in m or "was never run" in m


def test_the_memory_carries_no_internals():
    # Memory feeds the model and the model writes to a user.
    m = skill_unavailable_memory("check_risk")
    assert "Traceback" not in m and "Error" not in m


# ── 4. help and status reach the real commands ────────────────────────────

@pytest.mark.parametrize("text,skill", [
    ("help", "help"), ("how do i use this", "help"),
    ("status", "status"), ("system status", "status"),
])
def test_help_and_status_still_classify(router, text, skill):
    assert _skill(router, text)[0] == skill


def test_help_and_status_dispatch_to_their_commands():
    """Wired to the commands that already existed, not to the notice.

    "That tool is not available" would be true and useless for these two: the
    commands are right there. The honest notice is the backstop for skills with
    no equivalent, not the answer for the two words a new user types first.
    """
    handler = (REPO / "bot" / "skills" / "telegram_handler.py").read_text()
    free_text = handler[handler.index("intent = self.intent_router.classify_rules(text)"):]
    free_text = free_text[:free_text.index("# ── Fallback: AI chat")]
    for skill, cmd in (("help", "_cmd_help"), ("status", "_cmd_status")):
        assert f'intent.skill == "{skill}"' in free_text, f"{skill} has no branch"
        assert cmd in free_text, f"{skill} does not reach {cmd}"


def test_the_unavailable_branch_is_before_the_chat_fallback():
    """Placement is the whole fix — after the fallback it would never run."""
    handler = (REPO / "bot" / "skills" / "telegram_handler.py").read_text()
    notice = handler.index("skill_unavailable_notice(")
    fallback = handler.index("# ── Fallback: AI chat")
    assert notice < fallback, (
        "the unavailable notice sits after the AI chat fallback, so the chat "
        "model answers first and the notice is dead code")


# ── 5. The extractor must not invent a ticker ─────────────────────────────
#
# The rule `\b(analy[sz]e|look at|check out|...)` is broad on purpose, and
# `_extract_symbol`'s last-resort fallback turned whatever noun followed it
# into a symbol. Its own comment promised "2-10 UPPERCASE letters" and then
# tested `word.isalpha()` against a list built from `text.lower()` — case was
# destroyed two lines before it was checked, so the uppercase guard never ran
# and a hand-maintained denylist of English words was all that stood between a
# user and a fabricated asset. It lost, at confidence 1.0:
#
#     look at the docs        -> DOCS/USDT
#     analyze the situation   -> SITUATION/USDT
#     check out the news      -> OUT/USDT     (the verb's own particle)
#     check out my portfolio  -> OUT/USDT     (a portfolio request, analysed
#                                              as a coin called OUT)
#
# A denylist of English nouns can only ever lose that race. Case is the signal
# the comment always claimed to use, so it is the signal now.

MANUFACTURED = [
    ("look at the docs", "DOCS"), ("analyze the situation", "SITUATION"),
    ("look at the code", "CODE"), ("analyze the results", "RESULTS"),
    ("check out the news", "OUT"), ("check out my portfolio", "OUT"),
    ("scan the logs", "LOGS"), ("analyze the deployment", "DEPLOYMENT"),
]


@pytest.mark.parametrize("text,invented", MANUFACTURED)
def test_no_ticker_is_manufactured_from_a_noun(router, text, invented):
    i = router.classify_rules(text)
    got = i.kwargs.get("symbol")
    assert got != f"{invented}/USDT", (
        f"{text!r} produced {got} — a symbol nobody named, at confidence "
        f"{i.confidence}. That dispatches a real analysis against a fabricated "
        "asset.")
    assert got is None, f"{text!r} produced a symbol at all: {got}"


@pytest.mark.parametrize("text", [t for t, _ in MANUFACTURED
                                  if t != "check out my portfolio"])
def test_a_message_about_something_else_is_not_claimed(router, text):
    """Not merely 'no symbol' — not an asset request at all.

    Without this the fix would stop at confidence 0.5 and every one of these
    would be answered "what coin do you want me to look at?". True, and an
    answer to a question nobody asked.
    """
    i = router.classify_rules(text)
    assert not (i.matched and i.confidence >= 0.5), (
        f"{text!r} is still claimed as {i.skill!r} at {i.confidence}")


def test_a_portfolio_question_reaches_the_portfolio(router):
    # `check out my portfolio` used to resolve to OUT/USDT. The fix declines
    # the analyze rule and CONTINUES rather than returning, so the portfolio
    # rule further down gets its turn — which is why it is a `continue`.
    assert router.classify_rules("check out my portfolio").skill == "get_portfolio"


ASK_WHICH_COIN = [
    "analyze", "analyze the charts", "look at the chart", "check the setup",
    "where is liquidity", "give me entry zones", "long or short",
    "analyze the price",
]


@pytest.mark.parametrize("text", ASK_WHICH_COIN)
def test_a_bare_asset_request_still_asks_which_coin(router, text):
    """The other half, and the half that is easy to break while fixing the first.

    These name no asset either — but they name no OTHER subject, so the user
    does want a chart and simply has not said of what. Asking is the answer.
    `analyze the charts` is pinned by a test older than this file; the generic
    market nouns are in the filler set for exactly that reason.
    """
    i = router.classify_rules(text)
    assert i.skill == "analyze_asset" and 0.5 <= i.confidence < 1.0, (
        f"{text!r} -> {i.skill!r} at {i.confidence}; it should ask which coin")


UNKNOWN_TICKERS = [
    ("analyze FARTCOIN", "FARTCOIN/USDT"),   # too new for the known list
    ("look at $PEPE", "PEPE/USDT"),
    ("analyze WIF", "WIF/USDT"),
    ("analyze the BTC chart", "BTC/USDT"),   # an article before a real ticker
]


@pytest.mark.parametrize("text,symbol", UNKNOWN_TICKERS)
def test_a_real_ticker_still_resolves(router, text, symbol):
    """The fallback exists so a coin too new for the list is still tradeable.

    Requiring caps must not cost that. Deleting the fallback outright would
    have passed every test above and quietly broken every new listing.
    """
    assert router.classify_rules(text).kwargs.get("symbol") == symbol


def test_the_uppercase_guard_is_read_from_the_original_text():
    """Case must survive to the point where it is checked.

    The whole defect was that it did not: `words` is lowercased, so any guard
    reading from it can only ever see lowercase. This drives the difference
    directly rather than trusting that the new code reads the right string.
    """
    from bot.nlp.intent_router import _extract_symbol
    assert _extract_symbol("analyze FARTCOIN") == "FARTCOIN/USDT"
    assert _extract_symbol("analyze fartcoin") is None, (
        "a lowercase unknown word is a noun; treating it as a ticker is what "
        "produced DOCS/USDT")


# ── 6. The WEB surface, because the corollary says to check it ────────────
#
# "Ask which OTHER surface makes the same claim — before calling the fix done."
# `bot/web/user_gateway.py` routes free text through the same router and had
# the same `if skill:` with no else: a confident intent whose skill is neither
# registered nor aliased fell past into the LLM chat fallback, which has no
# tools and answers regardless.
#
# It was narrower than Telegram's — _INTENT_ALIASES already maps status and the
# scan modes — so `help` was the one that escaped. Someone asking a trading
# platform what it can do got a language model's guess at its own command list.

GATEWAY = REPO / "bot" / "web" / "user_gateway.py"


def _web_aliases() -> dict:
    src = GATEWAY.read_text()
    block = src[src.index("_INTENT_ALIASES"):src.index("skill_name = _INTENT_ALIASES")]
    return dict(re.findall(r'"(\w+)":\s*"(\w+)"', block))


def test_the_alias_extractor_sees_the_map():
    al = _web_aliases()
    assert len(al) >= 5 and al.get("status"), f"only parsed {al}"


def test_no_web_intent_can_fall_through_to_the_chat_model():
    """Same guard as the Telegram one, on the other transport.

    Registered, aliased to something registered, handled by prefix, or caught
    by the unavailable branch. Never silently answered by an LLM.
    """
    registry = build_default_registry()
    src = GATEWAY.read_text()
    aliases = _web_aliases()
    prefixes = re.findall(r'intent\.skill\.startswith\(\s*["\']([\w_]+)["\']', src)

    unreachable = []
    for skill in sorted(_emitted_skills()):
        if registry.get(aliases.get(skill, skill)) is not None:
            continue
        if any(skill.startswith(p) for p in prefixes):
            continue
        unreachable.append(skill)

    # The unavailable branch is what makes the remainder honest rather than
    # invisible, so it must exist for this list to be allowed to be non-empty.
    assert "skill_unavailable_notice" in src, (
        f"web intents {unreachable} reach no skill and there is no unavailable "
        "branch — the LLM answers them")


def test_the_web_unavailable_branch_precedes_the_chat_fallback():
    """Placement is the fix. After the fallback it is dead code."""
    src = GATEWAY.read_text()
    notice = src.index("skill_unavailable_notice(")
    fallback = src.index("# Fallback: LLM chat")
    assert notice < fallback, (
        "the web unavailable notice sits after the LLM fallback, so chat "
        "answers first and the notice never runs")


def test_the_web_branch_records_the_same_memory_as_telegram():
    """One vocabulary across surfaces, or a shared history contradicts itself.

    Both write into the SAME conversation store keyed by telegram id, so a web
    turn recorded as FAILED and a Telegram turn recorded as UNAVAILABLE would
    have the model reading two different accounts of one event.
    """
    src = GATEWAY.read_text()
    assert "skill_unavailable_memory" in src, (
        "the web branch tells the user honestly and records nothing, which is "
        "the gap skill_failure_memory was written to close on the other side")

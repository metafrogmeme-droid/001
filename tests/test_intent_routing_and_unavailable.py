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

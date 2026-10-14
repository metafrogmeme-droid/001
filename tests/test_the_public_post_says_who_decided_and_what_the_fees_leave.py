"""The public channel post says who made the call, and what it pays net of fees.

12 October, a TIA long on the public channel: "R:R: 6.5x" over
"🤖 AI-generated signal". Since 11 October the rule engine decides by default
(`AnalyzerConfig.llm_decides` off) and the model's read rides beside the call,
not used to make it, so the footer was false on most calls. And the post
computed its own gross ratio: a taker round trip leaves that call 5.7x, the
figure `trade_costs.net_reward_risk` gives every other ticket card, and an
unreadable stop printed a confident 0.0x.

Driven through `post_signal` on real `TradeIdea`s; the decider's labels are
read off the analyzer's own source, so a new route cannot go unnamed.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from bot.core import analyzer as an
from bot.marketing.channel_forwarder import ChannelForwarder
from bot.utils.models import Direction, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The call as it was posted on 12 October.
TIA = dict(id="T1", asset="TIA/USDT", direction=Direction.LONG,
           entry_price=0.58820, stop_loss=0.58164, take_profit=0.63065,
           confidence=0.71, reasoning="r", strategy_type="swing")


def _post_idea(idea) -> str:
    fwd = ChannelForwarder.__new__(ChannelForwarder)
    fwd._enabled, fwd._group_ids, fwd._bot = True, {1}, None
    sent: list[str] = []

    # The card under test is the members' room's (`members_text`); every
    # other group reads a teaser naming the market alone.
    async def _send(msg, members_text=None):
        sent.append(members_text if members_text is not None else msg)

    fwd._post = _send
    asyncio.run(fwd.post_signal(idea))
    assert len(sent) == 1, "the forwarder posted nothing"
    return sent[0]


def _post(**kw) -> str:
    return _post_idea(TradeIdea(**dict(TIA, **kw)))


# ── who decided ───────────────────────────────────────────────────────────


def test_a_call_the_rule_engine_made_is_not_called_ai_generated():
    msg = _post(model_provider="RULE_ENGINE_DECIDES")
    assert "Rule-engine signal | " in msg
    assert "AI-generated" not in msg


def test_a_call_the_model_made_still_says_so():
    msg = _post(model_provider="LLM_TIER3")
    assert "\U0001f916 AI-generated signal | " in msg
    assert "Rule-engine" not in msg


@pytest.mark.parametrize("provider", [None, "", "manual", "RECORDED_LLM"])
def test_a_call_whose_source_says_nothing_claims_nothing(provider):
    msg = _post(model_provider=provider)
    assert "AI-generated" not in msg and "Rule-engine" not in msg
    assert "#RUNECLAW #TIA" in msg


def _string_constants(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
        if isinstance(node, ast.JoinedStr) and node.values:
            head = node.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                out.add(head.value)
    return out


def test_every_source_the_analyzer_stamps_names_its_decider():
    """Every rule route and every model route the analyzer can stamp, read
    off its source rather than listed here."""
    consts = _string_constants(ROOT / "bot" / "core" / "analyzer.py")
    rules = {c for c in consts if c.startswith("RULE_ENGINE")}
    llm = {c for c in consts if c.startswith("LLM_")}
    assert {"RULE_ENGINE_DECIDES", "RULE_ENGINE_BUDGET"} <= rules
    assert {"LLM_", "LLM_FALLBACK_"} <= llm, "the model's f-string prefixes"
    for label in rules:
        assert an.thesis_decided_by(label) == an.DECIDED_BY_RULES, label
    for label in llm:
        assert an.thesis_decided_by(label + "TIER2") == an.DECIDED_BY_LLM, label
        assert an.thesis_decided_by(label + "TIER2_CACHED") == an.DECIDED_BY_LLM, label


def test_the_idea_carries_the_thesis_source_as_its_provider():
    """The footer reads `model_provider`; the analyzer must put the source
    there. A rule thesis has no model name, so the source is what lands."""
    tree = ast.parse((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    kws = [kw for node in ast.walk(tree) if isinstance(node, ast.Call)
           and getattr(node.func, "id", None) == "TradeIdea"
           for kw in node.keywords if kw.arg == "model_provider"]
    assert len(kws) == 1, "one TradeIdea built with a provider"
    reads = {n.args[0].value for n in ast.walk(kws[0].value)
             if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get"
             and n.args and isinstance(n.args[0], ast.Constant)}
    assert "source" in reads, ast.unparse(kws[0].value)


# ── what it pays ──────────────────────────────────────────────────────────


def test_the_ratio_is_gross_and_net_of_a_taker_round_trip():
    msg = _post(model_provider="RULE_ENGINE_DECIDES", order_type="market")
    assert "R:R: <code>6.5x</code> (5.7x after fees)" in msg, msg


def test_a_resting_limit_pays_the_makers_rate_on_entry():
    msg = _post(model_provider="RULE_ENGINE_DECIDES", order_type="limit")
    assert "R:R: <code>6.5x</code> (6.0x after fees)" in msg, msg


def test_a_target_inside_the_fees_says_so():
    msg = _post(take_profit=0.58850)
    assert "(the target does not cover the fees)" in msg, msg
    assert "after fees" not in msg


def test_a_ratio_the_reading_refuses_is_a_dash_not_a_zero():
    """A validated idea cannot hold a stop at its entry, so this is the
    reading's None branch, not a live state: built past validation, it is a
    dash. The old arithmetic printed 0.0x for it."""
    msg = _post_idea(TradeIdea.model_construct(**dict(TIA, stop_loss=0.58820)))
    assert "0.0x" not in msg
    assert "R:R: <code>—</code> (a level could not be read)" in msg, msg

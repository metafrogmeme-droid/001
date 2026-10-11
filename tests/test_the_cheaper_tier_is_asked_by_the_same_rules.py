"""The cheaper tier is asked by the same three rules as the full one.

The tier-2 thesis call feeds the same confidence blend as the full one, and its
system prompt was one sentence: no counter-case, no "cite only the data you
were given", and no "below 0.55, return direction null". So the tier the
pipeline routes most theses to could answer a weak setup with a direction and
a confidence, invent a level, and leave out the reason the trade fails, while
the full tier was told not to. It carries the three rules now, briefly.

Driven: the analyzer's real `_llm_thesis` at tier 2 and tier 3 against a
stand-in OpenAI-compatible client, reading the system message each was sent.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

import bot.core.analyzer as az
from bot.llm.provider import LLMConfig, LLMProvider
from bot.nlp.grounding import reset
from bot.utils.models import MarketSignal

SIG = MarketSignal(symbol="ETH/USDT", price=3100.0, change_pct_24h=2.0,
                   volume_usd_24h=1e9, momentum_score=0.4)
IND = {"confluence": 0.6, "regime": "RANGE", "rsi": 51.0, "candle_patterns": {}}

# The three rules, in the words each tier's prompt must carry.
RULES = {
    "counter-case": '"Against: "',
    "cite only the data": "cite only",
    "no-trade below 0.55": "below 0.55, return direction null",
}


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset()
    yield
    reset()


class _Completions:
    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        body = json.dumps({"direction": None, "confidence": 0.0,
                           "reasoning": "No trade -- missing: a trigger"})
        return NS(choices=[NS(message=NS(content=body))], usage=None)


def _system_sent(monkeypatch, tier: int) -> str:
    monkeypatch.setattr(az.AdaptiveFrequency, "should_use_llm",
                        staticmethod(lambda s, i: True))
    monkeypatch.setattr(az.TieredPipeline, "classify_tier",
                        staticmethod(lambda i, s: tier))
    a = az.Analyzer()
    comp = _Completions()
    client = NS(chat=NS(completions=comp))
    cfg = LLMConfig(provider=LLMProvider.OPENAI, api_key="k", model="stand-in",
                    base_url="")
    a._llm = client
    a._scan_client, a._scan_config = client, cfg
    a._thesis_client, a._thesis_config = client, cfg
    asyncio.run(a._llm_thesis(SIG, IND))
    assert len(comp.calls) == 1, "the stand-in model was not asked exactly once"
    (system,) = [m["content"] for m in comp.calls[0]["messages"] if m["role"] == "system"]
    return system


@pytest.mark.parametrize("tier", [2, 3])
@pytest.mark.parametrize("rule", sorted(RULES))
def test_each_tier_is_told_the_rule(monkeypatch, tier, rule):
    system = _system_sent(monkeypatch, tier)
    assert RULES[rule] in system, (tier, rule, system)


def test_a_fallback_provider_is_asked_by_the_same_rules(monkeypatch):
    """When the primary provider fails, every fallback provider is asked with
    the terse prompt, whatever the tier; it carries the rules too."""
    from bot.core.analyzer import TERSE_THESIS_PROMPT
    for rule, words in RULES.items():
        assert words in TERSE_THESIS_PROMPT, rule
    # The fallback walk builds provider clients from the environment, so the
    # assignment is read off the method's own AST (no comment can satisfy it).
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(az.Analyzer._try_llm_fallback)))
    sys_values = [ast.unparse(n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Assign) and len(n.targets) == 1
                  and isinstance(n.targets[0], ast.Name)
                  and n.targets[0].id == "sys_content"]
    assert sys_values == ["TERSE_THESIS_PROMPT"], sys_values


def test_the_cheaper_tier_stays_short(monkeypatch):
    """Its point is cost: the rules are added, the framework is not."""
    cheap = _system_sent(monkeypatch, 2)
    full = _system_sent(monkeypatch, 3)
    assert cheap != full
    assert len(cheap) < 600 < len(full)
    assert "json" in cheap      # the json_object response format needs the word

"""A figure the model wrote is checked against what the model was given.

Two holes in one rule. The trade thesis the LLM writes went onto every card
(`_thesis_bq`) with nothing checking its figures against the indicator values
in its prompt; "never invent indicator values" was only an instruction. And in
chat the figure check sat behind `if tool_events:`, so a reply built from the
prompt's price and position sections, or made up with no tool call at all,
was never checked.

Both now ask the chat's own shadow rule (`grounding.shadow_check`): a figure
with a unit is checked against what supports it, the turn is recorded under a
ledger key, and the words are annotated only once that key's published rate
is already below the line. Until then nothing the person reads changes. A
thesis is counted under its own key, apart from the same model's chat rate.

Driven: `_chat_ret` with and without tools, and the analyzer's real
`_llm_thesis` against a stand-in OpenAI-compatible client.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

import bot.core.analyzer as az
from bot.llm.provider import LLMConfig, LLMProvider
from bot.nlp import grounding
from bot.nlp.grounding import (
    given_support,
    prompt_values,
    rate,
    reset,
    shadow_check,
    thesis_ledger_key,
)
from bot.nlp.tool_reading import render_reading
from bot.skills.chat_runtime import _chat_ret
from bot.utils.models import MarketSignal

MODEL = "tier-test-model"
CFG = NS(provider=NS(value="ollama"), model=MODEL)


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset()
    yield
    reset()


# ── the rule itself ─────────────────────────────────────────────────────────

class TestTheShadowRule:

    def test_a_given_figure_is_supported_and_an_invented_one_is_not(self):
        given = given_support("BTC price $63,105.20 · 24h +1.2%")
        sc = shadow_check("BTC is at $63,105 after +1.2%, aiming at $70,000.",
                          given, "k", False)
        assert sc.checked == 3 and sc.unverified == ["$70,000"]
        assert rate("k") == pytest.approx(1 / 3)
        assert not sc.annotate

    def test_annotation_waits_for_a_rate_below_the_line(self):
        grounding.note("k", 1000, 0)
        sc = shadow_check("Up to $70,000.", given_support("price $63,105"), "k", True)
        assert sc.annotate and sc.unverified == ["$70,000"]

    def test_nothing_checked_is_not_recorded(self):
        sc = shadow_check("No figures here.", given_support("price $1"), "k", False)
        assert sc.checked == 0 and rate("k") is None

    def test_no_key_records_nothing_and_annotates_nothing(self):
        grounding.note("", 1000, 0)
        sc = shadow_check("Up to $70,000.", [], "", True)
        assert sc.unverified == ["$70,000"] and not sc.annotate

    def test_given_support_drops_what_is_not_text_and_stays_bounded(self):
        assert given_support(None, "", "  ", 5, "a") == ["a"]
        big = "x" * (grounding.SUPPORT_MAX_CHARS + 10)
        assert sum(map(len, given_support(big, "more"))) == grounding.SUPPORT_MAX_CHARS

    def test_a_bare_prompt_value_stands_as_dollars_and_nothing_else(self):
        vals = prompt_values("RSI=55.2 BB_upper=63500.12 fib_618=0.5 24h=1.5% R:R 1:2")
        assert "$55.2" in vals and "$63500.12" in vals
        assert "$618" not in vals          # part of a name
        assert "$1.5" not in vals          # already a percent
        assert "$1" not in vals.split() and "$2" not in vals.split()  # a ratio

    def test_the_thesis_key_is_apart_from_the_chat_key(self):
        assert thesis_ledger_key(MODEL) != MODEL
        assert MODEL in thesis_ledger_key(MODEL)
        assert thesis_ledger_key("") == ""


# ── chat: a turn that called no tool is checked against its prompt ─────────

class TestChatWithoutATool:

    def test_a_figure_the_prompt_did_not_state_is_counted(self):
        given = given_support("ACTIVE POSITIONS: none. BTC $63,105.")
        plain, _meta = _chat_ret("BTC is $63,105; you are up 12% today.", CFG, True,
                                 [], is_admin=True, given=given)
        assert plain == "BTC is $63,105; you are up 12% today."
        assert rate(MODEL) == pytest.approx(0.5)

    def test_once_the_rate_is_low_the_reply_is_annotated(self):
        grounding.note(MODEL, 1000, 0)
        given = given_support("BTC $63,105.")
        plain = _chat_ret("You are up 12% today.", CFG, False, [], is_admin=True,
                          given=given)
        assert plain.startswith("You are up 12% today.")
        assert "Unverified figure: 12%" in plain

    def test_a_reply_built_from_the_prompt_is_not_annotated(self):
        grounding.note(MODEL, 1000, 0)
        plain = _chat_ret("BTC is at $63,105.", CFG, False, [], is_admin=True,
                          given=given_support("BTC $63,105."))
        assert plain == "BTC is at $63,105."

    def test_a_canned_reply_with_nothing_given_is_left_alone(self):
        assert _chat_ret("Up 12%.", None, False) == "Up 12%."
        assert rate(MODEL) is None

    def test_a_tool_turn_is_still_checked_against_its_tools_alone(self):
        events = [{"name": "get_portfolio", "ok": True, "ms": 1,
                   "result": render_reading("get_portfolio", "read", "Equity $10")}]
        _chat_ret("Equity $10, BTC $63,105.", CFG, False, events, is_admin=True,
                  given=given_support("BTC $63,105."))
        assert rate(MODEL) == pytest.approx(0.5)


def test_the_chat_turn_hands_the_check_what_the_model_was_given():
    """`_llm_chat`'s answering return passes the prompt, the history and the
    question; a seam no other return needs."""
    import ast
    import inspect
    import textwrap

    from bot.skills.telegram_handler import TelegramHandler
    tree = ast.parse(textwrap.dedent(inspect.getsource(TelegramHandler._llm_chat)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_chat_ret"
             and any(k.arg == "tool_events" for k in n.keywords)]
    assert len(calls) == 1
    given = next(k.value for k in calls[0].keywords if k.arg == "given")
    text = ast.unparse(given)
    assert text.startswith("given_support(") and "_sp" in text
    assert "question" in text and "history" in text


# ── the thesis: checked against the values in its own prompt ──────────────

SIG = MarketSignal(symbol="BTC/USDT", price=63105.2, change_pct_24h=1.5,
                   volume_usd_24h=1e9, momentum_score=0.4)
IND = {"confluence": 0.62, "regime": "TREND_UP", "rsi": 58.4,
       "bb_upper": 63500.12, "bb_lower": 61000.5, "candle_patterns": {}}


class _Completions:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def create(self, **kw):
        self.calls.append(kw)
        return NS(choices=[NS(message=NS(content=self.reply))], usage=None)


def _analyzer(monkeypatch, reasoning):
    monkeypatch.setattr(az.AdaptiveFrequency, "should_use_llm",
                        staticmethod(lambda s, i: True))
    monkeypatch.setattr(az.TieredPipeline, "classify_tier", staticmethod(lambda i, s: 2))
    a = az.Analyzer()
    comp = _Completions('{"direction": "LONG", "confidence": 0.7, "reasoning": %s}'
                        % __import__("json").dumps(reasoning))
    client = NS(chat=NS(completions=comp))
    a._llm = client
    a._scan_client = client
    a._scan_config = LLMConfig(provider=LLMProvider.OPENAI, api_key="k",
                               model=MODEL, base_url="")
    return a, comp


class TestTheThesis:

    REASON = ("Price $63,105 holds over the band at $63,500 with RSI 58.4; "
              "target $71,000. Against: volume is thin.")

    def test_an_invented_level_is_counted_under_the_thesis_key(self, monkeypatch):
        a, comp = _analyzer(monkeypatch, self.REASON)
        out = asyncio.run(a._llm_thesis(SIG, IND))
        assert comp.calls, "the stand-in model was never asked"
        assert out["reasoning"] == self.REASON
        assert rate(thesis_ledger_key(MODEL)) == pytest.approx(1 / 3)
        assert rate(MODEL) is None

    def test_once_its_rate_is_low_the_reasoning_says_so_first(self, monkeypatch):
        grounding.note(thesis_ledger_key(MODEL), 1000, 0)
        a, _comp = _analyzer(monkeypatch, self.REASON)
        out = asyncio.run(a._llm_thesis(SIG, IND, is_admin=False))
        assert out["reasoning"].startswith("(Unverified figure: $71,000) ")
        assert out["reasoning"].endswith("Against: volume is thin.")

    def test_a_fallback_provider_thesis_is_checked_under_its_own_model(self, monkeypatch):
        """The primary provider failed and another answered: the thesis is the
        fallback model's, and so is the ledger row it is counted under."""
        a, comp = _analyzer(monkeypatch, self.REASON)

        async def _down(**kw):
            raise RuntimeError("primary provider down")
        comp.create = _down

        async def _fallback(prompt, signal, use_full_model, **kw):
            return {"direction": "LONG", "confidence": 0.7,
                    "reasoning": self.REASON, "model_used": "fallback-model",
                    "_fallback_provider": "OTHER"}
        monkeypatch.setattr(a, "_try_llm_fallback", _fallback)
        out = asyncio.run(a._llm_thesis(SIG, IND))
        assert out["source"] == "LLM_FALLBACK_OTHER"
        assert rate(thesis_ledger_key("fallback-model")) == pytest.approx(1 / 3)
        assert rate(thesis_ledger_key(MODEL)) is None

    def test_a_thesis_citing_only_what_it_was_given_is_untouched(self, monkeypatch):
        grounding.note(thesis_ledger_key(MODEL), 1000, 0)
        clean = "Price $63,105 under the band at $63,500. Against: RSI 58.4 is mid."
        a, _comp = _analyzer(monkeypatch, clean)
        out = asyncio.run(a._llm_thesis(SIG, IND))
        assert out["reasoning"] == clean

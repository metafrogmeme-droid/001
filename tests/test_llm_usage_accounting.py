"""Nobody could say what raising the universe 70 -> 85 cost.

RUNECLAW makes an LLM call per symbol per analysis stage. On 2026-07-30 the
scan universe went 70 -> 85 and analysis concurrency 12 -> 16 — both good
changes, both made blind, because `llm_complete` threw `response.usage` away
and returned only the text. The provider had been reporting the numbers all
along.

The line this file defends is the one the whole day was about:

  TOKENS ARE MEASURED — the provider reports them, we accumulate them.
  DOLLARS ARE NOT, unless the operator supplies prices.

A hardcoded price table looks measured, goes stale the first time a provider
re-prices or a model tier changes, and then misleads with the authority of a
printed figure. Absent is the honest shape for "not measured".
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from bot.llm import usage


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    usage.reset()
    monkeypatch.delenv("LLM_PRICE_PER_1M_IN", raising=False)
    monkeypatch.delenv("LLM_PRICE_PER_1M_OUT", raising=False)
    yield
    usage.reset()


class TestTokensAreMeasured:
    def test_calls_accumulate(self):
        usage.record("m", 1000, 200)
        usage.record("m", 500, 100)
        s = usage.snapshot()
        assert s == {
            "calls": 2, "tokens_in": 1500, "tokens_out": 300,
            "by_model": {"m": {"in": 1500, "out": 300, "calls": 2}},
        }

    def test_models_are_kept_apart(self):
        usage.record("haiku", 100, 10)
        usage.record("opus", 900, 90)
        by = usage.snapshot()["by_model"]
        assert by["haiku"]["in"] == 100 and by["opus"]["in"] == 900

    def test_anthropic_shape_is_read(self):
        usage.record_from_response(
            "m", NS(usage=NS(input_tokens=700, output_tokens=70)))
        assert usage.snapshot()["tokens_in"] == 700

    def test_openai_shape_is_read(self):
        usage.record_from_response(
            "m", NS(usage=NS(prompt_tokens=300, completion_tokens=30)))
        assert usage.snapshot()["tokens_out"] == 30


class TestUnmeasuredIsNeverZero:
    def test_a_response_without_usage_records_nothing(self):
        # A zero here would read as "this call was free" — a different and
        # false claim from "we did not measure this call".
        usage.record_from_response("m", NS())
        assert usage.snapshot()["calls"] == 0

    def test_a_usage_object_with_no_known_fields_records_nothing(self):
        usage.record_from_response("m", NS(usage=NS(something_else=5)))
        assert usage.snapshot()["calls"] == 0

    def test_negative_counts_are_refused(self):
        usage.record("m", -5, 10)
        assert usage.snapshot()["calls"] == 0

    def test_accounting_never_raises(self):
        usage.record_from_response("m", None)
        usage.record_from_response("m", object())
        usage.record(None, "x", "y")          # type: ignore[arg-type]
        assert usage.snapshot()["calls"] == 0


class TestDollarsOnlyWhenSupplied:
    def test_no_prices_means_no_cost_key_at_all(self):
        usage.record("m", 1_000_000, 1_000_000)
        s = usage.snapshot()
        assert "cost_usd" not in s, (
            "an invented price is a stale number wearing the authority of a "
            "measured one"
        )
        assert "cost_basis" not in s

    def test_both_prices_produce_a_labelled_cost(self, monkeypatch):
        monkeypatch.setenv("LLM_PRICE_PER_1M_IN", "3")
        monkeypatch.setenv("LLM_PRICE_PER_1M_OUT", "15")
        usage.record("m", 1_000_000, 1_000_000)
        s = usage.snapshot()
        assert s["cost_usd"] == 18.0
        assert "operator-supplied" in s["cost_basis"]

    def test_half_configured_prices_produce_no_cost(self, monkeypatch):
        # One rate cannot price two token classes; guessing the other is the
        # invented number again.
        monkeypatch.setenv("LLM_PRICE_PER_1M_IN", "3")
        usage.record("m", 1_000_000, 1_000_000)
        assert "cost_usd" not in usage.snapshot()

    def test_a_junk_price_is_ignored_not_treated_as_zero(self, monkeypatch):
        monkeypatch.setenv("LLM_PRICE_PER_1M_IN", "free")
        monkeypatch.setenv("LLM_PRICE_PER_1M_OUT", "15")
        usage.record("m", 1_000_000, 1_000_000)
        assert "cost_usd" not in usage.snapshot()


class TestItIsActuallyWired:
    def test_both_provider_branches_record(self):
        """Both SDK branches record, in BOTH completion functions.

        `llm_complete_with_tools` carries the same two branches as
        `llm_complete` — a tool loop that recorded on one provider and not the
        other would make the total depend on which provider answered, exactly
        the defect this test was written against. So the count is per
        function, not per file: two in each.
        """
        from tests.source_scan import code_only
        src = code_only(open("bot/llm/provider.py", encoding="utf-8").read())
        plain, _, tools = src.partition("async def llm_complete_with_tools(")
        assert tools, "llm_complete_with_tools is gone"
        needle = "_usage.record_from_response(config.model, response)"
        assert plain.count(needle) == 2, (
            "llm_complete: Anthropic and OpenAI-compatible branches must BOTH "
            "record — one wired branch means the total silently depends on "
            "which provider answered"
        )
        assert tools.count(needle) == 2, (
            "llm_complete_with_tools: both SDK loops must record every round"
        )

    def test_health_reports_it(self):
        from tests.source_scan import code_only
        src = code_only(open("api_bridge.py", encoding="utf-8").read())
        assert "llm_usage" in src

    def test_llmstatus_says_when_no_price_is_configured(self):
        from tests.source_scan import code_only
        # /llmstatus lives in the LLM mixin since the handler split.
        src = code_only(
            open("bot/skills/llm_commands.py", encoding="utf-8").read())
        assert "LLM_PRICE_PER_1M_IN" in src
        assert "no price is invented" in src

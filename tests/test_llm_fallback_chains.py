"""Two fallback chains, two literal lists, two retired model ids.

`TelegramHandler._llm_chat` and `Analyzer._try_llm_fallback` each carried
their own list of the providers to fall through to when the routed one
fails, and each list named a model the catalogue in `bot/llm/provider.py`
had already left behind: `gemini-2.0-flash`, two generations behind the
Gemini default, and `llama-3.3-70b-versatile`, which the Groq catalogue entry
records as RETIRED (June 2026). `tests/test_model_catalog_2026.py` checks the
ROUTING tables for dead ids; it never looked at the fallbacks, because they
were not a table — they were two lists in two functions.

A fallback exists for the day the routed provider is down. A dead id in it is
a failure nothing observes until that day, and on that day it looks like an
outage of the provider that was supposed to catch the outage.

The chain is one table now (`FALLBACK_CHAINS`), read by both callers through
`fallback_chain(kind, is_admin=...)`, and every id in it must be one the
catalogue still recommends and one `bot/core/cost.py` can price.
"""
from __future__ import annotations

import inspect

import pytest

from bot.core.cost import resolve_llm_price
from bot.llm import provider as prov
from bot.llm.provider import FALLBACK_CHAINS, PROVIDER_CATALOG, LLMProvider, fallback_chain
from tests.source_scan import code_only

KINDS = tuple(FALLBACK_CHAINS)


def test_the_two_chains_are_the_ones_the_callers_read():
    assert set(KINDS) == {"chat", "analysis"}


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize("kind", KINDS)
def test_every_model_is_one_the_catalogue_still_recommends(kind, is_admin):
    chain = fallback_chain(kind, is_admin=is_admin)
    assert chain, f"{kind}: an empty chain falls through to nothing"
    for provider, key_env, model in chain:
        entry = PROVIDER_CATALOG[provider]
        assert model == entry["default_model"] or model in entry["recommended_models"], (
            f"{kind}: {provider.value} falls back to {model!r}, which the catalogue "
            "neither defaults to nor recommends — a retired id in a fallback is a "
            "failure nothing observes until the day the routed provider is down")
        assert key_env == prov._PROVIDER_KEY_ENV[provider]


@pytest.mark.parametrize("kind", KINDS)
def test_every_model_can_be_priced(kind):
    for _provider, _key_env, model in fallback_chain(kind, is_admin=True):
        price, _exact = resolve_llm_price(model)
        assert price is not None, (
            f"{model} has no price: every call through it books $0.00 and "
            "disarms the daily budget guard")


@pytest.mark.parametrize("kind", KINDS)
def test_the_operators_anthropic_key_is_admin_only(kind):
    """The same rule `resolve_tier_config` enforces on the primary path."""
    assert LLMProvider.ANTHROPIC in [p for p, _, _ in fallback_chain(kind, is_admin=True)]
    assert LLMProvider.ANTHROPIC not in [p for p, _, _ in fallback_chain(kind, is_admin=False)]


def test_the_order_each_caller_used_is_kept():
    """The move is a refactor: same providers, same order, same admin insert
    position as the two literal lists had."""
    assert [p for p, _, _ in fallback_chain("chat", is_admin=True)] == [
        LLMProvider.GEMINI, LLMProvider.ANTHROPIC, LLMProvider.ALIBABA]
    assert [p for p, _, _ in fallback_chain("chat", is_admin=False)] == [
        LLMProvider.GEMINI, LLMProvider.ALIBABA]
    assert [p for p, _, _ in fallback_chain("analysis", is_admin=True)] == [
        LLMProvider.ALIBABA, LLMProvider.GEMINI, LLMProvider.GROQ,
        LLMProvider.ANTHROPIC, LLMProvider.DEEPSEEK]


def test_a_none_model_is_the_catalogue_default_for_that_provider():
    """The point of `None`: the chain follows the catalogue instead of
    carrying a copy of it."""
    resolved = {p: m for p, _, m in fallback_chain("analysis", is_admin=True)}
    assert resolved[LLMProvider.GEMINI] == PROVIDER_CATALOG[LLMProvider.GEMINI]["default_model"]
    assert resolved[LLMProvider.GROQ] == PROVIDER_CATALOG[LLMProvider.GROQ]["default_model"]


def test_an_unknown_chain_falls_through_to_nothing():
    assert fallback_chain("scan", is_admin=True) == []


def test_the_retired_ids_are_gone():
    for _p, _k, model in (fallback_chain("chat", is_admin=True)
                          + fallback_chain("analysis", is_admin=True)):
        assert not model.startswith(("llama-3.3", "llama-3.1", "gemini-2.0", "gemini-2.5")), model


def test_both_callers_read_the_table_and_carry_no_chain_of_their_own():
    """Wiring, pinned: a chain that comes back as a literal list in either
    caller is the drift this file exists to stop."""
    from bot.core.analyzer import Analyzer
    from bot.skills.telegram_handler import TelegramHandler
    for fn, kind in ((Analyzer._try_llm_fallback, "analysis"),
                     (TelegramHandler._llm_chat, "chat")):
        src = code_only(inspect.getsource(fn))
        assert f'fallback_chain("{kind}", is_admin=is_admin)' in src, (
            f"{fn.__qualname__} no longer reads the shared table with its caller's role")
        for literal in ('"GEMINI_API_KEY"', '"ANTHROPIC_API_KEY"', '"GROQ_API_KEY"',
                        "gemini-2.0-flash", "llama-3.3-70b-versatile"):
            assert literal not in src, (
                f"{fn.__qualname__} still carries {literal} — a second copy of the chain")

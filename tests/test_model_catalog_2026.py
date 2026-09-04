"""
Model catalog currency guard (2026 refresh).

The admin tier (which the operator's live trade analysis uses) was bumped from
Claude Sonnet 4.6 to the current Sonnet 5. This test guards two invariants so a
future catalog bump can't silently break live LLM calls or cost accounting:

  1. Every model referenced by a routing table has a KNOWN cost (else spend goes
     untracked and budgets misfire).
  2. The admin routing uses the current Sonnet id (not a stale one).
"""

import bot.llm.provider as _provider
from bot.core.cost import resolve_llm_price
from bot.llm.provider import (
    ADMIN_TIER_ROUTING,
    DEFAULT_TIER_ROUTING,
    ULTRA_TIER_ROUTING,
)


def _routing_tables():
    """Every `*_TIER_ROUTING` table in the provider module, DISCOVERED.

    The four-name tuple this replaces omitted ULTRA_TIER_ROUTING — the table
    holding `claude-fable-5`, the priciest model in the product, which was
    unpriced and therefore booking $0.00 against the daily budget guard this
    very test exists to keep armed. A hand-written list of what to check is a
    claim about coverage, and it was wrong about the one table that mattered.

    Discovered from the module so a table added later is covered without
    anybody remembering — the same reason preflight parses ci.yml instead of
    restating it.
    """
    for name in dir(_provider):
        if name.endswith("_TIER_ROUTING"):
            table = getattr(_provider, name)
            if isinstance(table, dict):
                yield name, table


def _models_in(table):
    """Yield every model id in a routing table, whatever its shape.

    Two shapes exist and the discovery found the second one immediately:
    the `*_TIER_ROUTING` tables map tier -> config, while USER_TIER_ROUTING
    maps ROLE -> (tier -> config). A flat `cfg["model"]` KeyErrors on the
    nested one — which is what a hand-written table list had been hiding.
    """
    for value in table.values():
        if isinstance(value, dict) and "model" in value:
            yield value["model"]
        elif isinstance(value, dict):
            yield from _models_in(value)


def _all_routing_models():
    for _name, table in _routing_tables():
        yield from _models_in(table)


def test_the_discovery_finds_every_known_table():
    """Pins the discovery itself: if it silently found nothing, every
    assertion below would pass vacuously."""
    found = {n for n, _ in _routing_tables()}
    for expected in ("DEFAULT_TIER_ROUTING", "ADMIN_TIER_ROUTING",
                     "ELITE_TIER_ROUTING", "PRO_TIER_ROUTING",
                     "ULTRA_TIER_ROUTING"):
        assert expected in found, f"{expected} not discovered"
    assert len(list(_all_routing_models())) >= 5


def test_ultra_thesis_model_is_priced():
    """The specific regression. ULTRA routes thesis at a model the product
    advertises as $10/$50 per MTok; it resolved to no price at all, so the
    budget guard could never trip on it."""
    model = next(cfg["model"] for tier, cfg in ULTRA_TIER_ROUTING.items()
                 if getattr(tier, "name", "") == "THESIS")
    price, _exact = resolve_llm_price(model)
    assert price, f"ULTRA thesis model {model} has no resolvable price"
    assert price["in"] > 0 and price["out"] > 0


def test_an_unknown_anthropic_model_still_gets_a_price():
    """The class fix, not the instance. Twice now a routed Anthropic model has
    matched no family and booked $0.00. An unknown one prices high, because an
    over-estimate trips the guard early and an under-estimate never trips."""
    price, exact = resolve_llm_price("claude-somethingnew-9")
    assert price and not exact
    assert price["in"] >= 10.0


def test_every_routing_model_is_priced():
    # A model with NO resolvable price books $0.00 and disarms the daily dollar
    # budget guard (2026-07-11 incident class). Exact-vs-family doesn't matter —
    # a resolvable (approximate) price is what arms the guard.
    unpriced = []
    for model in _all_routing_models():
        price, _exact = resolve_llm_price(model)
        if not price:
            unpriced.append(model)
    assert not unpriced, f"routing models with no resolvable price (untracked spend): {unpriced}"


def test_admin_tier_uses_current_sonnet():
    for tier, cfg in ADMIN_TIER_ROUTING.items():
        assert cfg["model"] == "claude-sonnet-5", (
            f"admin {tier} should use the current Sonnet, got {cfg['model']}")


def test_no_stale_sonnet_46_in_routing():
    assert "claude-sonnet-4-6" not in set(_all_routing_models())


def test_free_tier_routes_only_to_free_or_operator_funded_providers():
    # Free/default users (no BYOK key) must resolve to a provider with a real free
    # tier (Gemini/Groq/…) OR the operator-funded, quota-capped chat model (Grok) —
    # NEVER Alibaba's paid hackathon endpoint (the old SCAN/CHAT default that
    # silently required $30 of credits with no per-user quota fence).
    from bot.llm.provider import LLMProvider
    allowed = {LLMProvider.GEMINI, LLMProvider.GROQ, LLMProvider.OLLAMA,
               LLMProvider.RUNECLAW, LLMProvider.TOGETHER, LLMProvider.OPENROUTER,
               LLMProvider.GROK}   # Grok is operator-funded + 5/day quota-bounded
    for tier, cfg in DEFAULT_TIER_ROUTING.items():
        assert cfg["provider"] in allowed, (
            f"free-tier {tier} routes to disallowed {cfg['provider']}")
        assert cfg["provider"] != LLMProvider.ALIBABA


def test_no_deprecated_model_ids_in_routing():
    # Groq retired llama-3.3/3.1-instant (June 2026) and Gemini 2.5 was superseded
    # by the 3.x line — a deprecated id breaks live calls the moment it's retired.
    models = " ".join(_all_routing_models())
    for dead in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemini-2.5"):
        assert dead not in models, f"deprecated model id still routed: {dead}"

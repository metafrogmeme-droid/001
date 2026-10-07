"""A model probe that checked nothing does not say the model is served.

`classify_model_list` answered `ok` for a model list that could not be read,
for an empty list, and when no model was named; the tier card prints `ok` as
"reachable, configured model is served". And the probe named only a pinned
`LLM_TIER_<T>_MODEL`: a tier pinned to a provider with no model named calls
the catalogue's default model, so nothing was checked there either.
"""
import pytest

from bot.core.proactive_monitor import (
    UNCHECKED_PROBE_STATES,
    ProactiveMonitor,
    classify_model_list,
    effective_tier_model,
)
from bot.formatters.llm_tier_card import _PROBE_TEXT
from bot.llm.provider import PROVIDER_CATALOG, LLMProvider


def test_only_a_listed_model_is_ok():
    assert classify_model_list(["llama3:latest"], [("scan", "llama3")])[0] == "ok"
    assert classify_model_list(None, [("scan", "llama3")])[0] == "list_unreadable"
    assert classify_model_list([], [("scan", "llama3")])[0] == "list_empty"
    assert classify_model_list(["other"], [])[0] == "model_unchecked"
    assert classify_model_list(["other"], [("scan", "llama3")])[0] == "model_missing"


def test_each_unchecked_state_has_words_that_do_not_claim_served():
    for state in UNCHECKED_PROBE_STATES:
        assert state in _PROBE_TEXT
        assert "is served" not in _PROBE_TEXT[state]


def test_the_probe_names_the_model_the_tier_actually_calls(monkeypatch):
    monkeypatch.delenv("LLM_TIER_SCAN_MODEL", raising=False)
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "ollama")
    assert effective_tier_model("scan") == PROVIDER_CATALOG[LLMProvider("ollama")]["default_model"]
    monkeypatch.setenv("LLM_TIER_SCAN_MODEL", "runeclaw-scan14b")
    assert effective_tier_model("scan") == "runeclaw-scan14b"
    monkeypatch.delenv("LLM_TIER_SCAN_MODEL")
    monkeypatch.delenv("LLM_TIER_SCAN_PROVIDER")
    assert effective_tier_model("scan") == ""
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "not-a-provider")
    assert effective_tier_model("scan") == ""


@pytest.mark.parametrize("state", sorted(UNCHECKED_PROBE_STATES))
def test_an_unchecked_endpoint_is_not_paged_as_dead_and_counts_as_reachable(state):
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m._llm_probe = None
    m._llm_probe_at = 0.0
    m._llm_alerted_state = ""
    m._llm_probe = {"state": state, "tier": "chat", "host": "h.example",
                    "consecutive_failures": 5}
    alerts = m._check_llm_endpoint()
    assert not any("No answer" in a.body for a in alerts), [a.body for a in alerts]
    # After an outage was paged, an endpoint that answers again is recovered.
    m._llm_alerted_by_host = {"h.example": "unreachable"}
    back = m._check_llm_endpoint()
    assert back and "IN-HOUSE MODEL BACK" in back[0].body


def _probe_against(served_ids, monkeypatch, prev=None):
    """The real `_fetch_llm_probe` against a local `/models` endpoint."""
    import asyncio

    from aiohttp import web

    async def models(_request):
        return web.json_response({"data": [{"id": i} for i in served_ids]})

    async def go():
        app = web.Application()
        app.router.add_get("/v1/models", models)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            m = ProactiveMonitor.__new__(ProactiveMonitor)
            return await m._fetch_llm_probe(f"http://127.0.0.1:{port}/v1", "k",
                                            ("scan",), "LLM_TIER_SCAN_KEY", prev or {})
        finally:
            await runner.cleanup()

    return asyncio.run(go())


def test_a_provider_pinned_tier_is_checked_for_its_default_model(monkeypatch):
    monkeypatch.delenv("LLM_TIER_SCAN_MODEL", raising=False)
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "ollama")
    default = PROVIDER_CATALOG[LLMProvider("ollama")]["default_model"]
    missing = _probe_against(["some-other-model"], monkeypatch)
    assert missing["state"] == "model_missing" and missing["model"] == default
    served = _probe_against([f"{default}:latest"], monkeypatch)
    assert served["state"] == "ok"
    assert served["consecutive_failures"] == 0


def test_an_endpoint_that_answered_without_a_check_ends_a_failure_run(monkeypatch):
    # Reachable and authenticated is not a failure, so it does not extend a
    # run of failures toward the page threshold, and it ends one.
    monkeypatch.setenv("LLM_TIER_SCAN_MODEL", "llama3")
    empty = _probe_against([], monkeypatch, prev={"consecutive_failures": 3})
    assert empty["state"] == "list_empty"
    assert empty["consecutive_failures"] == 0
    monkeypatch.delenv("LLM_TIER_SCAN_MODEL")
    monkeypatch.delenv("LLM_TIER_SCAN_PROVIDER", raising=False)
    unnamed = _probe_against(["llama3"], monkeypatch, prev={"consecutive_failures": 2})
    assert unnamed["state"] == "model_unchecked"
    assert unnamed["consecutive_failures"] == 0
    # A model the list lacks is a finding and keeps counting.
    monkeypatch.setenv("LLM_TIER_SCAN_MODEL", "llama3")
    missing = _probe_against(["other"], monkeypatch, prev={"consecutive_failures": 2})
    assert missing["state"] == "model_missing"
    assert missing["consecutive_failures"] == 3

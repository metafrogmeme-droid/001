"""Chat runs on Claude Haiku 5.5 for every caller; the other tiers do not move.

The owner's call (2026-10-21): chat on Claude Haiku 5.5, on the operator's
prepaid Anthropic credit, for admin and members alike — "just the chat will do
in this model; we keep our own runeclaw model for trades analysis and signals".
Before it, member chat ran on xAI Grok (operator-funded, 5 a day) and admin
chat on Claude Sonnet 5.

What has to hold, and is driven here:

- the request Haiku receives is one it accepts: `output_config.effort` low
  (Anthropic's recommendation for chat on that model; its default is medium,
  and thinking counts toward max_tokens), no `temperature`, no `thinking`;
- web search, when an admin turns it on, carries the basic tool version on
  Haiku, which is not among the dynamic-filtering models;
- the free-chat cap still protects a budget the operator pays for: it turns on
  when the operator's Anthropic key funds chat, as it did for the Grok key;
- when Haiku fails (the prepaid credit runs out, the key is condemned), Grok,
  the model chat ran on before, answers.

The guard's narrowing (chat on Haiku only, nothing wider) is held in
tests/test_anthropic_admin_only.py::TestTheMemberChatException.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.llm.provider import (
    BASIC_WEB_SEARCH_TOOL_TYPE,
    CHAT_HAIKU_EFFORT,
    CHAT_HAIKU_MODEL,
    WEB_SEARCH_TOOL_TYPE,
    LLMConfig,
    LLMProvider,
    LLMTier,
    llm_complete,
    member_chat_on_operator_key,
    resolve_tier_config,
)

_KEYS = ("ANTHROPIC_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
         "ALIBABA_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY",
         "LLM_PROVIDER", "LLM_TIER_CHAT_PROVIDER", "FREE_CHAT_QUOTA_ENABLED")


def _clear(monkeypatch):
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)


def _primary():
    return LLMConfig(provider=LLMProvider.OPENAI, api_key="")


class _Messages:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi", citations=[])],
            stop_reason="end_turn")


class _Client:
    def __init__(self):
        self.messages = _Messages()


def _sent(config, **kw):
    client = _Client()
    asyncio.run(llm_complete(client, config, "sys", "how is BTC?", **kw))
    return client.messages.calls[0]


# ── the route ────────────────────────────────────────────────────────────

def test_every_caller_resolves_chat_to_haiku_on_low_effort(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for is_admin in (False, True):
        cfg = resolve_tier_config(LLMTier.CHAT, _primary(), is_admin=is_admin)
        assert (cfg.provider, cfg.model, cfg.effort) == (
            LLMProvider.ANTHROPIC, "claude-haiku-5-5", "low"), is_admin


def test_the_other_tiers_do_not_move(monkeypatch):
    """Only chat changed: a member's scan and thesis keep their cheap routes,
    and the admin's keep Sonnet."""
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gem")
    monkeypatch.setenv("GROQ_API_KEY", "gsk")
    for tier in (LLMTier.SCAN, LLMTier.THESIS, LLMTier.LEARNING):
        member = resolve_tier_config(tier, _primary(), is_admin=False)
        assert member.provider != LLMProvider.ANTHROPIC, tier
        admin = resolve_tier_config(tier, _primary(), is_admin=True)
        assert admin.model == "claude-sonnet-5", tier


# ── the request Haiku receives ───────────────────────────────────────────

def test_haiku_is_sent_low_effort_and_no_temperature_or_thinking():
    cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                    model=CHAT_HAIKU_MODEL, effort=CHAT_HAIKU_EFFORT)
    kw = _sent(cfg)
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["output_config"] == {"effort": "low"}
    assert "temperature" not in kw and "top_p" not in kw
    assert "thinking" not in kw


def test_a_model_with_no_effort_set_is_sent_none():
    """Both arms: the effort field rides only where a route set one, so the
    admin's Sonnet scans and theses are sent exactly what they were."""
    cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k", model="claude-sonnet-5")
    assert "output_config" not in _sent(cfg)


def test_web_search_on_haiku_is_the_basic_tool():
    haiku = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                      model=CHAT_HAIKU_MODEL, effort="low")
    tools = _sent(haiku, web_search=True)["tools"]
    assert tools[0]["type"] == BASIC_WEB_SEARCH_TOOL_TYPE == "web_search_20250305"
    sonnet = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k", model="claude-sonnet-5")
    assert _sent(sonnet, web_search=True)["tools"][0]["type"] == WEB_SEARCH_TOOL_TYPE


# ── the cap ──────────────────────────────────────────────────────────────

def test_the_free_chat_cap_turns_on_when_the_operator_key_funds_chat(monkeypatch):
    from bot.web import chat_quota
    _clear(monkeypatch)
    assert not member_chat_on_operator_key()
    assert not chat_quota.quota_enabled(), "no funded chat budget, nothing to protect"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert member_chat_on_operator_key()
    assert chat_quota.quota_enabled()
    # An operator who pins chat elsewhere is not funding it with this key.
    monkeypatch.setenv("LLM_TIER_CHAT_PROVIDER", "groq")
    assert not member_chat_on_operator_key()
    # And the explicit off still wins over any funded key.
    monkeypatch.delenv("LLM_TIER_CHAT_PROVIDER")
    monkeypatch.setenv("FREE_CHAT_QUOTA_ENABLED", "off")
    assert not chat_quota.quota_enabled()


# ── the fallback ─────────────────────────────────────────────────────────

def _stub():
    from bot.core.cost import CostTracker
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker()),
        conversations=SimpleNamespace(get_recent_as_llm_messages=lambda *a, **kw: []),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "sys",
    )


def test_when_haiku_fails_grok_answers(monkeypatch):
    import bot.skills.telegram_handler as th_mod
    from bot.skills.telegram_handler import TelegramHandler as H

    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: cfg)
    tried: list[str] = []

    async def _complete(client, cfg, *a, **kw):
        tried.append(cfg.provider.value)
        if cfg.provider == LLMProvider.ANTHROPIC:
            raise RuntimeError("credit balance is too low")
        return "grok says hi"

    monkeypatch.setattr(th_mod, "llm_complete", AsyncMock(side_effect=_complete))
    answer = asyncio.run(H._llm_chat(_stub(), "hello", is_admin=False))
    assert answer == "grok says hi"
    assert tried[:2] == ["anthropic", "grok"]


def test_when_haiku_answers_grok_is_not_called(monkeypatch):
    import bot.skills.telegram_handler as th_mod
    from bot.skills.telegram_handler import TelegramHandler as H

    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: cfg)
    seen: list[LLMConfig] = []

    async def _complete(client, cfg, *a, **kw):
        seen.append(cfg)
        return "haiku says hi"

    monkeypatch.setattr(th_mod, "llm_complete", AsyncMock(side_effect=_complete))
    answer = asyncio.run(H._llm_chat(_stub(), "hello", is_admin=False))
    assert answer == "haiku says hi"
    assert [c.model for c in seen] == ["claude-haiku-5-5"]

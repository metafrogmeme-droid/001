"""Ask the Claw, the coach in the Pi Browser app, keeps the Pi app's rules.

The Pi app carries no token, no sale and no link out (app/routes/piapp.js).
A prompt can ask a model for that; it cannot promise it. So the coach's turn
(`bot/web/user_gateway.py::handle_pi_coach`) has fixed answers before any
model (a token or sale question; a question needing a live price) and an
output check after it: a reply naming a sale, the project's token, a link,
signing up or exchange keys is withheld. Driven here through the gateway turn
with a stand-in model, and through `_llm_chat` with a stand-in provider, so
the prompt the model is actually handed is read, not the source that builds
it.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import TelegramHandler as H
from bot.web import pi_coach
from bot.web import user_gateway as ug


def _run(coro):
    return asyncio.run(coro)


# ── the gates, as readings ──────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "when does the presale end?", "Is RCLAW a good buy", "any airdrop for players?",
    "what is the token sale price", "tell me the tokenomics", "is there an ICO",
])
def test_a_token_or_sale_question_is_the_fixed_answer(q):
    assert pi_coach.token_ask(q)


@pytest.mark.parametrize("q", [
    "what is a stop-loss?", "explain the range round", "why did my BTC long lose?",
    "how do I check a receipt?", "what does 2R mean",
])
def test_a_trading_question_is_not(q):
    assert not pi_coach.token_ask(q)


@pytest.mark.parametrize("reply,why", [
    ('Read more at <a href="/token">our page</a>', "link"),
    ("See https://example.org for details", "link"),
    ("Visit www.humanoid-traders.com", "link"),
    ("Join us on t.me/somebot", "link"),
    ("The RCLAW presale is live", "token"),
    ("You could win an airdrop", "token"),
    ("Sign up on the website to trade for real", "sign_up"),
    ("Connect your exchange and add API keys", "exchange_keys"),
])
def test_a_reply_the_pi_app_may_not_show_is_named(reply, why):
    assert pi_coach.withheld_reason(reply) == why


@pytest.mark.parametrize("reply", [
    "A <b>stop-loss</b> closes your position at a price you chose, so one bad call cannot take everything.",
    "Your BTC long closed at <code>65500</code>: the price fell 1.2% after you opened, and 10× leverage made that 12%.",
    "Every round is sealed with a SHA-256 hash before anyone plays. Tap Check on a receipt to hash it again yourself.",
])
def test_a_plain_coaching_reply_is_shown(reply):
    assert pi_coach.withheld_reason(reply) is None


def test_the_facts_are_text_bounded_and_without_control_characters():
    assert pi_coach.clean_context(None) == ""
    assert pi_coach.clean_context({"x": 1}) == ""
    assert pi_coach.clean_context("a\x00b\x07c") == "abc"
    assert len(pi_coach.clean_context("x" * 9000)) == pi_coach.MAX_CONTEXT
    with_facts = pi_coach.build_prompt("Open: BTCUSDT LONG at 65000, 2x")
    assert "FACTS (read from the app" in with_facts and "BTCUSDT LONG at 65000" in with_facts
    assert "FACTS: none were read" in pi_coach.build_prompt("")


# ── the gateway turn ────────────────────────────────────────────────────────

class _Model:
    """A stand-in `_llm_chat`: answers `reply` with a model, or none at all."""

    def __init__(self, reply="A <b>stop-loss</b> closes the trade at your price.", meta=True):
        self.calls = []
        self.reply = reply
        self.meta = meta

    async def __call__(self, question, **kw):
        self.calls.append((question, kw))
        meta = {"provider": "p", "model": "m"} if self.meta else {}
        return (self.reply, meta) if kw.get("return_meta") else self.reply


def _turn(model, text="what is a stop-loss?", context="Open: none.", extra=None):
    body = {"text": text, "context": context, "lang": "en"}
    if extra:
        body.update(extra)

    async def _json():
        return body

    req = SimpleNamespace(app={"tg_handler": SimpleNamespace(_llm_chat=model), "engine": None},
                          json=_json, headers={}, remote="127.0.0.1")
    resp = _run(ug.handle_pi_coach(req))
    return resp.status, json.loads(resp.text)


def test_a_coaching_question_runs_the_account_free_turn_with_the_facts():
    model = _Model()
    status, out = _turn(model, context="Open: BTCUSDT LONG\x00 at 65000")
    assert status == 200
    assert out == {"reply_html": model.reply, "intent": "pi_coach", "answered": True}
    (_, kw), = model.calls
    assert kw["public"] is True, "the coach left the account-free turn"
    assert kw["user_id"] == "" and kw["is_admin"] is False
    assert kw["surface"] == "pi_coach"
    assert kw["coach_context"] == "Open: BTCUSDT LONG at 65000", "the facts were not cleaned"


def test_the_first_reply_of_a_session_starts_with_the_coach_notice(monkeypatch):
    monkeypatch.setenv("COACH_AI_DISCLOSURE_ENABLED", "1")
    monkeypatch.setenv("COACH_AI_DISCLOSURE_NETWORK", "testnet")
    assert pi_coach.disclosure_on() is True
    model = _Model(reply="A stop is a planned exit.")
    status, out = _turn(model, extra={"session_first": True})
    assert status == 200
    assert out["reply_html"].startswith(pi_coach.SESSION_PREFIX + "\n")
    assert out["reply_html"].endswith("A stop is a planned exit.")
    _, later = _turn(model)
    assert later["reply_html"] == "A stop is a planned exit."
    _, fixed = _turn(model, text="when is the RCLAW presale?", extra={"session_first": True})
    assert fixed["reply_html"].startswith(pi_coach.SESSION_PREFIX + "\n")
    assert pi_coach.TOKEN_ANSWER in fixed["reply_html"]
    assert len(model.calls) == 2


def test_the_prefix_stays_off_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("COACH_AI_DISCLOSURE_ENABLED", raising=False)
    monkeypatch.delenv("COACH_AI_DISCLOSURE_NETWORK", raising=False)
    assert pi_coach.disclosure_on() is False
    model = _Model()
    _, out = _turn(model, extra={"session_first": True})
    assert out == {"reply_html": model.reply, "intent": "pi_coach", "answered": True}


def test_a_token_question_is_answered_without_a_model_and_spends_nothing():
    model = _Model()
    _, out = _turn(model, text="when is the RCLAW presale?")
    assert out == {"reply_html": pi_coach.TOKEN_ANSWER, "intent": "pi_coach_token", "answered": False}
    assert model.calls == []


def test_a_live_price_question_is_answered_without_a_model():
    model = _Model()
    _, out = _turn(model, text="scan BTC")
    assert out["intent"] == "pi_coach_no_feed" and out["answered"] is False
    assert out["reply_html"] == pi_coach.NO_FEED_ANSWER
    assert model.calls == []


LIVE = pi_coach.LIVE_PRICES_HEAD + "2026-10-22 17:30 UTC:\n- LTCUSDT 88.42, 24h -1.30%"


@pytest.mark.parametrize("text", ["scan LTC", "what is the price of LTC now?", "LTC price"])
def test_a_price_question_goes_to_the_model_when_the_facts_carry_live_prices(text):
    model = _Model()
    _, out = _turn(model, text=text, context="Open: none.\n" + LIVE)
    assert out["intent"] == "pi_coach" and out["answered"] is True
    assert len(model.calls) == 1


@pytest.mark.parametrize("context", [
    "Open: none.\nLive prices: could not be read just now.",
    "Open: none.",
    "",
])
def test_without_live_prices_a_price_question_still_gets_the_fixed_answer(context):
    """Both arms: a failed read, and no read at all, keep the gate shut."""
    model = _Model()
    _, out = _turn(model, text="what is the price of LTC now?", context=context)
    assert out["intent"] == "pi_coach_no_feed" and model.calls == []


def test_the_coach_is_told_to_quote_only_the_prices_it_was_handed():
    assert "Live prices are in the FACTS block" in pi_coach.PROMPT
    assert "never state a price that is not there" in pi_coach.PROMPT
    assert "You have no live market feed" not in pi_coach.PROMPT
    assert "FACTS block's live prices" in pi_coach.REPLY_CONTRACT
    assert pi_coach.has_live_prices(LIVE) and not pi_coach.has_live_prices("Live prices: could not be read just now.")
    assert not pi_coach.has_live_prices(None)


def test_a_reply_with_a_link_is_withheld_but_counted():
    model = _Model(reply='Great question! Details at '
                         '<a href="https://www.humanoid-traders.com/token">the token page</a>.')
    _, out = _turn(model)
    assert out["reply_html"] == pi_coach.WITHHELD_ANSWER
    assert out["intent"] == "pi_coach_withheld" and out["withheld"] == "link"
    assert out["answered"] is True, "a model ran; the website counts it"
    assert "token" not in out["reply_html"].lower() or out["reply_html"] == pi_coach.WITHHELD_ANSWER


def test_no_model_answering_is_the_coachs_own_sentence_and_not_counted():
    model = _Model(reply="Chat has used its share... <a href=\"/dashboard\">Sign in</a>", meta=False)
    _, out = _turn(model)
    assert out == {"reply_html": pi_coach.UNAVAILABLE_ANSWER, "intent": "pi_coach_unavailable", "answered": False}


@pytest.mark.parametrize("text,status", [("", 400), ("x" * (pi_coach.MAX_TEXT + 1), 400)])
def test_an_empty_or_long_question_is_refused(text, status):
    model = _Model()
    got, _ = _turn(model, text=text)
    assert got == status and model.calls == []


def test_the_route_is_registered_behind_the_gateway_secret():
    import inspect
    src = inspect.getsource(ug.create_app) if hasattr(ug, "create_app") else inspect.getsource(ug)
    assert 'add_post("/chat/pi-coach", handle_pi_coach)' in src


# ── the prompt the model is actually handed ─────────────────────────────────

class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False, token_budget=None):
        return []


def _stub():
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker()),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "system prompt",
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture
def provider(monkeypatch):
    BYOK.reset()
    monkeypatch.setattr(th_mod, "resolve_tier_config",
                        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    llm = replace(th_mod.CONFIG.llm, api_key="", daily_call_limit=500, daily_budget_usd=1.0,
                  chat_budget_share=0.5, public_chat_budget_share=0.2)
    cfg = replace(th_mod.CONFIG, llm=llm)
    monkeypatch.setattr(th_mod, "CONFIG", cfg)
    import bot.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    seen: list = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        seen.append(system_prompt)
        return "a reply"

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    yield seen
    BYOK.reset()


def test_the_coach_turn_hands_the_model_the_coachs_prompt_and_facts(provider):
    _run(H._llm_chat(_stub(), "why did my trade lose?", public=True, surface="pi_coach",
                     coach_context="Closed: BTCUSDT LONG, result -12.50 credits"))
    assert provider, "no model was asked"
    prompt = provider[0]
    assert prompt.startswith(pi_coach.PROMPT)
    assert "Closed: BTCUSDT LONG, result -12.50 credits" in prompt
    assert "connect an exchange first" not in prompt, "the website's public prompt rode in"


def test_the_coach_turn_skips_the_websites_faq_and_the_public_turn_keeps_it(provider):
    # The FAQ's "what is RUNECLAW" answer is the website's pitch; on the coach
    # the model answers under the coach's rules instead.
    q = "What is RUNECLAW?"
    _run(H._llm_chat(_stub(), q, public=True, surface="pi_coach"))
    assert len(provider) == 1, "the coach answered from the website's FAQ"
    _run(H._llm_chat(_stub(), q, public=True))
    assert len(provider) == 1, "the public turn stopped answering its starter questions from the FAQ"

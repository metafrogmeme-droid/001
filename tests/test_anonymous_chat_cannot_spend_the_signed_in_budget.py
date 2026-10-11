"""Anonymous website chat has its own daily limits and cannot spend the
budget signed-in chat answers from.

The public chat (/api/public/chat, and the MCP `ask_runeclaw` tool, which is
the same chat) was limited to six messages a minute per address and nothing
per day, and every turn was booked to chat's one share of the LLM budget. At
the defaults a few hundred anonymous turns spent that share, and from then on
every signed-in user's chat said the budget was used up.

Two limits now, each named as the PREVIEW's when it is reached:

* a slice of chat's budget (`LLM_PUBLIC_CHAT_BUDGET_SHARE`, at most a half):
  the anonymous turns together stop there, and the rest is signed-in chat's;
* a per-visitor count per UTC day (`PUBLIC_CHAT_DAILY_LIMIT`), kept in the
  bot's memory against an opaque key the website sends, given back when no
  model answered.
"""
from __future__ import annotations

import ast
import asyncio
import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import (
    CHAT_SHARE_BOUND,
    PUBLIC_SHARE_BOUND,
    CostSummary,
    CostTracker,
    chat_budget_bound,
    chat_cap_usd,
    public_chat_budget_bound,
    public_chat_call_count,
    public_chat_spend_usd,
    resolve_llm_price,
)
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import TelegramHandler as H
from bot.utils.i18n import SUPPORTED_LANGS, t
from bot.web import public_chat_quota as pq
from bot.web import user_gateway as ug

ROOT = Path(__file__).resolve().parents[1]

#: A model id of the test's own, priced at $3.00 per million prompt tokens
#: (50k tokens is $0.15) by the fixture below.
_MODEL = "test-priced-model"


def _run(coro):
    return asyncio.run(coro)


def _spend(cost: CostTracker, usd: float, *, public: bool) -> None:
    cost.record_llm(model=_MODEL, prompt_tokens=int(math.ceil(usd / 3.0 * 1_000_000)),
                    completion_tokens=0, category="chat", public=public)


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False,
                                   token_budget=None):
        return []


def _stub(cost: CostTracker):
    return SimpleNamespace(
        engine=SimpleNamespace(cost=cost),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "system prompt",
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    import bot.core.cost as cost_mod
    monkeypatch.setitem(cost_mod.LLM_PRICING, _MODEL, {"in": 3.0, "out": 15.0})
    BYOK.reset()
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    pq._restore(None)
    yield
    BYOK.reset()


def _budget(monkeypatch, *, usd=1.0, share=0.5, public_share=0.2, calls=500,
            daily_limit=10):
    llm = replace(th_mod.CONFIG.llm, api_key="", daily_call_limit=calls,
                  daily_budget_usd=usd, chat_budget_share=share,
                  public_chat_budget_share=public_share,
                  public_chat_daily_limit=daily_limit)
    cfg = replace(th_mod.CONFIG, llm=llm)
    monkeypatch.setattr(th_mod, "CONFIG", cfg)
    import bot.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG", cfg)


def _ask(monkeypatch, cost: CostTracker, *, public: bool, turn_usd=0.0, lang=""):
    """One chat turn. Returns (answer, whether the model was asked). A model
    that answers reports `turn_usd` of measured usage, which `_llm_chat`
    books itself — the same path a real answer takes."""
    asked: list = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        asked.append(user_prompt)
        if kw.get("usage_out") is not None:
            price, _ = resolve_llm_price(cfg.model)
            tokens = int(math.ceil(turn_usd / price["in"] * 1_000_000))
            kw["usage_out"].update({"in": tokens, "out": 0})
        return "a reply"

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    answer = _run(H._llm_chat(_stub(cost), "how does funding work, roughly speaking?",
                              user_id="" if public else "u1", public=public,
                              reply_lang=lang))
    return answer, bool(asked)


# ── the budget slice, as a reading ──────────────────────────────────────────

class TestTheSlice:
    def _cfg(self, **kw):
        base = dict(daily_call_limit=500, daily_budget_usd=1.0, chat_budget_share=0.5,
                    chat_budget_usd=None, public_chat_budget_share=0.2)
        base.update(kw)
        return SimpleNamespace(**base)

    def _snap(self, *, public_usd, chat_usd, calls=1, public_calls=1):
        s = CostSummary(llm_cost_usd=chat_usd, llm_calls=calls)
        s.cost_by_category["chat"] = chat_usd
        s.calls_by_category["chat"] = calls
        s.public_chat_cost_usd = public_usd
        s.public_chat_calls = public_calls
        return s

    def test_the_preview_stops_at_its_slice_and_signed_in_chat_does_not(self):
        """$0.10 is a fifth of chat's $0.50: the preview's bound, and not
        chat's."""
        s = self._snap(public_usd=0.10, chat_usd=0.10)
        assert public_chat_budget_bound(s, self._cfg()) == PUBLIC_SHARE_BOUND
        assert chat_budget_bound(s, self._cfg()) == ""

    def test_under_the_slice_nothing_is_reached(self):
        s = self._snap(public_usd=0.09, chat_usd=0.40)
        assert public_chat_budget_bound(s, self._cfg()) == ""

    def test_chats_own_bound_comes_first_with_its_own_words(self):
        """Whatever stops chat stops the preview, and the refusal names the
        bound that was reached."""
        s = self._snap(public_usd=0.0, chat_usd=0.50, public_calls=0)
        assert public_chat_budget_bound(s, self._cfg()) == CHAT_SHARE_BOUND

    def test_the_slice_is_of_chats_own_cap_when_one_is_set(self):
        cfg = self._cfg(chat_budget_usd=0.30)
        assert chat_cap_usd(cfg) == pytest.approx(0.30)
        assert public_chat_budget_bound(self._snap(public_usd=0.059, chat_usd=0.1), cfg) == ""
        assert public_chat_budget_bound(
            self._snap(public_usd=0.06, chat_usd=0.1), cfg) == PUBLIC_SHARE_BOUND

    def test_a_cap_above_the_total_is_read_at_the_total(self):
        assert chat_cap_usd(self._cfg(chat_budget_usd=5.0)) == pytest.approx(1.0)

    def test_the_slice_holds_calls_too(self):
        """In-house models book zero dollars, so a dollar slice alone would
        not bind them."""
        cfg = self._cfg(daily_call_limit=10)
        assert public_chat_budget_bound(self._snap(public_usd=0, chat_usd=0, calls=2,
                                                   public_calls=1), cfg) == ""
        assert public_chat_budget_bound(self._snap(public_usd=0, chat_usd=0, calls=2,
                                                   public_calls=2), cfg) == PUBLIC_SHARE_BOUND

    def test_a_slice_of_zero_turns_the_model_off_for_the_preview(self):
        s = self._snap(public_usd=0.0, chat_usd=0.0, calls=0, public_calls=0)
        assert public_chat_budget_bound(s, self._cfg(public_chat_budget_share=0.0)) \
            == PUBLIC_SHARE_BOUND

    @pytest.mark.parametrize("value", [None, True, "0.01", float("nan"), -1.0])
    def test_an_unreadable_preview_figure_is_all_of_chats(self, value):
        """Every chat dollar may have been the preview's. Zero would let it
        spend unseen."""
        s = self._snap(public_usd=0.0, chat_usd=0.3)
        s.public_chat_cost_usd = value
        assert public_chat_spend_usd(s) == pytest.approx(0.3)

    @pytest.mark.parametrize("value", [None, True, -1, 1.5])
    def test_an_unreadable_preview_count_is_all_of_chats(self, value):
        s = self._snap(public_usd=0.0, chat_usd=0.0, calls=7)
        s.public_chat_calls = value
        assert public_chat_call_count(s) == 7

    def test_the_bounds_are_the_ones_the_config_declares(self):
        """The clamp is applied at import, which a test cannot re-run without
        reloading the whole config; the declaration is what a reload reads."""
        tree = ast.parse((ROOT / "bot" / "config.py").read_text(encoding="utf-8"))
        decls = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_env_float_bounded" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                decls[node.args[0].value] = [ast.literal_eval(a) for a in node.args[1:]
                                             if isinstance(a, ast.Constant)]
        assert decls["LLM_PUBLIC_CHAT_BUDGET_SHARE"] == [0.2, 0.0, 0.5]
        assert decls["PUBLIC_CHAT_DAILY_LIMIT"] == [10, 1, 500]


class TestTheLedger:
    def test_a_public_turn_is_chat_and_also_the_previews(self):
        c = CostTracker()
        _spend(c, 0.15, public=True)
        _spend(c, 0.15, public=False)
        s = c.snapshot()
        assert s.cost_by_category["chat"] == pytest.approx(0.30)
        assert s.public_chat_cost_usd == pytest.approx(0.15)
        assert s.public_chat_calls == 1
        assert s.llm_cost_usd == pytest.approx(0.30), "a subset, never added twice"

    def test_only_a_chat_call_can_be_the_previews(self):
        c = CostTracker()
        c.record_llm(model=_MODEL, prompt_tokens=50_000, completion_tokens=0,
                     category="thesis", public=True)
        assert c.snapshot().public_chat_calls == 0


# ── driven through the chat ─────────────────────────────────────────────────

def test_anonymous_turns_stop_and_signed_in_chat_still_answers(monkeypatch):
    """The finding, end to end: anonymous turns at $0.04 each until refused,
    then a signed-in turn. Before, the anonymous turns ran to chat's whole
    $0.50 and the signed-in turn was refused with "the AI budget is used up"."""
    _budget(monkeypatch)
    cost = CostTracker()
    answered = 0
    refusal = ""
    for _ in range(40):
        answer, asked = _ask(monkeypatch, cost, public=True, turn_usd=0.04)
        if not asked:
            refusal = answer
            break
        answered += 1
    assert answered == 3, answered  # 0, 0.04, 0.08 were under the $0.10 slice
    assert "public preview" in refusal and "not the bot's" in refusal
    assert "used up today's AI budget" not in refusal
    snap = cost.snapshot()
    assert snap.public_chat_cost_usd < chat_cap_usd(th_mod.CONFIG.llm)
    answer, asked = _ask(monkeypatch, cost, public=False, turn_usd=0.04)
    assert asked and answer == "a reply"


def test_the_signed_in_spend_does_not_count_as_the_previews(monkeypatch):
    """The other arm: chat spent $0.40 signed in, the preview $0, so the
    preview still answers (its slice is of what IT spent)."""
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.40, public=False)
    _, asked = _ask(monkeypatch, cost, public=True)
    assert asked


def test_the_preview_refusal_speaks_the_visitors_language(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.10, public=True)
    es, asked = _ask(monkeypatch, cost, public=True, lang="es")
    assert not asked
    assert es == t("chat_public_share_exhausted", "es") != t("chat_public_share_exhausted", "en")


def test_the_whole_budget_still_bounds_the_preview_with_its_own_words(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    cost.record_llm(model=_MODEL, prompt_tokens=400_000, completion_tokens=0,
                    category="thesis")  # $1.20, over the $1.00 total
    answer, asked = _ask(monkeypatch, cost, public=True)
    assert not asked
    assert "used up today's AI budget" in answer


# ── the per-visitor day count ───────────────────────────────────────────────

class _Model:
    """A stand-in `_llm_chat` that answers with a model, or without one."""

    def __init__(self, meta=True):
        self.calls = 0
        self.meta = meta

    async def __call__(self, question, **kw):
        self.calls += 1
        meta = {"provider": "test-provider", "model": "test-model"} if self.meta else {}
        return ("answer", meta) if kw.get("return_meta") else "answer"


def _turn(model, text="how does funding work, roughly speaking?", visitor="a" * 32,
          lang=""):
    body = {"text": text}
    if visitor is not None:
        body["visitor"] = visitor
    if lang:
        body["lang"] = lang

    async def _json():
        return body

    req = SimpleNamespace(app={"tg_handler": SimpleNamespace(_llm_chat=model),
                               "engine": SimpleNamespace()},
                          json=_json, headers={}, remote="127.0.0.1")
    return json.loads(_run(ug._public_chat_turn(req)).text)


def test_a_visitor_past_the_limit_is_told_the_previews_limit(monkeypatch):
    _budget(monkeypatch, daily_limit=3)
    model = _Model()
    for _ in range(3):
        assert _turn(model)["intent"] == "chat"
    out = _turn(model)
    assert out["intent"] == "public_daily_limit"
    assert model.calls == 3, "the model must not run past the limit"
    assert "public preview answers 3 questions a day" in out["reply_html"]
    assert "not the bot's budget" in out["reply_html"]
    # The other arm: another visitor is not this one.
    assert _turn(model, visitor="b" * 32)["intent"] == "chat"


def test_the_limit_speaks_the_visitors_language(monkeypatch):
    _budget(monkeypatch, daily_limit=1)
    model = _Model()
    _turn(model, lang="de")
    out = _turn(model, lang="de")
    assert out["intent"] == "public_daily_limit"
    assert "öffentliche Vorschau beantwortet 1 Fragen" in out["reply_html"]


@pytest.mark.parametrize("lang", sorted(SUPPORTED_LANGS))
def test_every_language_fills_the_limit_and_the_reset(lang):
    out = pq.limit_notice({"limit": 7, "reset_in_seconds": 5 * 3600}, lang)
    assert "7" in out and "{" not in out, out


def test_a_turn_no_model_answered_is_given_back(monkeypatch):
    """A built-in answer or a failure spent nothing, so it costs the visitor
    no question — the signed-in quota's rule."""
    _budget(monkeypatch, daily_limit=2)
    silent = _Model(meta=False)
    for _ in range(5):
        assert _turn(silent)["intent"] == "chat"
    answering = _Model()
    assert _turn(answering)["intent"] == "chat"
    assert _turn(answering)["intent"] == "chat"
    assert _turn(answering)["intent"] == "public_daily_limit"


def test_a_turn_with_no_visitor_key_is_allowed_and_not_counted(monkeypatch):
    """Absent is not a visitor: a website older than the key would otherwise
    put every visitor on one count."""
    _budget(monkeypatch, daily_limit=1)
    model = _Model()
    for _ in range(4):
        assert _turn(model, visitor=None)["intent"] == "chat"
    assert pq.consume(None)["unmetered"] == "no_visitor_key"
    # A malformed key is not a key either.
    assert pq.visitor_key("1.2.3.4") is None
    assert pq.visitor_key("A" * 32) == "a" * 32


def test_the_scan_gate_and_the_cards_cost_no_question(monkeypatch):
    _budget(monkeypatch, daily_limit=1)
    model = _Model()
    assert _turn(model, text="scan BTC")["intent"] == "public_scan_gate"
    assert _turn(model, text="what can you do")["intent"] == "help"
    assert _turn(model)["intent"] == "chat"
    assert _turn(model)["intent"] == "public_daily_limit"


def test_the_count_starts_again_on_a_new_utc_day(monkeypatch):
    _budget(monkeypatch, daily_limit=1)
    monkeypatch.setattr(pq, "_today", lambda: "2026-10-10")
    assert pq.consume("c" * 32)["allowed"]
    assert not pq.consume("c" * 32)["allowed"]
    monkeypatch.setattr(pq, "_today", lambda: "2026-10-11")
    assert pq.consume("c" * 32)["allowed"]


def test_a_full_table_lets_a_new_visitor_in_uncounted_and_keeps_the_others(monkeypatch):
    """Evicting somebody's count to make room would hand them a fresh day."""
    _budget(monkeypatch, daily_limit=1)
    monkeypatch.setattr(pq, "MAX_VISITORS", 1)
    assert pq.consume("d" * 32)["allowed"]
    assert pq.consume("e" * 32)["unmetered"] == "table_full"
    assert not pq.consume("d" * 32)["allowed"]


def test_a_refund_never_mints_a_question(monkeypatch):
    _budget(monkeypatch, daily_limit=1)
    pq.refund("f" * 32)
    pq.refund("f" * 32)
    assert pq.consume("f" * 32)["allowed"]
    assert not pq.consume("f" * 32)["allowed"]

"""One signed-in user cannot spend the chat budget every other user answers from.

Signed-in chat was one pool. Chat stops at its share of the daily LLM budget
(`LLM_CHAT_BUDGET_SHARE`), and the anonymous preview has its own slice of that
(`LLM_PUBLIC_CHAT_BUDGET_SHARE`), but among signed-in users nothing was
divided: one person asking all day spent chat's whole share, and from then on
every other user was told "Chat has used its share of today's AI budget" until
00:00 UTC. With registration open, that is one account taking the AI away from
everyone.

Each signed-in user who is not an admin now stops at a slice of chat's budget
(`LLM_USER_CHAT_BUDGET_SHARE`, default a quarter), in dollars and in calls:

* the ledger books every signed-in chat call to its user as a subset of chat's
  figures, kept across a restart like the rest of the day's spend;
* `user_chat_budget_bound` reads it, after chat's own bounds;
* the refusal names the person's share, not the bot's budget;
* admins are not held to it, and the anonymous preview keeps its own slice;
* the rolling-note fold, a chat call booked to the same user, stops there too.
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
    SPEND_RESTORED,
    SPEND_UNREADABLE,
    USER_SHARE_BOUND,
    CostSummary,
    CostTracker,
    budget_reading,
    chat_budget_bound,
    resolve_llm_price,
    user_chat_budget_bound,
    user_chat_call_count,
    user_chat_cap_usd,
    user_chat_spend_usd,
)
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import TelegramHandler as H
from bot.utils.i18n import SUPPORTED_LANGS, t

ROOT = Path(__file__).resolve().parents[1]

#: A model id of the test's own, priced at $3.00 per million prompt tokens.
_MODEL = "test-priced-model"


def _run(coro):
    return asyncio.run(coro)


def _usd(v):
    """A dollar figure booked through whole tokens: `_spend` rounds the token
    count up, so a booking can exceed its round figure by a token's price."""
    return pytest.approx(v, abs=1e-5)


def _spend(cost: CostTracker, usd: float, *, user=None, public=False, category="chat") -> None:
    cost.record_llm(model=_MODEL, prompt_tokens=int(math.ceil(usd / 3.0 * 1_000_000)),
                    completion_tokens=0, category=category, public=public, user=user)


class _Conversations:
    def __init__(self, pending=None):
        self.pending = list(pending or [])
        self.pushed_back: list = []

    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False,
                                   token_budget=None):
        return []

    def take_pending_summary(self, user_id):
        out, self.pending = self.pending, []
        return out

    def push_back_pending(self, user_id, pending):
        self.pushed_back.append((user_id, list(pending)))

    def get_context(self, user_id):
        return SimpleNamespace(summary="")

    def set_summary(self, user_id, note):
        self.note = note


def _stub(cost: CostTracker, conversations=None):
    return SimpleNamespace(
        engine=SimpleNamespace(cost=cost),
        conversations=conversations or _Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "system prompt",
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _SUMMARY_SYSTEM_PROMPT=H._SUMMARY_SYSTEM_PROMPT,
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
    yield
    BYOK.reset()


def _budget(monkeypatch, *, usd=1.0, share=0.5, user_share=0.25, calls=500):
    llm = replace(th_mod.CONFIG.llm, api_key="", daily_call_limit=calls,
                  daily_budget_usd=usd, chat_budget_share=share,
                  chat_budget_usd=None, user_chat_budget_share=user_share)
    cfg = replace(th_mod.CONFIG, llm=llm)
    monkeypatch.setattr(th_mod, "CONFIG", cfg)
    import bot.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG", cfg)


def _ask(monkeypatch, cost: CostTracker, *, user: str, is_admin=False, turn_usd=0.0, lang=""):
    """One signed-in chat turn. Returns (answer, whether the model was asked).
    A model that answers reports `turn_usd` of measured usage, which
    `_llm_chat` books itself, the path a real answer takes."""
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
                              user_id=user, is_admin=is_admin, reply_lang=lang))
    return answer, bool(asked)


def _cfg(**kw):
    base = dict(daily_call_limit=500, daily_budget_usd=1.0, chat_budget_share=0.5,
                chat_budget_usd=None, public_chat_budget_share=0.2,
                user_chat_budget_share=0.25)
    base.update(kw)
    return SimpleNamespace(**base)


def _snap(*, chat_usd, users=None, calls=None, chat_calls=1):
    s = CostSummary(llm_cost_usd=chat_usd, llm_calls=chat_calls)
    s.cost_by_category["chat"] = chat_usd
    s.calls_by_category["chat"] = chat_calls
    s.chat_cost_by_user = dict(users or {})
    s.chat_calls_by_user = dict(calls if calls is not None
                                else {u: 1 for u in (users or {})})
    return s


# ── the slice, as a reading ─────────────────────────────────────────────────

class TestTheSlice:
    def test_a_user_stops_at_their_slice_and_chat_does_not(self):
        """Chat's $0.50, a quarter of it each: $0.125 is this user's bound
        and not chat's, and another user is untouched."""
        s = _snap(chat_usd=0.125, users={"a": 0.125})
        assert user_chat_budget_bound(s, _cfg(), "a") == USER_SHARE_BOUND
        assert user_chat_budget_bound(s, _cfg(), "b") == ""
        assert chat_budget_bound(s, _cfg()) == ""

    def test_under_the_slice_nothing_is_reached(self):
        s = _snap(chat_usd=0.124, users={"a": 0.124})
        assert user_chat_budget_bound(s, _cfg(), "a") == ""

    def test_chats_own_bound_comes_first_with_its_own_words(self):
        s = _snap(chat_usd=0.50, users={"b": 0.50})
        assert user_chat_budget_bound(s, _cfg(), "a") == CHAT_SHARE_BOUND

    def test_the_slice_holds_calls_too(self):
        """In-house models book zero dollars, so a dollar slice alone would
        not bind them: a quarter of 20 calls is 5."""
        cfg = _cfg(daily_call_limit=20)
        assert user_chat_budget_bound(_snap(chat_usd=0, users={"a": 0.0}, calls={"a": 4},
                                            chat_calls=4), cfg, "a") == ""
        assert user_chat_budget_bound(_snap(chat_usd=0, users={"a": 0.0}, calls={"a": 5},
                                            chat_calls=5), cfg, "a") == USER_SHARE_BOUND

    def test_a_share_of_one_is_the_old_single_pool(self):
        """1.0 restores the old behaviour: one user can spend chat's whole share,
        and what stops them is chat's own bound."""
        cfg = _cfg(user_chat_budget_share=1.0)
        assert user_chat_budget_bound(_snap(chat_usd=0.49, users={"a": 0.49}), cfg, "a") == ""
        assert user_chat_budget_bound(_snap(chat_usd=0.50, users={"a": 0.50}), cfg, "a") \
            == CHAT_SHARE_BOUND

    def test_a_share_of_zero_turns_the_model_off_for_users(self):
        s = _snap(chat_usd=0.0, users={}, calls={}, chat_calls=0)
        assert user_chat_budget_bound(s, _cfg(user_chat_budget_share=0.0), "a") == USER_SHARE_BOUND

    def test_the_cap_is_a_slice_of_chats_own_cap_when_one_is_set(self):
        assert user_chat_cap_usd(_cfg(chat_budget_usd=0.40)) == pytest.approx(0.10)
        assert user_chat_cap_usd(_cfg()) == pytest.approx(0.125)

    def test_no_entry_is_a_measured_zero(self):
        """Every chat booking that names a user adds to its entry, so a user
        with none made no call today."""
        s = _snap(chat_usd=0.4, users={"b": 0.4})
        assert user_chat_spend_usd(s, "a") == 0.0
        assert user_chat_call_count(s, "a") == 0

    @pytest.mark.parametrize("value", [None, True, "0.01", float("nan"), -1.0])
    def test_an_unreadable_figure_is_all_of_chats(self, value):
        """Every chat dollar may have been this user's. Zero would let them
        spend unseen."""
        s = _snap(chat_usd=0.3, users={"a": 0.0})
        s.chat_cost_by_user["a"] = value
        assert user_chat_spend_usd(s, "a") == pytest.approx(0.3)

    def test_an_unreadable_map_is_all_of_chats(self):
        s = _snap(chat_usd=0.3, users={})
        s.chat_cost_by_user = None
        s.chat_calls_by_user = "garbled"
        assert user_chat_spend_usd(s, "a") == pytest.approx(0.3)
        assert user_chat_call_count(s, "a") == 1

    def test_the_bounds_are_the_ones_the_config_declares(self):
        tree = ast.parse((ROOT / "bot" / "config.py").read_text(encoding="utf-8"))
        decls = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_env_float_bounded" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                decls[node.args[0].value] = [ast.literal_eval(a) for a in node.args[1:]
                                             if isinstance(a, ast.Constant)]
        assert decls["LLM_USER_CHAT_BUDGET_SHARE"] == [0.25, 0.0, 1.0]


# ── the ledger ──────────────────────────────────────────────────────────────

class TestTheLedger:
    def test_a_signed_in_turn_is_chat_and_also_its_users(self):
        c = CostTracker()
        _spend(c, 0.15, user="a")
        _spend(c, 0.06, user="a")
        _spend(c, 0.03, user="b")
        s = c.snapshot()
        assert s.cost_by_category["chat"] == _usd(0.24)
        assert s.chat_cost_by_user == _usd({"a": 0.21, "b": 0.03})
        assert s.chat_calls_by_user == {"a": 2, "b": 1}
        assert s.llm_cost_usd == _usd(0.24), "a subset, never added twice"

    def test_only_a_signed_in_chat_call_is_a_users(self):
        c = CostTracker()
        _spend(c, 0.15, user="a", category="thesis")
        _spend(c, 0.15, user="a", public=True)
        _spend(c, 0.15, user=None)
        _spend(c, 0.15, user="")
        assert c.snapshot().chat_cost_by_user == {}

    def test_a_snapshot_is_a_copy(self):
        c = CostTracker()
        _spend(c, 0.05, user="a")
        s = c.snapshot()
        s.chat_cost_by_user["a"] = 99.0
        s.chat_calls_by_user["a"] = 99
        again = c.snapshot()
        assert again.chat_cost_by_user["a"] == _usd(0.05)
        assert again.chat_calls_by_user["a"] == 1

    def test_the_split_outlives_a_restart(self, tmp_path):
        path = str(tmp_path / "llm_spend.json")
        c = CostTracker(path=path)
        _spend(c, 0.10, user="a")
        back = CostTracker(path=path)
        assert back.persistence == SPEND_RESTORED
        s = back.snapshot()
        assert s.chat_cost_by_user == _usd({"a": 0.10})
        assert s.chat_calls_by_user == {"a": 1}
        assert s.user_split_since is None, "a ledger that kept the split covers the whole day"
        assert user_chat_budget_bound(s, _cfg(), "a") == ""
        _spend(back, 0.03, user="a")
        assert user_chat_budget_bound(back.snapshot(), _cfg(), "a") == USER_SHARE_BOUND

    def test_a_ledger_from_before_the_split_counts_users_from_this_start(self, tmp_path):
        """Today's figures stand; each user's split counts from the read, and
        the summary says when that was, so the card does not claim a whole day."""
        path = tmp_path / "llm_spend.json"
        c = CostTracker(path=str(path))
        _spend(c, 0.20, user="a")
        data = json.loads(path.read_text())
        for k in ("chat_cost_by_user", "chat_calls_by_user", "user_split_since"):
            del data["daily"][k]
        path.write_text(json.dumps(data))
        back = CostTracker(path=str(path))
        assert back.persistence == SPEND_RESTORED
        s = back.snapshot()
        assert s.cost_by_category["chat"] == _usd(0.20), "the day's spend stands"
        assert s.chat_cost_by_user == {}
        assert isinstance(s.user_split_since, str) and s.user_split_since
        assert budget_reading(s, _cfg())["user_split_since"] == s.user_split_since

    @pytest.mark.parametrize("hole", [
        {"chat_cost_by_user": {"a": 0.1}},                      # one map, not the other
        {"chat_cost_by_user": {"a": "x"}, "chat_calls_by_user": {"a": 1}},
        {"chat_cost_by_user": {}, "chat_calls_by_user": {}, "user_split_since": 5},
    ])
    def test_a_ledger_with_a_hole_in_the_split_is_unreadable(self, tmp_path, hole):
        path = tmp_path / "llm_spend.json"
        c = CostTracker(path=str(path))
        _spend(c, 0.05, user="a")
        data = json.loads(path.read_text())
        for k in ("chat_cost_by_user", "chat_calls_by_user", "user_split_since"):
            data["daily"].pop(k, None)
        data["daily"].update(hole)
        path.write_text(json.dumps(data))
        assert CostTracker(path=str(path)).persistence == SPEND_UNREADABLE


# ── driven through the chat ─────────────────────────────────────────────────

def test_one_user_stops_at_their_share_and_everyone_else_still_answers(monkeypatch):
    """The finding, end to end: one user asks at $0.04 a turn until refused,
    then a second user asks. Before, the first user ran to chat's whole $0.50
    and the second was told chat's share was used up."""
    _budget(monkeypatch)
    cost = CostTracker()
    answered, refusal = 0, ""
    for _ in range(40):
        answer, asked = _ask(monkeypatch, cost, user="u1", turn_usd=0.04)
        if not asked:
            refusal = answer
            break
        answered += 1
    assert answered == 4, answered  # at 0, 0.04, 0.08, 0.12: under the $0.125 slice
    assert "your share of today's AI chat budget" in refusal
    assert "used up today's AI budget" not in refusal
    assert "Chat has used its share" not in refusal
    snap = cost.snapshot()
    assert snap.chat_cost_by_user["u1"] == _usd(0.16)
    answer, asked = _ask(monkeypatch, cost, user="u2", turn_usd=0.04)
    assert asked and answer == "a reply", "the next user was refused for the first one's spend"


def test_an_admin_is_not_held_to_one_users_share(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    for _ in range(5):
        _answer, asked = _ask(monkeypatch, cost, user="op", is_admin=True, turn_usd=0.04)
        assert asked, "the operator was held to a user's slice"
    assert cost.snapshot().chat_cost_by_user["op"] == _usd(0.20)


def test_the_refusal_speaks_the_users_language(monkeypatch):
    _budget(monkeypatch, user_share=0.0)
    answer, asked = _ask(monkeypatch, CostTracker(), user="u1", lang="es")
    assert not asked
    assert t("chat_user_share_exhausted", "es") in answer
    assert t("chat_user_share_exhausted", "es") != t("chat_user_share_exhausted", "en")


def test_the_refusal_has_every_language():
    for lang in SUPPORTED_LANGS:
        assert t("chat_user_share_exhausted", lang).strip(), lang


def test_the_handlers_english_is_the_dictionarys():
    """The handler passes its English as the fallback; a second copy that
    drifted would be a second sentence for one refusal."""
    src = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and len(n.args) == 3 and isinstance(n.args[1], ast.Constant)
             and n.args[1].value == "chat_user_share_exhausted"]
    assert len(found) == 1
    assert ast.literal_eval(found[0].args[2]) == t("chat_user_share_exhausted", "en")


# ── the rolling-note fold ───────────────────────────────────────────────────

def _fold(monkeypatch, cost: CostTracker, *, user="u1", is_admin=False):
    folded: list = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        folded.append(user_prompt)
        return "a note"

    monkeypatch.setattr(th_mod, "resolve_tier_config",
                        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI,
                                                   api_key="k", model=_MODEL))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    conv = _Conversations(pending=[{"role": "user", "content": "an old turn"}])
    _run(H._summarize_if_due(_stub(cost, conv), user, is_admin=is_admin))
    return bool(folded), conv


def test_a_user_past_their_share_folds_nothing_and_loses_nothing(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.13, user="u1")
    asked, conv = _fold(monkeypatch, cost)
    assert not asked, "the fold spent past the user's share"
    assert conv.pushed_back, "the turns were dropped instead of kept for tomorrow"


def test_a_user_under_their_share_folds_and_it_is_booked_to_them(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.05, user="u1")
    asked, conv = _fold(monkeypatch, cost)
    assert asked and not conv.pushed_back
    assert cost.snapshot().chat_calls_by_user["u1"] == 2


def test_an_admin_folds_past_a_users_share(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.13, user="op")
    asked, _conv = _fold(monkeypatch, cost, user="op", is_admin=True)
    assert asked


# ── the card ────────────────────────────────────────────────────────────────

def test_the_reading_names_amounts_never_people():
    s = _snap(chat_usd=0.2, users={"tg-77410": 0.15, "tg-88123": 0.05})
    b = budget_reading(s, _cfg())
    assert b["user_cap"] == pytest.approx(0.125)
    assert b["users_today"] == 2
    assert b["busiest_user_spent"] == pytest.approx(0.15)
    dumped = json.dumps(b)
    assert "tg-77410" not in dumped and "tg-88123" not in dumped, "the reading names a user"
    empty = budget_reading(_snap(chat_usd=0.0, users={}, calls={}, chat_calls=0), _cfg())
    assert empty["busiest_user_spent"] is None, "no user booked is not 'the busiest spent $0'"

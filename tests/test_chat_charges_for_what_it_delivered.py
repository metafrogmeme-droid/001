"""Four chat defects: a quota spent on nothing, a tier read as free, a
prompt that told the model every trade lost, and a budget that could not add.

The thread through all four is that the correct value was already in scope and
something cheaper was used instead.
"""
import io
import os
import subprocess
import sys

import pytest

from bot.core.cost import COST_CATEGORIES, CostTracker
from bot.web import chat_quota
from tests.source_scan import code_only

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheTimeoutReachesEveryBranch:
    """`LLM_TIMEOUT_SEC` was set in .env and reached one branch of five."""

    @staticmethod
    def _timeout(value):
        """A FRESH interpreter: the field's default is read at instantiation,
        and reloading the module in-process is what wrecked the suite once."""
        env = dict(os.environ)
        env.pop("LLM_TIMEOUT_SEC", None)
        if value is not None:
            env["LLM_TIMEOUT_SEC"] = value
        out = subprocess.run(
            [sys.executable, "-c",
             "from bot.llm.provider import LLMConfig, LLMProvider;"
             " print(LLMConfig(provider=LLMProvider.OPENAI,"
             " api_key='k').timeout_seconds)"],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        return float(out.stdout.strip().splitlines()[-1])

    def test_unset_keeps_the_documented_default(self):
        assert self._timeout(None) == 15.0

    def test_set_is_read(self):
        assert self._timeout("90") == 90.0

    @pytest.mark.parametrize("junk", ["", "   ", "ninety", "9.0.1"])
    def test_junk_falls_back_rather_than_raising_at_import(self, junk):
        # Evaluated while a config object is being constructed: a bad env var
        # must not take the whole LLM stack down.
        assert self._timeout(junk) == 15.0

    @pytest.mark.parametrize("bad", ["0", "-5"])
    def test_a_non_positive_ceiling_is_refused(self, bad):
        # asyncio.wait_for(timeout=0) fires instantly on every call —
        # configured, and worse than unconfigured.
        assert self._timeout(bad) == 15.0

    def test_no_branch_of_the_resolver_opts_out_of_the_default(self):
        """SCANNED, not driven, and deliberately.

        Every `LLMConfig(...)` inside `resolve_tier_config` relies on the
        field's own default; only the primary branch inherited a configured
        value, via `replace(primary_config, ...)`. Which branch a bare
        environment happens to take is not controllable from a test, so the
        property that matters is that NO branch passes its own
        timeout_seconds and shadows the factory — a wiring fact a unit test
        cannot reach.
        """
        code = code_only(
            io.open("bot/llm/provider.py", encoding="utf-8").read())
        i = code.index("def resolve_tier_config")
        j = code.index("\ndef ", i + 20)
        assert "timeout_seconds" not in code[i:j], (
            "a branch passing its own timeout_seconds would shadow the "
            "env-reading default and reintroduce the bug for that path")
        assert "field(default_factory=_env_timeout)" in code


class TestAQuestionThatBoughtNothingIsRefunded:
    @pytest.fixture(autouse=True)
    def _funded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(chat_quota, "quota_enabled", lambda: True)
        monkeypatch.setattr(chat_quota, "free_daily_limit", lambda: 5)
        monkeypatch.setattr(chat_quota, "_STORE", str(tmp_path / "q.json"),
                            raising=False)
        monkeypatch.setattr(chat_quota, "_load", lambda: dict(_MEM))
        monkeypatch.setattr(chat_quota, "_save",
                            lambda d: (_MEM.clear(), _MEM.update(d)))
        _MEM.clear()

    def test_a_refund_gives_the_question_back(self):
        assert chat_quota.consume("u1", "basic")["used"] == 1
        chat_quota.refund("u1", "basic")
        assert chat_quota.status("u1", "basic")["used"] == 0

    def test_refunds_can_never_mint_questions(self):
        chat_quota.consume("u1", "basic")
        for _ in range(5):
            chat_quota.refund("u1", "basic")
        assert chat_quota.status("u1", "basic")["used"] == 0

    def test_an_exempt_caller_is_untouched(self):
        chat_quota.refund("u1", "pro")
        assert chat_quota.status("u1", "basic")["used"] == 0


class TestAnUnreadableTierIsNotAFreeTier:
    def test_none_is_not_exempt_which_is_why_the_guard_exists(self):
        # The trap: resolving an unreadable tier to None and passing it to
        # consume() would STILL meter the caller. The guard has to be at the
        # call site; making an absent tier exempt would silently unmeter every
        # caller that omits the argument.
        assert chat_quota.is_quota_exempt(None) is False

    def test_unmetered_allows_without_counting(self):
        u = chat_quota.unmetered()
        assert u["allowed"] is True and u["exempt"] is True
        assert u["unmetered"] is True

    def test_the_gateway_never_bills_an_unclassified_caller(self):
        code = code_only(
            io.open("bot/web/user_gateway.py", encoding="utf-8").read())
        # `basic` is not quota-exempt, so this billed pro/elite subscribers
        # against the FREE cap and then sold them an upgrade they had bought.
        assert '_tier = "admin" if _is_admin else "basic"' not in code
        # Both surfaces — chat and Contract Studio carry the same shape.
        assert code.count("chat_quota.unmetered()") == 2
        assert code.count("chat_quota.refund(") == 2


class TestTheBudgetCanAddUp:
    def test_chat_is_a_first_class_cost_category(self):
        # It was absent, so record_llm(category="chat") coerced to "other" and
        # chat spend could not be told from analysis spend.
        assert "chat" in COST_CATEGORIES

    def test_the_system_prompt_is_measured_not_guessed(self):
        code = code_only(
            io.open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "prompt_tokens=500 + history_tokens" not in code, (
            "500 against a ~1,030-token system prompt let the daily budget "
            "guard pass roughly three times what it was set")
        assert "len(system_prompt or \"\") // 4" in code

    def test_a_bigger_prompt_books_a_bigger_cost(self):
        """The property, driven: the estimate must track the real string."""
        c = CostTracker()
        c.record_llm(model="gpt-4o", prompt_tokens=250,
                     completion_tokens=10, category="chat")
        small = c.snapshot().llm_cost_usd
        c2 = CostTracker()
        c2.record_llm(model="gpt-4o", prompt_tokens=2500,
                      completion_tokens=10, category="chat")
        assert c2.snapshot().llm_cost_usd > small


_MEM: dict = {}


class TestThePaperPromptDoesNotClaimEveryTradeLost:
    def test_a_fresh_paper_account_is_not_measurable(self):
        from bot.utils.win_rate import win_stats
        # PortfolioState.win_rate is `... if total > 0 else 0.0`, and that 0.0
        # went into the LLM's context as "win rate 0%".
        assert win_stats([]) ["rate"] is None

    def test_the_paper_branch_uses_the_same_reader_as_the_live_one(self):
        code = code_only(
            io.open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert 'f"win rate {state.win_rate:.0%}, "' not in code, (
            "the live branch four lines up was cured of exactly this, under a "
            "comment saying a manufactured zero shapes the advice that comes "
            "back — and paper is the DEFAULT mode")
        assert "_wr_paper" in code

    def test_an_unscorable_paper_book_says_so(self):
        from bot.utils.win_rate import win_stats

        class _T:
            def __init__(self, p):
                self.pnl_usd = p
        s = win_stats([_T(None), _T(None)])
        assert s["rate"] is None and s["unscored"] == 2

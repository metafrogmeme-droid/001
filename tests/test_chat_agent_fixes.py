"""Four defects on the chat surface, one per section.

Ranked by what a user notices: a brochure instead of an answer, a silently
downgraded model, a position with no current price, and a bot that cannot say
what it is about to trade. The pricing one is money safety and lives in
tests/test_model_catalog_2026.py beside the guard that missed it.
"""
import os
import tempfile
from types import SimpleNamespace as NS

import pytest

from bot.core.faq_kb import faq_answer
from bot.nlp.conversation_store import ConversationStore
from bot.skills.telegram_handler import _live_positions_block

# ── 1. The FAQ short-circuit ────────────────────────────────────────────────

class TestFaqDoesNotHijackRealQuestions:
    """`if _norm(trig) in q` matched anywhere in the message, and the trigger
    list holds bare phrases like "what are you" and "leverage work"."""

    @pytest.mark.parametrize("q", [
        "what are you seeing on BTC right now",
        "who are you bullish on",
        "what are you doing with my money",
        "what are you thinking about my portfolio",
        "is my leverage working against me right now",
        "how do you manage risk on my open position",
    ])
    def test_an_account_question_reaches_the_model(self, q):
        assert faq_answer(q) is None, "canned brochure returned for a real question"

    @pytest.mark.parametrize("q", [
        "what is runeclaw",
        "what is runeclaw exactly?",
        "who are you",
        "tell me about runeclaw",
        "how does leverage work",
        "how do you manage risk",
        "what is a liquidity sweep",
        "which exchanges",
        "what exchanges are supported",
    ])
    def test_a_landing_page_starter_still_answers(self, q):
        assert faq_answer(q) is not None, "starter question stopped being answered"

    def test_the_slack_scales_with_the_trigger(self):
        # A flat allowance was tried first and let "who are you bullish on"
        # through on the 3-word trigger "who are you".
        from bot.core.faq_kb import _slack_for
        assert _slack_for(2) == 1
        assert _slack_for(3) == 1
        assert _slack_for(5) >= 2

    def test_an_empty_message_matches_nothing(self):
        assert faq_answer("") is None
        assert faq_answer("   ") is None


# ── 2. The history shape ────────────────────────────────────────────────────

def _store():
    return ConversationStore(
        persist_path=os.path.join(tempfile.mkdtemp(), "c.jsonl"))


class TestHistoryShape:
    """Both transports append the user turn BEFORE calling the chat path."""

    def _seeded(self, exchanges=4):
        cs = _store()
        for i in range(exchanges):
            cs.append("u1", "user", "q%d" % i)
            cs.append("u1", "assistant", "a%d" % i)
        cs.append("u1", "user", "why?")          # the handler's pre-append
        return cs

    def test_the_question_is_not_sent_twice(self):
        h = self._seeded().get_recent_as_llm_messages(
            "u1", limit=9, drop_trailing_user=True)
        assert not h or h[-1]["role"] != "user"
        assert "why?" not in [m["content"] for m in h]

    def test_history_starts_on_a_user_turn(self):
        # Anthropic requires it. Starting on an assistant turn 400s the
        # Anthropic candidate and falls through to a worse provider, visible
        # only as chat_fallback audit lines — a silent downgrade.
        h = self._seeded().get_recent_as_llm_messages(
            "u1", limit=9, drop_trailing_user=True)
        assert h and h[0]["role"] == "user"

    @pytest.mark.parametrize("exchanges", [1, 2, 3, 4, 5, 8])
    def test_it_starts_on_a_user_turn_at_every_depth(self, exchanges):
        h = self._seeded(exchanges).get_recent_as_llm_messages(
            "u1", limit=9, drop_trailing_user=True)
        assert not h or h[0]["role"] == "user"

    def test_the_old_call_shape_still_works(self):
        # Default arguments unchanged: other callers must not shift.
        h = self._seeded().get_recent_as_llm_messages("u1", limit=8)
        assert isinstance(h, list)


# ── 3. Live positions carry a mark ─────────────────────────────────────────

def _pos(**kw):
    base = dict(direction="LONG", symbol="BTC/USDT", entry_price=60000.0,
                quantity=0.01, cost_usd=200.0, leverage=10,
                stop_loss=58000.0, take_profit=64000.0, status="open")
    base.update(kw)
    return NS(**base)


class TestPositionsCarryAMark:
    def test_a_known_mark_yields_an_unrealized_figure(self):
        out = _live_positions_block(NS(open_positions=[_pos()]),
                                    {"BTC/USDT": 63000.0})
        assert "MARK $63,000" in out
        assert "+5.00%" in out

    def test_a_short_inverts_the_sign(self):
        out = _live_positions_block(
            NS(open_positions=[_pos(direction="SHORT")]), {"BTC/USDT": 63000.0})
        assert "-5.00%" in out

    def test_a_missing_mark_is_stated_not_omitted(self):
        # An omitted mark is a gap the model fills from the entry price.
        out = _live_positions_block(NS(open_positions=[_pos()]), {})
        assert "MARK UNAVAILABLE" in out
        assert "do NOT know" in out

    @pytest.mark.parametrize("bad", [None, 0, -1, "63000", True])
    def test_junk_marks_read_as_unavailable(self, bad):
        out = _live_positions_block(NS(open_positions=[_pos()]),
                                    {"BTC/USDT": bad})
        assert "MARK UNAVAILABLE" in out

    def test_marks_are_optional_so_old_callers_still_work(self):
        assert "ACTIVE POSITIONS" in _live_positions_block(
            NS(open_positions=[_pos()]))

    def test_a_flat_book_keeps_its_explicit_none(self):
        out = _live_positions_block(NS(open_positions=[]), {"BTC/USDT": 1.0})
        assert "none right now" in out


# ── 4. The bot can say what it is about to trade ───────────────────────────

class TestPendingIdeasReachThePrompt:
    def _handler(self, engine):
        from bot.skills.telegram_handler import TelegramHandler
        return NS(engine=engine,
                  _pending_ideas_block=TelegramHandler._pending_ideas_block.__get__(
                      NS(engine=engine)))

    def _block(self, engine):
        from bot.skills.telegram_handler import TelegramHandler
        return TelegramHandler._pending_ideas_block(NS(engine=engine))

    def test_a_queued_idea_is_named(self):
        idea = NS(asset="SOL/USDT", direction=NS(value="LONG"),
                  confidence=0.72, entry_price=180.0)
        out = self._block(NS(pending_ideas=[idea]))
        assert "SOL/USDT" in out and "LONG" in out and "72%" in out

    def test_an_empty_queue_says_so(self):
        out = self._block(NS(pending_ideas=[]))
        assert "none queued" in out

    def test_an_unreadable_queue_is_not_an_empty_one(self):
        class _Boom:
            @property
            def pending_ideas(self):
                raise RuntimeError("executor gone")
        out = self._block(_Boom())
        assert "could not be read" in out
        assert "not say the queue is empty" in out

    def test_ideas_are_not_described_as_open_positions(self):
        idea = NS(asset="SOL/USDT", direction=NS(value="LONG"),
                  confidence=0.72, entry_price=180.0)
        assert "NOT open positions" in self._block(NS(pending_ideas=[idea]))

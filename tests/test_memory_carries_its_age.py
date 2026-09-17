"""Replayed memory carries its age, and the memory note its date.

`Message.to_llm_message` returned `{"role", "content"}` and dropped the
timestamp, so a `[get_portfolio] result:` recorded on Monday reached the model
on Friday shaped exactly like one recorded a second ago — and was restated as
the current book. The rolling summary was an LLM's undated paraphrase injected
verbatim on every turn ("Previous conversation summary: the user holds ETH"),
never checked for the `[skill] result:` block shape the fabrication guard
exists for, and compaction re-dated it to the user's last message. And the
recall lines ("Last discussed asset: NEAR/USDT") were read by
`_extract_symbol` from anywhere in a sentence, so "why did you enter near the
top" became a fact about the user.

Every turn older than a minute now carries its age; a tool record says
"[recorded 3 d ago — as of then, not now]" on its own first line; a time not
on record is said, never computed from 0. The note is dated when written,
keeps its date through a restart and a compaction, is cut at the first
tool-result block, and the prompt names how old it is. A recall is a MENTION,
read from a word written as a ticker, with its count and its age.

Red herrings: a fresh turn is byte-identical to before; a measured mention
prints with its count; the record's own `[skill] result:` marker stays at the
start of a line; a clean note is written whole.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.nlp.conversation_store import ConversationStore, Message, UserContext, age_words, when_words
from bot.nlp.fabricated_tool_calls import find_fabricated_marker
from bot.nlp.intent_router import AMBIGUOUS_TICKER_WORDS, mentioned_symbol
from bot.nlp.sanitize import sanitize_history_for_llm
from bot.skills.chat_runtime import _CHAT_NO_TOOLS_RULE, _CHAT_TOOLS_RULE
from bot.skills.telegram_handler import TelegramHandler as H
from tests.source_scan import code_only

NOW = 1_780_000_000.0
DAY = 86_400.0
RECORD = "[get_portfolio] result:\nequity $100.00, 1 open position"


def _msg(content, age, role="assistant", **meta):
    return Message(role=role, content=content, timestamp=NOW - age, metadata=meta)


# ── the words ───────────────────────────────────────────────────────────────

class TestWords:
    def test_age_words(self):
        assert age_words(45) == "45 s"
        assert age_words(240) == "4 min"
        assert age_words(3 * 3600 + 120) == "3 h 2 min"
        assert age_words(3 * 3600) == "3 h"
        assert age_words(2 * DAY + 5 * 3600) == "2 d 5 h"
        assert age_words(2 * DAY) == "2 d"
        assert age_words(-5) == "0 s"

    def test_when_words_is_a_date_or_an_absence(self):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", when_words(NOW))
        for absent in (0, 0.0, None, "x", True, float("nan"), float("inf"), -1):
            assert when_words(absent) == "time not on record", absent


# ── the turns ───────────────────────────────────────────────────────────────

class TestTurnStamps:
    def test_a_fresh_turn_is_unchanged(self):
        # RED HERRING: nothing is stamped on a live exchange.
        m = _msg("what about SOL?", 5, role="user", intent="chat")
        assert m.to_llm_message(NOW) == {"role": "user", "content": "what about SOL?"}
        fresh_record = _msg(RECORD, 30, skill="get_portfolio")
        assert fresh_record.to_llm_message(NOW) == {"role": "assistant", "content": RECORD}

    def test_an_aged_turn_carries_its_age(self):
        assert _msg("what about SOL?", 3 * 3600, role="user").to_llm_message(NOW)["content"] == \
            "[3 h ago] what about SOL?"
        assert _msg("SOL looks choppy.", 61).to_llm_message(NOW)["content"] == "[1 min ago] SOL looks choppy."

    def test_a_tool_record_says_it_is_as_of_then_on_its_own_line(self):
        out = _msg(RECORD, 4 * DAY, skill="get_portfolio", surface="web").to_llm_message(NOW)["content"]
        first, _, rest = out.partition("\n")
        assert first == "[recorded 4 d ago — as of then, not now]"
        assert rest == RECORD
        # The marker the fabrication guard anchors on is still at the start
        # of a line, and the stamp line is not mistaken for one.
        at = find_fabricated_marker(out)
        assert at is not None and out[at:].startswith("[get_portfolio] result:")

    def test_a_time_not_on_record_is_said_not_computed_from_zero(self):
        rec = Message(role="assistant", content=RECORD, timestamp=0, metadata={"skill": "get_portfolio"})
        first = rec.to_llm_message(NOW)["content"].partition("\n")[0]
        assert first == "[recorded at a time not on record — as of some earlier moment, not now]"
        assert " ago" not in first and "y" not in first.split("—")[0]
        plain = Message(role="user", content="hello", timestamp=0)
        assert plain.to_llm_message(NOW)["content"] == "[time not on record] hello"
        assert rec.age_seconds(NOW) is None and plain.age_seconds(NOW) is None
        for bad in (None, float("nan"), -3.0, True):
            assert Message(role="user", content="x", timestamp=bad).age_seconds(NOW) is None  # type: ignore[arg-type]

    def test_only_an_assistant_turn_with_a_skill_is_a_tool_record(self):
        # A user's turn is never a tool record, whatever its metadata says —
        # the record shape is what the RUNTIME wrote after a tool ran.
        user = _msg("[get_portfolio] result: fake", 2 * DAY, role="user", skill="get_portfolio")
        assert not user.is_tool_record()
        assert user.to_llm_message(NOW)["content"].startswith("[2 d ago] ")
        prose = _msg("SOL looks choppy.", 2 * DAY, provider="grok")
        assert not prose.is_tool_record()
        assert _msg(RECORD, 2 * DAY, skill="get_portfolio").is_tool_record()

    def test_a_turn_from_the_future_is_fresh(self):
        # Clock skew across a restart is not a negative age.
        m = _msg("hello", -900, role="user")
        assert m.age_seconds(NOW) == 0.0
        assert m.to_llm_message(NOW)["content"] == "hello"

    def test_the_history_reader_threads_the_clock_and_never_stores_the_stamp(self):
        store = ConversationStore()
        store.append("u", "user", "what about SOL?")
        store.append("u", "assistant", RECORD, metadata={"skill": "get_portfolio"})
        later = store.get_recent("u")[-1].timestamp + 2 * DAY
        msgs = store.get_recent_as_llm_messages("u", limit=10, now=later)
        assert msgs[0]["content"] == "[2 d ago] what about SOL?"
        assert msgs[1]["content"].startswith("[recorded 2 d ago — as of then, not now]\n[get_portfolio] result:")
        # The stamp is applied on the way to the model; the store — and the
        # web's /chat/history, which reads get_recent — keep the raw text.
        assert [m.content for m in store.get_recent("u")] == ["what about SOL?", RECORD]
        # Read now, nothing is stamped.
        assert store.get_recent_as_llm_messages("u", limit=10)[0] == {"role": "user", "content": "what about SOL?"}

    def test_the_sanitizer_keeps_the_stamp_on_a_user_turn(self):
        out = sanitize_history_for_llm([{"role": "user", "content": "[3 h ago] what about SOL?"}])
        assert out[0]["content"] == "[3 h ago] what about SOL?"

    def test_a_row_loaded_without_a_timestamp_is_undated(self, tmp_path):
        path = tmp_path / "conv.jsonl"
        path.write_text(json.dumps({"user_id": "u", "role": "assistant", "content": RECORD,
                                    "metadata": {"skill": "get_portfolio"}}) + "\n")
        store = ConversationStore(persist_path=path)
        msg = store.get_recent("u")[0]
        assert msg.timestamp == 0
        assert msg.to_llm_message(NOW)["content"].startswith("[recorded at a time not on record")

    def test_the_chat_path_reads_history_through_the_stamping_reader(self):
        src = code_only(inspect.getsource(H._llm_chat))
        assert "get_recent_as_llm_messages(" in src


# ── the recall ──────────────────────────────────────────────────────────────

class TestRecall:
    @pytest.mark.parametrize("text, expected", [
        ("why did you enter near the top", None),
        ("what about sol?", "SOL/USDT"),
        ("$HYPE is pumping", "HYPE/USDT"),
        ("LINK looks weak", "LINK/USDT"),
        ("link to the docs", None),
        ("I use an algo", None),
        ("WHAT WENT WRONG", None),
        ("bitcoin dip?", "BTC/USDT"),
        ("eth/usdt short", "ETH/USDT"),
        ("check out the news", None),
        ("analyze BTC", "BTC/USDT"),
        ("the near term for eth looks weak", "ETH/USDT"),
        ("what is RSI?", None),              # all-caps but not a ticker anyone knows
        ("OK thanks", None),
        ("STOP NEAR THE TOP", None),         # shouted: caps carry no signal
        ("BUY THE DIP ON ETH", "ETH/USDT"),  # RED HERRING: a shouted unambiguous ticker still counts
        ("", None),
    ])
    def test_a_mention_is_a_word_written_as_a_ticker(self, text, expected):
        assert mentioned_symbol(text) == expected

    def test_the_ambiguous_words_are_known_tickers_that_are_english(self):
        from bot.nlp.intent_router import _KNOWN_SYMBOLS
        assert AMBIGUOUS_TICKER_WORDS <= _KNOWN_SYMBOLS
        assert {"near", "link", "op", "dot"} <= AMBIGUOUS_TICKER_WORDS

    def test_prose_records_no_asset(self):
        store = ConversationStore()
        store.append("u", "user", "why did you enter near the top")
        assert store.get_context("u").last_discussed_asset == ""
        assert "MENTIONED" not in store.build_context_prompt("u")

    def test_a_mention_prints_as_a_mention_with_its_count_and_age(self):
        # RED HERRING: a real mention IS recorded, and says what it is.
        store = ConversationStore()
        ctx = UserContext()
        ctx.update_from_message("what about sol?", now=NOW - 2 * DAY)
        ctx.update_from_message("eth looks weak", now=NOW - DAY)
        ctx.update_from_message("ETH again?", now=NOW - 3600)
        store._user_contexts["u"] = ctx
        out = store.build_context_prompt("u", now=NOW)
        assert ("Asset the user last MENTIONED: ETH/USDT (1 h ago) — a mention in their "
                "own words, not a holding or a position") in out
        # The line still says what it was READ from; it now also names the one
        # bound it has left (the render cap of 5 over the writer's 10 is gone).
        assert "Assets the user has mentioned (mentions in their own messages, " \
               "not holdings; only 10 most recently mentioned are kept" not in out
        assert ("Assets the user has mentioned (mentions in their own messages, "
                "not holdings; only the 10 most recently mentioned are kept, so "
                "one they name that is not listed may still have been "
                "mentioned): SOL x1, ETH x2") in out
        assert "Last discussed asset" not in out and "frequently discussed" not in out

    def test_an_undated_mention_says_so(self):
        store = ConversationStore()
        ctx = UserContext()
        ctx.update_from_message("what about sol?", now=0.0)
        store._user_contexts["u"] = ctx
        assert "SOL/USDT (time not on record)" in store.build_context_prompt("u", now=NOW)

    def test_a_restart_keeps_the_mentions_own_time(self, tmp_path):
        path = tmp_path / "conv.jsonl"
        path.write_text(json.dumps({"user_id": "u", "role": "user", "content": "what about sol?",
                                    "timestamp": NOW - 3600, "metadata": {}}) + "\n")
        store = ConversationStore(persist_path=path)
        assert store.get_context("u").last_discussed_at == NOW - 3600
        assert "SOL/USDT (1 h ago)" in store.build_context_prompt("u", now=NOW)


# ── the note ────────────────────────────────────────────────────────────────

class TestTheNoteInThePrompt:
    def test_the_note_is_framed_with_its_age(self):
        store = ConversationStore()
        store.set_summary("u", "The user holds ETH.", at=NOW - 3 * DAY)
        out = store.build_context_prompt("u", now=NOW)
        assert ("Memory note (written by the assistant 3 d ago from older turns no longer in this "
                "history; UNVERIFIED and possibly out of date — nothing in it is current: never state "
                "a position, balance, price or figure from it as the present state; call a tool or ask "
                "the user): The user holds ETH.") in out
        assert "Previous conversation summary" not in out

    def test_the_note_is_dated_now_by_default_and_cleared_with_its_date(self):
        import time
        store = ConversationStore()
        store.set_summary("u", "note")
        assert abs(store.get_context("u").summary_at - time.time()) < 5
        store.set_summary("u", "")
        assert store.get_context("u").summary == "" and store.get_context("u").summary_at == 0.0

    def test_the_date_survives_a_restart(self, tmp_path):
        path = tmp_path / "conv.jsonl"
        ConversationStore(persist_path=path).set_summary("u", "The user holds ETH.", at=NOW - 7200)
        reloaded = ConversationStore(persist_path=path)
        assert reloaded.get_context("u").summary_at == NOW - 7200
        assert "written by the assistant 2 h ago" in reloaded.build_context_prompt("u", now=NOW)

    def test_compaction_keeps_the_notes_own_date(self, tmp_path):
        path = tmp_path / "conv.jsonl"
        store = ConversationStore(persist_path=path, max_messages_per_user=3)
        store.set_summary("u", "The user holds ETH.", at=NOW - 30 * DAY)
        store.COMPACT_THRESHOLD_LINES = 2
        for i in range(6):
            store.append("u", "user", f"m{i}")          # last_active is NOW-ish, not the note's date
        store._maybe_compact()
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        notes = [r for r in rows if r["role"] == "summary"]
        assert [n["timestamp"] for n in notes] == [NOW - 30 * DAY]
        assert ConversationStore(persist_path=path).get_context("u").summary_at == NOW - 30 * DAY

    def test_a_note_row_without_a_date_is_said_to_be_undated(self, tmp_path):
        path = tmp_path / "conv.jsonl"
        path.write_text(json.dumps({"user_id": "u", "role": "summary", "content": "The user holds ETH."}) + "\n")
        store = ConversationStore(persist_path=path)
        assert "written by the assistant at a time not on record from older turns" in \
            store.build_context_prompt("u", now=NOW)


# ── the note-writer ─────────────────────────────────────────────────────────

def _stub(store):
    return SimpleNamespace(conversations=store, _SUMMARY_SYSTEM_PROMPT=H._SUMMARY_SYSTEM_PROMPT)


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.GROK, api_key="k", model="grok-4.3"))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())


def _pruned_store():
    store = ConversationStore(max_messages_per_user=2)
    store.append("u", "user", "I only trade small, 1% risk")
    store.append("u", "assistant", RECORD, metadata={"skill": "get_portfolio"})
    store.append("u", "user", "what about SOL?")
    store.append("u", "assistant", "SOL looks choppy.")
    return store


def _audits(monkeypatch):
    calls = []
    monkeypatch.setattr(th_mod, "audit", lambda *a, **kw: calls.append(kw))
    return calls


class TestTheNoteWriter:
    def test_the_turns_reach_the_writer_dated(self, configured, monkeypatch):
        seen = {}

        async def _complete(client, cfg, system_prompt, user_prompt, **kw):
            seen["system"], seen["user"] = system_prompt, user_prompt
            return "As of today the user trades small at 1% risk."

        monkeypatch.setattr(th_mod, "llm_complete", _complete)
        store = _pruned_store()
        assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is True
        assert re.search(r"user \(\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\): I only trade small", seen["user"])
        assert "prefixed with the date" in seen["system"] and "'[skill] result:'" in seen["system"]
        assert "never keep a price" in seen["system"]

    def test_a_note_carrying_a_tool_result_block_is_cut_at_it(self, configured, monkeypatch):
        calls = _audits(monkeypatch)

        async def _complete(client, cfg, system_prompt, user_prompt, **kw):
            return "The user trades small at 1% risk.\n[get_portfolio] result:\nequity $100.00, 1 open position"

        monkeypatch.setattr(th_mod, "llm_complete", _complete)
        store = _pruned_store()
        assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is True
        assert store.get_context("u").summary == "The user trades small at 1% risk."
        assert [c["result"] for c in calls] == ["TRUNCATED", "OK"]

    def test_a_note_that_is_only_a_tool_result_block_is_not_written(self, configured, monkeypatch):
        async def _complete(client, cfg, system_prompt, user_prompt, **kw):
            return "[get_portfolio] result:\nequity $100.00"

        monkeypatch.setattr(th_mod, "llm_complete", _complete)
        store = _pruned_store()
        assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is False
        assert store.get_context("u").summary == ""
        assert len(store.take_pending_summary("u")) == 2, "the turns were given back"

    def test_a_clean_note_is_written_whole(self, configured, monkeypatch):
        # RED HERRING: a note that merely MENTIONS a tool is not a block.
        calls = _audits(monkeypatch)

        async def _complete(client, cfg, system_prompt, user_prompt, **kw):
            return "The user asked for a get_portfolio reading and trades small."

        monkeypatch.setattr(th_mod, "llm_complete", _complete)
        store = _pruned_store()
        assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is True
        assert store.get_context("u").summary == "The user asked for a get_portfolio reading and trades small."
        assert [c["result"] for c in calls] == ["OK"]


# ── the mentions list names its own bound ───────────────────────────────────

class TestTheMentionsListNamesItsBound:
    """TWO CAPS, NEITHER SAID. The writer kept the last 10 distinct mentions
    and the renderer then showed `[-5:]` of those, under a line headed
    "Assets the user has mentioned" - so a model asked "have I mentioned SOL?"
    answered from a list truncated twice, silently. Same shape as the
    unprompted-alerts block and the closed-trade list, on the third bounded
    list in the same prompt; found by the sweep those two required.

    The render cap is GONE (all that is kept is shown) so there is only one
    bound left, and the sentence names it. That is a smaller claim than a
    count would be: the writer's evictions really are gone, so "may still
    have been mentioned" is what can honestly be said about them.
    """

    def _mentions_line(self, store, uid="u1"):
        for line in store.build_context_prompt(uid).splitlines():
            if "Assets the user has mentioned" in line:
                return line
        return ""

    def _store_with(self, n):
        cs = ConversationStore()
        syms = ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE", "PEPE", "WIF",
                "TIA", "ARB", "OP", "NEAR"][:n]
        for sym in syms:
            cs.append("u1", "user", f"what about {sym}/USDT")
        return cs, syms

    def test_everything_kept_is_shown_not_the_last_five_of_it(self):
        cs, syms = self._store_with(12)
        cap = UserContext.PREFERRED_ASSETS_MAX
        kept = cs.get_context("u1").preferred_assets
        assert len(kept) == cap and kept == syms[-cap:]
        line = self._mentions_line(cs)
        for sym in kept:
            assert sym in line, f"{sym} kept but not rendered\n{line}"

    def test_the_bound_that_is_left_is_named_and_is_not_a_denial(self):
        line = self._mentions_line(self._store_with(12)[0])
        assert (f"only the {UserContext.PREFERRED_ASSETS_MAX} most recently "
                "mentioned are kept") in line
        assert "may still have been mentioned" in line
        # ...and the line still says what it was READ from, which is the
        # claim it already made correctly.
        assert "not holdings" in line

    def test_the_bound_is_ONE_name_the_writer_and_the_renderer_share(
            self, monkeypatch):
        monkeypatch.setattr(UserContext, "PREFERRED_ASSETS_MAX", 3)
        cs, _ = self._store_with(12)
        assert len(cs.get_context("u1").preferred_assets) == 3
        line = self._mentions_line(cs)
        assert "only the 3 most recently mentioned are kept" in line
        assert "only the 10 most recently mentioned are kept" not in line

    def test_a_short_list_is_not_evicting_anything_and_still_says_the_rule(self):
        """The bound holds whether or not it has bitten, so it is stated
        either way - unlike the alert ring's FULL sentence, which is a claim
        about THIS list having reached it."""
        cs, syms = self._store_with(3)
        line = self._mentions_line(cs)
        for sym in syms:
            assert sym in line
        assert "most recently mentioned are kept" in line


# ── the rules the model reads ───────────────────────────────────────────────

class TestTheRules:
    def test_both_rules_name_the_stamp_and_what_it_means(self):
        assert "'[recorded 3 d ago — as of then, not now]'" in _CHAT_TOOLS_RULE
        assert "Never restate one as the current state" in _CHAT_TOOLS_RULE
        assert "as of then, not now" in _CHAT_NO_TOOLS_RULE and "say when it is from" in _CHAT_NO_TOOLS_RULE

    def test_the_stamp_the_rule_quotes_is_the_stamp_the_store_writes(self):
        assert _msg(RECORD, 3 * DAY, skill="x").age_stamp(NOW) == "[recorded 3 d ago — as of then, not now]"
        assert _msg("hi", 3 * DAY, role="user").age_stamp(NOW) == "[3 d ago]"

    def test_the_note_writer_is_told_to_date_facts(self):
        assert "as of 2026-09-10 the user held ETH" in H._SUMMARY_SYSTEM_PROMPT

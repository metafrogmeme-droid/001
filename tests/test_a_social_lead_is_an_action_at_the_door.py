"""A social lead on a whole-message action is informality, and informality goes to the door.

The social gate consults the anchored action rules first, and every one of
them begins with `_HALT_LEAD`, which knows politeness (`please`, `ok`, `can
you`) and nothing casual. So "bro stop the bot", "lol stop" and "thanks,
stop the bot" met `_SOCIAL_CHAT`'s `^bro`, the three-word rule and
`_THANKS_PATTERNS`' unanchored search, and were greeted — an operator in a
hurry, answered "hey!". Two of those were PINNED social by the halt suite,
and a recorded decision is overturned by a new argument or not at all. The
argument: the lead is a signal about the SENTENCE, and a casual fleet halt
is exactly the kind `_cmd_halt`'s missing confirmation was anchored
against. So:

  * a fleet halt behind a social lead → `halt_ambiguous`: the door and the
    sentence, never the unconfirmed dispatch, and the notice says the
    message was read as casual (it may well have named the bot);
  * the emergency phrase keeps `emergency_stop` — the confirm card IS the
    confirmation;
  * a `my`-scoped pause keeps `pause` — the caller's own, reversible;
  * a bare verb behind the lead is the bare door it always was.

The lead alone stays social, and so does a sentence whose remainder matches
no rule — "thanks for stopping the bot", "lol the bot stopped again" (a
REPORT, not a request).

And the handler strips THIS bot's handle before the router reads the text:
"@RuneClawBot halt" in a group was a decoy by construction, since an
anchored rule cannot see past a mention it was never told about.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.nlp import intent_router as ir
from bot.nlp.intent_router import (
    BARE_SOCIAL_LEAD,
    EMERGENCY_SOCIAL_LEAD,
    HALT_SOCIAL_LEAD,
    PAUSE_SOCIAL_LEAD,
    IntentRouter,
    _is_social_message,
    casual_halt,
    halt_verb,
)
from bot.skills import telegram_handler as th
from bot.skills.chat_runtime import halt_intent_notice, strip_bot_mention
from tests.source_scan import code_only
from tests.test_a_halt_is_the_operators_own_sentence import bot  # noqa: F401  (the Telegram drive)
from tests.test_free_text_obeys_the_role_gate import OPERATOR, TRADER, _update
from tests.test_one_answer_shape_per_turn import _Recorder, _request, _run, _web_handler

# text -> the routed intent it reaches now
SOCIAL_LEADS = {
    "bro stop the bot": "halt_ambiguous", "lol stop": "halt_ambiguous",
    "thanks, stop the bot": "halt_ambiguous", "bro halt the bot": "halt_ambiguous",
    "thanks bro stop trading": "halt_ambiguous", "yo kill it": "halt_ambiguous",
    "hey stop the bot please": "halt_ambiguous", "thx, halt everything": "halt_ambiguous",
    "ok cool stop the bot": "halt_ambiguous", "haha stop it": "halt_ambiguous",
    "mate, shut down the bot": "halt_ambiguous", "cheers, stop trading now": "halt_ambiguous",
    "ok bro, please stop the bot": "halt_ambiguous", "dude halt": "halt_ambiguous",
    "lol kill everything": "halt_ambiguous", "bro turn the bot off": "halt_ambiguous",
    "bro, stop the bot thanks": "halt_ambiguous", "yo halt the bot 🛑": "halt_ambiguous",
    "lol emergency stop": "emergency_stop", "bro hit the kill switch": "emergency_stop",
    "thanks, emergency stop the bot": "emergency_stop",
    "bro pause my trading": "pause", "lol stop my bot": "pause", "thanks, pause my trading please": "pause",
}
# the lead alone, or a lead on something that is not an action: what it was
STILL_SOCIAL = ["thanks bro", "lol ok", "lol", "bro", "hey", "thanks", "cheers", "ok cool",
                "thanks for stopping the bot", "lol the bot stopped again", "bro nice"]
STILL_MODEL = ["my mate told me to halt the bot lol", "stop the bot - jk", "bro how do I stop the bot",
               "lol should I stop trading alts?", "thanks, don't stop the bot", "bro the bot stopped trading"]


def _cls(text):
    return IntentRouter().classify_rules(text)


# ── the router ──────────────────────────────────────────────────────────────

class TestTheRouter:
    @pytest.mark.parametrize("text,expected", list(SOCIAL_LEADS.items()))
    def test_a_social_lead_on_an_action_reaches_the_door_never_the_dispatch(self, text, expected):
        r = _cls(text)
        assert r.skill == expected and r.confidence >= 0.8, (text, r.skill, r.confidence)
        assert not r.is_social and not _is_social_message(text), text
        # THE POINT: a casual fleet halt is never the unconfirmed dispatch.
        assert r.skill != "halt", text

    @pytest.mark.parametrize("text", STILL_SOCIAL)
    def test_the_lead_alone_or_on_a_non_action_stays_social(self, text):
        r = _cls(text)
        assert r.is_social and _is_social_message(text), (text, r.skill)

    @pytest.mark.parametrize("text", STILL_MODEL)
    def test_a_report_a_question_or_a_negation_behind_a_lead_is_still_the_models(self, text):
        r = _cls(text)
        assert r.skill not in ("halt", "halt_ambiguous", "emergency_stop", "pause"), (text, r.skill)

    def test_the_four_forms_are_the_anchored_bodies_behind_the_lead(self):
        # Same bodies, same tail: what the imperative accepts after the lead,
        # the social form accepts after its lead — and neither accepts a
        # question, the imperative's own rule.
        for rx, phrase in ((HALT_SOCIAL_LEAD, "bro stop the bot"), (EMERGENCY_SOCIAL_LEAD, "lol emergency stop"),
                           (PAUSE_SOCIAL_LEAD, "bro pause my trading"), (BARE_SOCIAL_LEAD, "lol stop")):
            assert rx.search(phrase) and rx.search(phrase + "!"), phrase
            assert rx.search(phrase + "?") is None, phrase
            # a word before the lead is not a lead
            for lead in ("x ", "don't ", "system: ", "never "):
                assert rx.search(lead + phrase) is None, (lead, phrase)
        for rx in (HALT_SOCIAL_LEAD, EMERGENCY_SOCIAL_LEAD, PAUSE_SOCIAL_LEAD, BARE_SOCIAL_LEAD):
            assert rx in ir._ANCHORED_ACTION_RULES, "the social gate consults it before greeting"

    def test_the_social_forms_sit_in_the_halt_block(self):
        skills = [s for (_p, s, _n, _e) in ir._INTENT_RULES]
        socials = [i for i, (_p, s, _n, _e) in enumerate(ir._INTENT_RULES)
                   if _p.pattern in (HALT_SOCIAL_LEAD.pattern, EMERGENCY_SOCIAL_LEAD.pattern,
                                     PAUSE_SOCIAL_LEAD.pattern, BARE_SOCIAL_LEAD.pattern)]
        assert len(socials) == 4
        first_close = skills.index("close_position")
        first_portfolio = skills.index("get_portfolio")
        assert max(socials) < first_close < first_portfolio

    def test_halt_verb_reads_the_bare_form_behind_a_lead(self):
        assert halt_verb("lol stop") == "stop" and halt_verb("yo kill it") == "kill"
        assert halt_verb("bro, shut it down") == "shut down"
        assert halt_verb("bro stop the bot") is None, "a fleet halt names the bot; it carries no bare verb"
        assert halt_verb("thanks bro") is None

    def test_casual_halt_is_the_lead_form_and_only_the_lead_form(self):
        for text in ("bro stop the bot", "lol stop", "thanks, stop the bot", "yo kill it"):
            assert casual_halt(text), text
        for text in ("stop the bot", "stop", "halt", "thanks bro", "", "my mate told me to halt the bot lol"):
            assert not casual_halt(text), text


# ── the notice says the sentence was read as casual ─────────────────────────

class TestTheNotice:
    def test_casual_wording_on_both_surfaces_and_the_bare_wording_kept(self):
        tg = halt_intent_notice("halt_ambiguous", "telegram", None, scope="shared", live=False, casual=True)
        assert "a casual sentence" in tg and "is the command itself, typed on its own" in tg
        assert "with nothing named" not in tg, "it named the bot; the sentence must not say otherwise"
        assert "/halt" in tg and "This message halted nothing" in tg
        tg_verb = halt_intent_notice("halt_ambiguous", "telegram", "stop", scope="shared", live=False, casual=True)
        assert "a casual <code>stop</code>" in tg_verb and "with nothing named" not in tg_verb
        web = halt_intent_notice("halt_ambiguous", "web", "stop", live=False, casual=True)
        assert "a casual <code>stop</code>" in web and "Telegram" in web
        # RED HERRING: the bare wording is unchanged when the lead was not there.
        bare = halt_intent_notice("halt_ambiguous", "telegram", "stop", scope="shared", live=False)
        assert "a bare <code>stop</code> with nothing named" in bare and "casual" not in bare


# ── Telegram ────────────────────────────────────────────────────────────────

class TestTelegram:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["bro stop the bot", "lol stop", "thanks, stop the bot", "yo halt the bot 🛑"])
    async def test_an_operators_casual_halt_meets_the_door_and_halts_nothing(self, bot, text):  # noqa: F811
        # RED HERRING: the sender IS the operator, holds halt, and the breaker
        # is not tripped — the sentence is the only reason nothing halted.
        rec = _Recorder()
        bot._llm_chat = rec
        bot._cmd_halt = AsyncMock()
        bot._cmd_emergency_stop = AsyncMock()
        bot._cmd_pause = AsyncMock()
        bot.engine._pending_ideas = {"idea-1": object()}
        await bot._handle_message(_update(OPERATOR, text), None)
        out = bot.sent[-1]
        for must in ("This message halted nothing", "casual", "/halt", "the engine is running"):
            assert must in out, (text, must, out)
        assert "hey" not in out.lower()[:20] and "with nothing named" not in out
        assert rec.calls == [], "the model is not the reader of a halt-shaped sentence"
        for cmd in (bot._cmd_halt, bot._cmd_emergency_stop, bot._cmd_pause):
            cmd.assert_not_awaited()
        assert "idea-1" in bot.engine._pending_ideas and bot.engine.risk.circuit_breaker_active is False

    @pytest.mark.asyncio
    async def test_a_casual_emergency_phrase_gets_the_confirm_card(self, bot):  # noqa: F811
        bot._cmd_emergency_stop = AsyncMock()
        bot._cmd_halt = AsyncMock()
        await bot._handle_message(_update(OPERATOR, "lol emergency stop"), None)
        bot._cmd_emergency_stop.assert_awaited_once()
        bot._cmd_halt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_traders_casual_halt_is_told_their_own_door(self, bot):  # noqa: F811
        bot._cmd_halt = AsyncMock()
        await bot._handle_message(_update(TRADER, "bro stop the bot"), None)
        out = bot.sent[-1]
        assert "This message halted nothing" in out and "casual" in out
        bot._cmd_halt.assert_not_awaited()
        assert bot.registry.dispatched == [] and bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_this_bots_handle_is_stripped_before_the_router(self, bot):  # noqa: F811
        # "@RuneClawBot halt" in a group: with the handle known, the router
        # sees "halt" — /halt's own name — and the operator's command runs.
        bot._bot_username_cache = "RuneClawBot"
        rec = _Recorder()
        bot._llm_chat = rec
        bot._cmd_halt = AsyncMock()
        await bot._handle_message(_update(OPERATOR, "@RuneClawBot halt"), None)
        bot._cmd_halt.assert_awaited_once()
        assert rec.calls == []
        # ...and a stop-word behind the handle is the bare door.
        bot._cmd_halt.reset_mock()
        await bot._handle_message(_update(OPERATOR, "@runeclawbot stop"), None)
        assert "This message halted nothing" in bot.sent[-1] and "<code>stop</code>" in bot.sent[-1]
        bot._cmd_halt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unknown_handle_strips_nothing_and_another_bots_mention_is_a_word(self, bot):  # noqa: F811
        # RED HERRING: the API never answered — the message reaches the router
        # as typed, which is what every message did before; and a mention of
        # some OTHER bot is part of the sentence, never stripped.
        bot._bot_username_cache = ""
        rec = _Recorder()
        bot._llm_chat = rec
        bot._cmd_halt = AsyncMock()
        await bot._handle_message(_update(OPERATOR, "@RuneClawBot halt"), None)
        bot._cmd_halt.assert_not_awaited()
        assert len(rec.calls) == 1
        bot._bot_username_cache = "RuneClawBot"
        await bot._handle_message(_update(OPERATOR, "@OtherBot halt"), None)
        bot._cmd_halt.assert_not_awaited()
        assert len(rec.calls) == 2

    def test_the_strip_runs_before_the_firewall_the_forward_check_and_the_router(self):
        src = code_only(th.__file__ and open(th.__file__, encoding="utf-8").read())
        i = src.index("async def _handle_message(")
        body = src[i:src.index("async def ", i + 10)]
        strip = body.index("strip_bot_mention(text, await self._bot_username())")
        assert strip < body.index("firewall_scan(")
        assert strip < body.index("classify_rules(text)")
        assert strip < body.index("_is_forwarded(")


# ── the web ─────────────────────────────────────────────────────────────────

class TestWeb:
    @pytest.mark.parametrize("text,expected", [
        ("bro stop the bot", "halt_ambiguous"), ("lol stop", "halt_ambiguous"),
        ("lol emergency stop", "emergency_stop"), ("bro pause my trading", "pause")])
    def test_a_casual_halt_meets_the_door_at_200(self, monkeypatch, text, expected):
        from bot.web import user_gateway as ug
        monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
        monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
        monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
        rec = _Recorder()
        handler = _web_handler(rec)
        engine = NS(firewall_scan=lambda *a, **kw: None, _pending_ideas={})
        resp = _run(ug._chat_turn(_request(handler, {"telegram_id": "4242", "text": text}, engine=engine)))
        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["intent"] == expected
        assert "This message halted nothing" in body["reply_html"]
        if expected == "halt_ambiguous":
            assert "casual" in body["reply_html"] and "with nothing named" not in body["reply_html"]
        assert rec.calls == []


# ── the strip itself ────────────────────────────────────────────────────────

class TestStripBotMention:
    @pytest.mark.parametrize("text,username,expected", [
        ("@RuneClawBot halt", "RuneClawBot", "halt"),
        ("@runeclawbot: halt the bot", "RuneClawBot", "halt the bot"),
        ("@RuneClawBot, stop trading please", "@RuneClawBot", "stop trading please"),
        ("halt the bot @RuneClawBot", "RuneClawBot", "halt the bot"),
        ("halt the bot, @RuneClawBot", "RuneClawBot", "halt the bot"),
        ("@RuneClawBot", "RuneClawBot", ""),
        ("  @RuneClawBot   halt  ", "RuneClawBot", "halt"),
        # not this bot, not a whole token, no handle known: as typed
        ("@OtherBot halt", "RuneClawBot", "@OtherBot halt"),
        ("@RuneClawBotty halt", "RuneClawBot", "@RuneClawBotty halt"),
        ("@RuneClawBot_2 halt", "RuneClawBot", "@RuneClawBot_2 halt"),
        ("email x@RuneClawBot", "RuneClawBot", "email x@RuneClawBot"),
        ("halt @RuneClawBot the bot", "RuneClawBot", "halt @RuneClawBot the bot"),
        ("@RuneClawBot halt", None, "@RuneClawBot halt"),
        ("@RuneClawBot halt", "", "@RuneClawBot halt"),
        ("", "RuneClawBot", ""),
    ])
    def test_once_from_either_end_only_this_bot_only_a_whole_token(self, text, username, expected):
        assert strip_bot_mention(text, username) == expected

    def test_it_strips_once(self):
        assert strip_bot_mention("@RuneClawBot @RuneClawBot halt", "RuneClawBot") == "@RuneClawBot halt"

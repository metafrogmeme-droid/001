""""What can you do?" was answered, on the web, with "that tool does not exist".

`help` classifies at confidence 1.0 and no skill is registered under the name,
so the web fell to `skill_unavailable_notice`: *"I understood that as help, but
that tool is not available on this bot right now."* The capability exists — the
identical text runs a working 126-command reference on Telegram. The statement
is false, and `skill_unavailable_memory` wrote the same falsehood into the
model's own history: *"this bot has no such tool wired up"*.

Seven of ten phrasings never even got that far. `what can you do` was eaten by
the social gate, `capabilities` and `/help` by the three-words-or-fewer rule,
and `how does this work`, `show me what you can do`, `what can i ask` and
`im new what now` matched nothing and reached a model with no tools and no
list — which is the improvised-feature-list failure the unavailable notice was
written to prevent, arriving through the other door.

**Reusing the Telegram card would have replaced a false refusal with a
mostly-false answer.** Of the 90 commands it names for a non-admin, 79 reach
the tool-less chat model on the web, 10 reach a skill by incidental word
matching, and 5 of those 10 reach the wrong engine. A card that names a command
is claiming the command does something.

So the answer is what this caller can ASK FOR, in words, derived from
`SKILL_SAYS` — a column on the table that already decides which skills chat can
reach — intersected with their role, plan and surface by the same walk
`tools_for` uses. Withheld skills are counted, never named, and the reason
travels, because "ask an admin", "upgrade" and "use Telegram" are three fixes.
"""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.formatters.capabilities import WITHHELD_REASONS, capability_answer
from bot.nlp.chat_tools import CHAT_TOOLS, skill_reach, tools_for
from bot.skills.skill_permissions import DANGEROUS_SKILLS, SKILL_PERMISSION, SKILL_SAYS, WEB_CHAT_SKILLS

CALLER = "4242"


# ── 1. the derivation is safe because the table is complete ─────────────────

def test_every_skill_chat_can_reach_has_a_phrase():
    """The whole point of a column rather than a map elsewhere: a skill added
    later fails HERE instead of vanishing from the answer to "what can you
    do?" — the `/setllm` ten-of-eleven shape."""
    assert set(SKILL_SAYS) == set(SKILL_PERMISSION), (
        set(SKILL_PERMISSION) ^ set(SKILL_SAYS))


#: A SLASH COMMAND, not any slash. "stop/take-profit" is a pair of order
#: types and `</b>` is a closing tag; a bare `"/" not in text` flagged both,
#: which is the "asserting a short string is ABSENT" misfire CLAUDE.md gives
#: its own section to.
_SLASH_COMMAND = re.compile(r"(?<![\w/<])/[a-z]{2,}")


def _commands_named(text: str) -> list[str]:
    return _SLASH_COMMAND.findall(re.sub(r"<[^>]+>", " ", text))


def test_the_slash_check_reads_commands_and_not_punctuation():
    """Guards the guard: the first draft flagged `stop/take-profit` and every
    closing HTML tag."""
    assert _commands_named("stop/take-profit triggers") == []
    assert _commands_named("<b>bold</b> and <i>x</i>") == []
    assert _commands_named("send /help for the reference") == ["/help"]
    assert _commands_named("— /venue to switch") == ["/venue"]


def test_no_phrase_names_a_command():
    """A card that names a command is claiming the command does something, and
    this card is rendered on a surface with no slash commands at all."""
    for name, says in SKILL_SAYS.items():
        assert says.strip(), name
        assert _commands_named(says) == [], (name, says)
        assert says[0].islower(), (name, says)


def test_a_phrase_is_written_for_a_person_not_for_the_model():
    """The tool descriptions cannot serve both readers: they are written AT
    the model and read as a stranger's notes in a card shown to the caller."""
    model_facing = ("call this", "the caller's", "use it", "do not")
    for name, says in SKILL_SAYS.items():
        low = says.lower()
        for phrase in model_facing:
            assert phrase not in low, (name, phrase)


# ── 2. one walk, two readers ────────────────────────────────────────────────

def _users(*, denied=(), tier_blocks=()):
    return NS(permission_denial=lambda u, p: "role" if p in denied else None,
              get_tier=lambda u: "elite",
              get=lambda u: {"role": "paper"},
              _tier_blocks=set(tier_blocks))


def test_the_tool_list_and_the_capability_list_cannot_disagree():
    """`tools_for` answers "what may the model call" and the card answers
    "what can I do for you". A second copy of the gate is a second answer, and
    this one decides what a caller is told the product can do."""
    users = _users(denied={"learn", "optimize"})
    tools = {t.name for t in tools_for(users, "u", "web")}
    reach, _withheld = skill_reach(users, "u", "web", [t.name for t in CHAT_TOOLS])
    assert set(reach) == tools


def test_the_tool_list_is_DERIVED_from_the_one_walk(monkeypatch):
    """The test above proves they AGREE; it cannot prove there is one walk.
    A byte-identical second copy agrees with every fixture and diverges on the
    first change to either — and the mutation that restored `tools_for`'s own
    copy of the permission loop survived that equality assertion, which is
    exactly what a second copy of a gate looks like from the outside.

    Driven rather than scanned: patch the walk, and a `tools_for` that reads
    it answers what it said. One that has its own copy ignores the patch.
    """
    import bot.nlp.chat_tools as ct

    monkeypatch.setattr(ct, "skill_reach",
                        lambda users, uid, surface, names: (["get_portfolio"], {}))
    assert [t.name for t in ct.tools_for(_users(), "u", "web")] == ["get_portfolio"]


def test_a_plan_block_is_recorded_as_plan_and_not_as_role(monkeypatch):
    """"Ask an admin" and "upgrade your plan" are two different fixes, so the
    count that stands for one must not stand for the other. The $RCLAW gate is
    OFF by default, so a fixture that only sets a tier string drives nothing —
    the first draft's did, and the mutation that stopped recording this reason
    survived the whole suite."""
    from bot.token import tier_gate

    gated = {"deepscan", "analyze_asset", "patterns"}
    monkeypatch.setattr(tier_gate, "check_user",
                        lambda users, uid, feature: (feature not in gated,
                                                     "insufficient"))
    reach, withheld = skill_reach(_users(), "u", "telegram",
                                  list(SKILL_PERMISSION))
    assert withheld.get("plan") == len(gated), withheld
    assert withheld.get("role", 0) == 0, "a plan block is not a role block"
    assert not (gated & set(reach))


def test_the_reason_a_skill_is_missing_travels():
    users = _users(denied={"backtest", "walkforward"})
    _reach, withheld = skill_reach(users, "u", "web", list(SKILL_PERMISSION))
    assert withheld.get("role") == 2, withheld
    # `trade_journal` is in SKILL_PERMISSION and not in WEB_CHAT_SKILLS.
    assert withheld.get("surface") == len(set(SKILL_PERMISSION) - WEB_CHAT_SKILLS
                                          - set(DANGEROUS_SKILLS)), withheld


def test_a_dangerous_skill_is_not_a_feature_this_caller_is_missing():
    """`halt` is not withheld and not counted: it is not a feature anybody
    reaches from chat, and the card says so in its own section."""
    _reach, withheld = skill_reach(_users(), "u", "telegram",
                                   list(SKILL_PERMISSION))
    assert sum(withheld.values()) == 0, withheld


def test_an_unreadable_role_withholds_rather_than_grants():
    def _raises(u, p):
        raise RuntimeError("store down")

    users = NS(permission_denial=_raises, get_tier=lambda u: "elite")
    reach, withheld = skill_reach(users, "u", "web", list(SKILL_PERMISSION))
    assert reach == []
    assert withheld.get("role", 0) > 0


# ── 3. the card ─────────────────────────────────────────────────────────────

def test_the_card_lists_what_the_caller_can_ask_for():
    out = capability_answer(["get_portfolio", "whynot"], surface="web")
    assert SKILL_SAYS["get_portfolio"] in out
    assert SKILL_SAYS["whynot"] in out
    assert SKILL_SAYS["deepscan"] not in out


def test_an_unknown_skill_name_is_dropped_not_printed():
    """A bare identifier in a card shown to a person is the shape of an answer
    rather than one; the table's own guard is what stops this being silent."""
    out = capability_answer(["get_portfolio", "brand_new_skill"], surface="web")
    assert "brand_new_skill" not in out
    assert SKILL_SAYS["get_portfolio"] in out


def test_a_caller_who_reaches_nothing_is_not_told_the_product_does_nothing():
    out = capability_answer([], surface="web", role="pending")
    assert "cannot reach any" in out
    for never in ("I can do nothing", "no features", "not available"):
        assert never not in out


def test_the_withheld_count_names_its_reason_and_never_a_skill():
    out = capability_answer(["get_portfolio"], surface="web", role="paper",
                            withheld={"role": 2, "plan": 3})
    assert "2 more " + WITHHELD_REASONS["role"] in out
    assert "3 more " + WITHHELD_REASONS["plan"] in out
    # "a command you are refused looks exactly like a command that is broken"
    # — by its PHRASE and by its NAME. The first draft checked only the
    # phrase, and a mutation that appended the bare keys survived it.
    for name in ("deepscan", "optimize", "run_backtest"):
        assert SKILL_SAYS[name] not in out
        assert name not in out, name
    assert not (set(SKILL_SAYS) - {"get_portfolio"}) & set(out.split()), out


def test_a_caller_missing_nothing_gets_no_withheld_section():
    # RED HERRING: `{"role": 0}` is a reading, and "0 more need a role you do
    # not have" is a sentence about nothing.
    out = capability_answer(["get_portfolio"], surface="web",
                            withheld={"role": 0, "plan": 0})
    assert "Not in that list" not in out


def test_an_unknown_withheld_reason_is_dropped():
    out = capability_answer(["get_portfolio"], surface="web",
                            withheld={"sunspots": 4})
    assert "4 more" not in out and "sunspots" not in out


def test_the_boundary_section_is_never_omitted():
    """It is the half a caller cannot get anywhere else, and the half that
    stops them asking a model that cannot act and would narrate one."""
    for kwargs in ({}, {"withheld": {"role": 1}}, {"surface": "telegram"}):
        out = capability_answer([], **kwargs)
        assert "What I will not do from chat" in out
        assert "I do not place, size or modify a trade" in out
        assert "I do not stop the engine" in out


def test_the_web_card_names_no_slash_command():
    out = capability_answer(list(SKILL_SAYS), surface="web", role="trader",
                            withheld={"plan": 2})
    assert _commands_named(out) == [], _commands_named(out)


def test_the_telegram_card_names_the_command_that_exists_there():
    out = capability_answer(["get_portfolio"], surface="telegram")
    assert _commands_named(out) == ["/help"], "the full reference IS a door here"


def test_an_extra_a_surface_knows_about_is_carried_but_not_invented():
    """The web client's own intercepts live in browser code; a Python answer
    that claimed to be exhaustive would overstate its coverage."""
    out = capability_answer(["get_portfolio"], surface="web",
                            extras=["your net worth across chains", "  "])
    assert "your net worth across chains" in out
    assert out.count("•") == 2 + 3          # one skill, one extra, three limits


# ── 3b. why the Telegram card is not the answer here ────────────────────────

#: Alias map copied from `_chat_turn` — the web resolves five scan intents
#: onto three engines before asking the registry, so a walk that skipped it
#: would overstate how many commands reach nothing.
_SCAN_ALIASES = {"scan_deep": "deepscan", "scan_scalp": "pro_scan",
                 "scan_swing": "pro_scan", "scan_intraday": "scan_market",
                 "scan_full": "scan_market"}


def catalogue_on_the_web() -> tuple[int, int, dict[str, str]]:
    """``(named, reach_nothing, {command: skill})`` for `/help`'s own list,
    typed into web chat as the card prints it.

    The web has no slash handling at all, so every line of that card is a
    sentence handed to the intent router. This is the measurement behind the
    decision not to reuse `_cmd_help` — recomputed rather than remembered,
    because the first draft of this slice wrote down 79/10/5 from an earlier
    walk and could not reproduce it.
    """
    from bot.nlp.intent_router import IntentRouter
    from bot.skills.command_catalog import help_sections
    from bot.skills.skill_registry import build_default_registry

    names = [n for _t, rows in help_sections(is_admin=False) for n, _d in rows]
    router, registry = IntentRouter(), build_default_registry()
    hits: dict[str, str] = {}
    for name in names:
        typed = name if name.startswith("/") else "/" + name
        skill = router.classify_rules(typed).skill or ""
        skill = _SCAN_ALIASES.get(skill, skill)
        if skill and registry.get(skill) is not None:
            hits[name] = skill
    return len(names), len(names) - len(hits), hits


def test_the_telegram_card_would_be_a_mostly_false_answer_here():
    """A card that names a command is claiming the command does something."""
    named, nothing, hits = catalogue_on_the_web()
    assert (named, nothing, len(hits)) == (91, 79, 12), (named, nothing, hits)
    # The sharpest one: the universe sweep answered by a single-asset read.
    assert hits.get("scan") == "analyze_asset", hits


def test_the_capability_card_names_none_of_them():
    """The replacement is what the caller can ASK FOR, in words."""
    out = capability_answer(list(SKILL_SAYS), surface="web")
    _named, _nothing, hits = catalogue_on_the_web()
    for command in hits:
        assert "/" + command not in out, command


# ── 4. the web answers it ───────────────────────────────────────────────────

def _web(monkeypatch, *, role="paper", denial=None):
    from bot.nlp.conversation_store import ConversationStore
    from bot.nlp.intent_router import IntentRouter
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
    asked: list[str] = []
    h = NS(intent_router=IntentRouter(),
           registry=NS(get=lambda n: asked.append(n) or None),
           conversations=ConversationStore(),
           users=NS(get_tier=lambda u: "elite", is_authorized=lambda u: True,
                    get=lambda u: {"role": role},
                    permission_denial=lambda u, p: denial),
           _llm_chat=AsyncMock(return_value="model answer"))
    return ug, h, asked


def _turn(ug, h, text):
    async def _json():
        return {"telegram_id": CALLER, "text": text}

    req = NS(app={"tg_handler": h,
                  "engine": NS(firewall_scan=lambda *a, **kw: None,
                               _pending_ideas={})},
             json=_json, headers={}, remote="1.2.3.4")
    resp = asyncio.run(ug._chat_turn(req))
    return resp, json.loads(resp.text)


@pytest.mark.parametrize("text", ["help", "what can you do", "capabilities",
                                  "how does this work", "im new what now"])
def test_the_web_answers_the_capability_question(monkeypatch, text):
    ug, h, asked = _web(monkeypatch)
    resp, body = _turn(ug, h, text)
    assert resp.status == 200
    assert body["intent"] == "help"
    assert "What I can do for you" in body["reply_html"]
    for never in ("not available on this bot", "no such tool"):
        assert never not in body["reply_html"], (text, never)
    assert "help" not in asked, "the registry was asked for a help skill"


def test_the_web_records_that_the_card_was_shown(monkeypatch):
    """The marker, not the card: every line of it is derived from the model's
    own tool catalogue, which it already holds in full."""
    ug, h, _asked = _web(monkeypatch)
    _turn(ug, h, "what can you do")
    turns = [(m.role, m.content) for m in h.conversations.get_recent(CALLER, limit=10)]
    assert [r for r, _ in turns] == ["user", "assistant"], turns
    assert turns[0][1] == "what can you do"
    assert "CONTENTS NOT RECORDED" in turns[1][1]
    assert "UNAVAILABLE" not in turns[1][1]


def test_a_pending_caller_is_told_what_the_product_does(monkeypatch):
    """`pending` holds `help` and nothing else, and `_cmd_help` carries no
    `@guard` for the same reason: somebody who cannot be told what the product
    does cannot ask for access to it."""
    from bot.utils.user_store import ROLE_PERMISSIONS

    assert "help" in ROLE_PERMISSIONS["pending"]
    assert "status" not in ROLE_PERMISSIONS["pending"]
    ug, h, _asked = _web(monkeypatch, role="pending", denial="role")
    resp, body = _turn(ug, h, "what can you do")
    assert resp.status == 200
    assert "What I can do for you" in body["reply_html"]
    assert "cannot reach any" in body["reply_html"]
    assert "What I will not do from chat" in body["reply_html"]


def test_help_is_not_in_the_gated_routed_table(monkeypatch):
    """Guards the decision above: adding it there would refuse `pending`, and
    the invariant would look for a guarded command that does not exist."""
    from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION
    assert "help" not in WEB_ROUTED_PERMISSION


# ── 5. Telegram answers the question; /help keeps the reference ─────────────

@pytest.mark.asyncio
async def test_the_typed_question_gets_the_card_and_not_the_catalogue(bot):
    from bot.nlp.conversation_store import ConversationStore
    bot.conversations = ConversationStore()
    bot._cmd_help = AsyncMock()
    await bot._handle_message(_update(OPERATOR, "what can you do"), None)
    bot._cmd_help.assert_not_awaited()
    out = "\n".join(bot.sent)
    assert "What I can do for you" in out
    assert "/help" in out, "the reference is still named, because it is a door here"


@pytest.mark.asyncio
async def test_the_command_still_renders_the_full_reference(bot):
    """The split: a typed QUESTION gets an answer, a COMMAND gets a reference.
    `/help` is untouched."""
    import inspect

    from bot.skills.start_commands import StartCommands
    src = inspect.getsource(StartCommands._cmd_help)
    assert "command_catalog" in src or "help_sections" in src


from tests.test_a_halt_is_the_operators_own_sentence import bot as _halt_bot  # noqa: E402
from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update  # noqa: E402


@pytest.fixture(name="bot")
def _bot(tmp_path):
    yield from _halt_bot.__wrapped__(tmp_path)

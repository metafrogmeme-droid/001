""""What can you do?" was answered, on the web, with "that tool does not exist".

`help` classifies at confidence 1.0 and no skill is registered under the name,
so the web fell to `skill_unavailable_notice`: *"I understood that as help, but
that tool is not available on this bot right now."* The capability exists — the
identical text runs a working command reference on Telegram. The statement is
false, and `skill_unavailable_memory` wrote the same falsehood into the model's
own history: *"this bot has no such tool wired up"*.

Seven of ten phrasings never even got that far. `what can you do` was eaten by
the social gate, `capabilities` and `/help` by the three-words-or-fewer rule,
and `how does this work`, `show me what you can do`, `what can i ask` and
`im new what now` matched nothing and reached a model with no tools and no
list — which is the improvised-feature-list failure the unavailable notice was
written to prevent, arriving through the other door.

**Reusing the Telegram card would have replaced a false refusal with a
mostly-false answer.** Most of what that card names for a non-admin reaches the
tool-less chat model on the web, and the rest reach a skill by incidental word
matching. A card that names a command is claiming the command does something.

The numbers are NOT restated in this docstring. They were — "79 reach the
tool-less chat model, 10 reach a skill … and 5 of those 10 reach the wrong
engine" — which is the very 79/10/5 that `catalogue_on_the_web` three hundred
lines below confesses it could not reproduce, sitting in the header of the file
containing the retraction. The walk is that function; the one written-down copy
is `CLAUDE.md`'s, and a test reads it back out of the prose.

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
import textwrap
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
#: The trailing `(?:_[a-z]+)*` is not cosmetic. Seven commands in the
#: catalogue carry an underscore — `emergency_stop`, `open_positions`,
#: `grant_live`, `revoke_live`, `set_tier`, `daily_report`, `latest_signal` —
#: and `/[a-z]{2,}` stops at the underscore, reading `/emergency_stop` as
#: `/emergency`. That is a false ACQUITTAL in
#: `test_no_phrase_names_a_command` and in the web card's no-slash check: a
#: card printing `/emergency_stop` on a surface with no slash handling would
#: have been checked against the string `/emergency`, which is not a command,
#: so nothing downstream could have named it. A false accusation is loud; this
#: one just sits there.
_SLASH_COMMAND = re.compile(r"(?<![\w/<])/[a-z]{2,}(?:_[a-z]+)*")


def _commands_named(text: str) -> list[str]:
    return _SLASH_COMMAND.findall(re.sub(r"<[^>]+>", " ", text))


def test_the_slash_check_reads_commands_and_not_punctuation():
    """Guards the guard: the first draft flagged `stop/take-profit` and every
    closing HTML tag."""
    assert _commands_named("stop/take-profit triggers") == []
    assert _commands_named("<b>bold</b> and <i>x</i>") == []
    assert _commands_named("send /help for the reference") == ["/help"]
    assert _commands_named("— /venue to switch") == ["/venue"]
    # The underscore half. `/[a-z]{2,}` read this as `/emergency`, which is
    # not in the catalogue, so every check built on this extractor was
    # measuring a name the product does not have.
    assert _commands_named("run /emergency_stop now") == ["/emergency_stop"]
    assert _commands_named("/open_positions and /set_tier") == [
        "/open_positions", "/set_tier"]


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


@pytest.mark.parametrize("reason,bucket", [
    ("insufficient", "plan"),
    ("no_wallet", "wallet"),
    ("unverified", "wallet"),
    ("bad_wallet", "wallet"),
    ("misconfigured", "unreadable"),
    ("unavailable", "unreadable"),
    # THE ONE NOTHING DROVE. `_tier_verdict`'s docstring promises "an unknown
    # reason buckets to `unreadable` rather than `plan`" — the whole point
    # being that a reason added to the gate tomorrow lands in the honest
    # bucket by default instead of the flattering one. A mutation flipping
    # that default to `plan` survived the entire round, because every fixture
    # planted a reason the map already knows.
    ("a_reason_added_next_year", "unreadable"),
    ("", "unreadable"),
])
def test_every_tier_reason_lands_in_the_bucket_that_names_its_fix(
        monkeypatch, reason, bucket):
    """Six reasons, three fixes, and a seventh case that is none of them.

    `tier_gate.check_user`'s own docstring says these must not be collapsed:
    "you have not staked enough" and "we could not check your stake" are
    different messages, and telling somebody holding 100,000 $RCLAW to stake
    more during an RPC outage is the defect the vocabulary exists to prevent.
    """
    from bot.token import tier_gate

    gated = {"deepscan"}
    monkeypatch.setattr(tier_gate, "check_user",
                        lambda users, uid, feature: (feature not in gated,
                                                     reason))
    _reach, withheld = skill_reach(_users(), "u", "telegram",
                                   list(SKILL_PERMISSION))
    assert withheld.get(bucket) == 1, (reason, bucket, withheld)
    for other in ("plan", "wallet", "unreadable"):
        if other != bucket:
            assert withheld.get(other, 0) == 0, (reason, other, withheld)


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
    """The DECISION this test owns — fail closed — is unchanged. The WORD is
    not: an unreadable store marks `unreadable`, not `role`."""
    def _raises(u, p):
        raise RuntimeError("store down")

    users = NS(permission_denial=_raises, get_tier=lambda u: "elite")
    reach, withheld = skill_reach(users, "u", "web", list(SKILL_PERMISSION))
    assert reach == []
    assert withheld.get("unreadable", 0) > 0
    assert withheld.get("role", 0) == 0, (
        "a store that is DOWN is not a role that is short")


def test_a_store_that_is_down_and_a_role_that_is_short_read_differently():
    """The defect, driven: the two produced BYTE-IDENTICAL cards, so a trader
    read "24 more need a role you do not have yet. Your role here is trader."
    during an outage — a confident negative with a specific wrong remedy, on
    the card whose whole job is to stop exactly that.

    Asserted as a DIFFERENCE between two rendered cards rather than as a
    literal, because a literal is what the next rewording quietly satisfies.
    """
    def _raises(u, p):
        raise RuntimeError("store down")

    down = NS(permission_denial=_raises, get_tier=lambda u: "elite")
    short = NS(permission_denial=lambda u, p: "role", get_tier=lambda u: "elite")

    def _card(users):
        reach, withheld = skill_reach(users, "u", "web", list(SKILL_PERMISSION))
        return capability_answer(reach, surface="web", role="trader",
                                 withheld=withheld)

    a, b = _card(down), _card(short)
    assert a != b, "an outage and a short role still render identically"
    assert "could not be checked" in a
    assert "need a role you do not have yet" not in a
    # And the role line explains the `role` count only — printed under an
    # `unreadable` count it names the reader as the obstacle when the bot is.
    assert "Your role here is" not in a
    assert "Your role here is" in b


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
    assert "2 more " + WITHHELD_REASONS["role"][1] in out
    assert "3 more " + WITHHELD_REASONS["plan"][1] in out
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
    """Telegram HAS slash handling, so naming a command is a true claim there.

    The assertion is that every command named EXISTS, not that exactly one is
    — pinning the literal `["/help"]` made the card unable to grow the row it
    needed, which is how a guard starts arguing against its own fix.
    """
    from bot.skills.command_catalog import all_entries

    out = capability_answer(["get_portfolio"], surface="telegram")
    named = _commands_named(out)
    assert "/help" in named, "the reference IS a door here"
    catalogue = set(all_entries())
    for cmd in named:
        assert cmd.lstrip("/") in catalogue, f"{cmd} is not a command"


@pytest.mark.parametrize("reason", sorted(WITHHELD_REASONS))
def test_one_withheld_skill_reads_as_one(reason):
    """n == 1 is the ORDINARY case, not the edge.

    The counts are what ONE caller is missing, and a live card printed
    "• 1 more need a role you do not have yet." and "• 1 more only work in the
    Telegram bot." for as long as the section existed — every fixture happened
    to withhold two or more, so nothing drove the singular.
    """
    one = capability_answer(["get_portfolio"], surface="web",
                            role="trader", withheld={reason: 1})
    many = capability_answer(["get_portfolio"], surface="web",
                             role="trader", withheld={reason: 4})
    singular, plural = WITHHELD_REASONS[reason]
    assert f"• 1 more {singular}." in one, one
    assert f"• 4 more {plural}." in many, many
    # A bare verb agreement is the thing that was wrong, so check it directly
    # rather than trusting the table: the singular must not read "1 more need".
    assert "1 more need a" not in one
    assert "1 more only work in" not in one


def test_the_client_capability_seam_has_a_cable_now(monkeypatch):
    """`extras` had no production caller for the life of the parameter.

    Its own docstring says it is for "capabilities a surface knows about and
    this module cannot see — the web client's own intercepts live in browser
    code". They do, in `app/routes/chat.js`, and the payload carried
    telegram_id/name/text/profile/lang and nothing else — so the card written
    to stop the bot OVERSTATING what it can do was understating it by every
    row of that table. A socket with no cable, which is the "computed and read
    by nobody" shape with the arrow reversed.
    """
    ug, h, _asked, _ids = _web(monkeypatch)
    body = {"telegram_id": CALLER, "text": "what can you do",
            "client_capabilities": ["your net worth across every chain",
                                    "a price alert you set here"]}

    async def _json():
        return body

    req = NS(app={"tg_handler": h,
                  "engine": NS(firewall_scan=lambda *a, **kw: None,
                               _pending_ideas={})},
             json=_json, headers={}, remote="1.2.3.4")
    html = json.loads(asyncio.run(ug._chat_turn(req)).text)["reply_html"]
    assert "your net worth across every chain" in html
    assert "a price alert you set here" in html
    # ...and they sit in the ASK list, because the client answers them when
    # the caller asks in words. A row in the "not from words" section would be
    # a false statement about the one surface they work on.
    ask_block = html.split("These I have, but not from words")[0]
    assert "your net worth across every chain" in ask_block


@pytest.mark.parametrize("raw", [None, "not a list", 42, {"a": 1},
                                 [1, 2, 3], [""], ["   "]])
def test_a_client_capability_field_that_is_not_one_contributes_nothing(raw):
    """ABSENT IS ABSENT. Telegram is a caller here too and has no intercepts,
    so a missing or malformed field is the ordinary case, not an error — and
    it must not become a row, a placeholder or a refusal."""
    from bot.web.user_gateway import _client_capabilities

    assert _client_capabilities({"client_capabilities": raw}) == []
    assert _client_capabilities({}) == []
    assert _client_capabilities(None) == []


def test_the_client_capability_field_is_bounded_and_deduped():
    """It arrives in a request BODY, so it is caller-controlled text however
    trusted the caller. `capability_answer` escapes what it prints; these
    bounds decide how much of it there can be."""
    import bot.web.user_gateway as _ug

    _MAX_CLIENT_CAPS = _ug._MAX_CLIENT_CAPS
    _MAX_CLIENT_CAP_LEN = _ug._MAX_CLIENT_CAP_LEN
    _client_capabilities = _ug._client_capabilities

    many = [f"row {i}" for i in range(_MAX_CLIENT_CAPS + 20)]
    assert len(_client_capabilities({"client_capabilities": many})) == _MAX_CLIENT_CAPS
    long = "x" * (_MAX_CLIENT_CAP_LEN + 500)
    got = _client_capabilities({"client_capabilities": [long]})
    assert len(got[0]) == _MAX_CLIENT_CAP_LEN
    # A duplicate is one capability, and newlines are not a way to add rows.
    assert _client_capabilities(
        {"client_capabilities": ["a\nb", "a b", " a  b "]}) == ["a b"]


def test_a_client_capability_cannot_smuggle_markup_onto_the_card():
    """The card is HTML. `capability_answer` escapes extras, and this drives
    that rather than reading it: the row arrives as text and leaves as text."""
    out = capability_answer(["get_portfolio"], surface="web",
                            extras=["<script>alert(1)</script>"])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_an_extra_a_surface_knows_about_is_carried_but_not_invented():
    """The web client's own intercepts live in browser code; a Python answer
    that claimed to be exhaustive would overstate its coverage."""
    out = capability_answer(["get_portfolio"], surface="web",
                            extras=["your net worth across chains", "  "])
    assert "your net worth across chains" in out
    assert out.count("•") == 2 + 3          # one skill, one extra, three limits


# ── 3b. why the Telegram card is not the answer here ────────────────────────

#: NOT a copy. This was one — five rows written out here as "copied from
#: `_chat_turn`" — and three of them were wrong: it sent `scan_scalp` and
#: `scan_swing` to `pro_scan`, which is the TELEGRAM dispatch, on a walk
#: whose whole subject is what the WEB reaches. A copy of a map is a second
#: answer, and this one was answering about the wrong surface. `skill_doors`
#: is the single table both surfaces read.


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
    from bot.nlp.skill_doors import web_scan_aliases
    from bot.skills.command_catalog import help_sections
    from bot.skills.skill_registry import build_default_registry

    _WEB_SCAN_ALIASES = web_scan_aliases()
    names = [n for _t, rows in help_sections(is_admin=False) for n, _d in rows]
    router, registry = IntentRouter(), build_default_registry()
    hits: dict[str, str] = {}
    for name in names:
        typed = name if name.startswith("/") else "/" + name
        skill = router.classify_rules(typed).skill or ""
        skill = _WEB_SCAN_ALIASES.get(skill, skill)
        if skill and registry.get(skill) is not None:
            hits[name] = skill
    return len(names), len(names) - len(hits), hits


def test_the_catalogue_count_is_written_down_in_exactly_one_place():
    """Four copies of one measurement, and three of them were wrong.

    `CLAUDE.md` says 91 / 79 / 12 and stays right because
    `test_the_catalogue_numbers_are_the_numbers_a_drive_returns` reads those
    integers back out of the prose and compares them to `catalogue_on_the_web`.
    Nothing pinned the other three, so `/alerts` becoming the 91st command left
    `capabilities.py` and `_chat_turn` both claiming 78 of 90, and this file's
    own header still carrying the retracted 79/10/5 — three hundred lines above
    the function that says it could not reproduce them.

    The fix is not a fourth pin. It is that the two production sites and this
    header state the SHAPE and name the drive, and a `<n> of the <m>` in any of
    them fails here. `CLAUDE.md` is deliberately not scanned: it is the one
    copy, and it is pinned.
    """
    import pathlib
    import re

    from tests.source_scan import code_only

    counted = re.compile(r"\b\d+\s+of\s+the\s+\d+\b")
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("bot/formatters/capabilities.py", "bot/web/user_gateway.py"):
        text = (root / rel).read_text()
        # The DOCSTRING and the comments are the rot surface, so read the
        # prose rather than `code_only` — which strips exactly the half that
        # went stale. `code_only` is used the other way round below.
        assert not counted.search(text), (rel, counted.search(text).group(0))
        assert "catalogue_on_the_web" in text, f"{rel} names no measurement"
    here = pathlib.Path(__file__).read_text()
    head = here.split('"""')[1]
    assert not counted.search(head), counted.search(head).group(0)
    # ...and the running code of this file must not grow a literal either:
    # the drive returns the numbers, so a test that hard-codes one beside it
    # is a copy that can disagree with the call two lines above it.
    assert "catalogue_on_the_web" in code_only(here)


def test_the_telegram_card_would_be_a_mostly_false_answer_here():
    """A card that names a command is claiming the command does something."""
    named, nothing, hits = catalogue_on_the_web()
    # Re-measured when the scan-mode rules became whole-message rules: the
    # bare `swing`/`scalp`/`intraday` alternatives that matched INSIDE
    # "/swing" are gone, so three more typed commands reach nothing.
    # Re-measured again when /nft, /spot and /airdrops joined the catalogue:
    # typed as commands none of the three reaches a registered skill, so
    # every one of them lands on the model. And again when /stake and
    # /unstake left the operator-only group for the Trading group: typed
    # bare on the web neither reaches a rule (a bare "stake" is a decoy the
    # router deliberately leaves to the model), so both land there too.
    assert (named, nothing, len(hits)) == (103, 94, 9), (named, nothing, hits)
    # The sharpest one: the universe sweep answered by a single-asset read.
    assert hits.get("scan") == "analyze_asset", hits


def test_the_capability_card_names_none_of_them():
    """The replacement is what the caller can ASK FOR, in words."""
    out = capability_answer(list(SKILL_SAYS), surface="web")
    _named, _nothing, hits = catalogue_on_the_web()
    for command in hits:
        assert "/" + command not in out, command


# ── 3c. the door table refuses what it has not measured ─────────────────────

def test_an_unmeasured_surface_is_refused_and_not_answered_with_everything():
    """`words_reach` narrowed only on `surface == "web"`.

    So every other string fell through to "the router's whole vocabulary plus
    every chat tool" — 36 names including `halt`, `close_position` and
    `emergency_stop`, on the function whose entire job is deciding what the
    capability card may PROMISE. Fail-OPEN on a door list, and it answered MORE
    for an unrecognised surface than for the one it modelled best, because the
    unrecognised branch skipped the scan dispatch and kept raw ROUTER INTENT
    names that are not skills at all.

    Guard, not omit: an unmeasured surface is neither "everything" nor
    "nothing", so it raises.
    """
    from bot.nlp.skill_doors import SURFACES, UnknownSurface, words_reach

    for bad in ("", "nonsense", "Web", "telegram "):
        with pytest.raises(UnknownSurface):
            words_reach(bad)
    for good in SURFACES:
        assert isinstance(words_reach(good), set)
    # The measured shape: telegram reaches strictly more than the web, and the
    # unknown surface used to reach more than EITHER.
    assert len(words_reach("telegram")) > len(words_reach("web")) > 0


def test_the_signed_out_surfaces_reach_no_skill_by_words():
    """Measured, not assumed. `_public_chat_turn` classifies the message,
    keeps only `needs_live_market_data`'s boolean and hands the text to
    `_llm_chat(public=True)`; `_chat_tools_for` returns `[]` for
    `public or not user_id`. Zero doors — the public scan gate is a REFUSAL,
    which is not a door.

    Driven rather than read, because the claim is about what the tool builder
    DOES, and a comment in `_public_chat_turn` already asserts something about
    that path which driving showed to be false.
    """
    from bot.nlp.skill_doors import words_reach
    from bot.skills.telegram_handler import _chat_tools_for

    assert words_reach("public") == set()
    assert words_reach("api") == set()
    handler = NS(registry=NS(get=lambda n: object()), users=object())
    assert _chat_tools_for(handler, "42", "web", True) == []
    assert _chat_tools_for(handler, "", "web", False) == []


def test_the_scan_dispatch_answers_no_bare_intent_name():
    """`row.get(surface, intent)` returned `scan_deep` — a router intent, not
    a skill — for any surface the row does not carry, so every downstream
    membership test silently missed."""
    from bot.nlp.skill_doors import SCAN_DISPATCH, dispatch_kwargs, dispatches_to
    from bot.skills.skill_registry import build_default_registry

    registry = build_default_registry()
    for intent, row in SCAN_DISPATCH.items():
        got = dispatches_to(intent)
        assert got != intent, intent
        assert registry.get(got) is not None, (intent, got)
        # THE ARGUMENTS ARE PART OF THE ANSWER. All three scan skills are
        # `execute(self, engine, **kwargs)`, so a table naming the skill and
        # not its kwargs dispatches `pro_scan` with no `mode` — and
        # `MODE_CFG.get(mode, MODE_CFG["intraday"])` renders a SCALP ask as
        # "RUNECLAW INTRADAY SCAN / Timeframe: 15M" with no marker at all.
        kw = dispatch_kwargs(intent)
        assert kw, intent
        assert dispatch_kwargs(intent) is not row["kwargs"], (
            f"{intent} hands out the table's own dict; one turn's mutation "
            "would reach the next")
    # An intent the table does not name is its own skill, with no kwargs —
    # the ordinary case.
    assert dispatches_to("get_portfolio") == "get_portfolio"
    assert dispatch_kwargs("get_portfolio") == {}


def test_every_askable_row_reaches_the_skill_it_describes():
    """THE SHARPEST FORM OF THE SUBJECT: type the card's own sentence.

    `words_reach` proves a door EXISTS for each row in the ask list. It does
    not prove the door leads where the row says, and one did not: the
    `get_orders` row — "your resting limit orders and stop/take-profit
    triggers, as the exchange reports them" — routed to `get_portfolio` at
    confidence 1.0, because the bare Portfolio keyword rule matches `profit`
    INSIDE "take-profit" and was registered first. A caller typing the
    sentence the card invited them to type got the POSITIONS card with no
    sentence: "no positions" over resting limits, which is the defect
    `CLAUDE.md` records as fixed when `get_orders` stopped being aliased to
    `get_portfolio` on the web. Fixed at the alias, reintroduced by rule
    ORDER — which is invisible from either rule on its own.

    A row that reaches NO rule is fine and expected: the model's tool
    catalogue is the second door, and `words_reach` counts it. What is never
    fine is reaching a DIFFERENT skill.
    """
    from bot.nlp.intent_router import IntentRouter
    from bot.nlp.skill_doors import dispatches_to, doorless

    router = IntentRouter()
    wrong = []
    for surface in ("telegram", "web"):
        askable = [n for n in SKILL_SAYS
                   if n not in doorless(list(SKILL_SAYS), surface)]
        for name in askable:
            got = router.classify_rules(SKILL_SAYS[name]).skill or ""
            if not got:
                continue          # no rule: the chat tool is the door
            if dispatches_to(got) != name:
                wrong.append((surface, name, got))
    assert wrong == [], wrong


def test_the_card_asks_the_model_what_it_actually_holds():
    """A SECOND COPY OF A GATE, and it decided what the card promises.

    Two of the card's rows — `proposals`, `rejected_trades` — have no router
    rule at all, so a chat TOOL is their only door. (Four when this was
    written: `check_event_risk` and `macro_brief` have rules of their own
    now, "event risk on eth" and "is macro cutting size".) `words_reach` read that door off the static
    `CHAT_TOOLS` tuple, while the catalogue the model is actually offered is
    `_chat_tools_for`, which applies two filters the tuple knows nothing about
    (`CONFIG.llm.chat_tools_enabled`, `registry.get(name) is not None`) and a
    bare `except: return []`.

    Driven with chat tools switched OFF, the model held ZERO tools and the
    card still printed every one of them under "Ask me in your own words for
    any of these" — a card naming a capability whose only door is shut, which is the
    `/vault` hint shape inverted and this module's own stated subject.
    """
    from bot.nlp.skill_doors import words_reach

    tool_only = {"proposals", "rejected_trades"}
    # The premise: no router rule names any of them, on either surface.
    from bot.nlp.intent_router import routed_skill_names
    assert not (tool_only & routed_skill_names()), tool_only & routed_skill_names()

    rows = sorted(tool_only) + ["get_portfolio"]
    held = capability_answer(rows, surface="web",
                             tools={"get_portfolio", *tool_only})
    shut = capability_answer(rows, surface="web", tools={"get_portfolio"})
    for name in tool_only:
        says = SKILL_SAYS[name]
        assert says in held.split("These I have, but not from words")[0], name
        assert says not in shut.split("These I have, but not from words")[0], name
        # ...and NOT dropped in silence: the product still has it, so it moves
        # into the other section rather than vanishing.
        assert says in shut, name
    # `words_reach` with no caller is a claim about the PRODUCT, not a person,
    # and that is the only case the static tuple is right for.
    assert tool_only <= words_reach("web", tools=None)
    assert not (tool_only & words_reach("web", tools=set()))


def test_both_card_call_sites_pass_the_caller_s_own_tool_catalogue():
    """The fix has to land at BOTH call sites, and a default of `None` means
    a caller that forgets silently gets the static tuple back — the exact
    behaviour the fix removes. Unparsed rather than grepped: `tools=` as a
    literal is what both the fix and a hard-coded set look like."""
    import ast
    import inspect

    from bot.skills import telegram_handler as th
    from bot.web import user_gateway as ug

    for fn in (ug._chat_turn, th.TelegramHandler._handle_message):
        src = textwrap.dedent(inspect.getsource(fn))
        for call in ast.walk(ast.parse(src)):
            if not (isinstance(call, ast.Call)
                    and ast.unparse(call.func) == "capability_answer"):
                continue
            kw = {k.arg: k.value for k in call.keywords}
            assert "tools" in kw, (fn.__qualname__, "no tool catalogue passed")
            # ...and it is READ, not written out: a set literal here would be
            # a third copy of the catalogue. The value is usually a local, so
            # the NAME is resolved back to what built it rather than matched
            # as text — a bare `ast.unparse(...)` sees `_tools` and learns
            # nothing, which is the substring-for-a-property trap.
            expr = ast.unparse(kw["tools"])
            if isinstance(kw["tools"], ast.Name):
                built = [ast.unparse(n.value) for n in ast.walk(ast.parse(src))
                         if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == kw["tools"].id
                                 for t in n.targets)]
                assert built, (fn.__qualname__, f"{expr} is never assigned")
                expr = " ".join(built)
            assert "_chat_tools_for" in expr, (fn.__qualname__, expr)
            break
        else:
            raise AssertionError(f"{fn.__qualname__} does not build the card")


def test_both_dispatchers_read_the_one_scan_table():
    """THREE COPIES, THREE ANSWERS, and the card derived from one of them.

    `telegram_handler`'s `scan_modes` sent the two deep modes to `deepscan`
    and the three timeframe modes to `pro_scan`; `user_gateway`'s
    `_INTENT_ALIASES` sent all five to `scan_market`; a test carried a third
    under a comment claiming it was "copied from `_chat_turn`", which it was
    not — it agreed with neither. `words_reach` is computed THROUGH that
    mapping, so a card could print a row as reachable on the strength of a
    map no dispatcher used.

    Driven by unparsing the two call sites rather than grepping for a literal:
    a literal is exactly what a copy looks like, so a grep for one cannot tell
    the fix from the defect.
    """
    import ast
    import inspect

    from bot.nlp.skill_doors import SCAN_DISPATCH
    from bot.skills import telegram_handler as th
    from bot.web import user_gateway as ug

    # WHICH skill, and WITH WHICH ARGUMENTS. `intent.kwargs` is empty for
    # every scan rule and all five skills take `**kwargs`, so a dispatcher
    # that reads only the skill name answers a scalp with the INTRADAY card
    # and raises nothing — which is why both readings are required here.
    for fn, wanted in (
        (ug._chat_turn, ("web_scan_aliases", "dispatch_kwargs")),
        (th.TelegramHandler._handle_message, ("dispatches_to", "dispatch_kwargs")),
    ):
        src = textwrap.dedent(inspect.getsource(fn))
        calls = {ast.unparse(c.func) for c in ast.walk(ast.parse(src))
                 if isinstance(c, ast.Call)}
        for name in wanted:
            # `from ... import dispatch_kwargs as _dispatch_kwargs` is still a
            # read of the table, so the leading underscores are stripped.
            assert any(c.lstrip("_") == name for c in calls), (
                fn.__qualname__, name, sorted(calls))

        # ...and no second copy beside it. The shape that was there is a dict
        # literal keyed on the scan intents; what makes one a DISPATCH copy is
        # its values — a skill name or a mode is an identifier, a waiting
        # message is a sentence. `scan_thinking` keeps those keys and carries
        # only prose, which is presentation and not a second answer.
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if not (keys & set(SCAN_DISPATCH)):
                continue
            named = {v.value for val in node.values for v in ast.walk(val)
                     if isinstance(v, ast.Constant)
                     and isinstance(v.value, str) and v.value.isidentifier()}
            nested = any(isinstance(v, (ast.Dict, ast.Tuple, ast.List))
                         for val in node.values for v in ast.walk(val))
            assert not named and not nested, (
                fn.__qualname__, sorted(named), ast.unparse(node)[:160])


def test_a_row_with_no_door_at_all_says_so_rather_than_vanishing():
    """The third rendering of a `commanded` row, and it is NOT reachable with
    today's table — which is why it needs a drive rather than a sweep.

    `doorless` finds four rows on Telegram and eight on the web; every
    Telegram one has a command, and the two web ones that do not
    (`trade_journal`, `halt`) never reach `reachable` — the first is withheld
    as `surface`, the second is dangerous and skipped. So the branch is a
    fail-safe for a skill added tomorrow with no router rule, no chat tool and
    no catalogue entry.

    It is kept rather than deleted, and that is the opposite call from the
    macro card's unreachable `check_risk()` fallback: there the default was
    WRONG (full size from an unreadable multiplier), here the row would
    silently VANISH from a card whose whole subject is what the product can
    do. An honest sentence for a state that does not exist beats a row that
    disappears when it does.
    """
    from bot.nlp.skill_doors import command_for, doorless

    # `trade_journal` is the real one: in `SKILL_SAYS`, absent from
    # `WEB_CHAT_SKILLS` so words do not reach it on the web, and named by no
    # command. A MADE-UP name cannot drive this branch — the renderer drops a
    # skill `SKILL_SAYS` does not carry, which is
    # `test_an_unknown_skill_name_is_dropped_not_printed`'s subject and was
    # this test's first draft failing for the right reason.
    orphan = "trade_journal"
    assert command_for(orphan) is None
    assert doorless([orphan], "web") == [orphan]
    assert "a_skill_nobody_has_wired_yet" not in SKILL_SAYS

    out = capability_answer(["get_portfolio", orphan], surface="web",
                            tools={"get_portfolio"})
    assert "no way to ask for it from here yet" in out
    # It is in the SECOND section, never offered as something to ask for.
    head, _, tail = out.partition("These I have, but not from words")
    assert SKILL_SAYS[orphan] in tail
    assert SKILL_SAYS[orphan] not in head
    # ...and on Telegram the same row IS askable, so the sentence must not
    # appear there: a fail-safe that fires when nothing is wrong is how a
    # reader learns to skip the next one.
    tg = capability_answer(["get_portfolio", orphan], surface="telegram",
                           tools={"get_portfolio", orphan})
    assert "no way to ask for it from here yet" not in tg
    assert SKILL_SAYS[orphan] in tg.partition("These I have, but not from words")[0]
    # ...and the state really is unreachable today, so this test is the only
    # thing exercising it. If that stops being true, the sweep below starts
    # naming rows and this comment is the thing to re-read.
    for surface in ("web", "telegram"):
        assert [n for n in doorless(list(SKILL_SAYS), surface)
                if command_for(n) is None] in ([], ["trade_journal", "halt"],
                                               ["halt", "trade_journal"])


def test_the_card_names_no_door_from_a_fallback():
    """`_CANNOT_HALT.get(surface, _CANNOT_HALT["web"])` answered the WEB
    sentence — "the Emergency-stop control on the dashboard is the door" — for
    any surface missing from the map, `telegram` included, where there is no
    dashboard and the door is a typed imperative. A door named by a fallback is
    a door named by a guess."""
    import bot.formatters.capabilities as cap

    assert cap._halt_surface("telegram") == "telegram"
    for web_shaped in ("web", "public", "api"):
        assert cap._halt_surface(web_shaped) == "web"
    with pytest.raises(ValueError):
        cap._halt_surface("carrier-pigeon")
    tg = capability_answer(["get_portfolio"], surface="telegram")
    web = capability_answer(["get_portfolio"], surface="web")
    assert "Emergency-stop control on the dashboard" in web
    assert "Emergency-stop control on the dashboard" not in tg
    assert "/emergency_stop" in tg


def test_the_surface_and_the_role_both_change_the_card():
    """Two arguments that nothing proved were READ.

    A `capability_answer` that ignored `surface` entirely, or `role`, would
    have passed the file this test was added to.
    """
    rows = ["get_portfolio", "optimize"]
    tg = capability_answer(rows, surface="telegram")
    web = capability_answer(rows, surface="web")
    assert tg != web
    assert "<code>/optimize</code>" in tg, "the command IS the door there"
    assert "/optimize" not in web, "and is not a door here"

    named = capability_answer([], surface="web", role="trader",
                              withheld={"role": 3})
    anon = capability_answer([], surface="web", role="", withheld={"role": 3})
    assert named != anon
    assert "Your role here is <b>trader</b>" in named
    assert "Your role here is" not in anon


# ── 4. the web answers it ───────────────────────────────────────────────────

#: A user id the store knows NOTHING about. The first version of `_web`
#: answered the same record for every id — `get=lambda u: {"role": role}`,
#: `permission_denial=lambda u, p: denial` — so no test could tell "asked
#: about the caller" from "asked about somebody else", and a gateway that read
#: the wrong id would have passed every drive in this file. That is the
#: symmetric-fixture defect CLAUDE.md records from the live-book slice: "a
#: fixture that cannot tell the two books apart".
STRANGER = "999999"


def _web(monkeypatch, *, role="paper", denial=None, raises=False):
    from bot.nlp.conversation_store import ConversationStore
    from bot.nlp.intent_router import IntentRouter
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
    asked: list[str] = []
    #: Every id the store was ASKED about, so a drive can assert the gateway
    #: read THIS caller and not a remembered one.
    ids: list[str] = []

    def _denial(uid, perm):
        ids.append(str(uid))
        if str(uid) != CALLER:
            # A stranger holds nothing. Answering `denial` here would make the
            # fixture symmetric again by the back door.
            return "role"
        if raises:
            raise RuntimeError("role store is down")
        return denial

    def _get(uid):
        ids.append(str(uid))
        return {"role": role} if str(uid) == CALLER else {}

    h = NS(intent_router=IntentRouter(),
           registry=NS(get=lambda n: asked.append(n) or None),
           conversations=ConversationStore(),
           users=NS(get_tier=lambda u: "elite", is_authorized=lambda u: True,
                    get=_get, permission_denial=_denial),
           _llm_chat=AsyncMock(return_value="model answer"))
    return ug, h, asked, ids


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
    ug, h, asked, _ids = _web(monkeypatch)
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
    ug, h, _asked, _ids = _web(monkeypatch)
    _turn(ug, h, "what can you do")
    turns = [(m.role, m.content) for m in h.conversations.get_recent(CALLER, limit=10)]
    assert [r for r, _ in turns] == ["user", "assistant"], turns
    assert turns[0][1] == "what can you do"
    # ANCHORED AT THE HEAD, and positive about the half that is true. A bare
    # `"CONTENTS NOT RECORDED" in ...` is a SUBSTRING of the mutation #95
    # records surviving its own first draft — `"NOT SHOWN, CONTENTS NOT
    # RECORDED"`, a record claiming the card was not shown, which has the
    # model tell the user it could not show them the card in front of them.
    # That fix landed in `test_a_routed_answer_is_in_the_transcript.py` and
    # not in this sibling, which is the "ask which OTHER surface makes the
    # same claim" rule arriving a slice late.
    assert turns[1][1].startswith("[help] SHOWN, CONTENTS NOT RECORDED"), turns[1][1]
    assert "NOT SHOWN" not in turns[1][1]
    assert "UNAVAILABLE" not in turns[1][1]


def test_a_pending_caller_is_told_what_the_product_does(monkeypatch):
    """`pending` holds `help` and nothing else, and `_cmd_help` carries no
    `@guard` for the same reason: somebody who cannot be told what the product
    does cannot ask for access to it."""
    from bot.utils.user_store import ROLE_PERMISSIONS

    assert "help" in ROLE_PERMISSIONS["pending"]
    assert "status" not in ROLE_PERMISSIONS["pending"]
    ug, h, _asked, _ids = _web(monkeypatch, role="pending", denial="role")
    resp, body = _turn(ug, h, "what can you do")
    html = body["reply_html"]
    assert resp.status == 200
    assert "What I can do for you" in html
    assert "cannot reach any" in html
    assert "What I will not do from chat" in html
    # `cannot reach any` ALONE was the weak half: it is the empty-list
    # sentence, so a bug that emptied the list for everybody satisfied it more
    # easily than the truth did. The claim is that something was READ and came
    # back short, so the reason and the role have to be on the card too — and
    # the not-measured wording must NOT be, because this store answered.
    assert "need a role you do not have yet" in html
    assert "Your role here is <b>pending</b>" in html
    assert "could not check what you can reach" not in html
    assert "could not be checked just now" not in html
    # The product's boundary is still stated to somebody who reaches nothing:
    # they are the caller most likely to ask the model to act instead.
    assert "I do not place, size or modify a trade" in html


def test_the_web_card_carries_the_capabilities_and_not_only_its_header(monkeypatch):
    """THE BODY, which nothing asserted.

    Every web drive above checks the HEADER (`"What I can do for you"`) and two
    absent strings. Driven as a mutation, a `_chat_turn` that built the card
    from an EMPTY reachable list — a fully-permitted caller told "Right now I
    cannot reach any of my read tools for you" instead of eighteen
    capabilities — left this file, `test_intent_routing_and_unavailable.py` and
    `test_claude_md_accuracy.py` all green: 161 passed. So did dropping the
    withheld counts.

    The trap is sharper than an ordinary gap. The only body-level string the
    web path asserted was `"cannot reach any"`, in the pending test — which is
    the EMPTY-LIST sentence, so emptying the list made that assertion EASIER to
    satisfy. A guard pointed at the defect's own output.

    This asserts the phrases are the TABLE's, not a literal list: a row added
    to `SKILL_SAYS` tomorrow is covered without editing here, and a card that
    silently stops rendering rows fails.
    """
    ug, h, _asked, _ids = _web(monkeypatch)
    _resp, body = _turn(ug, h, "what can you do")
    html = body["reply_html"]
    assert "Ask me in your own words for any of these:" in html
    assert "cannot reach any" not in html, "a permitted caller was told nothing"

    shown = {n for n, says in SKILL_SAYS.items() if says in html}
    # A paper caller on the web reaches the WEB_CHAT_SKILLS intersection; the
    # exact membership is `skill_reach`'s to decide and is asserted there.
    # What is asserted HERE is that the card printed what it was handed.
    assert len(shown) >= 18, sorted(shown)
    for must in ("get_portfolio", "check_risk", "trade_postmortem"):
        assert SKILL_SAYS[must] in html, must
    # ...and the rows it has but cannot be ASKED for are a separate section,
    # not silently dropped and not mixed into the first list.
    assert "These I have, but not from words" in html
    assert SKILL_SAYS["optimize"] in html


def test_the_web_card_reads_the_caller_it_was_given(monkeypatch):
    """The fixture used to answer the same record for ANY id, so a gateway
    reading a remembered caller passed every drive in this file."""
    ug, h, _asked, ids = _web(monkeypatch)
    _resp, body = _turn(ug, h, "what can you do")
    assert ids, "the role store was never asked about anybody"
    assert set(ids) == {CALLER}, ids
    assert STRANGER not in ids
    assert "Ask me in your own words" in body["reply_html"]


@pytest.mark.parametrize("kill", ["store", "caller"])
def test_an_absent_store_is_not_a_caller_who_reaches_nothing(monkeypatch, kill):
    """The OTHER half of `unreadable`, and nothing drove it.

    `skill_reach` returned `([], {})` for `users is None` or an empty
    `user_id`, so the card said "Right now I cannot reach any of my read tools
    for you" with no reason at all — a confident negative about this caller's
    access, from a store nobody asked. A mutation restoring that early return
    survived the round that killed the raising case, because every drive in
    this file planted a store that ANSWERS.
    """
    ug, h, _asked, _ids = _web(monkeypatch)
    if kill == "store":
        h.users = None
        body_id = CALLER
    else:
        body_id = ""

    async def _json():
        return {"telegram_id": body_id, "text": "what can you do"}

    req = NS(app={"tg_handler": h,
                  "engine": NS(firewall_scan=lambda *a, **kw: None,
                               _pending_ideas={})},
             json=_json, headers={}, remote="1.2.3.4")
    resp = asyncio.run(ug._chat_turn(req))
    if resp.status != 200:
        # An empty id may be refused before the card is built; that is a
        # different honest answer and not this test's subject. The LEAF is,
        # so drive it directly and keep the claim.
        from bot.nlp.chat_tools import skill_reach
        reach, withheld = skill_reach(None, body_id or CALLER, "web",
                                      list(SKILL_PERMISSION))
        assert reach == []
        assert withheld.get("unreadable", 0) > 0, withheld
        return
    html = json.loads(resp.text)["reply_html"]
    assert "could not check what you can reach" in html, html[:300]
    assert "could not be checked just now" in html
    for never in ("need a role you do not have yet", "cannot reach any"):
        assert never not in html, never


def test_a_store_that_is_down_is_not_a_caller_who_is_denied(monkeypatch):
    """Driven through the WEB TURN, not the renderer.

    `test_a_store_that_is_down_and_a_role_that_is_short_read_differently`
    already pins the two cards apart at the leaf. This pins the transport, and
    it is the half that was missing: the gateway could have discarded the
    reason, or caught the exception itself, and the leaf test would not have
    noticed.
    """
    ug, h, _asked, _ids = _web(monkeypatch, raises=True)
    resp, body = _turn(ug, h, "what can you do")
    assert resp.status == 200
    html = body["reply_html"]
    assert "could not check what you can reach" in html
    assert "could not be checked just now" in html
    for never in ("need a role you do not have yet", "cannot reach any"):
        assert never not in html, never


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
    # THE BODY. The same mutation that empties the web card empties this one,
    # and the Telegram drive asserted only the header until it was driven:
    # "161 passed" with an operator told they can do nothing.
    assert "Ask me in your own words for any of these:" in out
    assert "cannot reach any" not in out
    shown = {n for n, says in SKILL_SAYS.items() if says in out}
    assert len(shown) >= 18, sorted(shown)
    for must in ("get_portfolio", "check_risk", "trade_postmortem"):
        assert SKILL_SAYS[must] in out, must


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

"""What the bot can actually do for THIS caller, on THIS surface.

Somebody asking a trading platform what it can do was told, on the web, that
the capability does not exist: `help` classifies at confidence 1.0, no skill is
registered under that name, so it fell to `skill_unavailable_notice` —
*"I understood that as help, but that tool is not available on this bot right
now"* — and `skill_unavailable_memory` wrote *"this bot has no such tool wired
up"* into the model's own history. Both are false. The capability exists; it
had no door on that surface.

**Reusing the Telegram card would have replaced a false refusal with a
mostly-false answer.** `_cmd_help` emits three messages naming every slash
command a non-admin holds, and the web gateway has no slash handling at all:
typed as the card prints them, the great majority reach the tool-less chat
model and a handful reach a skill by incidental word matching — among them
`/scan`, whose whole job is the universe sweep, resolving to `analyze_asset`,
a read of ONE asset. A card that names a command is claiming the command does
something — the `/vault` hint shape, at a hundred times the scale. Worse, the
signed-in chat prompt forbids the model from suggesting slash commands, so the
fall-throughs land on a model told not to give the answer the card just gave.

The counts are DELIBERATELY not written here. Three copies of them existed —
this docstring, `user_gateway._chat_turn`'s comment and the test's own — and
all three went stale the day `/alerts` became the 91st command, while the one
copy in `CLAUDE.md` stayed right because
`test_the_catalogue_numbers_are_the_numbers_a_drive_returns` reads it out of
the prose and compares it to a live walk. A second copy of a measurement is a
second answer; the drive is
`tests/test_the_bot_can_say_what_it_does.catalogue_on_the_web`.

So the answer is what the caller can ASK FOR, in words, derived from
`SKILL_SAYS` — the column on the table that already decides which skills chat
can reach — intersected with what this caller's role, plan and surface
actually allow. Nothing here is hand-listed, so a skill added later is either
in the answer or fails `SKILL_SAYS`'s own guard.

WITHHELD SKILLS ARE COUNTED, NEVER NAMED, and the reason travels. `_cmd_help`
already argues the first half: *"a command you are refused looks exactly like a
command that is broken"*. The second half is that "ask an admin", "upgrade your
plan" and "use Telegram for this one" are three different fixes, so one count
cannot stand for all three.
"""
from __future__ import annotations

import html as _html
from typing import Iterable, Mapping, Sequence

from bot.skills.skill_permissions import SKILL_SAYS

#: Why a skill this product HAS is not in this caller's list. Each reason is a
#: different FIX — collapsing them into "some features are unavailable" is the
#: shape this module exists to remove one layer up.
#:
#: `unreadable` is the fourth, and it was missing. `skill_reach` marks a role
#: store that RAISES as `role` — correct for the withholding, which must fail
#: closed, and a lie in the sentence: a store that is down and a role that is
#: genuinely short produced BYTE-IDENTICAL cards, so a trader read "24 more
#: need a role you do not have yet. Your role here is trader." during an
#: outage. A confident negative with a specific wrong remedy is what this file
#: exists to prevent, and it was doing it about itself.
#:
#: `wallet` is the fifth, and it comes from the tier gate's own reason
#: vocabulary. `tier_gate.check_user`'s docstring says it in as many words:
#: "you have not staked enough" and "we could not check your stake" are
#: different messages, and conflating them tells someone holding 100,000
#: $RCLAW to go and stake more during an RPC outage. `_tier_allows` discarded
#: that reason, so all six of its denials printed as "need a higher plan".
#: reason -> (singular, plural) predicate. TWO forms, because the card has
#: printed "• 1 more need a role you do not have yet." and "• 1 more only work
#: in the Telegram bot." on a live surface for as long as the section existed:
#: the counts are small by design (they are what ONE caller is missing), so the
#: n=1 rendering is the common case, not the edge. Nothing drove it — every
#: fixture happened to withhold two or more.
WITHHELD_REASONS: dict[str, tuple[str, str]] = {
    "role": ("needs a role you do not have yet",
             "need a role you do not have yet"),
    "plan": ("needs a higher plan", "need a higher plan"),
    "surface": ("only works in the Telegram bot",
                "only work in the Telegram bot"),
    "wallet": ("needs a linked, verified wallet",
               "need a linked, verified wallet"),
    "unreadable": ("could not be checked just now — that is this bot's "
                   "problem, not yours",
                   "could not be checked just now — that is this bot's "
                   "problem, not yours"),
}

#: The reasons the ROLE line explains. Printing "Your role here is trader."
#: under a `plan` or `unreadable` count answers a question nobody asked and
#: implies the role is the obstacle when it is not.
_ROLE_EXPLAINS: frozenset = frozenset({"role"})

#: What no surface will do from chat, and why. Not a refusal to be apologised
#: for: it is the product's own boundary, and a caller who is told it stops
#: asking a model that cannot act and would narrate one.
_CANNOT: tuple[tuple[str, str], ...] = (
    ("place, size or modify a trade",
     "the Confirm card is the only door, and nothing is placed until you tap it"),
    ("close or cancel anything",
     "the Close and Cancel buttons on the positions card are owner-checked"),
)

#: The halt row, which is NOT the same sentence on both surfaces.
#:
#: It used to read "that is an operator control and it is not reachable from
#: chat" everywhere. On Telegram that is FALSE and has been since the halt
#: rules were anchored: driven, "halt the bot" classifies to `halt` at
#: confidence 1.0 and an operator's typed imperative reaches `_cmd_halt`. The
#: card was denying a door the product deliberately built — the `/vault` hint
#: shape with the sign flipped, printed one section above the list of things
#: the caller CAN ask for.
_CANNOT_HALT: dict[str, str] = {
    # No command token on the web, for the reason the row below it keeps: a
    # slash command printed on a surface with no slash handling is a door
    # painted on a wall. The dashboard control IS reachable from here, so it
    # is the one named.
    "web": ("stop the engine — nothing typed here halts anything; the "
            "Emergency-stop control on the dashboard is the door"),
    "telegram": ("stop the engine for you — an operator's own whole-message "
                 "\u201chalt the bot\u201d does, and /emergency_stop asks "
                 "first; I will not do it because you mentioned it"),
}


#: The reasons that ARE a reading of this caller. `surface` is not one: "this
#: only works in the Telegram bot" is a fact about the SKILL and holds for
#: everybody, so a card carrying nothing but `surface` and `unreadable` counts
#: has read nothing about the person it is talking to — which is the first
#: draft's bug, where `{"unreadable": 24, "surface": 1}` still opened with a
#: sentence about what this caller cannot reach.
_CALLER_MEASURED: frozenset = frozenset({"role", "plan", "wallet"})

#: Which halt sentence each surface gets. `public` and `api` are web-shaped —
#: browser surfaces with no slash handling — so they take the web row; the
#: mapping is written down rather than defaulted, so a surface added later
#: fails here instead of being told about a dashboard it may not have.
_HALT_SURFACE: dict[str, str] = {
    "web": "web", "public": "web", "api": "web", "telegram": "telegram",
}


def _halt_surface(surface: str) -> str:
    try:
        return _HALT_SURFACE[surface]
    except KeyError:
        raise ValueError(
            f"{surface!r} has no halt row; add it to _HALT_SURFACE rather "
            f"than letting it default to another surface's door") from None


def _line(skill: str) -> str:
    says = SKILL_SAYS.get(skill)
    # quote=False: this is message text, not an attribute value, and
    # `&#x27;` for an apostrophe in "the macro gate's posture" is a
    # rendering artefact on a card a person reads.
    return f"• {_html.escape(says, quote=False)}" if says else ""


def capability_answer(reachable: Sequence[str], *, surface: str = "web",
                      role: str = "", withheld: Mapping[str, int] | None = None,
                      extras: Iterable[str] = (),
                      tools: set[str] | None = None) -> str:
    """The honest answer to "what can you do?" for one caller.

    ``reachable`` is the skill names this caller can actually get HERE, in
    whatever order the caller hands them over; a name with no `SKILL_SAYS`
    entry is dropped rather than printed as a bare identifier, and the table's
    own guard is what stops that from being silent.

    ``withheld`` maps a `WITHHELD_REASONS` key to a COUNT, and each reason
    carries a singular and a plural form — the counts are what ONE caller is
    missing, so `n == 1` is the ordinary case and the card printed "1 more
    need a role you do not have yet" until something drove it. An unknown key
    is dropped for the same reason the rest of this file gives: it would
    rather say less than say a word it cannot explain.

    ``extras`` is for capabilities a surface knows about and this module
    cannot see — the web client's own intercepts live in browser code, so a
    Python answer that claimed to be exhaustive would be overstating its
    coverage, which is the one thing this file is against.

    ``tools`` is the chat-tool catalogue THIS caller actually gets, from
    `_chat_tools_for` — the only thing `_llm_chat` reads. Passing it is what
    keeps the ASK list honest: the static `CHAT_TOOLS` tuple `words_reach`
    falls back to knows nothing about `CONFIG.llm.chat_tools_enabled` or
    whether the skill is registered, so with tools switched off the card went
    on offering four capabilities (`proposals`, `rejected_trades`,
    `check_event_risk`, `macro_brief`) whose ONLY door is a chat tool the
    model no longer held. A second copy of a gate is a second answer.

    Nothing here promises the list is complete, and that is deliberate: it
    says what it can DO, not what it has.
    """
    from bot.nlp.skill_doors import command_for, doorless

    # THE HEADLINE IS A PROMISE ABOUT A DOOR, so the rows are split by whether
    # a door exists. Six of twenty-three rows on the web reached NOTHING: no
    # router rule emits `optimize`, `run_strategy`, `walk_forward` or
    # `quant_analyze`, and the web aliases every scan phrasing to the shallow
    # movers scan so `deepscan` and `pro_scan` are unreachable there too.
    # Driven, the card's own words for `optimize` landed on `trade_journal` on
    # Telegram and on a bare 403 on the web — one turn after the card invited
    # the ask. That is `/vault`'s hint shape with the sign flipped.
    #
    # `words_reach` is DERIVED from the router's own table and the chat-tool
    # catalogue, not a hand-written exception list: a rule added tomorrow moves
    # a row into the first group with nobody editing this file, and a rule
    # deleted moves it out the same way.
    # `doorless` is the ONE definition of "words do not reach this", so the
    # renderer cannot drift from the module that answers the question. The
    # first draft recomputed the set inline here and left `doorless` with no
    # caller at all — a function nothing calls, in the slice about doors,
    # which `test_no_new_unreachable_functions` caught on the full run and no
    # targeted suite could have.
    commanded = doorless(reachable, surface, tools=tools)
    _no_door = set(commanded)
    asked = [s for s in reachable if s not in _no_door]

    lines = [f"• {_html.escape(str(e), quote=False)}"
             for e in extras if str(e).strip()]
    rows = [r for r in (_line(s) for s in asked) if r] + lines

    counts = {k: int(v) for k, v in (withheld or {}).items()
              if k in WITHHELD_REASONS and int(v) > 0}

    out: list[str] = ["<b>What I can do for you</b>"]
    if rows:
        out += ["", "Ask me in your own words for any of these:", *rows]
    elif counts.get("unreadable") and not (set(counts) & _CALLER_MEASURED):
        # THREE OUTCOMES, NOT TWO. "Right now I cannot reach any of my read
        # tools for you" is a statement about this caller's ACCESS, and with
        # an absent or unreadable role store nobody's access was read: the
        # sentence was a confident negative built from no reading, which is
        # the defect this whole file exists to have removed. It is only true
        # when something was actually checked and came back short.
        out += ["", "I could not check what you can reach just now, so I am "
                    "not going to guess. Nothing below is a statement about "
                    "your access."]
    else:
        # NOT "I can do nothing" — the product has these; this caller does not
        # reach them. The difference is the whole fix.
        out += ["", "Right now I cannot reach any of my read tools for you."]

    # The rows a caller has the ROLE for and cannot ASK for.
    #
    # THE WEB PRINTS NO COMMAND TOKEN, and that is somebody else's decision
    # being kept rather than mine being added: `test_no_phrase_names_a_command`
    # states it — "a card that names a command is claiming the command does
    # something, and this card is rendered on a surface with no slash commands
    # at all". A first draft here rendered "— /deepscan in the Telegram bot",
    # which satisfies the REASON (it says where the door is) and breaks the
    # DECISION as written. Naming the surface without the token satisfies both,
    # so the rule did not have to be relitigated to fix the card.
    if commanded:
        out += ["", "<b>These I have, but not from words</b>"]
        for skill in commanded:
            says = SKILL_SAYS.get(skill)
            if not says:
                continue
            body = _html.escape(says, quote=False)
            cmd = command_for(skill)
            if surface == "web":
                out.append(f"• {body} — in the Telegram bot"
                           if cmd else
                           f"• {body} — no way to ask for it from here yet")
            elif cmd:
                out.append(f"• <code>{cmd}</code> — {body}")
            else:
                out.append(f"• {body} — no way to ask for it from here yet")

    if counts:
        out += ["", "<b>Not in that list</b>"]
        for key, n in sorted(counts.items()):
            _one, _many = WITHHELD_REASONS[key]
            out.append(f"• {n} more {_one if n == 1 else _many}.")
        # The role explains the `role` count and nothing else. Printed under a
        # `plan` or `unreadable` count it reads as the obstacle when it is not.
        if role and any(k in _ROLE_EXPLAINS for k in counts):
            out.append(f"Your role here is <b>{_html.escape(role, quote=False)}</b>.")

    out += ["", "<b>What I will not do from chat</b>"]
    for what, why in _CANNOT:
        out.append(f"• I do not {what} — {why}.")
    # NOT `.get(surface, _CANNOT_HALT["web"])`. That silently answered the
    # WEB sentence — "the Emergency-stop control on the dashboard is the door"
    # — for any surface not in the map, including `telegram`, where the door
    # is a typed imperative and there is no dashboard. A door named by a
    # fallback is a door named by a guess. `doorless` above has already
    # refused an unmeasured surface, so this is the second lock and not the
    # first.
    out.append("• I do not " + _CANNOT_HALT[_halt_surface(surface)] + ".")

    if surface == "web":
        out += ["", "I read; I never act. Every number I give you comes from a "
                    "tool I just ran, and when a read fails I say so instead of "
                    "guessing."]
    else:
        # NOT "the full command reference": /help names 91 of the catalogue's
        # 147 for a non-admin, so "full" overstated by 56. No count here on
        # purpose — a number in prose is the part that rots, and this one has
        # already been wrong in four places at once.
        out += ["", "Send /help for the commands you can run.",
                "I read; I never act. Every number I give you comes from a tool "
                "I just ran, and when a read fails I say so instead of guessing."]
    return "\n".join(out)


#: What signing in / switching surface actually buys, per TOOL-LESS surface.
#:
#: Both doors are real and the product already names them: the public drawer's
#: own first message says *"**Sign in** and connect an exchange to unlock live
#: scans and your own portfolio"*, and `api_bridge`'s `/chat` is reached with
#: the operator's own bearer token, so the person reading it has Telegram.
#: Naming a door that does not exist is the `/vault` hint shape, which is what
#: this whole file is against.
_TOOLLESS_DOOR: dict[str, str] = {
    "public": ("Sign in and I will tell you which of them your account "
               "reaches — I cannot know that from here."),
    "api": ("The bridge offers the model no tools at all. Ask through the "
            "Telegram bot or the web app and these run against your account."),
}

#: The surface whose door set describes what the caller would reach AFTER
#: taking the door above. `public` is the signed-out half of the web app, so
#: the web's set is the right one; `api` points at the same app.
_TOOLLESS_AFTER: dict[str, str] = {"public": "web", "api": "web"}


def toolless_capability_answer(surface: str) -> str:
    """"What can you do?" on a surface that runs NO tools.

    THE ANSWER WAS THE MODEL'S, AND THE MODEL HAD NOTHING TO READ. Public web
    chat and the api bridge both hand a capability ask straight to `_llm_chat`
    — `_chat_tools_for` returns `[]` for `public or not user_id`, and
    `headless_handler` sets `users = None` on purpose — so an anonymous
    visitor asking what the product does got a feature list improvised by a
    model with no catalogue in front of it. That is the failure
    `skill_unavailable_notice` was written to prevent, arriving through the
    other door, one surface over from where it was just fixed.

    It says three things and no more: nothing here runs a tool, this is what
    the product does, and here is the door. The list is the same `SKILL_SAYS`
    column the signed-in card derives from, narrowed by the door set of the
    surface the caller would land on — so it cannot promise a capability the
    signed-in card would then withhold for a reason other than their role.

    It states NOTHING about this caller's access, because nothing was read:
    there is no account to read. "Sign in and I will tell you which of them
    your account reaches" is the whole claim.
    """
    from bot.nlp.skill_doors import words_reach

    after = _TOOLLESS_AFTER[surface]      # KeyError, not a default: see below
    door = _TOOLLESS_DOOR[surface]
    reach = words_reach(after)
    rows = [_line(n) for n in SKILL_SAYS if n in reach]
    rows = [r for r in rows if r]

    out: list[str] = ["<b>What I can do for you</b>", "",
                      "On this surface I run no tools, so nothing I say here "
                      "is read from an account or from a live feed."]
    if rows:
        out += ["", "<b>What the product does</b>", *rows, "", door]
    else:
        # Reachable only if the door set comes back empty, which would mean
        # the surface the caller is being SENT to has no doors either. Saying
        # so is the honest answer; printing the door sentence over an empty
        # list would be an invitation to a room with nothing in it.
        out += ["", "I could not read the capability list just now, so I am "
                    "not going to describe one."]

    out += ["", "<b>What I will not do from chat</b>"]
    for what, why in _CANNOT:
        out.append(f"• I do not {what} — {why}.")
    out.append("• I do not " + _CANNOT_HALT[_halt_surface(surface)] + ".")
    return "\n".join(out)


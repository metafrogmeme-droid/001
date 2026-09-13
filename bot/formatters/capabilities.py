"""What the bot can actually do for THIS caller, on THIS surface.

Somebody asking a trading platform what it can do was told, on the web, that
the capability does not exist: `help` classifies at confidence 1.0, no skill is
registered under that name, so it fell to `skill_unavailable_notice` —
*"I understood that as help, but that tool is not available on this bot right
now"* — and `skill_unavailable_memory` wrote *"this bot has no such tool wired
up"* into the model's own history. Both are false. The capability exists; it
had no door on that surface.

**Reusing the Telegram card would have replaced a false refusal with a
mostly-false answer.** `_cmd_help` emits three messages naming 90 slash
commands for a non-admin, and the web gateway has no slash handling at all:
driven, typed as the card prints them, 78 of the 90 reach the tool-less chat
model and 12 reach a skill by incidental word matching — among them `/scan`,
whose whole job is the universe sweep, resolving to `analyze_asset`, a read of
ONE asset. A card that names a command is claiming the command does something
— the `/vault` hint shape, at ninety times the scale. Worse, the signed-in
chat prompt forbids the model from suggesting slash commands, so the 78 that
fall through land on a model told not to give the answer the card just gave.

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

#: Why a skill this product HAS is not in this caller's list. Three reasons,
#: three fixes — collapsing them into "some features are unavailable" is the
#: shape this module exists to remove one layer up.
WITHHELD_REASONS: dict[str, str] = {
    "role": "need a role you do not have yet",
    "plan": "need a higher plan",
    "surface": "only work in the Telegram bot",
}

#: What no surface will do from chat, and why. Not a refusal to be apologised
#: for: it is the product's own boundary, and a caller who is told it stops
#: asking a model that cannot act and would narrate one.
_CANNOT: tuple[tuple[str, str], ...] = (
    ("place, size or modify a trade",
     "the Confirm card is the only door, and nothing is placed until you tap it"),
    ("close or cancel anything",
     "the Close and Cancel buttons on the positions card are owner-checked"),
    ("stop the engine",
     "that is an operator control and it is not reachable from chat"),
)


def _line(skill: str) -> str:
    says = SKILL_SAYS.get(skill)
    # quote=False: this is message text, not an attribute value, and
    # `&#x27;` for an apostrophe in "the macro gate's posture" is a
    # rendering artefact on a card a person reads.
    return f"• {_html.escape(says, quote=False)}" if says else ""


def capability_answer(reachable: Sequence[str], *, surface: str = "web",
                      role: str = "", withheld: Mapping[str, int] | None = None,
                      extras: Iterable[str] = ()) -> str:
    """The honest answer to "what can you do?" for one caller.

    ``reachable`` is the skill names this caller can actually get HERE, in
    whatever order the caller hands them over; a name with no `SKILL_SAYS`
    entry is dropped rather than printed as a bare identifier, and the table's
    own guard is what stops that from being silent.

    ``withheld`` maps a `WITHHELD_REASONS` key to a COUNT. An unknown key is
    dropped for the same reason: this card would rather say less than say a
    word it cannot explain.

    ``extras`` is for capabilities a surface knows about and this module
    cannot see — the web client's own intercepts live in browser code, so a
    Python answer that claimed to be exhaustive would be overstating its
    coverage, which is the one thing this file is against.

    Nothing here promises the list is complete, and that is deliberate: it
    says what it can DO, not what it has.
    """
    lines = [f"• {_html.escape(str(e), quote=False)}"
             for e in extras if str(e).strip()]
    rows = [_line(s) for s in reachable]
    rows = [r for r in rows if r] + lines

    out: list[str] = ["<b>What I can do for you</b>"]
    if rows:
        out += ["", "Ask me in your own words for any of these:", *rows]
    else:
        # NOT "I can do nothing" — the product has these; this caller does not
        # reach them. The difference is the whole fix.
        out += ["", "Right now I cannot reach any of my read tools for you."]

    counts = {k: int(v) for k, v in (withheld or {}).items()
              if k in WITHHELD_REASONS and int(v) > 0}
    if counts:
        out += ["", "<b>Not in that list</b>"]
        for key, n in sorted(counts.items()):
            out.append(f"• {n} more {WITHHELD_REASONS[key]}.")
        if role:
            out.append(f"Your role here is <b>{_html.escape(role, quote=False)}</b>.")

    out += ["", "<b>What I will not do from chat</b>"]
    for what, why in _CANNOT:
        out.append(f"• I do not {what} — {why}.")

    if surface == "web":
        out += ["", "I read; I never act. Every number I give you comes from a "
                    "tool I just ran, and when a read fails I say so instead of "
                    "guessing."]
    else:
        out += ["", "Send /help for the full command reference.",
                "I read; I never act. Every number I give you comes from a tool "
                "I just ran, and when a read fails I say so instead of guessing."]
    return "\n".join(out)

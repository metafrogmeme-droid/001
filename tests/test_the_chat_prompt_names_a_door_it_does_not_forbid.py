"""The chat prompt does not forbid the commands its own rules name as doors.

`_CHAT_SYSTEM_PROMPT` carried "NEVER suggest slash commands. Just talk
naturally." a few lines under `_CHAT_CANNOT_ACT_RULE`, which tells the model
to name the door when it cannot act: /trade to open a trade, /halt and
/pause to stop the engine, /stake for Earn. One document, two instructions
that cannot both be followed, and the one that loses decides whether a
refusal comes with a door or is a dead end.

The personality line now says what it was after: no commands as padding, and
a command named only where a rule names it as the door, worded the way that
rule words it (on the web, "in Telegram"). That claim is held here: every
command the prompt names sits inside the rule that names doors.
"""
from __future__ import annotations

import re

import pytest

from bot.skills.chat_runtime import _CHAT_CANNOT_ACT_RULE, cannot_act_rule
from bot.skills.telegram_handler import TelegramHandler as H
from tests.test_alerts_name_real_commands import registered_commands

#: A blanket prohibition, in the shapes such a line takes.
_BLANKET = re.compile(r"\bnever\s+(?:suggest|mention|name|use|give)\s+(?:any\s+)?"
                      r"(?:slash\s+)?commands\b", re.IGNORECASE)
#: The rule the personality line states now.
_ONLY_AS_A_DOOR = ("Name a command only where a rule in this prompt names it as "
                   "the door for something this chat cannot do")


def _commands(text: str) -> set[str]:
    visible = re.sub(r"</?[a-z]+[^>]*>", " ", text)
    return set(re.findall(r"(?<![\w/<])/([a-z][a-z0-9_]*)", visible))


def _base(surface: str) -> str:
    """The static prompt as `_build_chat_system_prompt` takes it for each
    surface: the web swaps in its own close door."""
    base = H._CHAT_SYSTEM_PROMPT
    if surface == "web":
        base = base.replace(_CHAT_CANNOT_ACT_RULE, cannot_act_rule("web"))
    return base


@pytest.mark.parametrize("surface", ["telegram", "web"])
def test_the_prompt_names_doors_and_forbids_none(surface):
    base = _base(surface)
    named = _commands(base)
    # The premise: the prompt does name commands, as doors.
    assert {"trade", "halt", "pause"} <= named, named
    assert not _BLANKET.search(base), _BLANKET.search(base).group(0)
    assert _ONLY_AS_A_DOOR in base


@pytest.mark.parametrize("surface", ["telegram", "web"])
def test_every_command_the_prompt_names_is_named_by_the_door_rule(surface):
    """The line's own claim: a command appears only where a rule names it
    as a door. A command added anywhere else in the prompt fails here."""
    base = _base(surface)
    rule = cannot_act_rule(surface)
    assert rule in base
    assert _commands(base.replace(rule, "")) == set()


def test_every_door_the_rule_names_is_a_registered_command():
    assert _commands(_CHAT_CANNOT_ACT_RULE) <= registered_commands()


def test_the_web_rule_says_where_its_commands_run():
    """On the web the doors are Telegram's, and the rule says so beside
    each, which is the wording the personality line defers to."""
    web = cannot_act_rule("web")
    assert "/trade in Telegram" in web and "in Telegram, /halt" in web


def test_the_public_prompt_neither_names_nor_forbids_a_command():
    public = H._PUBLIC_CHAT_SYSTEM_PROMPT
    assert _commands(public) == set()
    assert not _BLANKET.search(public)

"""The command catalogue must match reality — exactly, forever.

Operator report: "too many / commands, some don't work or it's not clear what
they do." The audit found no dead handlers at all (125 registered, 125 real)
— the problem was documentation: /help named FIVE of them, so ~110 working
features existed only by word of mouth.

These tests make that state unreachable again. The catalogue and the handler's
registration list must agree exactly, so a new command cannot ship
undocumented and a retired one cannot linger in the docs.
"""

import re
from pathlib import Path

from bot.skills.command_catalog import GROUPS, all_entries, help_sections, render_help

_SRC = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
_REGISTERED = {c for c, _ in re.findall(r'\(\s*"(\w+)",\s*self\.(_cmd_\w+)\)', _SRC)}


def test_catalogue_and_registration_match_exactly():
    catalogued = set(all_entries())
    assert _REGISTERED, "registration list should not be empty — regex drifted?"
    undocumented = sorted(_REGISTERED - catalogued)
    phantom = sorted(catalogued - _REGISTERED)
    assert not undocumented, f"registered but undocumented: {undocumented}"
    assert not phantom, f"documented but not registered: {phantom}"


def test_every_entry_is_listed_once_and_actually_describes_something():
    seen = {}
    for title, audience, entries in GROUPS:
        assert audience in ("user", "admin"), f"{title} has a bogus audience"
        for name, desc in entries:
            assert name not in seen, f"/{name} appears in both {seen[name]} and {title}"
            seen[name] = title
            assert len(desc) >= 8, f"/{name} needs a real description"
            assert not desc.endswith("."), f"/{name} description should not end with a period"


def test_normal_users_never_see_commands_they_cannot_run():
    """A command you are refused is indistinguishable from one that is
    broken — which is most of why the surface felt untrustworthy."""
    user_cmds = {n for _, entries in help_sections(is_admin=False) for n, _ in entries}
    admin_only = {n for _, audience, entries in GROUPS if audience == "admin"
                  for n, _ in entries}
    assert user_cmds.isdisjoint(admin_only), "operator commands leaked into the user help"
    # The operator still sees everything.
    admin_cmds = {n for _, entries in help_sections(is_admin=True) for n, _ in entries}
    assert admin_cmds == set(all_entries())


def test_help_fits_telegram_and_never_tears_a_group_in_half():
    for is_admin in (False, True):
        chunks = render_help(is_admin=is_admin)
        assert chunks, "help must render something"
        for c in chunks:
            assert len(c) <= 4096, "Telegram hard-caps a message at 4096 chars"
        joined = "\n".join(chunks)
        # Every group header appears exactly once, unbroken.
        for title, audience, _ in GROUPS:
            if is_admin or audience == "user":
                assert joined.count(f"<b>{title}</b>") == 1, f"{title} split or missing"


def test_help_actually_sends_the_catalogue():
    assert "from bot.skills.command_catalog import render_help" in _SRC
    assert "render_help(is_admin=self._is_admin(update))" in _SRC

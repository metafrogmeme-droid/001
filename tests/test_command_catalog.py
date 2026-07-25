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
    assert "from bot.skills.command_catalog import render_group, render_help" in _SRC
    assert "render_help(is_admin=_admin, lang=_hl)" in _SRC


# ── "/help <group>" and the refusal message ───────────────────────────────
# Even grouped, 125 commands is a wall; and the old refusal was two words.
# Both are what made the surface feel like it "didn't work".

def test_group_deep_dive_answers_or_offers_alternatives():
    from bot.skills.command_catalog import GROUP_KEYS, render_group
    out = render_group("trading")
    assert "📈 Trading" in out and "/trade" in out
    # Every keyword resolves for the operator (no dead aliases in the table).
    for key in GROUP_KEYS:
        assert "No <b>" not in render_group(key, is_admin=True), f"/help {key} is dead"
    # An unknown keyword never answers with silence — it offers real sections.
    miss = render_group("zzzz")
    assert "No <b>zzzz</b>" in miss and "/help for everything" in miss
    assert "trading" in miss, "the offer must list keywords that work"


def test_normal_users_cannot_browse_operator_sections():
    from bot.skills.command_catalog import render_group
    refused = render_group("guardian", is_admin=False)
    assert "No <b>guardian</b> section" in refused
    assert "/guardian" not in refused, "operator commands leaked to a normal user"
    # ...and the operator can.
    assert "🛡 Guardian" in render_group("guardian", is_admin=True)


def test_the_refusal_explains_itself_and_points_somewhere():
    from bot.utils.i18n import t
    en = t("admin_only", "en")
    assert "operator command" in en, "must say WHAT it is"
    assert "/help" in en, "must offer a way forward"
    assert en != "Admin only.", "the two-word dead end is gone"
    # Localised too — a refusal is exactly where a user needs their language.
    zh = t("admin_only", "zh")
    assert "/help" in zh and zh != "僅限管理員。"


def test_help_accepts_an_argument():
    assert "render_group(_arg, is_admin=_admin, lang=_hl)" in _SRC


# ── Chinese coverage ──────────────────────────────────────────────────────
# The bot speaks en/zh. Shipping an English-only reference for 125 commands
# would have handed Chinese users a bigger wall than the one being fixed.

def test_every_command_has_a_chinese_description():
    from bot.skills.command_catalog import DESC_ZH, GROUPS, GROUP_TITLES_ZH, all_entries
    cmds = set(all_entries())
    assert not (cmds - set(DESC_ZH)), f"no zh description: {sorted(cmds - set(DESC_ZH))}"
    assert not (set(DESC_ZH) - cmds), f"zh description for a dead command: {sorted(set(DESC_ZH) - cmds)}"
    for title, _, _ in GROUPS:
        assert title in GROUP_TITLES_ZH, f"group {title} has no zh title"
    # A zh description must not be the English one copied over.
    from bot.skills.command_catalog import all_entries as ae
    for name, (_, _, en_desc) in ae().items():
        assert DESC_ZH[name] != en_desc, f"/{name} zh is just the English string"


def test_rendering_localises_and_falls_back_per_item():
    from bot.skills.command_catalog import render_group, render_help
    zh = render_help(lang="zh")[0]
    assert "從這裡開始" in zh and "註冊並查看目前狀態" in zh
    # English remains the default and is unaffected.
    assert "Start here" in render_help()[0]
    # Section deep-dives localise too, including the group title.
    assert "📈 交易" in render_group("trading", lang="zh")
    # An unknown language degrades to English rather than blanking.
    assert "Start here" in render_help(lang="fr")[0]


def test_help_passes_the_callers_language():
    assert 'lang=_hl' in _SRC
    assert '_hl = lang if lang in ("en", "zh") else "en"' in _SRC

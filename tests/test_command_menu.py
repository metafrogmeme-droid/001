"""The "/" command menus must be valid Telegram commands AND real handlers.

Guards two ways the menu could rot: a bad command string Telegram rejects
(uppercase, too long, illegal chars), and a menu entry pointing at a command
the handler no longer registers (dead entry → tapping it does nothing).
"""

import os
import re

from bot.skills.command_menu import (
    ADMIN_EXTRA_MENU,
    DEFAULT_MENU,
    admin_commands,
    default_commands,
)

_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _registered_commands() -> set:
    """Command names the handler actually registers, parsed from source so the
    test needs no bot token / running app."""
    src = os.path.join(os.path.dirname(__file__), "..", "bot", "skills",
                       "telegram_handler.py")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    # Registration tuples look like ("start", self._cmd_start) or ("me", _cmd_me)
    return set(re.findall(r'\(\s*"([a-z0-9_]+)"\s*,\s*(?:self\.)?_cmd', text))


class TestMenuFormat:
    def _all(self):
        return DEFAULT_MENU + ADMIN_EXTRA_MENU

    def test_command_names_are_valid_telegram_commands(self):
        for name, _ in self._all():
            assert _NAME_RE.match(name), f"illegal command name: {name!r}"

    def test_descriptions_within_telegram_limit(self):
        for name, desc in self._all():
            assert 1 <= len(desc) <= 256, f"{name}: description length {len(desc)}"

    def test_no_duplicate_commands_within_a_menu(self):
        for menu in (default_commands(), admin_commands()):
            names = [n for n, _ in menu]
            assert len(names) == len(set(names))

    def test_menus_are_non_empty_and_bounded(self):
        # Telegram caps a scope at 100 commands; keep the default short.
        assert 0 < len(default_commands()) <= 15
        assert 0 < len(admin_commands()) <= 100


class TestMenuMatchesHandlers:
    def test_every_menu_command_is_registered(self):
        registered = _registered_commands()
        assert registered, "failed to parse registered commands"
        for name, _ in DEFAULT_MENU + ADMIN_EXTRA_MENU:
            assert name in registered, f"menu command /{name} is not registered"

    def test_admin_menu_starts_with_the_essentials(self):
        # Operators get the essentials first, then their extra controls.
        assert admin_commands()[:len(default_commands())] == default_commands()


# ── Unknown commands must never be silent ─────────────────────────────────
# The operator's report: "too many / commands, some don't work or aren't
# clear". The single biggest cause was structural — the free-text handler
# excludes COMMAND and nothing caught the rest, so ANY unrecognised slash
# command (a typo, or an operator-only command a normal user tried) produced
# no reply whatsoever. A bot that answers nothing reads as broken.

def test_suggestions_only_ever_name_real_commands():
    from bot.skills.command_menu import suggest
    known = ["open_positions", "performance", "portfolio", "scan", "analyze", "help"]
    for typo in ("positions", "portfolo", "scann", "analze", "hlep"):
        for hit in suggest(typo, known):
            assert hit in known, f"{typo} suggested a non-existent /{hit}"
    # The common real-world near-misses land on the right command.
    assert "open_positions" in suggest("positions", known)
    assert "portfolio" in suggest("portfolo", known)
    # Nothing close enough → no guess at all. An honest miss beats a
    # confident wrong answer.
    assert suggest("qqqqzzz", known) == []
    assert suggest("", known) == []


def test_unknown_reply_always_helps_and_never_echoes_junk():
    from bot.skills.command_menu import unknown_command_reply
    known = ["open_positions", "scan", "help"]
    msg = unknown_command_reply("positions", known)
    assert "/open_positions" in msg, "must point at the real command"
    assert "/help" in msg and "just talk to me" in msg, "must offer a way forward"
    # Non-admins are told WHY some commands seem missing — the other half of
    # the "doesn't work" feeling.
    assert "operator-only" in unknown_command_reply("halt", known, is_admin=False)
    assert "operator-only" not in unknown_command_reply("halt", known, is_admin=True)
    # A hostile command name cannot inject markup into the HTML reply: the
    # name is stripped to [A-Za-z0-9_-], so the only tags that survive are
    # the ones this module writes itself.
    import re
    nasty = unknown_command_reply("<b>x</b><script>alert(1)</script>", known)
    assert "<script" not in nasty and "</script" not in nasty
    assert set(re.findall(r"</?(\w+)", nasty)) <= {"b", "i"}, "unexpected tag in reply"


def test_the_catcher_is_registered_after_every_real_command():
    """Ordering is the whole trick: python-telegram-bot dispatches to the
    first matching handler, so the catch-all must come after the real ones
    or it would swallow every command in the bot."""
    from pathlib import Path
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    reg_at = src.index("app.add_handler(CommandHandler(cmd, handler))")
    catch_at = src.index("filters.COMMAND, self._handle_unknown_command")
    text_at = src.index("filters.TEXT & ~filters.COMMAND")
    assert reg_at < catch_at < text_at, "catch-all must sit after commands, before text"
    # Suggestions draw only from what was actually registered.
    assert "self._known_commands.append(cmd)" in src
    assert "list(self._known_commands)" in src


# ── Chinese menu + replies ────────────────────────────────────────────────
# Telegram stores a menu per language, so a Chinese client can get a Chinese
# "/" popup. Anything untranslated falls back to the English text per item —
# never to a blank menu row, which is what a naive dict lookup would give.

def test_menu_localisation_falls_back_per_item():
    from bot.skills.command_menu import MENU_ZH, default_commands, localized
    en = default_commands()
    zh = localized(en, "zh")
    assert len(zh) == len(en), "localisation must not drop entries"
    assert dict(zh)["start"] != dict(en)["start"], "zh menu text should differ"
    for name, desc in zh:
        assert desc.strip(), f"/{name} rendered an empty menu row"
    # An unknown language is left exactly as English; a file-backed one is
    # translated per item, just like zh.
    assert localized(en, "sw") == en
    assert localized(en, "en") == en
    fr = localized(en, "fr")
    assert len(fr) == len(en) and all(d.strip() for _, d in fr)
    assert dict(fr)["start"] != dict(en)["start"]
    # No zh entry names a command that is not in a menu.
    menu_names = {n for n, _ in default_commands()} | {
        n for n, _ in __import__("bot.skills.command_menu", fromlist=["x"]).admin_commands()}
    assert not (set(MENU_ZH) - menu_names), f"stale zh menu keys: {sorted(set(MENU_ZH) - menu_names)}"


def test_unknown_command_reply_is_bilingual():
    from bot.skills.command_menu import unknown_command_reply
    known = ["open_positions", "scan", "help"]
    zh = unknown_command_reply("positions", known, lang="zh")
    assert "沒有" in zh and "/open_positions" in zh, "zh reply must still suggest the real command"
    assert "/help" in zh, "zh reply must still offer a way forward"
    assert "僅限操作員" in zh, "the operator-only note must be translated too"
    # English is unchanged and remains the default.
    en = unknown_command_reply("positions", known)
    assert "I don't have a" in en and "operator-only" in en


def test_every_dictionary_language_menu_is_actually_registered_with_telegram():
    from pathlib import Path
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    body = src.split("async def _register_command_menu", 1)[1].split("\n    async def ", 1)[0]
    assert "language_code=code" in body, "Telegram needs a per-language menu registration"
    assert "for code in SUPPORTED_LANGS" in body, "one registration per dictionary language"
    assert "localized(english, code)" in body and "localized(admin_english, code)" in body
    assert "if entries == english" in body, "a language with no menu text is not a copy of English"
    # And the unknown-command reply uses the caller's language.
    assert "lang=get_user_lang(self.users, self._get_tg_id(update))" in src

"""The bot speaks every language the website speaks, completely.

`SUPPORTED_LANGS` was en/zh for as long as the dictionary lived inline, while
the website offered fourteen languages — so a Spanish visitor read a Spanish
site, a Spanish chat answer, and English chrome around it, and a Spanish
Telegram client was onboarded in English. The other twelve languages live in
`bot/utils/locales/<code>.json` (the bot's words) and
`bot/skills/command_catalog_locales/<code>.json` (/help and the "/" menu).

Three things a file-backed dictionary can get wrong that an inline one could
not, each pinned here:

  1. A key missing from one file. `t()` falls back to English per item, so
     the bot keeps working and the gap is invisible in production. The suite
     is where it fails: every key, every language, exactly the English set.
  2. A placeholder or an HTML tag lost in translation. `{equity}` dropped
     renders a card with no equity on it; a `<b>` unclosed breaks Telegram's
     HTML parse and the message is not delivered at all.
  3. A language on one surface and not the other. The web's `LANGS` table
     and the bot's `SUPPORTED_LANGS` are pinned to each other, in order.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bot.skills import command_catalog as cat
from bot.skills.command_menu import MENU_ZH, admin_commands, default_commands, localized
from bot.utils import i18n
from bot.utils.i18n import (
    _INLINE_LANGS,
    _STRINGS,
    LOCALES_DIR,
    SUPPORTED_LANGS,
    resolve_lang_choice,
    t,
    ui_lang,
)

ROOT = Path(__file__).resolve().parents[1]
FILE_LANGS = [c for c in SUPPORTED_LANGS if c not in _INLINE_LANGS]
NON_EN = [c for c in SUPPORTED_LANGS if c != "en"]

PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(?::[^{}]*)?\}")
TAG = re.compile(r"<[^<>]+>")
ENTITY = re.compile(r"&(?:lt|gt|amp);")
# A slash command the text names: "/scan", "<code>/approve", "\n/start" — but
# not the second half of "supply/demand" or "Risk/Reward".
COMMAND = re.compile(r"(?<![\w/])/[a-z_]+")


def _en(key: str) -> str:
    return _STRINGS[key]["en"]


# ── the two surfaces list the same languages ─────────────────────────────

def test_the_bot_and_the_web_offer_the_same_languages_in_the_same_order():
    src = (ROOT / "app/public/js/i18n.js").read_text(encoding="utf-8")
    block = re.search(r"var LANGS = \[(.*?)\];", src, re.S).group(1)
    web = re.findall(r"\{\s*code:\s*'([a-z]{2})',\s*name:\s*'([^']+)'", block)
    assert [c for c, _ in web] == list(SUPPORTED_LANGS)
    assert dict(web) == dict(SUPPORTED_LANGS), "the native names match too"


def test_every_supported_language_has_a_locale_file_and_no_file_is_orphaned():
    on_disk = sorted(p.stem for p in LOCALES_DIR.glob("*.json"))
    assert on_disk == sorted(FILE_LANGS), (
        "a locale file for a language SUPPORTED_LANGS does not list is dead "
        "weight; a language it lists without a file reads English")
    cat_on_disk = sorted(p.stem for p in cat.LOCALES_DIR.glob("*.json"))
    assert cat_on_disk == sorted(FILE_LANGS)


# ── every key, every language ─────────────────────────────────────────────

@pytest.mark.parametrize("lang", FILE_LANGS)
def test_locale_file_carries_exactly_the_english_keys(lang):
    data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    missing = sorted(set(_STRINGS) - set(data))
    extra = sorted(set(data) - set(_STRINGS))
    assert not missing, f"{lang}: no translation for {missing}"
    assert not extra, f"{lang}: translation for a key English does not have: {extra}"
    empty = sorted(k for k, v in data.items() if not isinstance(v, str) or not v.strip())
    assert not empty, f"{lang}: empty: {empty}"


@pytest.mark.parametrize("lang", NON_EN)
def test_every_key_is_translated_not_copied(lang):
    same = sorted(k for k in _STRINGS if _STRINGS[k][lang] == _en(k))
    # A handful of labels are the same in every language — OK, SL, TP, R:R,
    # LONG/SHORT, Paper — and `revoke_usage` is pure code. Every one of them
    # is a word or two; a sentence left in English is a missing translation.
    assert len(same) <= 20, f"{lang}: {len(same)} keys are the English text verbatim: {same}"
    sentences = [k for k in same if len(_en(k).split()) > 3]
    assert not sentences, f"{lang}: English sentences copied verbatim: {sentences}"
    assert t("confirm", lang) != t("confirm", "en")
    assert SUPPORTED_LANGS[lang] in t("lang_switched", lang), (
        "the confirmation names the language in its own words")


def _placeholders(text: str) -> list:
    # The inline zh `welcome_ready` takes `{status_label_zh}` where English
    # takes `{status_label}`; the handler passes both. Same slot, same name.
    return sorted(p.replace("_zh}", "}") for p in PLACEHOLDER.findall(text))


@pytest.mark.parametrize("lang", NON_EN)
def test_placeholders_tags_entities_and_commands_survive_translation(lang):
    bad = {}
    for key in _STRINGS:
        en, tr = _en(key), _STRINGS[key][lang]
        problems = []
        if _placeholders(en) != _placeholders(tr):
            problems.append(f"placeholders {PLACEHOLDER.findall(tr)} != {PLACEHOLDER.findall(en)}")
        if sorted(TAG.findall(en)) != sorted(TAG.findall(tr)):
            problems.append(f"tags {TAG.findall(tr)} != {TAG.findall(en)}")
        # An `&` that English escapes must stay escaped; a translation that
        # has no ampersand at all (zh writes 損益 for P&L) needs no entity.
        if "&" in tr and sorted(ENTITY.findall(en)) != sorted(ENTITY.findall(tr)):
            problems.append("entities differ")
        lost = set(COMMAND.findall(en)) - set(COMMAND.findall(tr))
        if lost:
            problems.append(f"commands lost: {sorted(lost)}")
        if "{" in tr and not PLACEHOLDER.findall(tr) and "{" in en:
            problems.append("brace without a placeholder")
        if problems:
            bad[key] = problems
    assert not bad, f"{lang}: {json.dumps(bad, ensure_ascii=False, indent=1)}"


def test_the_loader_takes_only_known_keys_and_never_raises(tmp_path, monkeypatch):
    table = {"confirm": {"en": "Confirm", "zh": "確認"},
             "cancel": {"en": "Cancel", "zh": "取消"}}
    monkeypatch.setattr(i18n, "_STRINGS", table)
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    (tmp_path / "es.json").write_text(
        json.dumps({"confirm": "Confirmar", "cancel": "   ", "invented": "x"}),
        encoding="utf-8")
    (tmp_path / "fr.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "de.json").write_text("[]", encoding="utf-8")
    i18n._load_locales()          # pt.json etc. are simply absent
    assert table["confirm"]["es"] == "Confirmar"
    assert "es" not in table["cancel"], "blank text is not a translation"
    assert "invented" not in table, "a locale cannot invent a key"
    assert "fr" not in table["confirm"] and "de" not in table["confirm"]
    # And t() reads English for what could not be loaded, never the key.
    assert i18n.t("confirm", "fr") == "Confirm"


# ── the catalogue: /help and the "/" menu ─────────────────────────────────

@pytest.mark.parametrize("lang", FILE_LANGS)
def test_catalogue_locale_is_complete(lang):
    loc = cat._locale(lang)
    titles = [title for title, _, _ in cat.GROUPS]
    entries = cat.all_entries()
    menu_names = [n for n, _ in admin_commands()]
    assert sorted(loc.get("groups", {})) == sorted(titles), f"{lang}: group titles"
    assert sorted(loc.get("desc", {})) == sorted(entries), f"{lang}: descriptions"
    assert sorted(loc.get("menu", {})) == sorted(menu_names), f"{lang}: menu"
    for section in ("groups", "desc", "menu"):
        empty = sorted(k for k, v in loc[section].items() if not isinstance(v, str) or not v.strip())
        assert not empty, f"{lang}.{section}: empty {empty}"
    # Translated, not copied — the emoji leads every title and menu line.
    # "Trading" is the loanword in most European languages, so that one
    # title may read as English; no other may.
    for title in titles:
        assert loc["groups"][title][0] == title[0], f"{lang}: {title} lost its emoji"
    copied_titles = [title for title in titles if loc["groups"][title] == title]
    assert copied_titles in ([], ["📈 Trading"]), f"{lang}: English titles: {copied_titles}"
    copied = sorted(n for n, (_, _, d) in entries.items() if loc["desc"][n] == d)
    assert len(copied) <= 3, f"{lang}: English descriptions copied verbatim: {copied}"
    for name, desc in admin_commands():
        assert loc["menu"][name] != desc and loc["menu"][name][0] == desc[0], f"{lang}: /{name}"
        assert len(loc["menu"][name]) <= 256, "Telegram caps a menu line at 256"
        assert len(loc["menu"][name]) >= 3, "Telegram floors a menu line at 3"


def test_the_zh_menu_covers_the_whole_operator_menu_too():
    assert not ({n for n, _ in admin_commands()} - set(MENU_ZH))


@pytest.mark.parametrize("lang", NON_EN)
def test_help_and_menu_render_in_every_language(lang):
    en_help = "\n".join(cat.render_help(is_admin=True))
    help_ = "\n".join(cat.render_help(is_admin=True, lang=lang))
    assert help_ != en_help
    for name in cat.all_entries():
        assert f"/{name} — " in help_, f"{lang}: /{name} missing from /help"
    en_menu = default_commands()
    menu = localized(en_menu, lang)
    assert [n for n, _ in menu] == [n for n, _ in en_menu]
    assert all(d != e for (_, d), (_, e) in zip(menu, en_menu)), f"{lang}: menu is English"
    assert cat.render_group("trading", lang=lang) != cat.render_group("trading")


# ── /lang ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed,expected", [
    ("fr", "fr"), ("FR", "fr"), ("pt-BR", "pt"), ("zh_TW", "zh"), ("zh-Hant", "zh"),
    ("Français", "fr"), ("français", "fr"), ("French", "fr"), ("日本語", "ja"),
    ("japanese", "ja"), ("中文", "zh"), ("繁體中文", "zh"), ("chinese", "zh"),
    ("espanol", "es"), ("Español", "es"), ("spanish", "es"), ("eng", "en"),
    ("English", "en"), ("한국어", "ko"), ("hindi", "hi"), ("العربية", "ar"),
    ("  deutsch  ", "de"), ("Русский", "ru"), ("türkçe", "tr"), ("italiano", "it"),
    ("nederlands", "nl"), ("português", "pt"),
    ("sw", None), ("swahili", None), ("klingon", None), ("", None), (None, None),
    ("fr es", None),
])
def test_lang_choice_resolves_codes_names_and_aliases_and_never_guesses(typed, expected):
    assert resolve_lang_choice(typed) == expected


def test_ui_lang_covers_every_dictionary_language():
    for code in SUPPORTED_LANGS:
        assert ui_lang(code) == code
        assert ui_lang(f"{code}-XX") == code
    assert ui_lang("sw") == "en"

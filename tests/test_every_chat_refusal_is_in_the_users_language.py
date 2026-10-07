"""Every chat refusal `_say` names has a dictionary entry in every language.

`_say(lang, key, english)` falls back to the call site's English when the
key has no entry, silently. The chat's own-cap refusal shipped with a key no
dictionary held, so a Chinese user who reached the share cap was refused in
Chinese and one who reached the own cap was refused in English. The keys are
read off the calls (an AST walk over `bot/`), so the next one is held too.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from bot.skills.chat_runtime import _say
from bot.utils.i18n import _STRINGS

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _calls():
    out = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_say" and len(node.args) == 3):
                key, en = node.args[1], node.args[2]
                assert isinstance(key, ast.Constant), f"{path}:{node.lineno} key is not a literal"
                out.append((f"{path.relative_to(ROOT)}:{node.lineno}", key.value,
                            ast.literal_eval(en)))
    return out


CALLS = _calls()
# The languages the oldest refusal carries: the set every one must carry.
LANGS = sorted(_STRINGS["chat_unavailable"])


def test_the_walk_finds_the_refusals():
    keys = {k for _, k, _ in CALLS}
    assert {"chat_unavailable", "chat_share_exhausted", "chat_own_budget_exhausted",
            "chat_own_budget_exhausted_shared"} <= keys
    assert len(LANGS) >= 14


@pytest.mark.parametrize("where, key, en", CALLS, ids=[c[1] for c in CALLS])
def test_each_refusal_has_every_language_and_the_call_sites_english(where, key, en):
    entry = _STRINGS.get(key)
    assert entry is not None, f"{where}: {key} has no dictionary entry"
    assert entry.get("en") == en, f"{where}: the dictionary's English is not the call site's"
    missing = [lang for lang in LANGS if not entry.get(lang)]
    assert not missing, f"{where}: {key} has no {missing}"


@pytest.mark.parametrize("where, key, en", CALLS, ids=[c[1] for c in CALLS])
def test_a_non_english_user_is_answered_in_their_language(where, key, en):
    assert _say("zh", key, en) != en
    assert _say("de", key, en) != en
    assert _say("en", key, en) == en

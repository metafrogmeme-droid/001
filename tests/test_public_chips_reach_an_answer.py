"""The five chips a visitor is offered must be five questions this can answer.

`app/public/js/chat.js` shows an anonymous visitor five suggestion chips, and
those chips are now localised — the visitor reads and sends their own language,
which the FAQ correctly declines to match, so the model answers instead.

The ENGLISH payloads are the other half of that arrangement, and they are what
`chat_public_fallback` names in all fourteen languages as the phrasings that
still work when no model is reachable. Both claims stand on the same fact: each
chip's English text reaches a built-in answer. Nothing else checks it — the
chips are JS, the answers are Python, and the two lists have only ever agreed by
somebody remembering they should.

A card that names a question is claiming it can answer it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from bot.core.faq_kb import faq_answer
from bot.utils.i18n import SUPPORTED_LANGS, t

CHAT_JS = pathlib.Path(__file__).resolve().parent.parent / "app/public/js/chat.js"


def _public_chip_english() -> list:
    """The `en` field of each public chip descriptor, read from the source."""
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.index("const CHIP_PROMPTS = PUBLIC ? [")
    end = src.index("] : [", start)
    block = src[start:end]
    found = re.findall(r"en:\s*'((?:[^'\\]|\\.)*)'", block)
    return [f.replace("\\'", "'") for f in found]


CHIPS = _public_chip_english()


def test_the_chips_were_actually_found():
    """A regex that silently matches nothing would make every test below vacuous
    — the shape this file's JS sibling was caught in."""
    assert len(CHIPS) == 5, CHIPS
    assert all(c.strip() for c in CHIPS), CHIPS


@pytest.mark.parametrize("chip", CHIPS)
def test_every_chip_the_visitor_is_offered_reaches_an_answer(chip):
    assert faq_answer(chip), (
        f"the landing page offers {chip!r} and the built-in answers do not "
        "match it — either the chip's wording drifted or a trigger did")


def test_the_no_model_fallback_names_the_same_five():
    """`chat_public_fallback` lists the English phrasings in every language
    precisely because the answers are English-only. If a chip is reworded and
    that card is not, the card sends the visitor to a question nothing answers."""
    for code in SUPPORTED_LANGS:
        card = t("chat_public_fallback", code)
        for chip_text in CHIPS:
            assert chip_text in card, (
                f"{code}: the fallback card does not name {chip_text!r}")


def test_the_chip_keys_carry_every_language_the_bot_also_speaks():
    """The web dictionary and the bot dictionary are separate files with the
    same fourteen languages. A chip localised into thirteen of them would leave
    one reader an English suggestion beside a translated card."""
    src = CHAT_JS.read_text(encoding="utf-8")
    keys = re.findall(r"key:\s*'([^']+)'", src[src.index("const CHIP_PROMPTS"):])
    assert len(keys) == 5, keys
    web_dict = (CHAT_JS.parent / "i18n.js").read_text(encoding="utf-8")
    for key in keys:
        line = next((ln for ln in web_dict.splitlines() if f"'{key}':" in ln), "")
        assert line, f"{key} is not in the web dictionary"
        for code in SUPPORTED_LANGS:
            assert re.search(rf"\b{code}:\s*'", line), f"{key} has no {code}"

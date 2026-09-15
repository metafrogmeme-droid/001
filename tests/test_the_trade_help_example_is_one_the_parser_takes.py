"""The help that teaches the format must show an example the parser accepts.

`parse_manual_trade`'s own error message offered two examples, and the second
one — `short ETH 1721 sl 1695 tp 1842 margin 250` — is a SHORT written with a
LONG's geometry. Typed back verbatim, the same parser answers:

    SHORT: SL ($1,695.0000) must be above entry ($1,721.0000)

So a caller who mistypes the format, reads the help and copies the example
gets a different error and still no trade, on the one chat path that opens a
real position. The same string is `trade_help` in all FOURTEEN languages.

That is the `/vault` hint shape pointed at a FORMAT: a card names the thing to
type, and nothing ever checked that typing it works. These tests DRIVE it —
every `<code>` example the product shows, in every language, goes back through
the parser. Eyeballing is how the broken one survived.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from bot.skills.manual_trade import (
    looks_like_manual_trade,
    parse_manual_trade,
)
from bot.utils.i18n import SUPPORTED_LANGS, t
from tests.source_scan import code_only

#: Examples are shown inside <code> blocks, optionally prefixed with the
#: slash command — `/trade buy SOL …`. The parser is handed the BODY.
_CODE = re.compile(r"<code>(.*?)</code>", re.DOTALL)

#: An EXAMPLE is a code block that opens with one of the grammar's verbs and
#: a SPACE. The same card also puts `buy/long`, `sell/short` and `margin` in
#: code, and those are token references, not lines anyone types.
#:
#: The discriminator is deliberately NOT `looks_like_manual_trade` — asking
#: the intercept which blocks are examples and then asserting the intercept
#: takes them is circular, and would skip in silence exactly the broken
#: example this file exists to catch. A verb and a space is a claim about
#: what the block IS; whether it parses is the question.
_VERBS = ("buy ", "long ", "short ", "sell ")


def _examples(text: str) -> list[str]:
    out = []
    for raw in _CODE.findall(text or ""):
        ex = raw.strip()
        if ex.lower().startswith("/trade"):
            ex = ex[len("/trade"):].strip()
        if ex and ex.lower().startswith(_VERBS):
            out.append(ex)
    return out


class TestEveryExampleTheProductShowsIsOneItAccepts:
    def test_the_parsers_own_error_message_teaches_a_format_it_takes(self):
        """The error a caller sees names two formats. Both must parse."""
        message = parse_manual_trade("this is not a trade")
        assert isinstance(message, str), "a junk input must produce the help"
        examples = _examples(message)
        assert len(examples) >= 2, f"the help lost its examples: {message!r}"
        for ex in examples:
            got = parse_manual_trade(ex)
            assert isinstance(got, tuple), (
                f"the parser's OWN example is rejected by the parser: "
                f"{ex!r} -> {got!r}")

    @pytest.mark.parametrize("lang", sorted(SUPPORTED_LANGS))
    def test_the_trade_help_card_teaches_a_format_the_parser_takes(self, lang):
        """`trade_help` in every language. The examples are the same bytes in
        all fourteen, which is exactly why one wrong one was wrong fourteen
        times."""
        card = t("trade_help", lang)
        examples = _examples(card)
        assert examples, f"{lang}: the help card shows no example at all"
        for ex in examples:
            got = parse_manual_trade(ex)
            assert isinstance(got, tuple), (
                f"{lang}: the /trade help shows an example the parser "
                f"rejects: {ex!r} -> {got!r}")

    def test_a_short_example_really_is_a_short(self):
        """The defect was a DIRECTION error, not a typo, so pin the geometry.

        A SHORT's stop sits ABOVE its entry and its target BELOW. The old
        example had both the other way round — a long's shape under a short's
        verb — which no length or format check would ever have caught.
        """
        card = t("trade_help", "en")
        shorts = [ex for ex in _examples(card)
                  if ex.split()[0].lower() in ("short", "sell")]
        assert shorts, "the help must show a SHORT as well as a LONG"
        for ex in shorts:
            direction, _sym, entry, sl, tp, _margin = parse_manual_trade(ex)
            assert direction == "SHORT"
            assert sl > entry, f"{ex!r}: a short's stop is above its entry"
            assert tp < entry, f"{ex!r}: a short's target is below its entry"

    def test_the_examples_reach_the_intercept_that_routes_them(self):
        """A format the parser takes and the INTERCEPT declines is still a
        door that does nothing: the message would never reach `_cmd_trade`.

        Both surfaces ask `looks_like_manual_trade`, so this pins the whole
        path a caller who copies the example actually walks.
        """
        for lang in sorted(SUPPORTED_LANGS):
            for ex in _examples(t("trade_help", lang)):
                assert looks_like_manual_trade(ex) is not None, (
                    f"{lang}: {ex!r} parses but the intercept declines it, so "
                    "it never reaches the trade card")
                # And with the slash prefix the card actually prints.
                assert looks_like_manual_trade("trade " + ex) is not None


class TestOneReadingOfWhetherAMessageIsTheGrammar:
    """`telegram_handler` and `user_gateway` each carried the same six lines.

    They agreed — which is what a second copy looks like from outside until
    one of them is edited. The limit-price arming was this shape one slice
    earlier, and its second copy had lost the line that mattered.
    """

    def test_it_answers_the_body_not_a_boolean(self):
        # Both callers needed the stripped text and both were re-deriving it.
        assert looks_like_manual_trade("buy SOL 71 sl 70 tp 76") == "buy sol 71 sl 70 tp 76"
        assert looks_like_manual_trade("  TRADE Buy sol 71 SL 70 tp 76 ") == "buy sol 71 sl 70 tp 76"

    @pytest.mark.parametrize("text", [
        "buy eth", "long btc", "short sol", "sell doge",       # no levels
        "place a limit order on pendle", "Place limit pendle",  # no verb it knows
        "", "   ", "buy", "scan the market",
    ])
    def test_everything_short_of_the_full_grammar_is_declined(self, text):
        """The SL is mandatory, which is the discipline `user_gateway`'s own
        comment states: only the strict form proposes directly. Everything
        else is the router's to answer with a door."""
        assert looks_like_manual_trade(text) is None

    def test_neither_surface_keeps_a_private_copy_of_the_condition(self):
        for path in ("bot/skills/telegram_handler.py", "bot/web/user_gateway.py"):
            src = code_only(pathlib.Path(path).read_text())
            assert "looks_like_manual_trade(" in src, path
            assert '" sl " not in' not in src, path
            assert '" sl " in' not in src, (
                f"{path} tests for the SL itself again — that reading belongs "
                "to manual_trade.looks_like_manual_trade")

"""A prompt that asks for a price must not be sent unless something reads it.

Driven from a live Telegram transcript (PENDLE LONG, 2026-09-15 09:23). The
scan card's **Limit** button printed *"Type your limit price"*, the caller
typed ``$2.367``, and the bot answered with a macro-risk card in a different
language. Nothing was broken about the capture — it was never armed:

    if handler and hasattr(handler, '_pending_limit_input'):   # arming
        handler._pending_limit_input[caller_uid] = {...}
    await query.message.reply_text("... Type your limit price ...")  # prompt

`_pending_limit_input` is a bare annotation on the callback mixin, whose own
comment says *"created on first use"*, so `hasattr` is False until the OTHER
door has run once in the process. The prompt was unconditional; the listener
was not.

These tests DRIVE that. A source scan cannot see reachability, and
reachability is the entire defect — the code was present, correct-looking,
and never reached.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.core.limit_input import (
    STATE_ATTR,
    arm_limit_input,
    caller_lang,
    limit_prompt_text,
    limit_unarmed_text,
)
from tests.source_scan import code_only


class _Mixin:
    """A handler shaped like the real one: the attribute is ANNOTATED only.

    This is the fixture that matters. A stub that pre-creates the dict cannot
    tell the broken code from the fixed code — which is why the defect
    survived: every test of this flow had already armed it.
    """

    _pending_limit_input: dict

    def _lang(self, update):  # noqa: ARG002 - the update is not read here
        return "nl"


class TestArmingIsAVerdict:
    def test_it_creates_the_state_rather_than_testing_for_it(self):
        h = _Mixin()
        assert not hasattr(h, STATE_ATTR), (
            "the fixture must start as the real mixin does — annotated, "
            "unassigned — or it cannot see the defect")

        assert arm_limit_input(
            h, "4242", trade_id="T-1", asset="PENDLE", pair="PENDLE/USDT",
            direction="LONG", current_entry=2.367) is True

        assert "4242" in getattr(h, STATE_ATTR)
        assert getattr(h, STATE_ATTR)["4242"]["trade_id"] == "T-1"

    def test_no_handler_is_refused_not_armed_elsewhere(self):
        assert arm_limit_input(
            None, "4242", trade_id="T-1", asset="PENDLE", pair="PENDLE/USDT",
            direction="LONG", current_entry=2.367) is False

    def test_an_empty_caller_id_is_refused(self):
        """Arming under "" writes a row the intercept can never match.

        Both doors compute `str(update.effective_user.id) if
        update.effective_user else ""` — their own authors anticipated no
        user — and the free-text capture looks up `str(uid)`. So the old code
        armed a key nobody reads: the same silent no-listener a second way.
        """
        h = _Mixin()
        assert arm_limit_input(
            h, "", trade_id="T-1", asset="PENDLE", pair="PENDLE/USDT",
            direction="LONG", current_entry=2.367) is False
        assert not getattr(h, STATE_ATTR, {}), "nothing may be written"

    def test_the_row_is_what_the_intercept_reads(self):
        """The capture reads five fields off the row; arming writes all five."""
        h = _Mixin()
        arm_limit_input(h, "4242", trade_id="T-1", asset="PENDLE",
                        pair="PENDLE/USDT", direction="LONG",
                        current_entry=2.367, now=1_000_000.0)
        row = getattr(h, STATE_ATTR)["4242"]
        for field in ("trade_id", "pair", "direction", "current_entry",
                      "timestamp"):
            assert field in row, field
        assert row["timestamp"] == 1_000_000.0


class TestTheCardSpeaksTheCallersLanguage:
    def test_the_prompt_comes_from_the_table_not_the_keyboard(self):
        """`limit_prompt` has carried all fourteen translations all along.

        Both doors hand-wrote the English, so the Dutch caller in the
        transcript read an English prompt and a Dutch answer in one exchange.
        """
        nl = limit_prompt_text("nl", asset="PENDLE", direction="LONG",
                               entry=2.367, stop_loss=2.29599,
                               take_profit=2.50902)
        assert "limietprijs" in nl, nl
        assert "Type your limit price" not in nl

    def test_the_examples_are_this_asset_not_some_other_trade(self):
        """`callback_handler` offered "e.g. 84.07 or 0.0522" for every asset.

        Those are not guidance about a coin at $2.37; they are two numbers
        from a different trade, printed with the confidence of an example.
        """
        card = limit_prompt_text("en", asset="PENDLE", direction="LONG",
                                 entry=2.367, stop_loss=2.29599,
                                 take_profit=2.50902)
        assert "84.07" not in card and "0.0522" not in card
        assert "2.34333" in card or "2.34" in card, card

    def test_the_unarmed_sentence_claims_nothing_about_the_order(self):
        for lang in ("en", "nl"):
            s = limit_unarmed_text(lang)
            assert s, lang
            assert "limit" in s.lower() or "limiet" in s.lower(), s
        en = limit_unarmed_text("en")
        assert "no order was placed" in en.lower(), en
        assert "Type your limit price" not in en, (
            "the unarmed branch must not ask for a price — that is the defect")

    def test_every_language_has_both_strings(self):
        """Every key in this table carries all fourteen; a new one must too."""
        from bot.utils import i18n
        for key in ("limit_prompt", "limit_not_armed"):
            assert len(i18n._STRINGS[key]) == 14, (key, len(i18n._STRINGS[key]))

    def test_the_inline_table_holds_only_the_inline_languages(self):
        """`_INLINE_LANGS` is declared and was enforced by nothing.

        `i18n.py` keeps en and zh inline and the other twelve one file each
        under `locales/`, merged at import — and the first draft of this
        slice wrote all fourteen inline, which is a SECOND STORE for twelve
        strings that the locale guard could not see (it reads the files, and
        the files were what was missing). The tree has zero violations
        today, so this holds from here rather than carrying a baseline.
        """
        import ast
        import re

        src = pathlib.Path("bot/utils/i18n.py").read_text()
        m = re.search(r"^_STRINGS[^=]*=\s*\{", src, re.M)
        assert m, "the inline table moved; this guard must move with it"
        start = m.end() - 1
        depth, i = 0, start
        while True:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        inline = ast.literal_eval(src[start:i + 1])

        from bot.utils.i18n import _INLINE_LANGS
        stray = {k: sorted(set(v) - set(_INLINE_LANGS))
                 for k, v in inline.items() if set(v) - set(_INLINE_LANGS)}
        assert not stray, (
            "these keys carry a locale-file language inline, so one string "
            f"lives in two stores: {stray}")

    def test_language_falls_back_only_when_nobody_can_be_asked(self):
        assert caller_lang(_Mixin(), object()) == "nl"
        assert caller_lang(None, object()) == "en"

        class Raises:
            def _lang(self, update):
                raise RuntimeError("store down")

        assert caller_lang(Raises(), object()) == "en"


class TestBothDoorsAskOnlyAfterArming:
    """The source half: the prompt may not be reachable without the verdict.

    This is a SCAN and says so. The drives above prove the seam; what a
    drive cannot cheaply prove is that neither door grew a second, unguarded
    `reply_text` beside it — the branch sits inside a 120-line async callback
    behind a live-permission gate and an exchange fetch.
    """

    @pytest.mark.parametrize("path", [
        "bot/skills/scan_skill.py",
        "bot/skills/callback_handler.py",
    ])
    def test_no_door_hand_writes_the_prompt_any_more(self, path):
        src = code_only(pathlib.Path(path).read_text())
        assert "Type your limit price" not in src, (
            f"{path} hand-writes the prompt again; it belongs to "
            "`limit_prompt` so all fourteen languages move together")

    @pytest.mark.parametrize("path", [
        "bot/skills/scan_skill.py",
        "bot/skills/callback_handler.py",
    ])
    def test_the_prompt_call_sits_under_the_armed_verdict(self, path):
        src = code_only(pathlib.Path(path).read_text())
        assert "arm_limit_input(" in src, path
        armed_at = src.index("armed = arm_limit_input(")
        prompt_at = src.index("limit_prompt_text(")
        refusal_at = src.index("limit_unarmed_text(")
        assert armed_at < refusal_at < prompt_at, (
            f"{path}: the prompt must come after the arming verdict AND "
            "after the branch that declines to ask")
        assert "if not armed:" in src, path
        # ONE send site per door. Two would be two chances for a later edit
        # to move one out from under the verdict — which is exactly how the
        # unconditional prompt got there.
        sends = src.count("reply_text(said") + src.count("(update, said")
        assert sends == 1, (
            f"{path}: the chosen text must go out through exactly one send, "
            f"found {sends}")

    def test_neither_door_tests_for_the_state_instead_of_creating_it(self):
        """`hasattr` here is the defect's whole shape.

        It asks whether some EARLIER caller happened to make the dict, which
        is not the question — the question is whether THIS caller is being
        listened to.
        """
        for path in ("bot/skills/scan_skill.py",
                     "bot/skills/callback_handler.py"):
            src = code_only(pathlib.Path(path).read_text())
            assert "hasattr(self, '_pending_limit_input')" not in src, path
            assert "hasattr(handler, '_pending_limit_input')" not in src, path

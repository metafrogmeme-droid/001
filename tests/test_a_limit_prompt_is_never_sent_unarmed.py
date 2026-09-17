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

import ast
import pathlib

import pytest

from bot.core.limit_input import (
    _ROW_FIELDS,
    PENDING_TTL_SEC,
    STATE_ATTR,
    arm_limit_input,
    caller_lang,
    consume_pending,
    limit_expired_text,
    limit_prompt_text,
    limit_unarmed_text,
    read_pending,
)
from bot.utils import i18n
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


class TestAnExpiredPromptIsNotAPromptThatWasNeverSent:
    """The other half of the same door, filed on 2026-09-15 and driven now.

    The handler's block deleted a stale row, set ``pending_info = None`` and
    fell through — so an arming that TIMED OUT and one that was never made
    were one silence. Driven through the block as it stood:

        armed 10s ago    -> CAPTURED
        armed 301s ago   -> FELL THROUGH   bot said: []
        never armed      -> FELL THROUGH   bot said: []

    and driven through the router, every bare number (``2.367``, ``$2.367``,
    ``0.0522``, ``100k``, ``3``) reaches NO rule at any confidence, so the
    fall-through is the chat model every time. The caller answered the bot's
    own question six minutes late and was told about something else — which
    is this module's own incident arriving through the other door.

    It is REQUIRED by the button-transcript slice rather than optional: once
    the Limit tap's prompt is in the transcript, the model reads
    *"[limit] SHOWN: type your limit price"* followed by a bare number and
    will reasonably narrate that the price was set. Wiring the record makes
    the silence worse.
    """

    NOW = 1_000_000.0

    def _armed(self, h, *, when=None):
        arm_limit_input(h, "4242", trade_id="T-1", asset="PENDLE",
                        pair="PENDLE/USDT", direction="LONG",
                        current_entry=2.40, now=self.NOW if when is None else when)

    def test_a_fresh_arming_is_armed_and_keeps_its_row(self):
        h = _Mixin()
        self._armed(h)
        state, row = read_pending(h, "4242", now=self.NOW + 299)
        assert state == "armed"
        assert row["pair"] == "PENDLE/USDT"
        assert getattr(h, STATE_ATTR)["4242"] is row, (
            "an armed row survives the read — the capture body deletes it on "
            "success and KEEPS it on an unparseable price, so the caller can "
            "retry without re-tapping")

    def test_a_stale_arming_is_expired_and_is_consumed(self):
        h = _Mixin()
        self._armed(h)
        state, row = read_pending(h, "4242", now=self.NOW + PENDING_TTL_SEC + 1)
        assert state == "expired"
        assert row["pair"] == "PENDLE/USDT", (
            "the row comes BACK, because the sentence the caller is owed "
            "names the trade the price was for")
        assert "4242" not in getattr(h, STATE_ATTR), (
            "and is consumed, so a stale arming cannot answer the NEXT number")

    def test_expired_and_never_armed_are_different_answers(self):
        """They were one fall-through. That is the whole defect."""
        stale = _Mixin()
        self._armed(stale)
        never = _Mixin()
        assert read_pending(stale, "4242", now=self.NOW + 400)[0] == "expired"
        assert read_pending(never, "4242", now=self.NOW + 400)[0] == "none"

    def test_a_row_this_build_did_not_write_is_none_not_expired(self):
        """NOT "expired": that sentence names a trade and there is none to
        name. It is also what the capture body needs — that body reads
        ``row["trade_id"]`` outside any handler catching a KeyError, so such
        a row used to crash the message."""
        for junk in ({"nope": 1}, {"trade_id": "T"}, "a string", 7):
            h = _Mixin()
            setattr(h, STATE_ATTR, {"4242": junk})
            state, row = read_pending(h, "4242", now=self.NOW)
            assert (state, row) == ("none", None), junk
            assert "4242" not in getattr(h, STATE_ATTR), (
                "consumed, so it cannot sit there forever")

    def test_consuming_is_how_the_body_stops_listening(self):
        """The capture body deleted the row itself at three sites while
        `read_pending` decided whether a prompt was live. Two readers of one
        dict are two answers to that question."""
        h = _Mixin()
        self._armed(h)
        consume_pending(h, "4242")
        assert read_pending(h, "4242", now=self.NOW)[0] == "none"
        # twice is not an error: it is the second caller to finish
        consume_pending(h, "4242")
        consume_pending(h, "")
        consume_pending(_Mixin(), "4242")

    def test_an_empty_caller_id_reads_nothing(self):
        h = _Mixin()
        self._armed(h)
        assert read_pending(h, "", now=self.NOW)[0] == "none"
        assert read_pending(h, None, now=self.NOW)[0] == "none"

    def test_the_writer_carries_at_least_what_the_readers_need(self):
        """A SUPERSET, never an equality.

        The first draft asserted equality, which made the precondition the
        writer's output rather than the readers' needs — and the full gate
        found two existing tests planting exactly the four fields the capture
        body reads, which that precondition answered NONE for. A row carrying
        what its readers need is armed; a field nobody reads is not part of
        the contract.
        """
        h = _Mixin()
        self._armed(h)
        written = set(getattr(h, STATE_ATTR)["4242"])
        assert _ROW_FIELDS <= written, sorted(_ROW_FIELDS - written)

    def test_a_row_with_only_what_the_readers_need_is_armed(self):
        """The shape the two transcript tests plant, and the shape an arming
        already on the handler when this build started would have."""
        h = _Mixin()
        setattr(h, STATE_ATTR, {"4242": {"trade_id": "t1", "pair": "SOL/USDT",
                                         "direction": "long",
                                         "timestamp": self.NOW}})
        assert read_pending(h, "4242", now=self.NOW + 1)[0] == "armed"
        assert read_pending(h, "4242", now=self.NOW + 400)[0] == "expired"

    def test_the_sentence_names_the_trade_and_denies_placing_anything(self):
        said = limit_expired_text("en", pair="PENDLE/USDT")
        assert "PENDLE/USDT" in said
        assert "Nothing was placed" in said
        assert str(PENDING_TTL_SEC // 60) in said, "it says how long they had"

    def test_every_language_has_it(self):
        """Read `_STRINGS` directly: `translate` falls back to English, so a
        key present in one language answers for all fourteen and a guard
        written through it cannot see a missing translation."""
        entry = i18n._STRINGS["limit_expired"]
        assert len(entry) == 14, sorted(entry)
        for code, text in entry.items():
            assert "{pair}" in text and "{minutes}" in text, code

    def test_the_handler_reads_the_seam_and_answers_the_expired_case(self):
        """The branch's SHAPE, because the mutation round found the literal
        check one step short twice.

        It is a scan and says so. Driving `_handle_message` to this line means
        standing up the mention strip, the firewall pre-scan, the admission
        store and the router — a fixture larger than the three statements it
        would check — which is the argument `/deepscan all`'s label guard
        already records. So the SHAPE is asserted instead: the expired branch
        must SEND and must RETURN. `"limit_expired_text" in body` survived
        deleting the send (the sentence is still computed) and survived
        deleting the return (the caller then falls into the capture body with
        a consumed row) — a literal cannot see either.
        """
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "_handle_message")
        body = ast.unparse(fn)
        assert "read_pending(self, caller_uid)" in body
        assert "limit_expired_text" in body
        assert "_pending_limit_input" not in body, (
            "the handler asks the reading rather than reaching into the state "
            "— two readers of one dict are two answers about whether a "
            "prompt is still live")
        assert body.count("consume_pending(self, caller_uid)") == 3, (
            "the price used, the idea gone, the caller cancelling")

        expired = [n for n in ast.walk(fn)
                   if isinstance(n, ast.If) and "'expired'" in ast.unparse(n.test)]
        assert len(expired) == 1, "one expired branch"
        block = expired[0].body
        said = [ast.unparse(st) for st in block]
        assert any("limit_expired_text" in line for line in said), (
            "the branch builds the sentence")
        assert any("self._send(" in line for line in said), (
            "AND SENDS IT. Without this the row is consumed, nothing is said, "
            "and the caller's price still reaches the chat model — the exact "
            "silence this slice removes, rebuilt inside the cure for it")
        assert isinstance(block[-1], ast.Return), (
            "and RETURNS. Falling through takes the caller into the capture "
            f"body with a consumed row; the branch ends {said[-1]!r}")

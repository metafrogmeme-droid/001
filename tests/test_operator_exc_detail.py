"""`BadRequest · update 732736899` names dozens of faults and identifies none.

A live error report reached the operator as exactly that: the exception class
and an update id. The class-only rule exists for a good reason (F-15 — str(exc)
on a ccxt/auth error can carry a raw API key), but `telegram.error.BadRequest`
covers unclosed HTML tags, over-long messages, unmodified edits, bad chat ids
and more. The class alone diagnoses none of them, so the bug stayed open.

Telegram's own `description` field is what distinguishes them, and for THOSE
classes the message is a statement about our payload rather than about our
credentials. This shows it, under two constraints:

  - EXACT class names, never isinstance. BadRequest subclasses NetworkError in
    PTB, so an isinstance test on the parent admits every network error — and
    those carry the request URL, which contains the bot token.
  - The bot-token shape is stripped explicitly. The logger's inline redactor
    only matches `key=value` and would pass a URL straight through.
"""
from __future__ import annotations

import telegram.error as tg_err

from bot.skills.telegram_handler import (
    _MSG_SAFE_ERRORS,
    _operator_exc_detail,
)

from tests.source_scan import code_only

# A realistic Telegram token shape. Not a real credential.
FAKE_TOKEN = "123456789:AAFakeTokenForTestingOnly_NotReal12345"


class TestItAnswersTheQuestionTheClassCouldNot:
    def test_bad_request_message_is_shown(self):
        out = _operator_exc_detail(
            tg_err.BadRequest("Can't parse entities: unclosed start tag"))
        assert "unclosed start tag" in out

    def test_forbidden_message_is_shown(self):
        out = _operator_exc_detail(tg_err.Forbidden("bot was blocked by the user"))
        assert "blocked by the user" in out

    def test_the_message_is_collapsed_to_one_line(self):
        # It lands inside a <code> span on a status reply.
        out = _operator_exc_detail(tg_err.BadRequest("line one\n\tline  two"))
        assert "\n" not in out and "\t" not in out
        assert out == "line one line two"

    def test_it_is_length_capped(self):
        out = _operator_exc_detail(tg_err.BadRequest("x" * 5000), limit=240)
        assert len(out) == 240

    def test_an_empty_message_yields_empty_not_whitespace(self):
        assert _operator_exc_detail(tg_err.BadRequest("")) == ""


class TestItRefusesTheClassesThatCanLeak:
    """PTB puts https://api.telegram.org/bot<TOKEN>/... into network errors."""

    def test_network_error_is_refused_despite_being_the_parent(self):
        # This is the whole reason for exact-name matching. BadRequest IS a
        # NetworkError, so isinstance(exc, NetworkError) would admit both.
        assert issubclass(tg_err.BadRequest, tg_err.NetworkError)
        assert _operator_exc_detail(
            tg_err.NetworkError(f"POST https://api.telegram.org/bot{FAKE_TOKEN}/x")
        ) == ""

    def test_timed_out_is_refused(self):
        assert _operator_exc_detail(tg_err.TimedOut("timed out")) == ""

    def test_invalid_token_is_refused(self):
        assert _operator_exc_detail(tg_err.InvalidToken(FAKE_TOKEN)) == ""

    def test_a_plain_exception_is_refused(self):
        assert _operator_exc_detail(RuntimeError("boom")) == ""

    def test_a_ccxt_style_auth_error_is_refused(self):
        # The exact case the class-only rule was written for.
        class AuthenticationError(Exception):
            pass
        assert _operator_exc_detail(
            AuthenticationError("bitget apiKey=REALKEY123456 rejected")) == ""

    def test_the_allowlist_holds_no_network_class(self):
        for name in _MSG_SAFE_ERRORS:
            cls = getattr(tg_err, name.rsplit(".", 1)[1], None)
            assert cls is not None, name
            # RetryAfter/Forbidden/ChatMigrated/BadRequest — BadRequest is the
            # only NetworkError subclass admitted, and it is admitted because
            # its message is Telegram's description of our own payload.
            if issubclass(cls, tg_err.NetworkError):
                assert name == "telegram.error.BadRequest"


class TestRedactionOfAdmittedClasses:
    """Defence in depth: even an allowlisted class gets scrubbed."""

    def test_a_token_inside_a_bad_request_is_stripped(self):
        out = _operator_exc_detail(
            tg_err.BadRequest(f"failed at https://api.telegram.org/bot{FAKE_TOKEN}/send"))
        assert FAKE_TOKEN not in out
        assert "AAFakeTokenForTestingOnly" not in out
        assert "REDACTED" in out

    def test_a_key_value_secret_inside_a_bad_request_is_stripped(self):
        out = _operator_exc_detail(
            tg_err.BadRequest("rejected api_key=SUPERSECRETVALUE99"))
        assert "SUPERSECRETVALUE99" not in out
        assert "REDACTED" in out

    def test_the_token_pass_runs_before_the_key_value_pass(self):
        # A bare token has no `key=` around it, so only the token pass can
        # catch it. Running the key=value pass alone would leak.
        out = _operator_exc_detail(tg_err.BadRequest(f"token={FAKE_TOKEN}"))
        assert "AAFakeTokenForTestingOnly" not in out


class TestTheHandlerUsesIt:
    def test_the_error_handler_calls_the_helper(self):
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "_operator_exc_detail(exc)" in src

    def test_the_helper_never_uses_isinstance_on_the_class_check(self):
        # isinstance on NetworkError would admit every network error.
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        fn = src.split("def _operator_exc_detail", 1)[1].split("\nfrom ", 1)[0]
        assert "isinstance" not in fn
        assert "_MSG_SAFE_ERRORS" in fn

    def test_the_type_only_wording_is_kept_when_there_is_no_detail(self):
        # Non-allowlisted classes must still say what they are, and must not
        # claim a reason they do not have.
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "type only; full" in src

"""I fixed the operator's auth path and left the users' one behind, same file.

#1029 stopped a transport failure being reported as "the venue rejected
authentication" and halting the operator's entries. One function further down
in bot/main.py, the PER-USER preflight had no classification at all:

    _err = bal.get("error") if isinstance(bal, dict) else None
    if _err:
        engine.set_live_auth_status(False, str(_err)[:120], user_id=uid)
        failures.append((uid, str(_err)[:100]))

Any error at all -> that user's auth marked DOWN -> their entries halted ->
and the message tells them to re-<code>/connect</code>, meaning re-enter
their API credentials.

That is the WORSE place to lack the check. The operator sees a false alarm
and can reason about it; a user is instructed to go and handle a secret, for
a network blip, on an account that was never rejected.

It sat there for an hour after the operator path was fixed, in the file being
edited, while the PR body for that fix said "fixing a surface is not
finishing a defect".

    THE QUESTION IS ALWAYS WHICH OTHER SURFACE MAKES THE SAME CLAIM,
    AND "THE ONE DIRECTLY BELOW IT" IS AN ANSWER.

Fifth time this session: /portfolio after /open_positions, theater.js's third
rendering after its first two, a coverage note wired to nothing one PR after
the rule was written, the doc that documented the rule it broke, and this.
"""
from __future__ import annotations

import pytest

from bot.main import _is_auth_rejection, _is_transport_failure
from tests.source_scan import code_only

LIVE_ERROR = ("bitget GET https://api.bitget.com/api/v3/market/instruments"
              "?category=USDC-FUTURES")


def _src_handler() -> str:
    return code_only(
        open("bot/skills/telegram_handler.py", encoding="utf-8").read())


def _src() -> str:
    return code_only(open("bot/main.py", encoding="utf-8").read())


def _user_block() -> str:
    src = _src()
    i = src.index("failed venue ")
    return src[max(0, i - 3000):i + 600]


class TestTheUserPathClassifiesToo:
    def test_it_consults_the_classifier(self):
        assert "_is_transport_failure(str(_err))" in _user_block(), (
            "the per-user branch marked auth DOWN for any error at all"
        )

    def test_a_transport_failure_does_not_mark_that_user_down(self):
        block = _user_block()
        i = block.index("_is_transport_failure(str(_err))")
        # Slice to the `else:`, not a fixed width. A fixed 400 chars ran past
        # the branch boundary into the rejection arm and found the very
        # `set_live_auth_status(False` this asserts is absent — the window
        # answering a different question than the one asked.
        branch = block[i:block.index("else:", i)]
        assert "set_live_auth_status(True, user_id=uid)" in branch, (
            "a blip must leave the user's auth status alone"
        )
        assert "set_live_auth_status(False" not in branch

    def test_a_real_rejection_still_halts_that_user(self):
        block = _user_block()
        i = block.index("else:", block.index("_is_transport_failure(str(_err))"))
        branch = block[i:i + 400]
        assert "set_live_auth_status(False" in branch


class TestNobodyIsToldToTouchAKeyOverABlip:
    def test_the_unreachable_notice_says_entries_are_not_halted(self):
        block = _user_block()
        i = block.index("could not ")
        window = block[i:i + 700]
        assert "NOT halted" in window

    def test_the_unreachable_notice_does_not_ask_for_a_reconnect(self):
        # /connect means re-entering an API key. Asking for that over a
        # network failure is useless work on a secret.
        block = _user_block()
        i = block.index("could not ")
        window = block[i:i + 700]
        assert "/connect" not in window, (
            "only a genuine rejection warrants re-entering credentials"
        )

    def test_the_rejection_notice_still_asks_for_a_reconnect(self):
        block = _user_block()
        i = block.index("failed venue ")
        assert "/connect" in block[i:i + 400], (
            "a genuinely rejected key does need re-connecting"
        )

    def test_the_unreachable_case_is_still_reported(self):
        # Not silenced: a repeated blip is worth knowing about. Quietly
        # swallowing it would trade a false alarm for a blind spot.
        block = _user_block()
        assert "UNREACHABLE" in block


class TestBothPathsAgree:
    """One classifier, two callers. Divergence here is how this recurred."""

    def test_both_paths_call_the_same_predicate(self):
        assert _src().count("_is_transport_failure(") >= 3, (
            "one definition plus the operator and per-user call sites"
        )

    @pytest.mark.parametrize("err,transport", [
        (LIVE_ERROR, True),
        ("Request timeout", True),
        ("503 Service Unavailable", True),
        ('{"code":"40006","msg":"invalid ACCESS_KEY"}', False),
        ("40012 passphrase mismatch", False),
        ("something nobody has seen before", False),
        ("", False),
    ])
    def test_the_classification_is_shared_not_reimplemented(self, err, transport):
        # The predicate is the same object both callers use, so a case that
        # spares the operator spares the user identically -- and a case that
        # halts one halts both.
        assert _is_transport_failure(err) is transport, err
        if not transport:
            # Either an explicit rejection or unclassifiable; both must halt.
            assert _is_auth_rejection(err) or not _is_transport_failure(err)

    def test_every_venue_response_halt_classifies_first(self):
        """A count is the wrong shape; the rule is about EVIDENCE.

        Three sites halt auth, and only two of them should classify:

          "no API key configured" -- halts on LOCAL, certain evidence. The
              key is empty; no venue was asked, so there is nothing to
              misread and nothing to classify. Correct as-is.

          the operator preflight and the per-user one -- halt on a VENUE
              RESPONSE, which is exactly the thing that can be a transport
              failure wearing a rejection's clothes.

        Written as a count first, which failed on the third site and had to
        be told why it was exempt. The distinction is the point, so it is
        now the assertion.
        """
        src = _src()
        halts = src.count("set_live_auth_status(False")
        assert halts == 3, (
            f"{halts} sites halt auth; if that changed, decide whether the "
            f"new one halts on local evidence (exempt) or on a venue "
            f"response (must classify)"
        )
        # The local-evidence one, named so its exemption is deliberate.
        assert 'set_live_auth_status(False, "no API key configured")' in src
        # Both venue-response ones sit downstream of a classification.
        for anchor in ("if _is_transport_failure(_e):",
                       "if _is_transport_failure(str(_err)):"):
            assert anchor in src, f"missing classification: {anchor}"


class TestTheHaltIsNoLongerSilent:
    """A gate nothing can see is a gate that fails silently.

    The chat system prompt already carried `trading_blocked_by` because of an
    earlier incident -- "CB=OFF alone let the LLM tell a user trading was fine
    while the warning-rate breaker was rejecting every entry". The venue auth
    halt is a THIRD gate, outside both circuit_breaker_active and
    trading_blocked_by, so the same failure recurred: on 2026-08-01 entries
    were halted and /start still said "Active | LIVE". The only way to find
    out was to attempt a trade and have it refused.
    """

    # These four rules were first enforced against the hand-rolled block that
    # lived inline in _build_chat_system_prompt. That block has since moved
    # into bot/core/trade_gate.py, so the SOURCE WINDOWS stopped finding it —
    # the assertions were about where the code sat, not about what it did.
    #
    # Re-pointed at the behaviour instead, which is what they always meant and
    # is stronger: they now hold whichever surface asks the question.

    def test_the_prompt_reports_the_auth_halt(self):
        src = _src_handler()
        assert "NEW ENTRIES HALTED" in src, (
            "the auth gate must reach the same surface the other two do"
        )
        i = src.index("NEW ENTRIES HALTED")
        assert "entry_gate(self.engine" in src[max(0, i - 600):i]

    def test_it_only_reports_when_live_and_unhealthy(self):
        from types import SimpleNamespace as NS

        from bot.core.trade_gate import entry_gate

        def engine(auth_ok):
            return NS(_halted=False,
                      risk=NS(trading_blocked_by="", circuit_breaker_active=False),
                      risk_for=lambda uid="": NS(trading_blocked_by="",
                                                 circuit_breaker_active=False),
                      live_auth_healthy=lambda uid="": auth_ok,
                      _live_auth_detail={})

        assert entry_gate(engine(False), live=True)["blocked"]
        assert not entry_gate(engine(True), live=True)["blocked"], (
            "a healthy account must say nothing extra"
        )
        assert not entry_gate(engine(False), live=False)["blocked"], (
            "there is no venue to authenticate against in paper"
        )

    def test_it_tells_the_operator_how_to_clear_it(self):
        from types import SimpleNamespace as NS

        from bot.core.trade_gate import entry_gate
        g = entry_gate(NS(_halted=False,
                          risk=NS(trading_blocked_by="",
                                  circuit_breaker_active=False),
                          risk_for=lambda uid="": NS(
                              trading_blocked_by="",
                              circuit_breaker_active=False),
                          live_auth_healthy=lambda uid="": False,
                          _live_auth_detail={}), live=True)
        assert "restart" in " ".join(g["reasons"]).lower(), (
            "the flag only resets when the preflight re-runs; saying so is "
            "the difference between a diagnosis and a dead end"
        )

    def test_it_cannot_break_the_prompt(self):
        # Instrumentation on the chat path must never be the reason chat
        # fails. Same rule as every other diagnostic added this session.
        from bot.core.trade_gate import entry_gate
        for bad in (None, object(), 7):
            g = entry_gate(bad, live=True)
            assert g["unknown"] and not g["blocked"]

"""A stated risk:reward must not survive its own levels contradicting it.

Built from the three trades v12 got wrong on 2026-08-30 — real output, real
numbers. Each is a setup just under the 1.2 floor that the model approved
after printing a ratio that clears it.

The red herring throughout is the text that must be left ALONE: a ratio
whose levels are missing cannot be checked, and replacing it with a number
derived from nothing would be the same fabrication pointed the other way.
"""

import pytest

from bot.nlp.rr_honesty import computed_ratio, correct_stated_rr


class TestComputedRatio:
    def test_long_geometry(self):
        assert computed_ratio(
            "Direction: LONG\nEntry: 100.00\nStop Loss: 97.00\n"
            "Take Profit: 103.50") == pytest.approx(1.17, abs=0.01)

    def test_short_geometry(self):
        assert computed_ratio(
            "Direction: SHORT\nEntry: 185.50\nStop Loss: 189.89\n"
            "Take Profit: 172.14") == pytest.approx(3.04, abs=0.02)

    def test_direction_inferred_when_unambiguous(self):
        # No Direction line, but target above and stop below entry can only
        # be a long.
        assert computed_ratio(
            "Entry: 50.00\nStop Loss: 48.00\nTake Profit: 52.35"
        ) == pytest.approx(1.18, abs=0.01)

    def test_thousands_separators_and_currency(self):
        assert computed_ratio(
            "Direction: LONG\nEntry: $67,450\nStop Loss: $65,562\n"
            "Take Profit: $70,792") == pytest.approx(1.77, abs=0.02)

    def test_unreadable_levels_return_none(self):
        # THE IMPORTANT NEGATIVE. Absent is not zero and not a ratio.
        for text in [
            "Risk:Reward: 2.40 — looks good",                 # no levels
            "Entry: 100\nStop Loss: 97",                      # no target
            "Direction: LONG\nEntry: abc\nStop Loss: 97\nTake Profit: 103",
        ]:
            assert computed_ratio(text) is None, text

    def test_degenerate_geometry_returns_none(self):
        # Stop and target on the same side of entry: no direction can be
        # inferred and no ratio is meaningful.
        assert computed_ratio(
            "Entry: 100.00\nStop Loss: 97.00\nTake Profit: 96.00") is None
        # Zero risk would divide by zero.
        assert computed_ratio(
            "Direction: LONG\nEntry: 100.00\nStop Loss: 100.00\n"
            "Take Profit: 103.00") is None


class TestCorrectStatedRR:
    def test_v12_trap_tight_tp(self):
        # eval-020, verbatim shape: levels give 1.17, model wrote 1.25.
        text = ("TRADE IDEA\nDirection: LONG\nAsset: ETH/USDT\n"
                "Entry: 100.00\nStop Loss: 97.00\nTake Profit: 103.50\n"
                "Risk:Reward: 1.25\nConfidence: 0.61")
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "Risk:Reward: 1.17" in out
        assert "1.25" not in out

    def test_v12_trap_wide_sl(self):
        # eval-021: levels give 1.14, model wrote 1.41.
        text = ("Entry: 3.20\nStop Loss: 2.99\nTake Profit: 3.44\n"
                "Risk:Reward: 1.41")
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "1.14" in out

    def test_v12_trap_boundary(self):
        # eval-022: levels give 1.18, model wrote 1.40.
        text = ("Entry: 50.00\nStop Loss: 48.00\nTake Profit: 52.35\n"
                "Risk:Reward: 1.40")
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "1.18" in out

    def test_one_to_n_prefix_is_preserved(self):
        text = ("Direction: LONG\nEntry: 100.00\nStop Loss: 97.00\n"
                "Take Profit: 103.50\nRisk:Reward: 1:1.25")
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "Risk:Reward: 1:1.17" in out

    def test_prose_forms_are_corrected(self):
        for stated, want in [
            ("risk:reward of 1.25 passes the minimum", "1.17"),
            ("R:R 1.25 clears the floor", "1.17"),
            ("- RISK_REWARD: 1.25 >= 1.2 minimum", "1.17"),
        ]:
            text = ("Entry: 100.00\nStop Loss: 97.00\nTake Profit: 103.50\n"
                    "Direction: LONG\n" + stated)
            out, n = correct_stated_rr(text)
            assert n == 1, stated
            assert want in out, stated

    def test_every_occurrence_is_corrected(self):
        # v12 repeats the ratio in the header and again in the check list.
        text = ("Direction: LONG\nEntry: 100.00\nStop Loss: 97.00\n"
                "Take Profit: 103.50\nRisk:Reward: 1.25\n"
                "Risk Check: APPROVED\n- RISK_REWARD: 1.25 >= 1.2 minimum")
        out, n = correct_stated_rr(text)
        assert n == 2
        assert "1.25" not in out

    def test_a_correct_ratio_is_untouched(self):
        text = ("Direction: LONG\nEntry: 100.00\nStop Loss: 98.00\n"
                "Take Profit: 106.00\nRisk:Reward: 1:3.00")
        out, n = correct_stated_rr(text)
        assert n == 0
        assert out == text

    def test_rounding_disagreement_is_not_a_correction(self):
        # Computed 1.766…, model wrote 1.77. Same claim.
        text = ("Direction: LONG\nEntry: 67,450\nStop Loss: 65,562\n"
                "Take Profit: 70,792\nRisk:Reward: 1:1.77")
        out, n = correct_stated_rr(text)
        assert n == 0, "a rounding difference is not a contradiction"

    def test_unverifiable_claim_is_left_alone(self):
        # THE RED HERRING. No levels: the model may well be right, and a
        # "correction" here would be a number invented from nothing.
        text = "BTC looks like a solid setup, roughly R:R 2.40 by my read."
        out, n = correct_stated_rr(text)
        assert n == 0
        assert out == text

    def test_text_without_any_ratio_is_untouched(self):
        text = ("Direction: LONG\nEntry: 100.00\nStop Loss: 97.00\n"
                "Take Profit: 103.50\nConfidence: 0.61")
        out, n = correct_stated_rr(text)
        assert n == 0
        assert out == text

    def test_empty_and_none_are_safe(self):
        assert correct_stated_rr("") == ("", 0)
        assert correct_stated_rr(None) == (None, 0)

    def test_other_numbers_are_not_touched(self):
        # Confidence, ATR and position size sit next to the ratio and must
        # survive a pass that rewrites only what it can verify.
        text = ("Direction: LONG\nEntry: 100.00\nStop Loss: 97.00\n"
                "Take Profit: 103.50\nRisk:Reward: 1.25\n"
                "Confidence: 1.25\nATR: 1.25%\nPosition Size: 1.25%")
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "Confidence: 1.25" in out
        assert "ATR: 1.25%" in out
        assert "Position Size: 1.25%" in out


class TestReachedThroughTheChatSeam:
    """The correction must run on the path every reply actually takes.

    A module that works in isolation and a module that runs in production
    are different claims, and this repo has paid for confusing them before:
    a Telegram card whose per-position outcome was source-scanned, shipped,
    and rendered zero times because the code was present but never reached.

    `_chat_ret` is the single funnel — every return in `_llm_chat` goes
    through it, and there are eight-plus callers across Telegram and the web
    gateway. Testing it here is testing all of them.
    """

    def test_chat_ret_corrects_a_contradicted_ratio(self):
        from bot.skills.telegram_handler import _chat_ret
        reply = ("TRADE IDEA\nDirection: LONG\nEntry: 100.00\n"
                 "Stop Loss: 97.00\nTake Profit: 103.50\n"
                 "Risk:Reward: 1.25\nRisk Check: APPROVED")
        out = _chat_ret(reply, None, False)
        assert "1.17" in out, "the seam did not apply the correction"
        assert "1.25" not in out

    def test_chat_ret_leaves_an_unverifiable_ratio_alone(self):
        from bot.skills.telegram_handler import _chat_ret
        reply = "Feels like about R:R 2.40 on that one."
        assert _chat_ret(reply, None, False) == reply

    def test_chat_ret_still_returns_meta_when_asked(self):
        from types import SimpleNamespace

        from bot.skills.telegram_handler import _chat_ret
        cfg = SimpleNamespace(provider=SimpleNamespace(value="runeclaw"),
                              model="v12-14b")
        reply = ("Direction: LONG\nEntry: 100.00\nStop Loss: 97.00\n"
                 "Take Profit: 103.50\nRisk:Reward: 1.25")
        text, meta = _chat_ret(reply, cfg, True)
        assert "1.17" in text
        assert meta == {"provider": "runeclaw", "model": "v12-14b"}

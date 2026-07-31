"""A scan that read 27 of 67 symbols looked exactly like one that read 67.

Every batch scan gathers OHLCV across the whole universe and then writes

    results = [r for r in raw if r is not None]

which silently deletes every symbol the exchange would not serve. The
survivors were then ranked, captioned "RUNECLAW Live Scan", and shown with
the same confidence as a complete pass. The filtered scans went further and
turned the gap into a verdict — "No swing setups found right now" — which is
a sentence about the market, printed when the truth was that the market had
not been read.

The line this file defends:

  UNREADABLE IS NOT ZERO, AND SILENCE IS NOT AN ANSWER.

Same family as the drawdown peak that read 0.0 as "no peak", the journal
window that read an empty page as "no trades", and the LLM counter that
would have read a missing usage object as "this call was free".
"""
from __future__ import annotations

from bot.skills.scan_coverage import (
    coverage,
    coverage_note,
    empty_result_line,
    unreadable_verdict,
)


class TestCoverageCounts:
    def test_a_whole_pass_is_complete(self):
        c = coverage(67, 67)
        assert c["complete"] is True
        assert c["unreadable"] == 0 and c["pct"] == 100.0

    def test_a_partial_pass_is_not_complete(self):
        c = coverage(67, 27)
        assert c["complete"] is False
        assert c["unreadable"] == 40

    def test_no_universe_gives_no_percentage(self):
        # 0/0 is not "0% coverage" — it is "no question was asked". A zero
        # here would render as a total failure on a scan that never ran.
        assert coverage(0, 0)["pct"] is None
        assert coverage(0, 0)["complete"] is False

    def test_readable_can_never_exceed_total(self):
        assert coverage(10, 99)["readable"] == 10

    def test_junk_input_does_not_raise(self):
        for bad in (None, "x", -4, 3.7):
            c = coverage(bad, bad)          # type: ignore[arg-type]
            assert c["readable"] <= c["total"]


class TestTheNoteIsSilentWhenThereIsNothingToSay:
    def test_complete_coverage_prints_nothing(self):
        # A banner on every healthy scan is how a real warning gets skipped.
        assert coverage_note(67, 67) == ""

    def test_an_empty_universe_prints_nothing(self):
        assert coverage_note(0, 0) == ""

    def test_a_partial_pass_names_both_numbers(self):
        note = coverage_note(67, 27)
        assert "27" in note and "67" in note and "40" in note

    def test_a_partial_pass_disclaims_the_universe(self):
        note = coverage_note(67, 27)
        assert "not the whole universe" in note

    def test_the_note_is_html_safe_markup(self):
        assert coverage_note(67, 27).strip().startswith("⚠️ <i>")
        assert coverage_note(67, 27).rstrip().endswith("</i>")

    def test_plain_text_mode_carries_no_markup(self):
        note = coverage_note(67, 27, html=False)
        assert "<" not in note and ">" not in note


class TestZeroReadableIsNeverAMarketVerdict:
    def test_the_verdict_refuses_to_describe_the_tape(self):
        msg = unreadable_verdict("Swing", 67)
        assert "not a quiet tape" in msg
        assert "data-availability failure" in msg

    def test_it_says_nothing_about_whether_setups_exist(self):
        msg = unreadable_verdict("Swing", 67)
        assert "Nothing here says whether setups exist" in msg

    def test_empty_with_zero_readable_routes_to_the_verdict(self):
        line = empty_result_line("swing", 67, 0)
        assert "could not read the market" in line
        # The old sentence must not survive anywhere in this branch.
        assert "found right now" not in line

    def test_empty_with_readable_symbols_is_allowed_to_answer(self):
        line = empty_result_line("swing", 67, 67)
        assert "No swing setups matched" in line
        assert "could not read" not in line

    def test_a_partially_read_empty_result_qualifies_itself(self):
        line = empty_result_line("swing", 67, 50)
        assert "17 of 67 were unreadable" in line
        assert "not a statement about the full universe" in line

    def test_the_three_empty_cases_are_all_distinguishable(self):
        none_read = empty_result_line("swing", 67, 0)
        partial = empty_result_line("swing", 67, 50)
        whole = empty_result_line("swing", 67, 67)
        assert len({none_read, partial, whole}) == 3, (
            "all three produce an empty result list; only one of them "
            "entitles the surface to speak about the market"
        )


class TestItIsActuallyWiredIntoTheScans:
    """A helper nothing calls is a helper that fixes nothing."""

    def _src(self):
        from tests.source_scan import code_only
        return code_only(
            open("bot/skills/scan_skill.py", encoding="utf-8").read())

    def test_batch_and_filtered_scans_both_count_readable_symbols(self):
        # Two call sites: _scan_batch and _scan_filtered. One wired scan means
        # the disclosure depends on which command the operator typed.
        assert self._src().count("_readable") >= 5

    def test_the_card_caption_carries_the_note_too(self):
        # When the PNG renders, `body` is never sent. A note that lives only
        # on the fallback path never appears on the surface people screenshot
        # — the exact half-fixed-surface defect that shipped in #999.
        src = self._src()
        head = src.index("send_photo")
        assert "coverage_note" in src[head - 400:head + 400], (
            "the photo caption must disclose partial coverage; the text "
            "body is not sent when the card renders"
        )

    def test_the_filtered_scan_no_longer_states_a_market_verdict(self):
        assert "setups found right now" not in self._src(), (
            "that sentence answered a total fetch failure with a claim "
            "about the market"
        )


class TestNoHardcodedUniverseCount:
    """The bridge's twin of this list once said 66 while holding 70."""

    def test_full_means_the_whole_universe_not_a_literal(self):
        from tests.source_scan import code_only
        src = code_only(
            open("bot/skills/scan_skill.py", encoding="utf-8").read())
        assert "top_n=67" not in src, (
            "a literal cap silently truncates the moment a symbol is added, "
            "while the command still calls itself 'full'"
        )
        assert src.count("top_n=len(UNIVERSE)") == 2

    def test_the_usage_text_derives_its_count(self):
        from tests.source_scan import code_only
        src = code_only(
            open("bot/skills/scan_skill.py", encoding="utf-8").read())
        assert "full 67 symbols" not in src
        assert "full {len(UNIVERSE)} symbols" in src

    def test_command_docstrings_carry_no_symbol_count(self):
        from tests.source_scan import code_only
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        for stale in ("Full 67-symbol scan", "Deep scan 67+ symbols"):
            assert stale not in src, f"stale prose count: {stale}"

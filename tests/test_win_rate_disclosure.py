"""#1020 fixed the arithmetic and shipped the disclosure half-built.

The win rate stopped counting unpriced closes as defeats. Then the corrected
rate was printed beside a total it no longer covered, and the count that
tells them apart was written and never read:

    bot/utils/win_rate.py::coverage_note   — defined, called by nothing
    live_stats.live_win_stats()["unscored"] — set, read by nothing

That is exactly the rule #1018 added, applied to my own work one PR later:

    A TALLY NOTHING READS ANSWERS NOTHING.

"Trades: 20" and "Win rate: 60%" side by side is a claim about the
denominator. After #1020 the rate runs over however many closes carried a
recorded P&L, so the pair says something the code no longer means — the
half-fixed surface again, except this time the two halves were the number
and its own caveat.

Every live surface that prints a rate now prints what it covers, and stays
silent when it covers everything.
"""
from __future__ import annotations

from bot.formatters.rich_cards import render_live_portfolio_summary
from bot.utils.win_rate import coverage_note, win_stats


class TestTheRendererDisclosesItsWindow:
    def test_a_complete_set_says_nothing_extra(self):
        out = render_live_portfolio_summary(100, 1, 50, 10, 20, 60)
        assert not any("covers" in ln for ln in out), (
            "a caveat on every healthy card is how a real one gets skipped"
        )

    def test_a_partial_set_names_both_counts(self):
        out = "\n".join(render_live_portfolio_summary(
            100, 1, 50, 10, 20, 60, unscored=8))
        assert "12 of 20" in out
        assert "8 carry no recorded" in out

    def test_the_rate_and_the_total_still_render(self):
        # The disclosure adds to the card; it must not replace the numbers.
        out = "\n".join(render_live_portfolio_summary(
            100, 1, 50, 10, 20, 60, unscored=8))
        assert "Trades: <code>20</code>" in out
        assert "Win rate: <code>60%</code>" in out

    def test_the_default_keeps_old_callers_silent(self):
        # unscored defaults to 0 so a caller with nothing to disclose is
        # byte-identical to before.
        assert (render_live_portfolio_summary(100, 1, 50, 10, 20, 60)
                == render_live_portfolio_summary(100, 1, 50, 10, 20, 60,
                                                 unscored=0))

    def test_a_nonsense_unscored_does_not_break_the_card(self):
        for bad in (-5, None, "x"):
            out = render_live_portfolio_summary(
                100, 1, 50, 10, 20, 60, unscored=bad)   # type: ignore[arg-type]
            assert any("Win rate" in ln for ln in out), bad

    def test_no_closes_means_no_line_and_no_note(self):
        out = render_live_portfolio_summary(100, 0, 0, 0, 0, 0, unscored=3)
        assert not any("Win rate" in ln for ln in out)
        assert not any("covers" in ln for ln in out)


class TestTheHelpersAreActuallyRead:
    """The rule this file exists for. A definition is not a wiring."""

    def _src(self, path):
        from tests.source_scan import code_only
        return code_only(open(path, encoding="utf-8").read())

    def test_coverage_note_has_a_caller(self):
        src = self._src("bot/formatters/rich_cards.py")
        assert "coverage_note as _wr_note" in src, (
            "it was defined in #1020 and called by nothing"
        )

    def test_the_unscored_count_reaches_the_renderer(self):
        src = self._src("bot/skills/skill_registry.py")
        assert 'unscored=_stats.get("unscored", 0)' in src, (
            "live_win_stats set the field; nothing passed it on"
        )

    def test_the_wl_lines_carry_the_unpriced_tag(self):
        src = self._src("bot/skills/telegram_handler.py")
        assert "def _unpriced_tag(" in src
        assert "_unpriced_tag(_ws)" in src, (
            "a W/L pair reads as the whole set unless the remainder is named"
        )

    def test_the_performance_card_labels_its_denominator(self):
        # "Win Rate" and "Trades" are neighbouring tiles; without this the
        # pair implies a denominator the rate never used.
        src = self._src("bot/skills/telegram_handler.py")
        assert '"win_rate_scored"' in src
        assert 'f"Win Rate (of {data.get(\'win_rate_scored\', 0)})"' in src


class TestTheTagItself:
    def test_it_is_silent_on_a_clean_set(self):
        from bot.skills.telegram_handler import _unpriced_tag
        assert _unpriced_tag(win_stats([])) == ""
        assert _unpriced_tag({"unscored": 0}) == ""

    def test_it_names_the_remainder(self):
        from bot.skills.telegram_handler import _unpriced_tag
        assert "+2 unpriced" in _unpriced_tag({"unscored": 2})

    def test_it_never_raises(self):
        from bot.skills.telegram_handler import _unpriced_tag
        for bad in (None, {}, {"unscored": "x"}, {"unscored": None}, 7):
            assert _unpriced_tag(bad) == ""      # type: ignore[arg-type]


class TestTheWordingIsSharedNotRetyped:
    def test_the_note_comes_from_one_place(self):
        # Two surfaces, one sentence. Retyping it is how two surfaces end up
        # describing the same rule differently.
        note = coverage_note({"unscored": 8, "total": 20, "scored": 12})
        rendered = "\n".join(render_live_portfolio_summary(
            100, 1, 50, 10, 20, 60, unscored=8))
        assert note.strip() in rendered

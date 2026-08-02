"""The open-positions header summed percentages and overstated the loss 3.3x.

On 2026-08-02 `/open_positions` read **-47.84% total** while the book was down
**-15.4%**. The same account's `/status` said "Daily PnL -2.2%", because
-$4.60 against $186.18 of equity is -2.5%. Two surfaces, one book, a 20x
disagreement — and the wrong one was the first line on the screen.

    MSFT  -7.86%   net -$1.25   margin $12.51
    HYPE  -8.57%   net -$1.22   margin $11.38
    LTC  -33.67%   net -$2.13   margin  $5.93
    sum                        -50.10%   <- displayed
    -$4.60 / $29.82            -15.43%   <- true

THE TEST THAT MATTERS USES THREE POSITIONS OF DIFFERENT SIZES.

A sum and a weighted mean are identical when there is exactly one position, so
a single-position test passes against the broken code and proves nothing. That
is the vacuous-guard shape this audit kept finding: a test named for a defect
it cannot reach.

The live numbers above are used verbatim as the fixture, so the assertion is
against a case that actually happened rather than one invented to pass.
"""
from __future__ import annotations

import pytest

from bot.utils.portfolio_return import coverage_note, open_book_return


# The three positions exactly as they stood at 11:03 UTC on 2026-08-02.
LIVE_BOOK = [
    {"pair": "MSFTUSDT", "pnl_pct": -7.86, "pnl_usd": -1.25, "size_usd": 12.51},
    {"pair": "HYPEUSDT", "pnl_pct": -8.57, "pnl_usd": -1.22, "size_usd": 11.38},
    {"pair": "LTCUSDT", "pnl_pct": -33.67, "pnl_usd": -2.13, "size_usd": 5.93},
]


class TestTheLiveIncident:
    def test_the_book_is_down_fifteen_percent_not_fifty(self):
        book = open_book_return(LIVE_BOOK)
        assert book["pct"] == pytest.approx(-15.43, abs=0.01)

    def test_it_is_not_the_sum_of_the_displayed_percentages(self):
        # -50.10 was what the header showed. Pinned so a future "simplification"
        # back to sum() fails here instead of on the operator's screen.
        assert open_book_return(LIVE_BOOK)["pct"] != pytest.approx(-50.10, abs=0.5)

    def test_the_dollar_total_and_margin_are_reported(self):
        book = open_book_return(LIVE_BOOK)
        assert book["pnl_usd"] == pytest.approx(-4.60, abs=0.01)
        assert book["margin_usd"] == pytest.approx(29.82, abs=0.01)

    def test_the_caller_can_derive_the_equity_return(self):
        # /status showed -2.2% because it divides by equity, not margin. Both
        # readings are legitimate; margin_usd is returned so a caller holding
        # an equity figure never has to re-derive the P&L.
        book = open_book_return(LIVE_BOOK)
        assert (book["pnl_usd"] / 186.18 * 100) == pytest.approx(-2.47, abs=0.01)


class TestSummingIsWrongWheneverSizesDiffer:
    def test_three_equal_losses_do_not_triple(self):
        # The clearest statement of the bug: each position down 10% of its own
        # margin means the BOOK is down 10%, not 30%.
        book = open_book_return([
            {"pnl_usd": -1.0, "size_usd": 10.0},
            {"pnl_usd": -2.0, "size_usd": 20.0},
            {"pnl_usd": -3.0, "size_usd": 30.0},
        ])
        assert book["pct"] == pytest.approx(-10.0)

    def test_a_tiny_disaster_does_not_dominate_a_large_book(self):
        # Summing percentages let a $5 position at -90% outweigh a $500 one at
        # +1%. Dollar-weighting puts them in proportion.
        book = open_book_return([
            {"pnl_usd": -4.5, "size_usd": 5.0},      # -90%
            {"pnl_usd": 5.0, "size_usd": 500.0},     # +1%
        ])
        assert book["pct"] == pytest.approx(0.5 / 505.0 * 100, abs=0.01)
        assert book["pct"] > -1.0, "a small position must not swamp the book"

    def test_one_position_is_the_case_that_proves_nothing(self):
        # Sum and weighted mean agree here — documented so nobody mistakes this
        # for coverage of the defect.
        book = open_book_return([{"pnl_usd": -1.25, "size_usd": 12.51}])
        assert book["pct"] == pytest.approx(-9.99, abs=0.01)


class TestAbsentIsNeverZero:
    def test_an_unreadable_mark_leaves_both_sides(self):
        book = open_book_return([
            {"pnl_usd": -1.25, "size_usd": 12.51},
            {"pnl_usd": None, "size_usd": 100.0},    # price_unavailable
        ])
        # 100.0 in the denominator only would have dragged -9.99% to -1.11%.
        assert book["pct"] == pytest.approx(-9.99, abs=0.01)
        assert book["measured"] == 1 and book["unmeasured"] == 1

    @pytest.mark.parametrize("book", [
        [],
        [{"pnl_usd": None, "size_usd": 10.0}],
        [{"pnl_usd": -1.0, "size_usd": 0}],
        [{"pnl_usd": -1.0, "size_usd": None}],
        [{"pnl_usd": float("nan"), "size_usd": 10.0}],
        [{"pnl_usd": float("inf"), "size_usd": 10.0}],
        [{}],
    ])
    def test_nothing_priceable_returns_None_not_zero(self, book):
        # 0.0 would claim the book is exactly flat. "We could not read these"
        # is a different statement and the header must render it as "—".
        assert open_book_return(book)["pct"] is None

    def test_it_never_raises_on_junk(self):
        assert open_book_return([None, 42, "x"])["pct"] is None
        assert open_book_return(None)["pct"] is None


class TestTheCoverageNote:
    def test_silent_when_everything_was_priced(self):
        assert coverage_note(open_book_return(LIVE_BOOK)) == ""

    def test_speaks_when_something_was_not(self):
        book = open_book_return(LIVE_BOOK + [{"pnl_usd": None, "size_usd": 5.0}])
        note = coverage_note(book)
        assert "3 of 4" in note and "neither way" in note

    def test_survives_a_malformed_argument(self):
        assert coverage_note({}) == ""
        assert coverage_note(None) == ""

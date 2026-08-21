"""The cure was applied to one surface and the disease left on its twin.

/open_positions stopped rendering an unfetched price as +0.00%. /portfolio,
which shows the same book, still did this:

    live_unrealized = 0.0
    for lp in live_open:
        if hasattr(lp, 'pnl_usd') and lp.pnl_usd:
            live_unrealized += lp.pnl_usd

Two defects in two lines, and they are the two this codebase keeps meeting:

  * `and lp.pnl_usd` is a FALSINESS test, so a position marked at exactly
    break-even was skipped alongside one that was never marked at all;
  * an unmarked position then contributed nothing to a sum that was
    rendered as THE unrealized total, with a green or red icon on it.

Three marked positions out of five produced a confident number computed
from three. A partial sum presented as a whole one is a wrong number
wearing a measured number's authority.

The half-fixed surface is now this repo's most repeated defect -- #990's
PNG tile against its text twin, #999's adoption detail, #1010's /risk,
#1014's four timeout paths, and this. Fixing a surface is not finishing a
defect; the question is always which OTHER surface makes the same claim.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from tests.source_scan import code_only


def _src() -> str:
    return code_only(
        open("bot/skills/telegram_handler.py", encoding="utf-8").read())


def _portfolio_block() -> str:
    src = _src()
    i = src.index("live_unrealized = 0.0")
    return src[i - 900:i + 2600]


class TestUnmarkedPositionsAreNotCountedAsZero:
    def test_the_falsiness_test_is_gone(self):
        assert "and lp.pnl_usd:" not in _src(), (
            "0.0 is falsy and 0.0 is a real break-even mark; that test "
            "discarded a measurement and a gap with the same branch"
        )

    def test_the_mark_is_tested_for_absence(self):
        block = _portfolio_block()
        assert "if _u is None:" in block, (
            "test `is None`, not truthiness -- the rule this file exists for"
        )

    def test_marked_positions_are_counted(self):
        block = _portfolio_block()
        assert "_marked += 1" in block
        assert "_unmarked = len(live_open) - _marked" in block


class TestThePartialTotalDisclosesItself:
    def test_a_short_count_is_reported(self):
        block = _portfolio_block()
        assert "total_partial" in block, (
            "a total computed from three of five positions must say so"
        )

    def test_no_marks_at_all_renders_unknown_not_zero(self):
        block = _portfolio_block()
        assert "_marked == 0" in block
        assert "pnl_unknown" in block, (
            "$0.00 and a silent omission both read as 'nothing is riding on "
            "the book'"
        )

    def test_a_complete_book_adds_no_caveat(self):
        # The caveat must sit behind a shortfall check, so a fully readable
        # book is quiet. A banner on every healthy render is how a real one
        # gets skipped.
        #
        # THIS ASSERTION USED `block.index("total_partial")` — the FIRST
        # occurrence — and asserted `if _unmarked:` preceded it. That pinned
        # ONE site and read as though it pinned the rule. When the REALIZED
        # total gained the same caveat (guarded by `if _unpriced_closes:`) it
        # landed earlier in the function, `index()` found the new one, and the
        # test failed over a correctly guarded line while still never checking
        # the second. Same shape as the `/portfolio` label pinned by grepping
        # the file that builds it: it passed with the label present AND moved
        # below the thing it labelled.
        #
        # Every occurrence now, so a third caveat is covered the day it is
        # written rather than the day someone remembers.
        block = _portfolio_block()
        guards = ("if _unmarked:", "if _unpriced_closes:")
        sites = [i for i in range(len(block))
                 if block.startswith("total_partial", i)]
        assert sites, "the caveat vanished entirely"
        for i in sites:
            window = block[max(0, i - 200):i]
            assert any(g in window for g in guards), (
                f"a total_partial caveat at offset {i} is not behind a "
                f"shortfall check; one of {guards} must precede it:\n"
                f"...{window[-160:]}"
            )


class TestTheArithmeticItself:
    """The rule, exercised rather than only scanned for."""

    @staticmethod
    def _sum(positions):
        # Mirrors the handler's loop; kept in step by the source assertions
        # above, which fail if the handler's shape drifts from this.
        total, marked = 0.0, 0
        for lp in positions:
            u = getattr(lp, "pnl_usd", None)
            if u is None:
                continue
            total += float(u)
            marked += 1
        return total, marked, len(positions) - marked

    def test_a_real_break_even_is_counted(self):
        total, marked, unmarked = self._sum([NS(pnl_usd=0.0)])
        assert marked == 1 and unmarked == 0
        assert total == 0.0

    def test_an_absent_mark_is_excluded_not_zeroed(self):
        total, marked, unmarked = self._sum([NS(pnl_usd=None), NS(pnl_usd=12.0)])
        assert total == 12.0
        assert (marked, unmarked) == (1, 1)

    def test_a_position_with_no_attribute_at_all_is_excluded(self):
        total, marked, unmarked = self._sum([NS(), NS(pnl_usd=-4.0)])
        assert total == -4.0
        assert (marked, unmarked) == (1, 1)

    def test_break_even_and_unmarked_produce_different_counts(self):
        # Same total, different truth. The count is what separates them.
        a = self._sum([NS(pnl_usd=0.0)])
        b = self._sum([NS(pnl_usd=None)])
        assert a[0] == b[0] == 0.0
        assert a[1:] != b[1:], (
            "a zero total from one measured flat position and a zero total "
            "from one unread position must not be the same reading"
        )


class TestStockScanDisclosesItsCoverage:
    def test_unpriced_stock_perps_are_counted(self):
        src = _src()
        i = src.index("_stock_seen = 0")
        block = src[i:i + 3400]
        assert "_stock_seen += 1" in block
        assert "coverage_note(_stock_seen, len(stock_signals))" in block, (
            "heading the card with the surviving count presents a partial "
            "read as the whole board"
        )

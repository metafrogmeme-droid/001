"""The realized totals, as a thing that can be exercised rather than grepped.

`/portfolio` computed these inline with `sum((p.pnl_usd or 0) for p in ...)`,
five lines below the UNREALIZED total that had already been rewritten to count
what marked and say so when the count was short — under the sentence "a partial
sum presented as a whole one is a wrong number wearing a measured number's
authority". The realized total is the bigger claim, because it is the money
already gone, and it kept the defect.

Nothing could plant an unpriced close and read what the card would say, because
the arithmetic was welded into a 13,500-line handler. `realized_totals` is the
seam. The rule this pins: an unreadable total is None, and None has no colour.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.formatters.realized_totals import realized_totals


def _t(pnl=None, commission=None, gross=None):
    return SimpleNamespace(pnl_usd=pnl, commission=commission, gross_pnl=gross)


class TestAnUnreadableTotalIsNoneNotZero:
    def test_all_unpriced_is_none(self):
        r = realized_totals([_t(), _t(), _t()])
        assert r["net"] is None, "three unpriced closes are not a break-even book"
        assert r["priced"] == 0
        assert r["unpriced"] == 3

    def test_an_empty_book_is_a_readable_zero(self):
        """The distinction the whole module turns on. No closes is a real,
        measured $0.00 — collapsing it into 'unreadable' would be the same
        defect pointing the other way."""
        r = realized_totals([])
        assert r["net"] == 0.0
        assert r["unpriced"] == 0

    def test_a_partial_book_sums_only_what_was_priced(self):
        r = realized_totals([_t(pnl=10.0), _t(), _t(pnl=-4.0), _t()])
        assert r["net"] == pytest.approx(6.0)
        assert r["priced"] == 2
        assert r["unpriced"] == 2

    def test_a_measured_zero_is_kept(self):
        """0.0 is falsy and is a real, measured, break-even position."""
        r = realized_totals([_t(pnl=0.0)])
        assert r["net"] == 0.0
        assert r["priced"] == 1, "a break-even close was priced"
        assert r["unpriced"] == 0

    def test_the_old_shape_would_have_said_zero(self):
        """The defect, stated as arithmetic, so the difference is visible.

        `sum((p.pnl_usd or 0) ...)` over three unpriced closes is 0 — a number
        that formats as `$+0.00` and colours green under `>= 0`.
        """
        rows = [_t(), _t(), _t()]
        old = sum((r.pnl_usd or 0) for r in rows)
        assert old == 0, "the shape being replaced"
        assert realized_totals(rows)["net"] is None, "the shape replacing it"


class TestFeesAndGross:
    def test_an_absent_commission_is_not_a_zero_fee(self):
        assert realized_totals([_t(pnl=1.0), _t(pnl=2.0)])["fees"] is None

    def test_fees_sum_the_rows_that_have_them(self):
        r = realized_totals([_t(pnl=1.0, commission=0.5), _t(pnl=2.0)])
        assert r["fees"] == pytest.approx(0.5)

    def test_a_break_even_gross_does_not_fall_through_to_the_net(self):
        """`gross_pnl or p.pnl_usd` was a FALSINESS test, so a gross of exactly
        0.0 — a real reading — was discarded in favour of the net."""
        r = realized_totals([_t(pnl=-3.0, gross=0.0)])
        assert r["gross"] == 0.0, "gross 0.0 is a measurement, not an absence"
        assert r["net"] == -3.0

    def test_gross_falls_back_to_net_only_when_truly_absent(self):
        r = realized_totals([_t(pnl=7.0, gross=None)])
        assert r["gross"] == 7.0


class TestBoolsAreNotMoney:
    """`isinstance(True, int)` is True in Python, so a stray bool would be
    summed as $1.00 — a fabricated dollar in a total about real money."""

    @pytest.mark.parametrize("value", [True, False])
    def test_a_bool_pnl_is_treated_as_unreadable(self, value):
        r = realized_totals([_t(pnl=value)])
        assert r["net"] is None
        assert r["unpriced"] == 1

    def test_a_bool_does_not_inflate_a_real_total(self):
        r = realized_totals([_t(pnl=5.0), _t(pnl=True)])
        assert r["net"] == pytest.approx(5.0), "True must not add 1.0"
        assert r["priced"] == 1


class TestTheCountsReconcile:
    """priced + unpriced == total, always — so a caller cannot be handed a
    breakdown that silently loses rows the way `len(all) - wins` did."""

    @pytest.mark.parametrize("rows", [
        [],
        [_t()],
        [_t(pnl=1.0)],
        [_t(pnl=1.0), _t(), _t(pnl=0.0), _t(pnl=True)],
    ])
    def test_priced_plus_unpriced_equals_total(self, rows):
        r = realized_totals(rows)
        assert r["priced"] + r["unpriced"] == r["total"] == len(rows)

    def test_it_accepts_any_iterable_once(self):
        """A generator must not be exhausted by the first of three passes —
        the inline version read `live_closed` three times."""
        rows = (_t(pnl=1.0) for _ in range(3))
        r = realized_totals(rows)
        assert r["total"] == 3
        assert r["net"] == pytest.approx(3.0)

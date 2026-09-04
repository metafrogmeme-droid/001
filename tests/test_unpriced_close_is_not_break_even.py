"""An exit price nobody could read is not the entry price.

`_close_position_inner` ended its price chain with

    if fill_price == 0:
        fill_price = pos.entry_price  # absolute fallback — no phantom PnL

and the reconcile loop did the same after ten retries. The comment has it
exactly backwards: booking the exit AT the entry produces a gross PnL of
precisely zero, which is not the absence of a measurement but the calmest
measurement there is — "this trade broke even" — printed as `-0.00% margin /
-0.00% notional` beside a fee, on a real position, assembled from three
failed reads.

The card that prompted this was honest (`_record_exchange_close` returns None
rather than falling back, and its ticker read was genuine). These two paths
are the ones that were not.

The seam is `close_pnl_line` / `close_pct`: pure, so the unreadable case can
be driven straight at the renderer instead of grepped for.
"""
import io

import pytest

from bot.core.live_executor import UNREAD, close_pct, close_pnl_line, money
from tests.source_scan import code_only


class TestCloseP0nlLine:
    def test_a_real_close_renders_exactly_as_before(self):
        pnl, pct, fee = close_pnl_line(-0.2128, -0.5, -10.0, 20, 0.21)
        assert pnl == "-$0.2128"
        assert pct == "-10.00% margin / -0.50% notional, 20×"
        assert fee == "$0.21"

    def test_a_measured_break_even_is_still_printed(self):
        # 0.0 is a real, measured, break-even close — falsy and true.
        pnl, pct, fee = close_pnl_line(0.0, 0.0, 0.0, 1, 0.0)
        assert pnl == "+$0.0000"
        assert pct == "+0.00%"
        assert fee == "$0.00"

    def test_an_unread_pnl_is_not_zero(self):
        pnl, _, _ = close_pnl_line(None, None, None, 1, None)
        assert pnl == UNREAD
        assert "0" not in pnl

    def test_an_unread_percentage_is_not_zero_and_keeps_the_leverage(self):
        _, pct, _ = close_pnl_line(None, None, None, 20, None)
        assert pct == f"{UNREAD}, 20×"
        assert "0.00%" not in pct

    def test_an_unread_fee_is_not_a_free_trade(self):
        _, _, fee = close_pnl_line(None, None, None, 1, None)
        assert fee == UNREAD
        assert fee != "$0.00"

    def test_the_three_readings_fail_independently(self):
        # The venue can report realized PnL for a close whose exit price no
        # source would give up: the money is known, the percentages are not.
        pnl, pct, fee = close_pnl_line(-1.5, None, None, 5, 0.30)
        assert pnl == "-$1.5000"
        assert pct == f"{UNREAD}, 5×"
        assert fee == "$0.30"


class TestClosePct:
    def test_a_normal_long(self):
        pct, margin = close_pct(110.0, 100.0, "LONG", 10)
        assert pct == pytest.approx(10.0)
        assert margin == pytest.approx(100.0)

    def test_a_short_inverts(self):
        pct, _ = close_pct(90.0, 100.0, "SHORT", 1)
        assert pct == pytest.approx(10.0)

    def test_an_unread_exit_is_not_minus_one_hundred_percent(self):
        # The trap this exists for: a price of 0 against an entry of 100 is
        # -100%, the largest loss the arithmetic can express, and it would be
        # printed as a measurement.
        assert close_pct(None, 100.0, "LONG", 1) == (None, None)
        assert close_pct(0.0, 100.0, "LONG", 1) == (None, None)

    def test_a_missing_entry_is_also_unpriceable(self):
        assert close_pct(100.0, 0.0, "LONG", 1) == (None, None)


class TestMoney:
    def test_unread_stays_a_word(self):
        assert money(None) == UNREAD

    def test_a_number_stays_a_number(self):
        assert money(-0.2128) == "$-0.2128"
        assert money(0.0) == "$0.0000"


class TestTheSubstitutionIsGone:
    """Wiring, not behaviour — the rendering above is the behaviour half.

    Comments are stripped first: the replacement comments at both sites say
    the words `pos.entry_price` while explaining why they are NOT used, and a
    comment that quotes the string it forbids is indistinguishable from the
    code doing it.
    """

    def _code(self):
        return code_only(
            io.open("bot/core/live_executor.py", encoding="utf-8").read())

    def test_close_position_inner_no_longer_substitutes_the_entry(self):
        assert "fill_price = pos.entry_price" not in self._code()

    def test_reconcile_no_longer_substitutes_the_entry(self):
        assert "est_exit = pos.entry_price" not in self._code()

    def test_the_unread_case_still_books_the_close(self):
        # Not closing is the opposite failure: the position IS gone from the
        # venue, so leaving it open would be its own false claim. Both sites
        # keep booking it and stop pricing it.
        code = self._code()
        assert 'gross_pnl = net_pnl = commission = None' in code
        assert code.count('gross_pnl = net_pnl = commission = None') == 2

    def test_persisted_record_carries_none_not_a_rounded_zero(self):
        code = self._code()
        assert "pos.pnl_usd = None if net_pnl is None else round(net_pnl, 4)" in code
        assert "pos.close_price = fill_price if exit_price_known else None" in code

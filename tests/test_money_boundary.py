"""Stage A: a figure at the boundary is a Decimal, or it is not a figure.

``0`` is a reading. A blank, a bool, a percent, NaN and infinity are not.
``live_executor.money`` keeps the close-card spelling (sign after ``$``,
four places, no comma) and says ``unread`` when there is nothing to print.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bot.core.live_executor import UNREAD, money
from bot.utils.money import fmt, quantize_to_tick, to_money


class TestToMoney:
    @pytest.mark.parametrize("value", [None, "", "  ", "nan", "NaN", True, False,
                                       float("nan"), float("inf"), float("-inf"),
                                       "1.2%", "$", "not-a-number"])
    def test_unreadable_is_none(self, value):
        assert to_money(value) is None

    def test_a_zero_is_a_reading(self):
        assert to_money(0) == Decimal("0")
        assert to_money("0") == Decimal("0")
        assert to_money(0.0) == Decimal("0")

    def test_a_dollar_string_loses_its_spelling(self):
        assert to_money("$1,234.50") == Decimal("1234.50")

    def test_a_float_is_read_from_its_decimal_spelling(self):
        assert to_money(0.1) == Decimal("0.1")


class TestQuantize:
    def test_half_even_on_an_exact_decimal(self):
        assert quantize_to_tick(Decimal("1.225"), Decimal("0.01")) == Decimal("1.22")
        assert quantize_to_tick(Decimal("1.235"), Decimal("0.01")) == Decimal("1.24")

    @pytest.mark.parametrize("tick", [None, 0, Decimal("0"), -1, "nan"])
    def test_a_tick_that_is_not_a_step_is_none(self, tick):
        assert quantize_to_tick(Decimal("1.20"), tick) is None

    def test_an_unreadable_amount_is_none(self):
        assert quantize_to_tick(None, Decimal("0.01")) is None


class TestFmt:
    def test_none_stays_none(self):
        assert fmt(None) is None
        assert fmt("nan") is None

    def test_the_sign_sits_in_front_of_the_prefix(self):
        assert fmt(Decimal("-1.23"), places=4, thousands=False) == "-$1.2300"

    def test_thousands_are_optional(self):
        assert fmt(1234.5, places=2, thousands=True) == "$1,234.50"
        assert fmt(1234.5, places=2, thousands=False) == "$1234.50"


class TestTheCloseCardSpelling:
    def test_unread_stays_a_word(self):
        assert money(None) == UNREAD
        assert money(float("nan")) == UNREAD

    def test_a_number_stays_a_number(self):
        assert money(-0.2128) == "$-0.2128"
        assert money(0.0) == "$0.0000"
        assert money(1234.5) == "$1234.5000"

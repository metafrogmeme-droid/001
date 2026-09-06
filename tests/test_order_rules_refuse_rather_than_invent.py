"""The weekend order rules run on the live path and had no test of any kind.

``adjust_size_for_weekend``, ``adjust_sl_for_gap_risk`` and
``should_defer_tp_sl`` are imported at ``bot/core/live_executor.py:38`` and
called inside ``execute()`` — they cut position size 35%, widen the stop 40%,
and defer stop placement until after fill. Grepping the whole suite for their
names returned nothing. The two files that import ``order_rules`` at all
exercise only its market-hours helpers.

WHAT WAS BEHIND THE GAP.

    adjust_sl_for_gap_risk(98.0, entry_price=0.0, 'LONG', 'Metal', True) -> 0.0

``if entry_price > 0 else 0`` set the stop DISTANCE to zero, and
``entry_price * (1 - 0)`` is then ``0.0``. The caller assigns on
``new_sl != old_sl``, so ``0.0 != 98.0`` took it — a long position's stop set
to zero, which is no stop, audited as ``SL widened: $98.0000 → $0.0000,
result="WIDENED"``. The removal of a stop, recorded as an improvement to it.
An unreadable STOP was worse: ``abs(entry - 0)/entry`` is 1.0, widened to 1.4,
so the "widened" stop came out NEGATIVE.

And any ``direction`` that was not exactly ``"LONG"`` fell through to the
short branch, moving a long's stop UP through entry.

THE RULE THESE PIN. This function may make a stop wider. It must never be able
to make one disappear, and when it cannot compute an honest widening it
returns the submitted stop untouched rather than a number it invented. Every
case below asserts the refusal is a no-op on the caller's value, because
that — not the log line — is what decides whether a real position keeps a
stop.

Reachability, stated honestly: ``TradeIdea.entry_price`` is a bare ``float``
with no ``gt=0`` validator and is populated from an LLM response, so a zero is
not excluded by the model; I did not prove a live path that produces one. The
``'BUY'`` case is not reachable from ``live_executor`` today, because the call
site passes ``Direction(str, Enum).value`` and that enum holds only LONG and
SHORT. These are guarded anyway: it is a public helper on the live order path,
and the cost of the fallthrough is a position with no stop.
"""
from __future__ import annotations

import math

import pytest

from bot.core.order_rules import (adjust_size_for_weekend,
                                  adjust_sl_for_gap_risk, should_defer_tp_sl)

# The caller's shape: `new_sl = f(...)` then `if new_sl != old_sl: assign`.
SUBMITTED_SL = 98.0
ENTRY = 100.0


def _widen(stop=SUBMITTED_SL, entry=ENTRY, direction="LONG", cls="Metal", weekend=True):
    return adjust_sl_for_gap_risk(stop, entry, direction, cls, weekend)


class TestItWidensWhenItCan:
    def test_a_long_stop_moves_further_below_entry(self):
        assert _widen() == pytest.approx(97.2)

    def test_a_short_stop_moves_further_above_entry(self):
        assert _widen(stop=102.0, direction="SHORT") == pytest.approx(102.8)

    def test_a_widened_stop_is_still_on_the_losing_side(self):
        """The property, not the arithmetic: whatever the factor becomes, a
        long's stop stays below entry and a short's above it."""
        assert _widen() < ENTRY
        assert _widen(stop=102.0, direction="SHORT") > ENTRY

    @pytest.mark.parametrize("cls", ["Crypto", "Pre-IPO"])
    def test_classes_that_never_gap_are_untouched(self, cls):
        assert _widen(cls=cls) == SUBMITTED_SL

    def test_a_weekday_is_untouched(self):
        assert _widen(weekend=False) == SUBMITTED_SL


class TestItRefusesRatherThanInvents:
    """Each case returns the SUBMITTED stop, so the caller's `!=` is False and
    nothing is assigned. Asserting the value — not the absence of a crash — is
    the point: the old code did not crash either."""

    @pytest.mark.parametrize("entry", [0.0, -1.0, None, float("nan"), float("inf"), "x"])
    def test_an_unreadable_entry_price_leaves_the_stop_alone(self, entry):
        """THE ONE THAT SET A STOP TO ZERO. `entry_price=0.0` returned 0.0."""
        assert _widen(entry=entry) == SUBMITTED_SL

    @pytest.mark.parametrize("stop", [0.0, -5.0, None, float("nan")])
    def test_an_unreadable_stop_is_returned_exactly_as_given(self, stop):
        """It cannot be widened, and 1.0 * 1.4 would have made it negative.
        Returned unchanged so the risk engine judges what was submitted."""
        got = _widen(stop=stop)
        assert got is stop or got == stop or (
            isinstance(stop, float) and math.isnan(stop) and math.isnan(got))

    @pytest.mark.parametrize("direction", ["BUY", "SELL", "long", "", None, "UP"])
    def test_a_direction_it_does_not_understand_is_refused_not_guessed(self, direction):
        """`"long"` lowercase is accepted (it is upper-cased); the rest are
        refused. Previously every one of these took the SHORT branch, so a
        long's stop moved UP through entry."""
        got = _widen(direction=direction)
        if str(direction or "").strip().upper() == "LONG":
            assert got == pytest.approx(97.2)
        else:
            assert got == SUBMITTED_SL

    def test_a_stop_on_the_wrong_side_of_entry_is_not_silently_repaired(self):
        """A long with a stop above entry is an invalid thesis. Widening it
        moved it across entry and called that a widening — inventing a trade
        the caller did not submit."""
        assert _widen(stop=110.0, direction="LONG") == 110.0
        assert _widen(stop=90.0, direction="SHORT") == 90.0

    def test_the_result_is_never_zero_or_negative_for_any_input(self):
        """The invariant behind all of the above, asserted directly: over a
        grid of hostile inputs, this function never returns a NEW value that
        is <= 0. It may return a bad value the CALLER submitted, unchanged."""
        hostile = [0.0, -1.0, 1e-12, 1e12, float("nan"), float("inf"), None]
        for entry in hostile:
            for stop in hostile:
                for direction in ("LONG", "SHORT", "BUY", None):
                    out = adjust_sl_for_gap_risk(stop, entry, direction, "Metal", True)
                    if out is stop or out == stop:
                        continue          # refused — the caller's own value
                    assert isinstance(out, float) and math.isfinite(out) and out > 0, (
                        f"invented {out!r} from entry={entry!r} stop={stop!r} dir={direction!r}")


class TestWeekendSizing:
    def test_a_metal_is_sized_down_35_percent(self):
        assert adjust_size_for_weekend(100.0, "Metal", True) == pytest.approx(65.0)

    def test_crypto_is_untouched(self):
        assert adjust_size_for_weekend(100.0, "Crypto", True) == 100.0

    @pytest.mark.parametrize("size", [float("nan"), 0.0, -5.0, None])
    def test_an_unreadable_size_is_not_multiplied_into_an_order(self, size):
        """`nan * 0.65` is `nan`, and a NaN size reaches the order payload."""
        got = adjust_size_for_weekend(size, "Metal", True)
        assert got is size or got == size or (
            isinstance(size, float) and math.isnan(size) and math.isnan(got))


class TestDeferral:
    @pytest.mark.parametrize(
        "cls,weekend,order_type,want",
        [("Metal", True, "limit", True),
         ("Metal", True, "market", False),
         ("Metal", False, "limit", False),
         ("Crypto", True, "limit", False),
         ("Pre-IPO", True, "limit", False)])
    def test_only_a_weekend_queued_gap_risk_limit_defers(self, cls, weekend, order_type, want):
        assert should_defer_tp_sl(cls, weekend, order_type) is want

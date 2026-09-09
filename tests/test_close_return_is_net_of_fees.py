"""The published "return on margin" was price-move x leverage, before fees.

Three close-data producers build `pnl_pct_margin` with `close_pct`, which sees
two prices and a leverage and nothing else. Four surfaces render it as THE
return — the public Telegram channel, the share sheet, the share button's
win/lose label, and the PNG close card.

WHAT IT OMITS IS A CONSTANT, WHICH IS WHY THIS IS NOT A ROUNDING ARGUMENT.
Fees are a fraction of NOTIONAL and notional is margin x leverage, so

    fees / margin  =  2 * fee_pct * leverage

At this repo's configured rates (taker 0.06%, maker 0.02% — bot/config.py:563)
that is 1.2% of margin at 10x and 2.4% at 20x on a market round trip, charged
before the position has done anything. And it runs the FLATTERING way every
time: gross exceeds net for a winner and for a loser alike, so a win prints
larger than it was and a loss smaller.

`tests/test_close_card_pct_basis.py`'s TRIA fixture has demonstrated this since
the day it was written and nobody read it that way — it is internally
consistent, and the consistency is the proof:

    published   pnl_pct_margin  +26.62%
    actual      $1.89 / $7.44   +25.40%
    difference  $0.09 / $7.44     1.21 points   <- exactly its fees

The live cards that prompted the fix:

    CLUSDT  Move -0.08% | on margin -1.67%     (limit entry, 20x)
    TRX     -$0.1008 (-0.06% margin / -0.00% notional, 20x) | Fees: $0.10

On the TRX card $0.10 of a $0.1008 loss is fees, so the published margin figure
describes the remaining $0.0008 — under 1% of what happened to the account.
"""

import pytest

from bot.core.live_executor import position_size_basis
from bot.formatters.share_invite import (
    _is_win,
    close_share_button,
    close_share_text,
)
from bot.marketing.public_text import public_close_line
from bot.skills.trading_commands import position_fee_estimate
from bot.utils.leveraged_return import (
    fee_drag_on_margin_pct,
    realized_margin_return_pct,
)

TAKER = 0.06


class TestTheArithmeticItself:
    def test_the_tria_fixture_was_the_proof_all_along(self):
        """+26.62% published, +25.40% earned, the gap being exactly the fees."""
        assert realized_margin_return_pct(1.89, 7.44) == pytest.approx(25.40, abs=0.01)
        assert fee_drag_on_margin_pct(0.09, 7.44) == pytest.approx(1.21, abs=0.01)
        # gross - net == fee drag, to the cent. That identity is the finding.
        assert 26.62 - 25.40 == pytest.approx(1.21, abs=0.02)

    @pytest.mark.parametrize("leverage,expected", [(1, 0.12), (10, 1.2), (20, 2.4)])
    def test_the_omission_scales_with_leverage_and_nothing_else(
            self, leverage, expected):
        """fees/margin = 2 * fee_pct * leverage — independent of position size.

        Driven at three sizes at each leverage precisely because a single-size
        test would pass against an implementation that happened to divide by
        the notional. The whole defect is a factor that is constant in the
        quantity a naive test would vary.
        """
        for margin in (4.2, 7.44, 500.0):
            notional = margin * leverage
            fees = 2 * notional * (TAKER / 100.0)
            assert fee_drag_on_margin_pct(fees, margin) == pytest.approx(
                expected, rel=1e-9)

    def test_gross_flatters_a_loser_too(self):
        """Not just "wins look bigger" — losses look smaller, which is worse.

        A loser's gross is ABOVE its net, so the published figure understates
        the damage. That is the direction that keeps an operator in a trade.
        """
        margin, leverage = 10.0, 20
        gross_pct_margin = -1.0                     # price move x leverage
        gross_usd = margin * gross_pct_margin / 100.0
        fees = 2 * (margin * leverage) * (TAKER / 100.0)
        net = realized_margin_return_pct(gross_usd - fees, margin)
        assert net < gross_pct_margin
        assert net == pytest.approx(-3.4, abs=0.01)

    @pytest.mark.parametrize("pnl,margin", [
        (1.89, None), (1.89, 0.0), (1.89, -5.0), (None, 7.44),
        (float("nan"), 7.44), (1.89, float("nan")), (float("inf"), 7.44),
        ("x", 7.44), (1.89, "x"),
    ])
    def test_unmeasurable_is_none_and_never_zero(self, pnl, margin):
        assert realized_margin_return_pct(pnl, margin) is None
        assert fee_drag_on_margin_pct(pnl, margin) is None

    def test_a_real_breakeven_is_still_zero(self):
        """0.0 is a measurement. Only the absences answer None."""
        assert realized_margin_return_pct(0.0, 7.44) == 0.0

    def test_fee_drag_is_a_positive_magnitude(self):
        """Signed beside a signed return, a reader would add the two."""
        assert fee_drag_on_margin_pct(-0.09, 7.44) > 0


class TestTheSizeBasis:
    """`size_usd` held margin OR notional depending on a falsy check."""

    class _Pos:
        cost_usd = 7.44
        entry_price = 0.009167
        quantity = 8113.0
        leverage = 10

    def test_a_recorded_cost_gives_both_and_they_differ_by_leverage(self):
        margin, notional = position_size_basis(self._Pos())
        assert margin == pytest.approx(7.44)
        assert notional == pytest.approx(74.37, abs=0.01)
        assert notional / margin == pytest.approx(10, rel=0.01)

    def test_an_unrecorded_cost_does_not_become_the_notional(self):
        """The whole bug in one assertion.

        `cost_usd > 0` selected margin, and `else` substituted
        `entry_price * quantity` — not a worse estimate of the same quantity
        but a DIFFERENT one, twenty times larger at 20x, under a key naming
        neither. 0.0 there means the venue never told us, which is the ORPHAN.
        """
        class Orphan(self._Pos):
            cost_usd = 0.0
        margin, notional = position_size_basis(Orphan())
        assert margin is None, "the notional was substituted for the margin"
        assert notional == pytest.approx(74.37, abs=0.01)

    def test_it_does_not_derive_the_margin_from_the_leverage(self):
        """`notional / pos.leverage` is the obvious last resort and is barred.

        These records include the closes written when the venue filled at 20x
        against a 5x target, so `leverage` is the field whose unreliability the
        record exists to document. A margin derived from it would be wrong by
        exactly the unnoticed factor and would then be published as a return.
        """
        class Orphan(self._Pos):
            cost_usd = 0.0
            leverage = 20
        assert position_size_basis(Orphan())[0] is None

    def test_nothing_readable_is_two_nones(self):
        class Empty:
            pass
        assert position_size_basis(Empty()) == (None, None)


#: The CLUSDT card, field for field, with the margin now recorded.
CLUSDT = {
    "symbol": "CLUSDT", "direction": "LONG", "reason": "leverage_overshoot",
    "pnl_pct": -0.08, "pnl_pct_margin": -1.67, "pnl_pct_margin_net": -3.30,
    "pnl_usd": -0.81, "leverage": 20, "hold_time": "1.0h",
}


class TestThePublicChannel:
    def test_the_margin_figure_published_is_the_net_one(self):
        out = public_close_line(CLUSDT)
        assert "on margin <code>-3.30%</code>" in out
        assert "-1.67%" not in out, "the gross figure reached a public channel"

    def test_the_price_move_is_still_told(self):
        assert "Move: <code>-0.08%</code>" in public_close_line(CLUSDT)

    def test_no_margin_figure_means_none_is_published(self):
        """OMIT, not substitute. The gross number must not fill the hole."""
        out = public_close_line(dict(CLUSDT, pnl_pct_margin_net=None))
        assert "on margin" not in out
        assert "-1.67%" not in out

    def test_but_the_leverage_still_goes_out(self):
        """A leverage is a fact about the position, not a claim about return.

        Without it a 20x close reads as a -0.08% nothing, which is its own
        quiet misrepresentation.
        """
        out = public_close_line(dict(CLUSDT, pnl_pct_margin_net=None))
        assert "20×" in out

    def test_a_move_up_that_fees_turned_into_a_loss_is_not_painted_green(self):
        """Colour is a claim, and it was keyed on the chart, not the account.

        At 20x the round trip costs 0.12% of notional, so any move smaller
        than that flips the sign of the outcome while leaving `pnl_pct`
        positive. `pnl_usd` is net and readable; it decides.
        """
        out = public_close_line(dict(
            CLUSDT, pnl_pct=+0.05, pnl_pct_margin_net=-1.4, pnl_usd=-0.14))
        assert "\U0001f534" in out, "a losing close was painted green"
        assert "\U0001f7e2" not in out

    def test_a_genuine_winner_is_still_green(self):
        out = public_close_line(dict(
            CLUSDT, pnl_pct=+0.50, pnl_pct_margin_net=+7.6, pnl_usd=+0.76))
        assert "\U0001f7e2" in out

    def test_no_dollars_reach_the_public_line(self):
        """`pnl_usd` is now READ here. It must still never be printed (§4)."""
        assert "$" not in public_close_line(CLUSDT)
        assert "0.81" not in public_close_line(CLUSDT)


class TestTheShareSheet:
    #: +0.10% at 20x: +2.0% gross on margin, -0.4% net after a 2.4% fee drag.
    LOSER_THAT_LOOKED_LIKE_A_WIN = {
        "symbol": "TAO/USDT:USDT", "direction": "LONG",
        "pnl_pct": 0.10, "pnl_pct_margin": 2.00,
        "pnl_pct_margin_net": -0.40, "pnl_usd": -0.04,
        "leverage": 20, "hold_time": "12m",
    }

    def test_the_button_does_not_call_a_losing_trade_a_win(self):
        assert _is_win(self.LOSER_THAT_LOOKED_LIKE_A_WIN) is False
        btn = close_share_button(self.LOSER_THAT_LOOKED_LIKE_A_WIN, "runeclaw_bot")
        assert btn is not None
        assert btn["text"] == "\U0001f4e3 Share"

    def test_and_publishes_the_net_figure_not_the_gross_one(self):
        text = close_share_text(self.LOSER_THAT_LOOKED_LIKE_A_WIN)
        assert "-0.40%" in text
        assert "+2.00%" not in text

    def test_a_real_win_is_still_a_win(self):
        data = dict(self.LOSER_THAT_LOOKED_LIKE_A_WIN,
                    pnl_pct_margin_net=6.9, pnl_usd=0.69)
        assert _is_win(data) is True
        assert close_share_button(data, "runeclaw_bot")["text"].endswith("win")

    def test_the_price_move_is_no_longer_a_win_test(self):
        """It was the fallback, and it is price-derived like the gross one.

        A close with no readable net is not a win — the same fail-closed
        answer the function already gave for an unreadable percent.
        """
        assert _is_win({"pnl_pct": 5.0}) is False

    def test_an_unmeasurable_close_is_not_a_win(self):
        assert _is_win({}) is False
        assert _is_win(None) is False
        assert _is_win({"pnl_pct_margin_net": None, "pnl_usd": None}) is False


class TestTheFeeEstimate:
    """Two halves of one round trip, computed on bases a leverage apart."""

    ROW = {"entry": 0.3403, "current": 0.3403, "quantity": 247.0,
           "size_usd": 4.2, "notional_usd": 84.05, "leverage": 20,
           "hold_hours": 0.0}

    def test_the_entry_fee_is_taken_on_the_notional(self):
        got = position_fee_estimate(self.ROW, TAKER)
        assert got["entry_fee"] == pytest.approx(84.05 * TAKER / 100, rel=1e-6)

    def test_and_that_is_twenty_times_the_margin_based_answer(self):
        """The regression, stated as the ratio it actually was.

        The old line took the fraction of `size_usd` — the margin — while the
        exit leg one line below used the notional.
        """
        got = position_fee_estimate(self.ROW, TAKER)
        margin_based = self.ROW["size_usd"] * TAKER / 100
        assert got["entry_fee"] / margin_based == pytest.approx(20, rel=0.01)

    def test_both_legs_are_now_on_the_same_basis(self):
        got = position_fee_estimate(self.ROW, TAKER)
        assert got["entry_fee"] == pytest.approx(got["exit_fee"], rel=0.01)

    def test_funding_is_on_the_notional_too(self):
        got = position_fee_estimate(dict(self.ROW, hold_hours=8.0), TAKER)
        assert got["funding_paid"] == pytest.approx(84.05 * 0.0001, rel=1e-6)

    def test_an_unknown_age_leaves_the_fees_readable(self):
        """They fail INDEPENDENTLY — an ageless position still paid to open.

        The old block forced all four to None together, which is why the
        `net_pnl` guard below it only had to test one of them.
        """
        got = position_fee_estimate(dict(self.ROW, hold_hours=None), TAKER)
        assert got["entry_fee"] is not None
        assert got["funding_paid"] is None

    @pytest.mark.parametrize("row", [
        {"entry": 0, "quantity": 247.0, "hold_hours": 1.0},
        {"entry": 0.34, "quantity": 0, "hold_hours": 1.0},
        {"entry": 0.34, "quantity": None, "hold_hours": 1.0},
        {"hold_hours": 1.0},
    ])
    def test_no_notional_means_no_fee_rather_than_a_free_position(self, row):
        got = position_fee_estimate(row, TAKER)
        assert got["entry_fee"] is None
        assert got["total_fees"] is None

    def test_an_absent_entry_of_zero_is_not_a_price(self):
        """Both row builders write `pos.get("entry", 0)`, so 0 is ABSENT here.

        `entry is not None` passes on that and yields a notional of 0, which
        prints `fees $0.00` — the free position the block exists to refuse.
        """
        assert position_fee_estimate(
            {"entry": 0, "quantity": 247.0, "hold_hours": 1.0},
            TAKER)["total_fees"] is None

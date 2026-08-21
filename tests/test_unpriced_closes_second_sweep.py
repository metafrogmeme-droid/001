"""The sites the first sweep missed, found by triaging its own leftovers.

#122 cured `/portfolio`, `/daily_report`, `/performance` and the net-worth
panel, and explicitly left 78 further `or 0` sites untriaged as "a landscape,
not a to-do list". Triaging them (78 sites, 25 claimed, 10 verified by an
adversarial refuter, 8 survivors) found that the landscape contained live
defects — including one on the card #122 had just finished converting.

Three are worth naming because of WHERE they were:

  * The chat LLM's context. 53 lines above the offending line sits a comment
    refusing to invent a number for PAPER open positions, because "a
    fabrication laundered through natural language is harder to catch than a
    wrong number on a card, because the sentence sounds considered". The LIVE
    closed-trade block below it did `t.pnl_usd or 0`, so a close nobody could
    price reached the model as "PnL $+0.00" and came back to the user as prose.

  * `/balance`, the un-cured sibling of `/portfolio` — the same store, the same
    three sums, the same `or 0`, printed beside "{n} trades" so the total read
    as covering all of them.

  * `/portfolio`'s excluded-orphans parenthetical: the ONE line on that card
    that #122 did not convert, 180 lines below the ones it did. "($+0.00)"
    reads as "excluding them changed nothing", on the rows the bot did not open
    and knows least about.

And the carve-out that turned out to be real. A sort key is usually not a
claim — but the performance card PUBLISHES the ends of the order as "Best 🏆"
and "Worst", so `sorted(key=lambda t: (t.pnl_usd or 0))` crowned an unpriced
row on any book of losses.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.formatters.realized_totals import best_and_worst
from bot.formatters.rich_cards import render_status_card


def _t(symbol="BTC/USDT", pnl=None):
    return SimpleNamespace(symbol=symbol, pnl_usd=pnl)


class TestBestAndWorstRankOnlyWhatWasPriced:
    def test_an_unpriced_row_is_not_crowned_best(self):
        """The reported failure, exactly: one real loss and one unpriced row.

        `(t.pnl_usd or 0)` maps the unpriced row to 0.0, which is greater than
        -5.0, so it sorted highest and was named the best trade.
        """
        loser = _t("ETH/USDT", -5.0)
        unpriced = _t("DOGE/USDT", None)
        best, worst = best_and_worst([loser, unpriced])
        assert best is loser, "the only priced trade is the only rankable one"
        assert worst is loser
        # And state the old behaviour as arithmetic so the difference is visible.
        old = sorted([loser, unpriced], key=lambda t: (t.pnl_usd or 0))
        assert old[-1] is unpriced, "the shape being replaced crowned the unpriced row"

    def test_nothing_priced_names_nobody(self):
        best, worst = best_and_worst([_t(pnl=None), _t(pnl=None)])
        assert best is None and worst is None, (
            "inventing a winner from an unscorable book is the same defect as "
            "inventing its total")

    def test_an_empty_book_names_nobody(self):
        assert best_and_worst([]) == (None, None)

    def test_a_measured_zero_is_rankable(self):
        """0.0 is falsy and is a real, measured, break-even trade."""
        flat = _t("SOL/USDT", 0.0)
        loser = _t("ETH/USDT", -5.0)
        best, worst = best_and_worst([loser, flat])
        assert best is flat and worst is loser

    def test_a_normal_book_is_unchanged(self):
        """The control — over-guarding would blank a perfectly good ranking."""
        a, b, c = _t("A", 10.0), _t("B", -3.0), _t("C", 4.0)
        best, worst = best_and_worst([a, b, c])
        assert best is a and worst is b

    def test_a_bool_is_not_a_pnl(self):
        """`isinstance(True, int)` is True, so a bool would rank as $1.00."""
        best, worst = best_and_worst([_t("A", True), _t("B", -2.0)])
        assert best.symbol == "B" and worst.symbol == "B", (
            "the bool row is unrankable, leaving one real trade")


class TestTheStatusCardDailyPnl:
    """`daily_pnl` is three-valued now, the same shape this card already used
    for `equity`. A day where closes exist but none could be priced rendered
    `⚪ 0.0%` beside a `/ +5.0% limit` — a measured flat day, from nothing."""

    def _card(self, daily_pnl):
        return render_status_card(
            mode="LIVE", active=True, equity=10_000.0, open_positions=2,
            daily_pnl=daily_pnl, drawdown=1.0, max_drawdown=5.0,
            market_bias="Normal")

    def test_an_unknown_daily_pnl_is_not_a_flat_day(self):
        out = self._card(None)
        line = [ln for ln in out.splitlines() if "PnL" in ln or "P&L" in ln]
        assert line, f"no daily P&L line in:\n{out}"
        assert "0.0%" not in line[0], line[0]

    def test_it_does_not_take_the_break_even_glyph(self):
        """⚪ already meant 'exactly break-even' on this card, so an unknown
        total taking the same glyph would be indistinguishable from a measured
        flat day — which is the whole defect."""
        unknown = self._card(None)
        flat = self._card(0.0)
        u_line = [ln for ln in unknown.splitlines() if "PnL" in ln or "P&L" in ln][0]
        f_line = [ln for ln in flat.splitlines() if "PnL" in ln or "P&L" in ln][0]
        assert u_line != f_line, "unknown and break-even render identically"
        assert "⚪" in f_line, "a measured flat day keeps the neutral glyph"
        assert "⚪" not in u_line

    @pytest.mark.parametrize("value,expect", [(2.5, "+2.5%"), (-1.5, "-1.5%"), (0.0, "+0.0%")])
    def test_real_readings_are_unchanged(self, value, expect):
        """The control. A measured loss must still show as a loss."""
        out = self._card(value)
        assert expect in out, out

    def test_a_bool_is_not_a_percentage(self):
        out = self._card(True)
        line = [ln for ln in out.splitlines() if "PnL" in ln or "P&L" in ln][0]
        assert "100" not in line and "1.0%" not in line, line

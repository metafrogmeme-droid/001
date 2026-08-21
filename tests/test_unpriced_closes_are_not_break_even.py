"""A close nobody could price, folded into the total as break-even.

`pnl_usd` is Optional BY DESIGN. live_executor's loader preserves a JSON null
verbatim, under a comment recording that `float(x or 0)` reading it back as 0.0
"silently converted 'we could not price this' into 'this broke even'".

Three surfaces then did exactly that anyway:

  /portfolio (LIVE)   sum((p.pnl_usd or 0) for p in live_closed)
                      -> "Net PnL: $+0.00 🟢" from zero measurements, and
                         "✅ BTCUSDT LONG → $+0.00" per unpriced row.
  /daily_report       wins / trades, scored numerator over ALL-closes
                      denominator -> a rate that counts unpriced closes as
                         defeats, printed above "Total/Wins/Losses" that do
                         not add up.
  /performance        .get("win_rate", 0.0) -> 0%, an empty ring and an empty
                         bar: the picture of total defeat drawn from no data.

Each had a correct neighbour. /portfolio's UNREALIZED total five lines below
was already tri-state under the sentence "a partial sum presented as a whole one
is a wrong number wearing a measured number's authority". The PUBLIC daily post
already rendered 'n/a' under "A 0% win rate is a claim that everything lost".
The PNG tile beside /performance already labelled itself "Win Rate (of N)".
The fix reached the neighbour and not the thing next to it, three times.

These are renderer tests: plant the state, assert what the card says.
"""
from __future__ import annotations

import pytest

from bot.warroom.warroom_bot import render_daily_report, render_performance


class TestTheDailyReportWinRate:
    """`wins / trades` — a numerator and denominator from different sets."""

    def test_unpriced_closes_do_not_drag_the_rate_down(self):
        # 10 closes, 6 scorable, 4 of those won. The honest rate is 67%.
        out = render_daily_report({
            "trades": 10, "wins": 4, "losses": 2, "net_pnl": 18.40,
        })["text"]
        assert "67%" in out, out
        assert "40%" not in out, (
            "40% is 4/10 — the scored numerator over the all-closes "
            "denominator, which counts every unpriced close as a defeat"
        )

    def test_the_denominator_matches_the_numbers_printed_beside_it(self):
        """Total 10 / Wins 4 / Losses 2 does not add up, and the gauge must at
        least agree with the two numbers it sits under."""
        out = render_daily_report({
            "trades": 10, "wins": 4, "losses": 2, "net_pnl": 0.0,
        })["text"]
        assert "Rate covers 6 of 10 closes" in out, out
        assert "4 carry no recorded" in out, out

    def test_nothing_scorable_is_not_a_zero_percent_win_rate(self):
        """The whole point. A 0% win rate is a claim that everything lost."""
        out = render_daily_report({
            "trades": 5, "wins": 0, "losses": 0, "net_pnl": 0.0,
        })["text"]
        assert "n/a" in out, out
        # Anchored to the gauge's own line. "0%" appears legitimately
        # elsewhere on this card, and asserting a short string is absent from a
        # whole document is the assertion that keeps misfiring in this repo.
        # The first draft of this test picked the line with a fiddly
        # `"│" in ln and "○" in ln or "n/a" in ln`, which would still have
        # passed had it selected the wrong line; the gauge is the only line
        # carrying the bar character, so select on that alone.
        gauge = [ln for ln in out.splitlines() if "│" in ln and "<code>" in ln]
        assert len(gauge) == 1, f"expected exactly one gauge line, got {gauge}"
        assert "n/a" in gauge[0], gauge[0]
        assert "0%" not in gauge[0], gauge[0]

    def test_a_fully_scored_day_is_unchanged(self):
        """The control. Nothing unpriced -> no note, and the plain rate."""
        out = render_daily_report({
            "trades": 8, "wins": 6, "losses": 2, "net_pnl": 100.0,
        })["text"]
        assert "75%" in out
        assert "Rate covers" not in out, "no shortfall, so no disclosure"


class TestThePerformanceCardWinRate:
    """`.get("win_rate", 0.0)` — the absent case taking the reassuring default."""

    def test_an_absent_rate_is_not_zero_percent(self):
        out = render_performance({"today_pnl": 0.0, "trades_today": 3})["text"]
        assert "n/a" in out, out

    def test_an_explicit_none_is_not_zero_percent(self):
        """The caller now passes None through rather than substituting 0 one
        layer above the renderer, so None must survive here too."""
        out = render_performance({
            "today_pnl": 0.0, "trades_today": 3, "win_rate": None,
        })["text"]
        assert "n/a" in out, out

    def test_a_real_zero_percent_still_renders_as_zero(self):
        """The control that keeps the fix honest in the other direction: a
        MEASURED 0% is a real, terrible, true reading and must still show."""
        out = render_performance({
            "today_pnl": -50.0, "trades_today": 4, "win_rate": 0.0,
            "win_rate_scored": 4, "win_rate_unscored": 0,
        })["text"]
        assert "0%" in out, out
        assert "n/a" not in out, out

    def test_the_shortfall_is_disclosed_when_partial(self):
        out = render_performance({
            "today_pnl": 0.0, "trades_today": 10, "total_trades": 10,
            "win_rate": 66.6, "win_rate_scored": 6, "win_rate_unscored": 4,
        })["text"]
        assert "67%" in out
        assert "Rate covers 6 of 10 closes" in out, out

    @pytest.mark.parametrize("bad", [True, False])
    def test_a_bool_is_not_a_win_rate(self, bad):
        """`isinstance(True, int)` is True in Python, so a bool would sail
        through a naive numeric check and render as 100% or 0%."""
        out = render_performance({
            "today_pnl": 0.0, "trades_today": 1, "win_rate": bad,
        })["text"]
        assert "n/a" in out, out

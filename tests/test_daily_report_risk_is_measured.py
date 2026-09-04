"""`/daily_report`'s LIVE branch printed a verdict it had not measured.

    live_eq = await self.engine.get_effective_equity_async(user_id)
    dd = 0.0
    risk_status = "Healthy"

Hardcoded. Not a read that failed — no read at all, under a shield icon, on
the LIVE branch, while the PAPER branch six lines below computed both from a
portfolio snapshot. The one branch that could not afford to guess was the one
that did. (`live_eq` was awaited and never used, so the only real work that
line did was a network round-trip nobody read.)

Two more in the same block, and these were made REACHABLE by the change that
stopped the executor substituting an entry price for an unread exit:

  * `sum((t.pnl_usd or 0) for t in closed)` — a partial total printed as the
    day's net;
  * `sorted(closed, key=lambda t: (t.pnl_usd or 0))` — an unpriced close could
    be named the day's best or worst trade, at $0.00.

And the renderer defaulted `risk_status` to "Healthy" and `net_pnl` to 0.0,
so the two agreed about a thing neither had measured.
"""
import re

import pytest

from bot.formatters.drawdown_card import UNKNOWN_RISK, live_risk_status
from bot.warroom.warroom_bot import render_daily_report

LIMIT = 7.0          # CONFIG.risk.live_max_drawdown_pct default


def _text(data):
    return re.sub(r"<[^>]+>", "", render_daily_report(data)["text"])


class TestTheVerdict:
    def test_an_unreadable_status_is_not_healthy(self):
        # `drawdown_status()` is documented "returns empty on any error", so
        # {} is the unreadable case and must not collapse into the calmest of
        # the three verdicts.
        assert live_risk_status({}) == (None, UNKNOWN_RISK)
        assert live_risk_status(None) == (None, UNKNOWN_RISK)

    def test_a_measured_flat_book_is_healthy(self):
        # 0.0 drawdown is a real reading and the commonest state there is.
        dd, status = live_risk_status(
            {"drawdown_pct": 0.0, "effective_limit_pct": LIMIT})
        assert (dd, status) == (0.0, "Healthy")

    @pytest.mark.parametrize("dd,expected", [
        (4.1, "Healthy"),     # the operator's own /status card
        (4.7, "Warning"),     # just past 2/3 of 7.0 (4.667)
        (4.6, "Healthy"),     # just under it
        (6.9, "Warning"),
        (7.0, "Critical"),    # at the limit the breaker enforces
        (9.0, "Critical"),
    ])
    def test_the_bands_come_off_the_enforced_limit(self, dd, expected):
        # A fixed "Critical above 3%" is meaningless against a 7% live cap and
        # would read Critical on a book the gate is perfectly happy with.
        assert live_risk_status(
            {"drawdown_pct": dd, "effective_limit_pct": LIMIT})[1] == expected

    def test_a_drawdown_with_no_limit_is_a_number_not_a_verdict(self):
        dd, status = live_risk_status({"drawdown_pct": 4.1})
        assert dd == 4.1
        assert status == UNKNOWN_RISK

    @pytest.mark.parametrize("junk", [None, "4.1", True, float("nan")])
    def test_junk_is_unreadable(self, junk):
        assert live_risk_status(
            {"drawdown_pct": junk, "effective_limit_pct": LIMIT})[0] is None


class TestTheCard:
    def test_an_absent_risk_status_no_longer_defaults_to_healthy(self):
        out = _text({})
        assert "Healthy" not in out
        assert UNKNOWN_RISK in out

    def test_an_absent_net_pnl_is_not_a_flat_day(self):
        out = _text({})
        assert "$+0.00" not in out
        assert "unread" in out

    def test_unknown_gets_the_neutral_icon_not_the_red_one(self):
        # The icon expression had no unknown arm, so "Unknown" fell to the
        # final `else` and painted RED — the opposite lie, but still a claim.
        out = _text({"risk_status": UNKNOWN_RISK})
        assert "⚪" in out          # white circle
        assert "\U0001f534" not in out.split("Risk Status")[1]

    def test_a_real_status_still_renders_with_its_number(self):
        out = _text({"trades": 4, "wins": 3, "losses": 1, "net_pnl": 12.34,
                     "best_trade": "SOL", "best_pnl": 8.0,
                     "worst_trade": "ETH", "worst_pnl": -3.0,
                     "risk_status": "Healthy", "drawdown_pct": 4.1})
        assert "Healthy" in out
        assert "drawdown 4.1%" in out
        assert "$+12.34" in out

    def test_no_scorable_trade_shows_no_dollar_figure_for_best_or_worst(self):
        out = _text({"trades": 3, "net_pnl": None, "best_trade": "N/A",
                     "best_pnl": None, "worst_trade": "N/A",
                     "worst_pnl": None, "risk_status": UNKNOWN_RISK,
                     "unscored": 3})
        line = [ln for ln in out.split("\n") if "Best" in ln][0]
        assert "$" not in line
        assert "0.00" not in line

    def test_the_unscored_shortfall_is_disclosed(self):
        out = _text({"trades": 3, "wins": 0, "losses": 0, "net_pnl": None,
                     "risk_status": UNKNOWN_RISK, "unscored": 3})
        assert "carry no recorded P&amp;L" in out or "carry no recorded P&L" in out


class TestTheHandlerWiring:
    def _daily_report(self):
        """The whole `_cmd_daily_report` body, comments stripped.

        Sliced def-to-next-def rather than by a fixed character window: the
        first two drafts of this used a comment as the anchor (code_only
        blanks those) and then a 3000-char window that stopped short of the
        lines under test, and BOTH failed in the direction that looks like a
        code bug.
        """
        import io

        from tests.source_scan import code_only
        code = code_only(
            io.open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        i = code.index("async def _cmd_daily_report")
        j = code.index("async def ", i + 20)
        return code[i:j]

    def test_the_live_branch_no_longer_hardcodes_either_value(self):
        # Anchored to the END OF THE LINE, because the PAPER branch legitimately
        # computes `risk_status = "Healthy" if dd < 2.0 else ...` and a bare
        # substring check matches its prefix. The first draft did exactly that
        # and failed on true code — the assertion was wrong, not the branch.
        block = self._daily_report()
        assert not re.search(r'^\s*risk_status = "Healthy"\s*$', block, re.M)
        assert not re.search(r"^\s*dd = 0\.0\s*$", block, re.M)
        assert "live_risk_status(_dd_st)" in block

    def test_the_paper_branch_keeps_its_computed_verdict(self):
        # Not every match is a defect, including in this file's own asserts.
        # The paper ternary IS a reading and must survive.
        assert 'risk_status = "Healthy" if dd < 2.0' in self._daily_report()

    def test_the_net_no_longer_counts_an_unread_close_as_zero(self):
        block = self._daily_report()
        assert "sum((t.pnl_usd or 0) for t in closed)" not in block
        assert '_ps["total"]' in block

    def test_the_dead_equity_await_is_gone(self):
        # Awaited, assigned, never read — a network round-trip for nobody.
        assert "live_eq = await" not in self._daily_report()

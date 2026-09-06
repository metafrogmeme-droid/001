"""Six surfaces that answered from no reading. One shape, six depths.

Each was found by asking the corollary CLAUDE.md names — *which OTHER surface
makes the same claim?* — after the close-card and daily-report sweeps. Four of
the six sit next to code that already got the tri-state right, sometimes in
the same file and sometimes in the same dict.
"""
import io
import re

import pytest

from bot.warroom.warroom_bot import render_performance
from tests.source_scan import code_only, handler_sources


def _code(path):
    return code_only(io.open(path, encoding="utf-8").read())


def _handler_code():
    """Every file the Telegram handler class is made of, comments stripped.

    The handler is being split into mixins; `/equitycurve` lives in the
    engine-ops one and `/performance` is next to move. A scan of
    telegram_handler.py alone reads a move as the guarded line vanishing —
    and, for the `not in` assertions below, as a fold that was never there.
    """
    return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())


def _js_code(path):
    """JS source with `//` line comments blanked.

    `code_only` is tokenize-based and Python-only, and the first draft of the
    chat.js assertions read the raw file — matching the COMMENT that quotes
    `Number(d.daily_pnl || 0)` while explaining why it is gone. A comment
    quoting the string it forbids is indistinguishable from the code doing
    it; CLAUDE.md counts four false failures from exactly this.

    Line comments only: no `/* */` occurs in the spans under test, and a
    half-right stripper would be worse than an honest one.
    """
    out = []
    for line in io.open(path, encoding="utf-8").read().splitlines():
        i = line.find("//")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


def _text(data):
    return re.sub(r"<[^>]+>", "", render_performance(data)["text"])


class TestPerformanceTotals:
    """`_total_known` was computed and had ONE occurrence in the file."""

    ALL_UNPRICED = {"today_pnl": None, "week_pnl": None, "total_pnl": None,
                    "win_rate": None, "win_rate_scored": 0,
                    "win_rate_unscored": 3, "total_trades": 3}

    def test_an_unpriceable_book_prints_no_all_time_figure(self):
        out = _text(self.ALL_UNPRICED)
        assert "$+0.00" not in out

    def test_it_is_not_painted_as_profit(self):
        # `_pnl_arrow(None)` is the white circle; green would claim a gain.
        out = _text(self.ALL_UNPRICED)
        assert "⚪" in out

    def test_a_real_total_still_renders(self):
        out = _text(dict(self.ALL_UNPRICED, total_pnl=12.34, today_pnl=1.0,
                         week_pnl=5.0, win_rate=60.0, win_rate_scored=3,
                         win_rate_unscored=0))
        assert "$+12.34" in out

    def test_a_measured_flat_day_still_prints_zero(self):
        # 0.0 is a reading. Only None is not.
        out = _text(dict(self.ALL_UNPRICED, today_pnl=0.0))
        assert "$+0.00" in out

    def test_the_caller_no_longer_folds_the_total(self):
        code = _handler_code()
        assert '_tot["net"] if _tot["net"] is not None else 0.0' not in code
        # ...and the flag that recorded the fold is gone with it.
        assert "_total_known" not in code

    def test_an_unpriced_window_is_not_a_flat_window(self):
        code = _handler_code()
        assert "if _today_priced == 0 and _today_unpriced > 0:" in code
        assert "if _week_priced == 0 and _week_unpriced > 0:" in code


class TestDashboardPositionRows:
    """Constants, not failed reads — the harder kind to see."""

    def test_the_hardcoded_zeros_are_gone(self):
        code = _code("bot/skills/scan_skill.py")
        assert '"unrealized_pnl": 0.0,' not in code
        assert '"margin": 0.0,' not in code

    def test_they_are_null_instead(self):
        code = _code("bot/skills/scan_skill.py")
        assert '"unrealized_pnl": None,' in code
        assert '"margin": None,' in code


class TestLiveEquityIsTriState:
    def test_a_successful_fetch_with_no_parseable_total_is_unreadable(self):
        code = _code("bot/skills/scan_skill.py")
        # Both folds are gone: the USDT dict read and the `if equity == 0`
        # second attempt, either of which published $0.00 on a funded account.
        assert 'equity = float(usdt.get("total", 0) or 0)' not in code
        assert "if equity == 0:" not in code

    def test_the_payload_carries_the_null(self):
        code = _code("bot/skills/scan_skill.py")
        assert 'result["equity"] = None if equity is None else round(equity, 2)' in code

    def test_the_log_line_does_not_format_a_none(self):
        # `equity=$%.2f` raises on None — the fix would have crashed the very
        # path it was making honest.
        assert 'log.info("Live data: equity=$%.2f' not in _code("bot/skills/scan_skill.py")

    def test_cb_equity_starts_unmeasured(self):
        code = _code("bot/skills/scan_skill.py")
        assert "cb_equity: Optional[float] = None" in code
        assert "\n    cb_equity = 0\n" not in code


class TestChatHeaderColour:
    def test_a_null_daily_pnl_is_not_painted_green(self):
        js = _js_code("app/public/js/chat.js")
        assert "Number(d.daily_pnl || 0)" not in js
        assert "const dpKnown =" in js

    def test_the_colour_class_is_empty_when_unknown(self):
        js = _js_code("app/public/js/chat.js")
        assert "const dpCls = !dpKnown ? '' : (dp >= 0 ? 'up' : 'down');" in js
        # The inline ternary that could not express "neither" is gone.
        assert "${dp >= 0 ? 'up' : 'down'}" not in js

    def test_the_api_really_does_send_null(self):
        # Reachability: this is not defensive coding. The LIVE-linked branch
        # hardcodes it.
        js = io.open("app/routes/portfolio.js", encoding="utf-8").read()
        assert "daily_pnl: null" in js   # raw: this one IS the code


class TestEquityCurveVerdict:
    def test_it_does_not_claim_a_comparison_it_could_not_make(self):
        code = _handler_code()
        assert "if _snaps < _ma_period:" in code
        assert "NOT YET MEASURED" in code

    def test_the_healthy_wording_survives_for_a_real_window(self):
        # The fix must not delete the true verdict, only gate it.
        assert "HEALTHY — equity above MA" in _handler_code()


class TestNoDeadExchangeCall:
    def test_the_ticker_fetch_is_gone(self):
        code = _code("bot/formatters/rich_cards.py")
        assert "await exchange.fetch_ticker(symbol)" not in code

    def test_the_orderbook_fetch_that_IS_read_stays(self):
        # Not every await is waste; this one feeds the depth fields.
        assert "await exchange.fetch_order_book(symbol, limit=20)" in _code(
            "bot/formatters/rich_cards.py")

    @pytest.mark.parametrize("name", ["ohlcv", "orderbook"])
    def test_the_remaining_reads_are_still_initialised(self, name):
        assert f"{name}" in _code("bot/formatters/rich_cards.py")

"""A paper close sends the close card, as the live close has since the card existed.

The live close has handed `render_close_card` a picture since the card was
written; the paper close sent text alone (2026-10-19: a TRUMP short closed
on the practice book as eight lines of text under a position card drawn as
a picture). `paper_close_card_data` is the seam: the practice book's record
in the keys the card reads, driven here with a real book.
"""
from __future__ import annotations

import os

from bot.formatters import signal_card as SC
from bot.formatters.money import signed_usd
from bot.skills.callback_handler import paper_close_card_data
from tests.png_text import drawn, fill_of
from tests.source_scan import code_only


def _closed(leverage: float = 5.0, exit_price=1.685):
    from bot.risk.portfolio import PortfolioTracker
    from bot.utils.models import Direction, TradeIdea
    book = PortfolioTracker(initial_balance=10_000.0)
    idea = TradeIdea(asset="TRUMP/USDT", direction=Direction.SHORT, entry_price=1.7468,
                     stop_loss=1.8262, take_profit=1.6277, confidence=0.8,
                     reasoning="t", signals_used=["t"])
    pos = book.open_position(idea, size_usd=527.88, leverage=leverage)
    return book.close_position(pos.trade_id, exit_price)


class TestTheSeam:
    def test_margin_is_the_collateral_and_the_hero_is_net_on_it(self):
        ct = _closed(5.0)
        d = paper_close_card_data("TRUMPUSDT", ct, 7.2)
        notional = ct.quantity * ct.entry_price
        assert abs(d["margin_usd"] - notional / 5) < 0.01, "the margin, as /positions prints it"
        assert d["leverage"] == 5.0
        assert d["pnl_usd"] == ct.pnl, "net of commission, the figure the book booked"
        assert abs(d["pnl_pct_margin_net"] - ct.pnl / d["margin_usd"] * 100) < 1e-9
        assert d["direction"] == "SHORT" and d["symbol"] == "TRUMPUSDT"
        assert d["entry"] == ct.entry_price and d["exit"] == 1.685
        assert d["hold_time"] == "7.2h" and d["reason"] == "manual"
        assert d["fees"] == ct.commission

    def test_no_leverage_on_record_is_the_notional_at_1x(self):
        ct = _closed(1.0)
        d = paper_close_card_data("TRUMPUSDT", ct, 0.0)
        assert abs(d["margin_usd"] - ct.quantity * ct.entry_price) < 0.01
        assert d["leverage"] == 1.0 and d["hold_time"] == ""

    def test_the_card_draws_the_same_figures(self):
        ct = _closed(5.0)
        d = paper_close_card_data("TRUMPUSDT", ct, 7.2)
        rows = drawn(SC.render_close_card, d)
        texts = [t for t, _ in rows]
        assert "CLOSED" in texts and "TRUMPUSDT" in texts
        pct = d["pnl_pct_margin_net"]
        assert f"{'+' if pct >= 0 else ''}{pct:.2f}%" in texts, texts
        assert "on margin, after fees" in texts, "the hero names its basis"
        assert f"${d['margin_usd']:,.2f} | 5x" in texts
        net_line = f"{signed_usd(ct.pnl)}  (fees ${ct.commission:.2f})"
        assert fill_of(rows, net_line) == (SC._GREEN if ct.pnl >= 0 else SC._RED)


class TestTheHandler:
    """The handler's close branch is a 400-line async method with the venue,
    the book and the chat behind it; the seam above is what a test can drive,
    and this scan says the branch asks it."""

    def test_the_paper_close_sends_the_card_with_the_breakdown_as_caption(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = code_only(open(os.path.join(os.path.dirname(here), "bot", "skills", "callback_handler.py"),
                             encoding="utf-8").read())
        # The PAPER branch: the live close's message two hundred lines up
        # names its pair as `ep_clean`, this one as `pair`.
        i = src.index("html.escape(pair)} — Position Closed</b>")
        branch = src[i:i + 2500]
        assert "render_close_card(paper_close_card_data(pair, closed_trade, hold_h))" in branch
        assert 'await self._send_photo(update, close_png, "\\n".join(lines))' in branch
        assert 'await self._send(update, "\\n".join(lines), edit=True)' in branch, "text alone when no card"

"""The paper book settles in Decimal and publishes the float path's cents.

Stage B. ``open_quantity``, ``close_figures`` and ``leg_value`` are the
arithmetic; the portfolio stores floats and still rounds the trade record
to cents. The balance moves by the unrounded net. A book that added the
already-rounded net would publish a different balance on the sequence
below, which is why that sequence is fixed rather than a fresh sample.
"""
from __future__ import annotations

import pytest

from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeIdea
from bot.utils.paper_money import close_figures, leg_value, open_quantity


def _rows() -> list[tuple[float, float, float, int, bool]]:
    """(entry, exit, margin, leverage, is_long). One commission, 0.06."""
    base = [
        (50000.0, 55000.0, 200.0, 1, True),
        (50000.0, 45000.0, 200.0, 1, True),
        (50000.0, 51000.0, 1.0, 1, True),
        (4321.5, 4100.25, 33.84, 5, False),
        (0.0522, 0.061, 50.0, 10, True),
        (63000.25, 63000.25, 100.0, 20, False),
        (2.367, 2.1, 10.0, 3, True),
        (184.6, 190.0, 25.5, 2, False),
    ]
    extras = []
    for i in range(40):
        entry = 100 + i * 0.37
        exit_p = entry + 1.13 + (i % 7) * 0.019
        size = 50 + (i % 5) * 3.33
        lev = 1 + (i % 4)
        extras.append((entry, exit_p, size, lev, i % 2 == 0))
    return base + extras


_PCT = 0.06


def _float_close(entry, exit_p, qty, lev, pct, is_long):
    """The formula the portfolio used before this reading."""
    gross = (exit_p - entry) * qty if is_long else (entry - exit_p) * qty
    notional = entry * qty
    margin = notional / lev
    commission = (notional + exit_p * qty) * (pct / 100)
    return gross, commission, gross - commission, margin


def _float_leg(entry, qty, lev, mark, is_long):
    margin = entry * qty / lev
    upnl = (mark - entry) * qty if is_long else (entry - mark) * qty
    return margin + upnl, upnl


def _books():
    """Published cents of the unrounded book and of one that adds rounded nets."""
    full = 10000.0
    rounded = 10000.0
    for entry, exit_p, size, lev, is_long in _rows():
        qty = open_quantity(size, lev, entry)
        _, _, net, margin = _float_close(entry, exit_p, qty, lev, _PCT, is_long)
        full += -size + margin + net
        rounded += -size + margin + round(net, 2)
    return round(full, 2), round(rounded, 2)


def _idea(idea_id: str, entry: float, direction: Direction) -> TradeIdea:
    if direction == Direction.LONG:
        sl, tp = entry * 0.98, entry * 1.04
    else:
        sl, tp = entry * 1.02, entry * 0.96
    return TradeIdea(
        id=idea_id,
        asset="BTC/USDT",
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=0.7,
        reasoning="paper money sequence",
        signals_used=["test"],
    )


class TestThePublishedCentsMatchTheFloatPath:
    def test_each_close_rounds_to_the_float_cents(self):
        for entry, exit_p, size, lev, is_long in _rows():
            qty = open_quantity(size, lev, entry)
            assert qty == round(size * lev / entry, 8)
            gross, commission, net, margin = _float_close(
                entry, exit_p, qty, lev, _PCT, is_long,
            )
            fig = close_figures(
                entry, exit_p, qty, lev, _PCT, is_long=is_long,
            )
            assert round(fig.gross, 2) == round(gross, 2)
            assert round(fig.commission, 2) == round(commission, 2)
            assert round(fig.net, 2) == round(net, 2)
            assert round(fig.margin + fig.net, 2) == round(margin + net, 2)

    def test_adding_the_rounded_net_publishes_a_different_balance(self):
        full, rounded = _books()
        assert full != rounded

    def test_the_book_publishes_the_unrounded_balance(self):
        port = PortfolioTracker(initial_balance=10000.0, commission_pct=_PCT)
        for i, (entry, exit_p, size, lev, is_long) in enumerate(_rows()):
            direction = Direction.LONG if is_long else Direction.SHORT
            idea = _idea(f"TI-seq-{i}", entry, direction)
            opened = port.open_position(idea, size, leverage=lev)
            qty = open_quantity(size, lev, entry)
            assert opened.quantity == qty
            gross, commission, net, _margin = _float_close(
                entry, exit_p, qty, lev, _PCT, is_long,
            )
            closed = port.close_position(idea.id, exit_p)
            assert closed is not None
            assert closed.pnl == round(net, 2)
            assert closed.gross_pnl == round(gross, 2)
            assert closed.commission == round(commission, 2)
        full, _rounded = _books()
        assert round(port.balance, 2) == full


class TestTheBookAsksTheReading:
    def test_a_patched_close_moves_the_record_and_the_balance(self, monkeypatch):
        from bot.utils import paper_money

        def bump(*args, **kwargs):
            fig = paper_money.close_figures(*args, **kwargs)
            return fig._replace(net=fig.net + 1.25)

        monkeypatch.setattr("bot.risk.portfolio.close_figures", bump)
        port = PortfolioTracker(initial_balance=10000.0, commission_pct=0.0)
        idea = _idea("TI-bump", 100.0, Direction.LONG)
        port.open_position(idea, 100.0, leverage=1)
        closed = port.close_position(idea.id, 110.0)
        assert closed is not None
        assert closed.pnl == 11.25
        assert round(port.balance, 2) == 10011.25

    def test_a_patched_leg_moves_exposure_equity_and_the_peak(self, monkeypatch):
        from bot.utils import paper_money

        def bump(*args, **kwargs):
            contrib, upnl = paper_money.leg_value(*args, **kwargs)
            return contrib + 100.0, upnl + 100.0

        monkeypatch.setattr("bot.risk.portfolio.leg_value", bump)
        port = PortfolioTracker(initial_balance=10000.0, commission_pct=0.0)
        btc = _idea("TI-btc", 50000.0, Direction.LONG)
        eth = TradeIdea(
            id="TI-eth",
            asset="ETH/USDT",
            direction=Direction.SHORT,
            entry_price=3000.0,
            stop_loss=3100.0,
            take_profit=2800.0,
            confidence=0.7,
            reasoning="second leg",
            signals_used=["test"],
        )
        ob = port.open_position(btc, 200.0, leverage=1)
        oe = port.open_position(eth, 100.0, leverage=5)
        port.mark_to_market({"BTC/USDT": 52000.0, "ETH/USDT": 2900.0})
        # One patch, three readers. Each leg is the float figure plus the
        # planted 100, so a reader that skipped the reading stays on the
        # float sum.
        float_exposure = (
            _float_leg(50000.0, ob.quantity, 1, 52000.0, True)[0]
            + _float_leg(3000.0, oe.quantity, 5, 2900.0, False)[0]
        )
        planted = float_exposure + 200.0
        assert port.get_position_value() == pytest.approx(planted, abs=1e-6)
        assert port.snapshot().equity_usd == round(port.balance + planted, 2)
        assert port._peak_equity == pytest.approx(port.balance + planted, abs=1e-6)

    def test_the_open_stores_the_quantity_the_reading_returned(self, monkeypatch):
        monkeypatch.setattr("bot.risk.portfolio.open_quantity", lambda *a, **k: 0.123456789)
        port = PortfolioTracker(initial_balance=10000.0, commission_pct=0.0)
        idea = _idea("TI-qty", 50000.0, Direction.LONG)
        opened = port.open_position(idea, 100.0, leverage=1)
        assert opened.quantity == 0.123456789

    def test_two_open_legs_sum_to_the_float_equity(self):
        port = PortfolioTracker(initial_balance=10000.0, commission_pct=0.0)
        btc = _idea("TI-btc2", 50000.0, Direction.LONG)
        eth = TradeIdea(
            id="TI-eth2",
            asset="ETH/USDT",
            direction=Direction.SHORT,
            entry_price=3000.0,
            stop_loss=3100.0,
            take_profit=2800.0,
            confidence=0.7,
            reasoning="equity",
            signals_used=["test"],
        )
        ob = port.open_position(btc, 200.0, leverage=1)
        oe = port.open_position(eth, 100.0, leverage=5)
        marks = {"BTC/USDT": 52000.0, "ETH/USDT": 2900.0}
        port.mark_to_market(marks)
        btc_leg = _float_leg(50000.0, ob.quantity, 1, 52000.0, True)
        eth_leg = _float_leg(3000.0, oe.quantity, 5, 2900.0, False)
        exposure = btc_leg[0] + eth_leg[0]
        assert abs(port.get_position_value() - exposure) < 1e-9
        assert round(port.get_position_value(), 2) == round(exposure, 2)
        equity = port.balance + exposure
        assert port.snapshot().equity_usd == round(equity, 2)


class TestAnUnreadableFigureIsNotSettled:
    def test_nan_none_and_infinity_raise(self):
        bad = (float("nan"), None, float("inf"))
        for value in bad:
            with pytest.raises(ValueError):
                close_figures(value, 110.0, 1.0, 1, 0.0, is_long=True)
            with pytest.raises(ValueError):
                open_quantity(100.0, 1, value)
            with pytest.raises(ValueError):
                leg_value(100.0, 1.0, 1, value, is_long=True)

    def test_a_zero_commission_is_a_book_that_charges_nothing(self):
        fig = close_figures(100.0, 110.0, 2.0, 1, 0, is_long=True)
        assert fig.gross == 20.0
        assert fig.commission == 0.0
        assert fig.net == 20.0
        assert fig.margin == 200.0

    def test_a_non_positive_leverage_raises(self):
        with pytest.raises(ValueError):
            close_figures(100.0, 110.0, 1.0, 0, 0.0, is_long=True)
        with pytest.raises(ValueError):
            open_quantity(100.0, 0, 50.0)
        with pytest.raises(ValueError):
            leg_value(100.0, 1.0, 0, 110.0, is_long=True)

    def test_a_bool_leverage_is_not_a_figure(self):
        with pytest.raises(ValueError):
            close_figures(100.0, 110.0, 1.0, True, 0.0, is_long=True)

    def test_a_stored_zero_leverage_still_settles_as_one(self):
        # The fallback lives at the portfolio call, beside the read. Moving
        # it into the reading would refuse a row the book has always closed.
        port = PortfolioTracker(initial_balance=1000.0, commission_pct=0.0)
        idea = _idea("TI-lev0", 100.0, Direction.LONG)
        port.open_position(idea, 100.0, leverage=1)
        port.open_positions[0].leverage = 0
        closed = port.close_position(idea.id, 110.0)
        assert closed is not None
        assert closed.pnl == 10.0

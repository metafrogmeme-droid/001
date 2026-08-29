"""The concentration check scored a portfolio nobody was holding.

`_check_concentration` gated on `len(open_positions) >= 2` and then built its
returns matrix out of CLOSED-TRADE history. Those two sets need not share a
single asset, so the check could report healthy diversification from last
month's BTC and ETH round-trips while the open book was five correlated
alt-longs — the exact failure a concentration limit exists to catch, scored
against the wrong portfolio.

It also required 5 closed trades and 2 assets with 3 closes each, so it
answered "no data" for the whole early life of an account. A fresh book is not
a diversified one, and concentration is least survivable when there is no
history to absorb it.

The machinery to do it properly was already in the file: `_aligned_returns`
(#49) builds per-asset return series on a common timestamp grid, and the
covariance VaR has used it for a while. This check now uses the same series
over the assets actually held.

WHY THERE ARE THREE OUTCOMES AND NOT TWO
----------------------------------------
The call site rendered `CONCENTRATION_PCA: OK or skipped (no data)` — one
string for a measured pass and for a check that never ran. On the line that
decides whether a book is dangerously correlated that is CLAUDE.md's most-cited
defect, so the detail now says which happened, and a fallback to closed-trade
history says so out loud rather than passing itself off as a reading of the
open book.
"""
from __future__ import annotations

import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction


def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-conc-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


class _Pos:
    """An open position, in the two attributes the check reads."""

    def __init__(self, asset, entry_price=100.0, quantity=10.0, direction="LONG"):
        self.asset = asset
        self.entry_price = entry_price
        self.quantity = quantity
        self.direction = Direction.LONG if direction == "LONG" else Direction.SHORT


def _prices(eng, asset, series, t0=1_000.0):
    """Plant a timestamped price history, the shape _aligned_returns needs."""
    eng._price_history[asset] = [(t0 + i, p) for i, p in enumerate(series)]


def _open(eng, positions):
    """`open_positions` / `trade_history` are read-only properties over the
    tracker's own `_positions` dict and `_history` list, so the state is
    planted there rather than by assigning to the property."""
    eng._portfolio._positions = {f"t{i}": p for i, p in enumerate(positions)}


def _closed_book(eng, trades):
    eng._portfolio._history = list(trades)


# `_aligned_returns` needs var_covariance_min_points + 1 observations on the
# COMMON grid (21 at the default 20). The first draft of this file supplied 12
# and every live-book assertion failed — the fixture was under-specified, not
# the code, and a floor that low would not estimate a correlation matrix worth
# rejecting a trade over anyway.
_N = 30

# Two assets driven by the SAME shock sequence: correlated by construction.
_SHOCKS = [0.02, 0.01, -0.015, 0.025, 0.005, -0.02, 0.03, -0.01, 0.015, 0.02,
           -0.025, 0.01, 0.02, -0.005, 0.03, -0.015, 0.01, 0.025, -0.02, 0.005,
           0.02, -0.01, 0.015, 0.03, -0.025, 0.01, 0.02, -0.015, 0.005]
# A third driven by an unrelated one.
_OTHER = [-0.01, 0.03, 0.005, -0.025, 0.02, 0.015, -0.03, 0.01, -0.005, 0.025,
          0.02, -0.015, -0.01, 0.03, 0.005, 0.02, -0.025, -0.005, 0.015, 0.03,
          -0.01, 0.02, -0.03, 0.005, 0.015, -0.02, 0.025, 0.01, -0.005]


def _walk(start, shocks):
    px, out = start, [start]
    for r in shocks[:_N - 1]:
        px *= (1 + r)
        out.append(round(px, 6))
    return out


_LOCKSTEP_A = _walk(100.0, _SHOCKS)
_LOCKSTEP_B = _walk(50.0, _SHOCKS)
_INDEPENDENT = _walk(80.0, _OTHER)


class TestItReadsTheBookYouAreActuallyHolding:
    def test_a_correlated_open_book_is_caught_with_no_closed_trades_at_all(self):
        # The original could not reach a verdict here: zero closed trades meant
        # `len(history) < 5` and an unconditional skip, on a book of two assets
        # moving in lockstep.
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("BBB/USDT")])
        _prices(eng, "AAA/USDT", _LOCKSTEP_A)
        _prices(eng, "BBB/USDT", _LOCKSTEP_B)
        assert eng._portfolio.trade_history == [], "the book must start with no closes"
        failure, detail = eng._check_concentration()
        assert "live book" in detail, (
            f"the open book was not the thing measured: {detail}")
        assert failure is not None, (
            "two assets moving in lockstep did not read as concentrated")
        assert "CONCENTRATION_PCA" in failure

    def test_an_uncorrelated_open_book_passes_and_says_it_measured_it(self):
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("CCC/USDT")])
        _prices(eng, "AAA/USDT", _LOCKSTEP_A)
        _prices(eng, "CCC/USDT", _INDEPENDENT)
        failure, detail = eng._check_concentration()
        assert failure is None
        assert "live book" in detail
        assert "2 assets" in detail

    def test_the_verdict_does_not_come_from_assets_you_no_longer_hold(self):
        """The defect, stated as a test.

        Closed history is two uncorrelated assets; the OPEN book is two that
        move in lockstep. The old code scored the history and passed.
        """
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("BBB/USDT")])
        _prices(eng, "AAA/USDT", _LOCKSTEP_A)
        _prices(eng, "BBB/USDT", _LOCKSTEP_B)
        # Plant closed trades on entirely different, uncorrelated assets.
        _closed_book(eng, [
            _closed("OLD1/USDT", r) for r in (0.05, -0.03, 0.04, -0.02, 0.06)
        ] + [_closed("OLD2/USDT", r) for r in (-0.05, 0.03, -0.04, 0.02, -0.06)])
        failure, detail = eng._check_concentration()
        assert failure is not None, (
            "the correlated OPEN book passed because uncorrelated CLOSED "
            "trades were what got scored")
        assert "live book" in detail


def _closed(asset, ret):
    class _T:
        pass
    t = _T()
    t.asset = asset
    t.entry_price = 100.0
    t.exit_price = 100.0 * (1 + ret)
    t.quantity = 1.0
    t.direction = Direction.LONG
    return t


class TestAbsentIsNotAPass:
    def test_no_price_history_and_no_closes_says_it_could_not_measure(self):
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("BBB/USDT")])
        failure, detail = eng._check_concentration()
        assert failure is None, "a check that could not run must not REJECT"
        assert "could not measure" in detail, (
            f"an unmeasured book reported as a pass: {detail}")

    def test_the_closed_trade_fallback_names_itself(self):
        # Better than nothing, but it is a claim about assets the book may no
        # longer hold, so it must not read as a verdict on the open one.
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("BBB/USDT")])   # no price history
        _closed_book(eng, [
            _closed("OLD1/USDT", r) for r in (0.05, -0.03, 0.04)
        ] + [_closed("OLD2/USDT", r) for r in (-0.05, 0.03, -0.04)])
        failure, detail = eng._check_concentration()
        assert "CLOSED-TRADE history" in detail
        assert "not the open book" in detail

    def test_a_single_position_is_not_applicable_not_diversified(self):
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT")])
        failure, detail = eng._check_concentration()
        assert failure is None
        assert "not applicable" in detail

    def test_the_check_line_no_longer_conflates_pass_with_skip(self):
        # code_only() first: the comment explaining this change QUOTES the
        # string it forbids, and the first draft failed on its own prose.
        # CLAUDE.md records four false failures of exactly this shape.
        import inspect

        from tests.source_scan import code_only
        src = code_only(inspect.getsource(RiskEngine._evaluate_locked))
        assert "OK or skipped (no data)" not in src, (
            "one string for a measured pass and a check that never ran, on the "
            "line that decides whether a book is dangerously correlated")
        assert "CONCENTRATION_PCA: {conc_detail}" in src


class TestItCannotBreakARiskEvaluation:
    def test_an_unreadable_price_history_falls_back_rather_than_raising(self, monkeypatch):
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("BBB/USDT")])
        monkeypatch.setattr(
            RiskEngine, "_aligned_returns",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        failure, detail = eng._check_concentration()
        assert failure is None and "could not measure" in detail

    def test_a_full_evaluate_still_reports_the_check(self):
        from bot.utils.models import TradeIdea
        eng = _engine()
        _open(eng, [_Pos("AAA/USDT"), _Pos("BBB/USDT")])
        _prices(eng, "AAA/USDT", _LOCKSTEP_A)
        _prices(eng, "BBB/USDT", _LOCKSTEP_B)
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                         confidence=0.9, reasoning="test")
        check = eng.evaluate(idea)
        lines = check.checks_passed + check.checks_failed
        assert any("CONCENTRATION_PCA" in ln for ln in lines), (
            "check #20 vanished from the report entirely")

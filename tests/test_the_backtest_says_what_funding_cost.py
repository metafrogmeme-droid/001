"""`net_pnl_usd` was `pnl - commission`, and its own field comment said so.

A perpetual position pays or receives funding at every settlement it is open
across. The backtest charged none, so every net it reported was wrong by a
signed amount it never named — and, worse, it named nothing: a reader shown a
scorecard with Commission and Slippage rows and no funding row reads that net
as complete.

`bot/proofofpnl/csf.py` records the identical defect one module over, with the
cure this file reuses: "no funding" has to be READABLE, because on a market
that pays none it means there was nothing to charge and on one that does it
means nobody priced it, "and those must not produce the same number".

The tests below are in three groups:

* the reading (`bot/backtest/funding.py`) across its four states, its sign, and
  the boundary rule that is the whole modelling decision;
* the ENGINE, driven — a real position opened and closed through
  `BacktestEngine`, with and without a rate, because a reading nothing calls is
  this repository's signature failure;
* the two scorecards that print a net, because a fix that lands in the
  calculation and not on the card has not landed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.funding import (
    CHARGED,
    NO_SETTLEMENT,
    NOT_PERP,
    UNPRICED,
    combine,
    funding_figure,
    funding_for_position,
    funding_note,
    settlements_spanned,
)
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _ts(hour: float) -> datetime:
    return T0 + timedelta(hours=hour)


# ── the reading ─────────────────────────────────────────────────────────────

class TestSettlementsAreBoundariesCrossed:
    """The modelling decision, and the one a division gets backwards both ways."""

    def test_two_minutes_across_a_boundary_pays_once(self):
        # 07:59 -> 08:01. Two minutes. `duration / 8h` is 0.004 -> 0 settlements.
        assert settlements_spanned(_ts(7 + 59 / 60).timestamp(),
                                   _ts(8 + 1 / 60).timestamp()) == 1

    def test_eight_hours_inside_one_window_pays_nothing(self):
        # 00:01 -> 07:59. Nearly eight hours. `duration / 8h` rounds to 1.
        assert settlements_spanned(_ts(0 + 1 / 60).timestamp(),
                                   _ts(7 + 59 / 60).timestamp()) == 0

    def test_a_full_day_crosses_two_not_three(self):
        # Open AT 00:00: you do not pay for the settlement you opened on.
        assert settlements_spanned(T0.timestamp(), _ts(23.99).timestamp()) == 2

    def test_a_close_before_its_open_is_no_settlements_not_negative(self):
        assert settlements_spanned(_ts(8).timestamp(), _ts(4).timestamp()) == 0

    def test_it_reads_the_one_settlement_definition(self):
        """`funding_clock` owns the interval and the live path reads it. A
        second copy here would be a second answer the day Bitget changes it."""
        import inspect

        from bot.backtest import funding as mod
        src = inspect.getsource(mod)
        assert "SETTLEMENT_INTERVAL_SEC" in src
        assert "8 * 3600" not in src and "28800" not in src, (
            "the interval is restated here instead of read from funding_clock")


class TestTheFourStates:
    def test_no_rate_is_unpriced_and_carries_no_figure(self):
        r = funding_for_position("LONG", 10_000.0, T0, _ts(9), None, True)
        assert r.state == UNPRICED
        assert r.usd is None, "an unpriced cost must not be a zero"
        assert r.usd is None

    def test_perp_ness_unstated_is_unpriced_even_with_a_rate(self):
        """The backtest cannot infer perp-ness from "BTC/USDT" and must not try."""
        r = funding_for_position("LONG", 10_000.0, T0, _ts(9), 0.0001, None)
        assert r.state == UNPRICED and r.usd is None

    def test_stated_not_a_perp_is_a_measured_zero(self):
        r = funding_for_position("LONG", 10_000.0, T0, _ts(9), 0.0001, False)
        assert r.state == NOT_PERP
        assert r.usd == 0.0

    def test_priced_but_no_settlement_crossed_is_its_own_zero(self):
        r = funding_for_position("LONG", 10_000.0, _ts(0.1), _ts(7.9), 0.0001, True)
        assert r.state == NO_SETTLEMENT
        assert r.usd == 0.0 and r.intervals == 0

    def test_the_two_zeros_are_different_facts(self):
        """Both are 0.0 and they are not the same reading — the `funding_applies`
        distinction, which is the whole reason this is a state and not a float."""
        not_perp = funding_for_position("LONG", 1.0, T0, _ts(9), 0.0001, False)
        no_settle = funding_for_position("LONG", 1.0, _ts(0.1), _ts(7.9), 0.0001, True)
        assert not_perp.usd == no_settle.usd == 0.0
        assert not_perp.state != no_settle.state
        assert funding_note(not_perp.state, 0.0) != funding_note(no_settle.state, 0.0)

    def test_an_unreadable_hold_is_unpriced_not_zero_settlements(self):
        r = funding_for_position("LONG", 10_000.0, "not a time", _ts(9), 0.0001, True)
        assert r.state == UNPRICED and r.usd is None


class TestTheSign:
    """`pays_funding`: positive rate, longs pay shorts. A flipped sign is the
    `r_multiple` defect this repo already records, one quantity over."""

    def test_a_long_pays_a_positive_rate(self):
        r = funding_for_position("LONG", 10_000.0, _ts(7.9), _ts(8.1), 0.0001, True)
        assert r.usd == pytest.approx(-1.0), "paid funding must reduce P&L"

    def test_a_short_receives_a_positive_rate(self):
        r = funding_for_position("SHORT", 10_000.0, _ts(7.9), _ts(8.1), 0.0001, True)
        assert r.usd == pytest.approx(+1.0), "received funding must raise P&L"

    def test_a_long_receives_a_negative_rate(self):
        r = funding_for_position("LONG", 10_000.0, _ts(7.9), _ts(8.1), -0.0001, True)
        assert r.usd == pytest.approx(+1.0)

    def test_a_rate_of_exactly_zero_pays_nobody_either_way(self):
        for d in ("LONG", "SHORT"):
            r = funding_for_position(d, 10_000.0, _ts(7.9), _ts(8.1), 0.0, True)
            assert r.state == CHARGED and r.usd == 0.0

    def test_it_scales_with_settlements_and_notional(self):
        one = funding_for_position("LONG", 10_000.0, _ts(7.9), _ts(8.1), 0.0001, True)
        three = funding_for_position("LONG", 10_000.0, T0, _ts(23.99), 0.0001, True)
        assert three.intervals == 2
        assert three.usd == pytest.approx(one.usd * 2)
        bigger = funding_for_position("LONG", 20_000.0, _ts(7.9), _ts(8.1), 0.0001, True)
        assert bigger.usd == pytest.approx(one.usd * 2)


class TestARunIsUnpricedIfAnyPositionWas:
    def test_one_unpriced_position_makes_the_total_unpriced(self):
        """A sum over a set that includes unreadable rows, printed as a whole,
        is the shape CLAUDE.md tabulates."""
        assert combine([CHARGED, CHARGED, UNPRICED]) == UNPRICED

    def test_all_charged_is_charged(self):
        assert combine([CHARGED, NO_SETTLEMENT]) == CHARGED

    def test_no_positions_is_unpriced_not_a_zero_total(self):
        assert combine([]) == UNPRICED

    def test_a_word_it_does_not_know_is_unpriced(self):
        assert combine([CHARGED, "something_new"]) == UNPRICED


# ── the engine, DRIVEN ──────────────────────────────────────────────────────

def _bar(price: float, hours: float) -> BacktestBar:
    return BacktestBar(timestamp=_ts(hours), open=price, high=price,
                       low=price, close=price, volume=1000.0, symbol="BTC/USDT")


def _engine(rate, is_perp) -> BacktestEngine:
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", initial_balance=10_000.0, commission_pct=0.0,
        slippage_pct=0.0, funding_rate=rate, market_is_perp=is_perp))


def _open(eng: BacktestEngine, entry: float = 100.0, size_usd: float = 1000.0,
          direction: Direction = Direction.LONG) -> str:
    # A SHORT's stop is ABOVE its entry — `TradeIdea` validates the geometry,
    # which is the same check the `/trade` help's own example once failed.
    long_ = direction == Direction.LONG
    idea = TradeIdea(asset="BTC/USDT", direction=direction, entry_price=entry,
                     stop_loss=entry * (0.9 if long_ else 1.1),
                     take_profit=entry * (1.2 if long_ else 0.8),
                     confidence=0.7, reasoning="funding drive")
    eng.portfolio.open_position(idea, size_usd)
    eng._open_bt_positions[idea.id] = {
        "entry_time": T0, "adjusted_entry": entry, "commission_entry": 0.0,
        "slippage_entry": 0.0, "idea": idea, "risk_verdict": "APPROVED",
    }
    return idea.id


def _close_flat(eng: BacktestEngine, tid: str, hours: float = 9.0):
    """Close at the entry price, so the ONLY thing moving net is funding."""
    eng._close_position(tid, 100.0, _bar(100.0, hours), "MANUAL")
    return eng._trades[-1]


class TestTheEngineChargesWhatItPriced:
    def test_with_no_rate_nothing_is_charged_and_the_trade_says_unpriced(self):
        eng = _engine(None, None)
        t = _close_flat(eng, _open(eng))
        assert t.funding_state == UNPRICED
        assert t.funding_usd is None, "unpriced must not be recorded as 0.0"
        assert t.net_pnl_usd == pytest.approx(0.0), (
            "a flat round trip with no commission is 0 — the default must be "
            "byte-identical to the behaviour before funding existed")

    def test_a_long_across_one_settlement_pays_and_the_net_moves(self):
        eng = _engine(0.0001, True)
        t = _close_flat(eng, _open(eng))     # T0 -> +9h crosses 08:00 once
        assert t.funding_state == CHARGED and t.funding_settlements == 1
        # notional 1000 x 1bp x 1 settlement
        assert t.funding_usd == pytest.approx(-0.10)
        assert t.net_pnl_usd == pytest.approx(-0.10), (
            "the charge did not reach net_pnl_usd — the calculation landed and "
            "the record did not")

    def test_a_short_across_one_settlement_receives(self):
        eng = _engine(0.0001, True)
        t = _close_flat(eng, _open(eng, direction=Direction.SHORT))
        assert t.funding_usd == pytest.approx(+0.10)
        assert t.net_pnl_usd == pytest.approx(+0.10)

    def test_the_balance_agrees_with_the_trade_record(self):
        """Charging the record and not the balance would make equity and the
        trade log disagree about the same close."""
        priced = _engine(0.0001, True)
        _close_flat(priced, _open(priced))
        unpriced = _engine(None, None)
        _close_flat(unpriced, _open(unpriced))
        assert priced.portfolio.balance == pytest.approx(
            unpriced.portfolio.balance - 0.10), (
            "the funding transfer never reached the portfolio balance")

    def test_a_position_closed_inside_one_window_is_charged_nothing(self):
        eng = _engine(0.0001, True)
        t = _close_flat(eng, _open(eng), hours=7.9)
        assert t.funding_state == NO_SETTLEMENT
        assert t.funding_usd == 0.0 and t.net_pnl_usd == pytest.approx(0.0)

    def test_a_stated_non_perp_is_charged_nothing_and_says_so(self):
        eng = _engine(0.0001, False)
        t = _close_flat(eng, _open(eng))
        assert t.funding_state == NOT_PERP and t.funding_usd == 0.0


class TestTheScaleOutLegIsChargedToo:
    """The PARTIAL close is a second close path, and the first draft of this
    file drove only the final one — the mutation round said so by surviving
    `if False:` around the partial charge. Two close paths, two drives."""

    def _scale_out(self, eng: BacktestEngine, tid: str, qty: float, hours: float):
        from bot.core.partial_tp import create_partial_tp_state

        bt_meta = eng._open_bt_positions[tid]
        pos = eng.portfolio._positions[tid]
        # The ladder state `_partial_close` reads, built the way the existing
        # partial-TP suite builds it.
        bt_meta["ptp_state"] = create_partial_tp_state(
            trade_id=tid, direction=pos.direction.value, entry_price=100.0,
            stop_loss=90.0, take_profit=120.0, quantity=pos.quantity, atr=10.0)
        eng._partial_close(tid, bt_meta, pos, qty, 100.0, _bar(100.0, hours), "TP1")
        return eng._trades[-1]

    def test_a_scaled_out_leg_pays_on_its_own_notional(self):
        eng = _engine(0.0001, True)
        tid = _open(eng, entry=100.0, size_usd=1000.0)   # 10 units
        leg = self._scale_out(eng, tid, qty=5.0, hours=9.0)   # half, across 08:00
        assert leg.funding_state == CHARGED and leg.funding_settlements == 1
        # half the position: notional 500 x 1bp x 1 settlement
        assert leg.funding_usd == pytest.approx(-0.05)
        assert leg.net_pnl_usd == pytest.approx(-0.05)

    def test_an_unpriced_scale_out_charges_nothing_and_says_unpriced(self):
        eng = _engine(None, None)
        tid = _open(eng, entry=100.0, size_usd=1000.0)
        leg = self._scale_out(eng, tid, qty=5.0, hours=9.0)
        assert leg.funding_state == UNPRICED and leg.funding_usd is None
        assert leg.net_pnl_usd == pytest.approx(0.0)


# ── the scorecards ──────────────────────────────────────────────────────────

def _result(state: str, total):
    """The smallest BacktestResult the summary renderer will accept."""
    from bot.backtest.models import BacktestResult
    fields = {n: (0.0 if f.annotation in (float, int) else 0)
              for n, f in BacktestResult.model_fields.items() if f.is_required()}
    fields.update(symbol="BTC/USDT", timeframe="1h", start_date="2025-01-01",
                  end_date="2025-01-02", funding_state=state, total_funding=total,
                  trades=[], equity_curve=[])
    return BacktestResult(**fields)


class TestBothScorecardsSayWhich:
    def test_the_column_never_prints_a_figure_for_an_unpriced_run(self):
        assert funding_figure(UNPRICED, None) == "not priced"
        assert "0" not in funding_figure(UNPRICED, None), (
            "an unpriced run printed as a number is the whole defect")

    def test_the_column_prints_the_signed_figure_when_priced(self):
        assert funding_figure(CHARGED, -12.5) == "$-12.50"
        assert funding_figure(NOT_PERP, 0.0) == "$+0.00"

    def test_the_unpriced_sentence_says_the_net_is_before_funding(self):
        note = funding_note(UNPRICED, None)
        assert "NOT priced" in note and "before funding" in note

    def test_the_runner_card_RENDERS_both(self):
        """DRIVEN, and the first draft of this was not.

        It asserted `"Funding:" in inspect.getsource(...)` — and the comment
        above the f-string says "Funding:" too, so deleting the actual row left
        the assertion matching the PROSE. The mutation round reported it green
        over the defect it was written for, which is the false pass CLAUDE.md's
        source-scanning section opens with. Render the card and read it.
        """
        from bot.backtest import runner
        out = runner._format_result_summary(_result(UNPRICED, None))
        assert "Funding:" in out, "the printed report has no funding row"
        assert "not priced" in out
        assert "before funding" in out, "no sentence says the net excludes funding"

        priced = runner._format_result_summary(_result(CHARGED, -12.5))
        assert "$-12.50" in priced
        assert "not priced" not in priced

    def test_the_backtest_skill_card_carries_both(self):
        """A scan, because the row lives inside a guarded skill method a drive
        would have to stand the whole registry up to reach — and comments are
        stripped first, which is the lesson the test above is."""
        import pathlib

        from tests.source_scan import code_only
        src = code_only(pathlib.Path("bot/skills/skill_registry.py").read_text())
        assert "  Funding        " in src, "the /backtest scorecard has no funding row"
        assert src.count("funding_note(getattr(r,") == 1

    def test_the_paper_portfolio_card_is_deliberately_untouched(self):
        """`skill_registry`'s PnL Waterfall reads `state.total_gross_pnl` and
        `cost.llm_cost_usd` — the PAPER portfolio, a different producer with no
        BacktestResult behind it. Check reachability before fixing."""
        import inspect

        from bot.skills import skill_registry
        src = inspect.getsource(skill_registry)
        i = src.index("PnL Waterfall")
        block = src[i:i + 600]
        assert "state.total_commission" in block
        assert "funding" not in block.lower(), (
            "a funding row was added to the paper portfolio card, which has no "
            "backtest result to read one from")

"""The realized fee rate is read over the closes whose round trip the VENUE
stated, and the record says which those are.

The weekly parity card of 2026-09-28 read:

    Fees: realized 0.093%/round-trip vs modeled 0.200% -> 0.46x (better than model)

on a record 182 of whose 211 strategy exits were ticker-priced. A
ticker-priced close carries a commission `_local_close_commission` built at
the CONFIGURED rates (the entry leg at the configured entry rate, the close
leg at the configured exit rate), so the "realized" rate was the model
compared with itself -- and the comparison came out flattering because a
maker entry is modelled cheaper than the taker round trip the modeled rate
assumes. `parity._fees` counted any numeric `commission` as a fee record,
and nothing on the row said what the commission was made of.

`fee_basis` is that record: `venue` (a position-history row stated both
legs), `close_leg` (a fill or the close order stated its own leg, the entry
leg estimated beside it), `estimated` (both legs at the configured rates),
None on a row a build before this reading wrote. `_fee_reading` is the ONE
predicate: the commission arithmetic and the basis the record carries read
it, so they cannot disagree about whether a fee was stated. Parity reads the
rate over `venue` rows only, needs `MIN_FEE_SAMPLE` of them for a verdict,
names the sample it is over, and says what the other recorded commissions
are made of.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from bot.backtest.parity import (
    MIN_FEE_SAMPLE,
    fee_composition,
    fee_line,
    format_report,
    parity_summary,
)
from bot.core import web_reports as wr
from bot.core.close_lookup import FEE_BASES, FEE_CLOSE_LEG, FEE_ESTIMATED, FEE_VENUE, fee_stated
from bot.core.live_executor import LiveExecutor, closed_trade_row
from tests.test_an_unread_entry_is_an_unpriced_close import (  # noqa: F401
    _executor,
    _hist_row,
    _no_sleep,
    _pos100,
)
from tests.test_an_unstated_fill_profit_is_not_a_break_even import (
    _entry_fee,
    _exit_fee,
    _fill,
)
from tests.test_an_unstated_fill_profit_is_not_a_break_even import _own_close as _own_close_fills
from tests.test_the_history_stage_speaks_the_clients_api_family import (
    EXIT_PX,
    _found,
    _own_close,
)
from tests.test_the_parity_card_reads_the_benchmark_on_record import _digest_for, _reading

MODEL = 0.1   # per-side %, so 0.2% per round trip


# ── the vocabulary ───────────────────────────────────────────────────────────

class TestTheVocabulary:
    def test_three_words(self):
        assert FEE_BASES == (FEE_VENUE, FEE_CLOSE_LEG, FEE_ESTIMATED)
        assert len(set(FEE_BASES)) == 3

    @pytest.mark.parametrize("row, expect", [
        ({"fee_basis": FEE_VENUE}, True),
        ({"fee_basis": FEE_CLOSE_LEG}, False),
        ({"fee_basis": FEE_ESTIMATED}, False),
        ({"fee_basis": None}, False),
        ({}, False),
        ({"fee_basis": "VENUE"}, False),      # a word this build does not know
        (None, False),
        ("venue", False),
    ])
    def test_only_a_venue_stated_round_trip_is_stated(self, row, expect):
        assert fee_stated(row) is expect


# ── one predicate for the arithmetic and the record ─────────────────────────

class TestOneReading:
    @pytest.mark.parametrize("fees", [None, 0.0, -0.1])
    def test_an_unstated_fee_is_estimated(self, fees):
        assert LiveExecutor._fee_reading(fees, "close") == (None, FEE_ESTIMATED)
        assert LiveExecutor._fee_reading(fees, "round_trip") == (None, FEE_ESTIMATED)

    def test_a_stated_close_leg(self):
        assert LiveExecutor._fee_reading(0.2, "close") == (0.2, FEE_CLOSE_LEG)

    def test_a_stated_round_trip(self):
        assert LiveExecutor._fee_reading(0.55, "round_trip") == (0.55, FEE_VENUE)

    def test_the_commission_arithmetic_reads_the_same_predicate(self, monkeypatch):
        """A stated round trip is the commission; a stated close leg is added
        to the estimated entry; nothing stated is two estimates -- and every
        branch is decided by `_fee_reading`, proved by planting a reading no
        honest input produces and watching the arithmetic follow it."""
        def comm(stated, cover):
            return LiveExecutor._local_close_commission(1000.0, 950.0, 0.06, 0.04, stated, cover)
        assert comm(0.55, "round_trip") == 0.55
        assert comm(0.2, "close") == pytest.approx(0.6 + 0.2)
        assert comm(None, "close") == pytest.approx(0.6 + 0.38)
        monkeypatch.setattr(LiveExecutor, "_fee_reading",
                            staticmethod(lambda f, c: (None, FEE_ESTIMATED)))
        assert comm(0.55, "round_trip") == pytest.approx(0.6 + 0.38), (
            "the arithmetic keeps a copy of the predicate")


# ── the accounting carries the basis ────────────────────────────────────────

class TestTheAccountingCarriesIt:
    R = staticmethod(LiveExecutor._reconcile_exchange_close_pnl)

    def test_a_venue_net_with_its_fees_is_venue_stated(self):
        assert self.R(-50.5, 0.55, True, entry_notional=1000.0, entry_fee_pct=0.06).fee_basis == FEE_VENUE

    def test_a_venue_net_with_no_fee_stated_carries_no_basis(self):
        # The net is the venue's, the commission is 0.0 because the row stated
        # none: the record cannot say what the commission is made of.
        assert self.R(-50.5, 0.0, True, entry_notional=1000.0, entry_fee_pct=0.06).fee_basis is None

    def test_a_gross_with_no_entry_notional_carries_no_basis(self):
        acct = self.R(-50.0, 0.2, False, entry_notional=None, entry_fee_pct=0.06)
        assert acct.commission is None and acct.fee_basis is None

    def test_a_gross_beside_a_stated_close_leg(self):
        assert self.R(-50.0, 0.2, False, entry_notional=1000.0, entry_fee_pct=0.06).fee_basis == FEE_CLOSE_LEG

    def test_a_gross_with_no_leg_stated_is_estimated(self):
        assert self.R(-50.0, 0.0, False, entry_notional=1000.0, entry_fee_pct=0.06).fee_basis == FEE_ESTIMATED


# ── the bot's own close: every way a fee reaches it ─────────────────────────

class TestTheBotsOwnClose:
    def test_a_venue_net_stamps_venue(self):
        p = _own_close(_found(pnl=-50.5, pnl_is_net=True, fees=0.55, fees_cover="round_trip",
                              source="bitget_position_history", reason="SL HIT",
                              reason_inferred=False))
        assert p.fee_basis == FEE_VENUE
        assert p.commission == pytest.approx(0.55)

    def test_a_history_row_that_priced_it_locally_stamps_venue(self):
        p = _own_close(_found(pnl=None, fees=0.55, fees_cover="round_trip",
                              source="bitget_position_history_local_pnl"))
        assert p.fee_basis == FEE_VENUE
        assert p.commission == pytest.approx(0.55), "the venue's round trip, not a guess"

    def test_a_fill_that_priced_it_stamps_the_close_leg(self):
        p = _own_close(_found(pnl=None, fees=0.2, fees_cover="close"))
        assert p.fee_basis == FEE_CLOSE_LEG
        assert p.commission == pytest.approx(0.2 + _entry_fee(p))

    def test_the_verified_close_orders_fee_is_the_close_leg(self):
        p = _own_close(None, verify_price=EXIT_PX, verify_fee=0.15)
        assert p.fee_basis == FEE_CLOSE_LEG
        assert p.commission == pytest.approx(0.15 + _entry_fee(p))

    def test_the_fills_stage_overrides_a_round_trip_the_lookup_priced_locally(self):
        """The close order's own fills are the more direct reading of the
        close leg, and they REPLACE a round trip the history stage had
        stated for the local price: the fee is then the close leg alone,
        and the basis must say so, or a close-leg fee would be recorded as
        a venue-stated round trip."""
        ex, x = _executor(ticker=96.0)
        x.fetch_my_trades = AsyncMock(return_value=[_fill("0", "-0.2")])
        p = _pos100()
        ex._positions = {p.trade_id: p}
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 0.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        ex._fetch_bitget_close_data = AsyncMock(return_value=_found(
            pnl=None, fees=0.55, fees_cover="round_trip",
            source="bitget_position_history_local_pnl"))
        asyncio.run(ex.close_position(p.trade_id, reason="SL"))
        assert p.commission == pytest.approx(0.2 + _entry_fee(p))
        assert p.fee_basis == FEE_CLOSE_LEG

    def test_the_fills_stages_fee_is_the_close_leg(self):
        p = _own_close_fills([_fill("0", "-0.2")])
        assert p.fee_basis == FEE_CLOSE_LEG
        assert p.commission == pytest.approx(0.2 + _entry_fee(p))

    def test_nothing_stated_is_estimated(self):
        p = _own_close_fills([])
        assert p.fee_basis == FEE_ESTIMATED
        assert p.commission == pytest.approx(_entry_fee(p) + _exit_fee())

    def test_the_pessimistic_guess_is_not_a_stated_fee(self):
        p = _own_close_fills(trades_raise=True)
        assert p.fee_basis == FEE_ESTIMATED

    def test_an_unpriced_close_carries_no_basis(self):
        # A close nobody priced has no commission, so nothing to say what it
        # is made of: the field stays None rather than "estimated".
        ex, _x = _executor(ticker=0.0)
        p = _pos100()
        ex._positions = {p.trade_id: p}
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 0.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        ex._fetch_bitget_close_data = AsyncMock(return_value=None)
        asyncio.run(ex.close_position(p.trade_id, reason="SL"))
        assert p.commission is None and p.fee_basis is None


# ── a close found already done (25227) ──────────────────────────────────────

class TestACloseFoundAlreadyDone:
    def test_a_history_rows_round_trip_stamps_venue(self):
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.25")])
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.fee_basis == FEE_VENUE and p.commission == pytest.approx(0.55)

    def test_a_venue_net_stamps_venue(self):
        ex, _x = _executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value=_found(
            pnl=-50.5, pnl_is_net=True, fees=0.55, fees_cover="round_trip",
            source="bitget_position_history"))
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.fee_basis == FEE_VENUE

    def test_a_fill_stamps_the_close_leg(self):
        ex, _x = _executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value=_found(pnl=None, fees=0.2, fees_cover="close"))
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.fee_basis == FEE_CLOSE_LEG and p.commission == pytest.approx(0.2 + _entry_fee(p))

    def test_a_row_stating_no_fee_is_estimated(self):
        ex, _x = _executor(history=[_hist_row()])
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.fee_basis == FEE_ESTIMATED


# ── reconcile ───────────────────────────────────────────────────────────────

class TestReconcile:
    def _run(self, ex, p):
        ex._positions = {p.trade_id: p}
        asyncio.run(ex.reconcile_positions())

    def test_a_history_rows_round_trip_stamps_venue(self):
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.25")])
        p = _pos100()
        self._run(ex, p)
        assert p.status == "closed" and p.fee_basis == FEE_VENUE
        assert p.commission == pytest.approx(0.55)

    def test_a_venue_net_stamps_venue(self):
        ex, _x = _executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value=_found(
            pnl=-50.5, pnl_is_net=True, fees=0.55, fees_cover="round_trip",
            source="bitget_position_history"))
        p = _pos100()
        self._run(ex, p)
        assert p.status == "closed" and p.fee_basis == FEE_VENUE

    def test_a_fill_stamps_the_close_leg(self):
        ex, _x = _executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value=_found(pnl=None, fees=0.2, fees_cover="close"))
        p = _pos100()
        self._run(ex, p)
        assert p.status == "closed" and p.fee_basis == FEE_CLOSE_LEG

    def test_a_row_stating_no_fee_is_estimated(self):
        ex, _x = _executor(history=[_hist_row()])
        p = _pos100()
        self._run(ex, p)
        assert p.status == "closed" and p.fee_basis == FEE_ESTIMATED


# ── the record ──────────────────────────────────────────────────────────────

class TestTheRecord:
    def _closed(self, **over):
        p = _pos100()
        p.status = "closed"
        p.close_price = EXIT_PX
        p.gross_pnl, p.commission, p.pnl_usd = -50.0, 0.55, -50.55
        for k, v in over.items():
            setattr(p, k, v)
        return p

    def test_the_row_carries_the_basis(self):
        assert closed_trade_row(self._closed(fee_basis=FEE_VENUE))["fee_basis"] == FEE_VENUE
        assert closed_trade_row(self._closed())["fee_basis"] is None

    def _reload(self, tmp_path, rows):
        f = tmp_path / "closed.json"
        f.write_text(json.dumps(rows))
        ex = LiveExecutor()
        ex._closed_trades_file = str(f)
        ex._load_closed_trades()
        assert not ex._closed_trades_read_failed
        return ex.closed_positions

    def test_it_survives_a_restart(self, tmp_path):
        row = closed_trade_row(self._closed(fee_basis=FEE_CLOSE_LEG))
        (back,) = self._reload(tmp_path, [row])
        assert back.fee_basis == FEE_CLOSE_LEG

    def test_a_row_written_before_the_reading_has_none(self, tmp_path):
        row = closed_trade_row(self._closed())
        del row["fee_basis"]
        (back,) = self._reload(tmp_path, [row])
        assert back.fee_basis is None

    @pytest.mark.parametrize("junk", [0, 1.5, True, ["venue"], {"venue": 1}])
    def test_anything_but_a_word_is_no_basis(self, tmp_path, junk):
        row = closed_trade_row(self._closed())
        row["fee_basis"] = junk
        (back,) = self._reload(tmp_path, [row])
        assert back.fee_basis is None

    def test_a_word_this_build_does_not_know_is_kept_as_written(self, tmp_path):
        row = closed_trade_row(self._closed())
        row["fee_basis"] = "maker_rebate"
        (back,) = self._reload(tmp_path, [row])
        assert back.fee_basis == "maker_rebate"
        assert fee_stated(closed_trade_row(back)) is False


# ── parity reads the venue's rows and says so ───────────────────────────────

def _t(net=1.0, fees=0.12, basis=FEE_VENUE, entry=100.0, qty=1.0, **over):
    t = {"symbol": "BTC/USDT:USDT", "entry_price": entry, "quantity": qty,
         "cost_usd": entry * qty / 10, "leverage": 10, "pnl_usd": net,
         "gross_pnl": None if net is None else net + (fees or 0.0),
         "commission": fees, "signal_type": "regime_trend", "strategy_type": "swing",
         "close_reason": "TP HIT", "fill_source": "exchange"}
    if basis != "absent":
        t["fee_basis"] = basis
    t.update(over)
    return t


class TestParityReadsTheVenuesRows:
    def test_the_rate_is_over_the_stated_rows_only(self):
        rows = ([_t(fees=0.12) for _ in range(10)]
                + [_t(fees=0.50, basis=FEE_ESTIMATED) for _ in range(10)])
        s = parity_summary(rows, MODEL)
        assert s["fees_read"] == 20 and s["fees_stated"] == 10
        assert s["fees_estimated"] == 10 and s["fees_close_leg"] == 0
        assert s["fees_basis_unrecorded"] == 0
        assert s["realized_fee_rate"] == pytest.approx(1.2 / 1000.0), (
            "the estimated rows moved the rate")
        assert s["total_fees"] == pytest.approx(6.2), "the recorded total still counts every commission"
        assert s["fee_vs_model"] == pytest.approx(0.0012 / 0.002)

    def test_the_model_compared_with_itself_is_no_verdict(self):
        """Twenty closes whose commission IS the configured rate: the old
        reading answered 1.00x 'matches model' -- the model agreeing with
        itself -- and the new one says the venue stated nothing."""
        rows = [_t(fees=100.0 * 0.002, basis=FEE_ESTIMATED) for _ in range(20)]
        s = parity_summary(rows, MODEL)
        assert s["fees_stated"] == 0 and s["realized_fee_rate"] is None
        assert s["fee_vs_model"] is None
        out = format_report(s)
        assert "the venue stated a round trip on none of the 20 closes" in out
        assert "matches model" not in out and "better than model" not in out

    def test_a_close_leg_row_is_not_a_stated_round_trip(self):
        rows = ([_t(fees=0.12) for _ in range(10)]
                + [_t(fees=0.40, basis=FEE_CLOSE_LEG) for _ in range(5)])
        s = parity_summary(rows, MODEL)
        assert s["fees_stated"] == 10 and s["fees_close_leg"] == 5
        assert s["realized_fee_rate"] == pytest.approx(1.2 / 1000.0)

    def test_a_row_written_before_the_reading_is_counted_as_such(self):
        rows = [_t(fees=0.12) for _ in range(10)] + [_t(fees=0.12, basis="absent"), _t(fees=0.12, basis=None)]
        s = parity_summary(rows, MODEL)
        assert s["fees_basis_unrecorded"] == 2 and s["fees_stated"] == 10

    def test_the_counts_close_over_the_recorded_commissions(self):
        rows = ([_t() for _ in range(3)] + [_t(basis=FEE_CLOSE_LEG)] * 2
                + [_t(basis=FEE_ESTIMATED)] * 4 + [_t(basis="absent")] + [_t(fees=None)])
        s = parity_summary(rows, MODEL)
        assert s["fees_read"] == 10
        assert (s["fees_stated"] + s["fees_close_leg"] + s["fees_estimated"]
                + s["fees_basis_unrecorded"]) == s["fees_read"]

    def test_the_floor_is_min_fee_sample(self):
        under = parity_summary([_t() for _ in range(MIN_FEE_SAMPLE - 1)], MODEL)
        at = parity_summary([_t() for _ in range(MIN_FEE_SAMPLE)], MODEL)
        assert under["realized_fee_rate"] is not None and under["fee_vs_model"] is None
        assert at["fee_vs_model"] is not None
        assert under["min_fee_sample"] == MIN_FEE_SAMPLE == 10

    def test_the_sample_is_named_beside_the_verdict(self):
        rows = [_t() for _ in range(10)] + [_t(basis=FEE_ESTIMATED)] * 3 + [_t(basis=FEE_CLOSE_LEG)] * 2
        out = format_report(parity_summary(rows, MODEL))
        assert "on the 10 of 15 closes whose round trip the venue stated" in out
        assert "recorded over 15 of 15 closes (3 at the configured rates; " \
               "2 with the close leg stated and the entry leg estimated)" in out

    def test_a_record_the_venue_stated_in_full_carries_no_composition(self):
        s = parity_summary([_t() for _ in range(10)], MODEL)
        assert fee_composition(s) == ""
        assert "(" not in fee_line(s).split("recorded over")[1]


class TestTheFeeSentence:
    def _s(self, **over):
        s = {"fee_vs_model": None, "fees_read": 12, "fees_stated": 10, "trades": 12,
             "modeled_fee_rate": 0.002, "realized_fee_rate": 0.001, "total_fees": 1.5,
             "min_fee_sample": 10, "fees_estimated": 2, "fees_close_leg": 0,
             "fees_basis_unrecorded": 0, "fee_drag_of_gross": None}
        s.update(over)
        return s

    def test_a_verdict(self):
        line = fee_line(self._s(fee_vs_model=0.5))
        assert line.startswith("Fees: realized 0.100%/round-trip on the 10 of 12 closes "
                               "whose round trip the venue stated")
        assert "0.50× (better than model)" in line
        assert "$1.50 recorded over 12 of 12 closes (2 at the configured rates)" in line

    def test_withheld_under_the_floor(self):
        line = fee_line(self._s(fees_stated=4))
        assert "on the 4 of 12 closes whose round trip the venue stated" in line
        assert "verdict withheld — fewer than 10 stated" in line
        for w in ("better than model", "matches model", "WORSE than model"):
            assert w not in line

    def test_none_stated(self):
        line = fee_line(self._s(fees_stated=0, realized_fee_rate=None, fees_estimated=12))
        assert line.startswith("Fees: the venue stated a round trip on none of the 12 closes")
        assert "cannot be measured" in line and "$1.50 recorded over 12 of 12 closes" in line

    def test_no_record(self):
        line = fee_line(self._s(fees_read=0, fees_stated=0, realized_fee_rate=None, fees_estimated=0))
        assert line == "Fees: no fee record on any close — fee parity cannot be measured (modeled 0.200%/round-trip)"

    def test_the_card_prints_it(self):
        s = parity_summary([_t() for _ in range(10)], MODEL)
        assert fee_line(s) in format_report(s)

    def test_the_composition_names_three_kinds(self):
        s = self._s(fees_estimated=3, fees_close_leg=2, fees_basis_unrecorded=1)
        assert fee_composition(s) == ("3 at the configured rates; 2 with the close leg stated "
                                      "and the entry leg estimated; 1 from before the record said")


# ── the digest and the web section follow ───────────────────────────────────

class TestTheDigest:
    def test_a_withheld_ratio_names_the_stated_sample(self, monkeypatch, tmp_path):
        rows = [_t(basis=FEE_ESTIMATED) for _ in range(4)]
        body = _digest_for(rows, monkeypatch, tmp_path, benchmark=_reading())
        assert "round trip stated by the venue on 0 of 4 closes — ratio withheld" in body
        assert "the modeled rate" not in body

    def test_a_verdict_prints_the_ratio(self, monkeypatch, tmp_path):
        # The digest reads the modeled rate off CONFIG, so the fee is derived
        # for the rate this box runs rather than typed for the live box's.
        from bot.config import CONFIG
        fee = 0.46 * (2.0 * CONFIG.risk.commission_pct / 100.0) * 100.0
        rows = [_t(fees=fee) for _ in range(10)]
        body = _digest_for(rows, monkeypatch, tmp_path, benchmark=_reading())
        assert "<code>0.46×</code> the modeled rate" in body


class TestTheWebSection:
    def test_the_stated_count_travels_and_the_dollars_do_not(self, tmp_path):
        f = tmp_path / "closed.json"
        f.write_text(json.dumps([_t() for _ in range(10)] + [_t(basis=FEE_ESTIMATED)]))
        engine = type("E", (), {})()
        engine.live_executor = type("X", (), {"_closed_trades_file": str(f)})()
        sec = wr._parity_section(engine)
        assert sec["fees_stated"] == 10 and sec["fees_read"] == 11
        assert "total_fees" not in sec and "net_pnl" not in sec

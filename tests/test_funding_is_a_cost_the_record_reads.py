"""Funding is a cost, the venue states it on the row we already fetch, and
nothing read it.

Driven, not read. `_close_from_history` fetches
`/api/v2/mix/position/history-position` and reads seven fields off the matched
row; the row also carries `totalFunding`, and a grep of `bot/` for funding found
only the entry-time WARN. `closed_trade_row` had no funding field at all, so
every figure built on `pnl_usd` was short by it -- the governor's window, the
loss streak, the cooldown, the daily accumulator, the throttle's profit factor,
parity's net, /performance, the journal's R and the public close return.

The rule the backtest already follows: "no funding" has to be READABLE, because
on a market that pays none it means there was nothing to charge and on one that
does it means nobody priced it, and those must not produce the same number.

WHICH FIGURE CONTAINS IT IS MEASURED HERE, NOT BELIEVED. The vendored venue docs
document the UTA twins of these fields, but the call is the CLASSIC v2 endpoint,
so the relationship is checked against each row's own arithmetic:

    funding-inclusive:  netProfit == pnl + funding - (|openFee| + |closeFee|)
    fee-only:           netProfit == pnl - (|openFee| + |closeFee|)
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.close_funding import (
    NET_EXCLUDES,
    NET_INCLUDES,
    UNPLACED,
    UNSTATED,
    ZERO,
    funding_on_row,
    place_funding,
)
from bot.core.live_executor import (
    CloseAccounting,
    LiveExecutor,
    LivePosition,
    close_funding_text,
    closed_trade_row,
)
from bot.core.venues import get_venue
from tests.source_scan import code_only
from tests.test_an_unread_entry_is_an_unpriced_close import (  # noqa: F401
    _no_sleep,
    _pos100,
)

UTC = timezone.utc
OPENED = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
OPENED_MS = int(OPENED.timestamp() * 1000)

#: One row, two ways. Gross 12.50, funding PAID 0.84, fees 1.22 -- so the
#: funding-inclusive net is 10.44 and the fee-only net is 11.28. The two differ
#: by exactly the funding, which is what lets a row decide for itself.
GROSS, FUND, OPEN_FEE, CLOSE_FEE = 12.50, -0.84, -0.60, -0.62
FEES = abs(OPEN_FEE) + abs(CLOSE_FEE)
NET_INC = GROSS + FUND - FEES
NET_EXC = GROSS - FEES


def _row(net, **kw):
    r = {"openAvgPrice": "100000", "closeAvgPrice": "101250", "pnl": str(GROSS),
         "netProfit": str(net), "openFee": str(OPEN_FEE),
         "closeFee": str(CLOSE_FEE), "totalFunding": str(FUND),
         "closeType": "tp"}
    r.update(kw)
    return r


def _executor() -> LiveExecutor:
    ex = LiveExecutor()
    ex._exchange = AsyncMock()
    ex._venue = get_venue("bitget")
    ex._save_positions = MagicMock()
    ex._save_closed_trades = MagicMock()
    ex._fire_position_closed = MagicMock()
    return ex


def _pos(**kw) -> LivePosition:
    base = dict(
        trade_id="T-1", symbol="BTC/USDT", direction="LONG", entry_price=100_000.0,
        quantity=0.01, cost_usd=100.0, stop_loss=95_000.0, take_profit=110_000.0,
        leverage=10, opened_at=OPENED, status="open",
    )
    base.update(kw)
    return LivePosition(**base)


class TestTheRowDecidesForItself:
    """The arithmetic places the funding; no doc for a sibling endpoint does."""

    def test_a_row_whose_net_carries_the_funding_says_so(self):
        assert funding_on_row(_row(NET_INC)) == (FUND, True, NET_INCLUDES)

    def test_a_row_whose_net_is_fee_only_says_so(self):
        assert funding_on_row(_row(NET_EXC)) == (FUND, False, NET_EXCLUDES)

    def test_a_stated_zero_is_a_measurement_and_is_in_every_net(self):
        """The venue's own words: zero means no fees have been charged."""
        assert funding_on_row(_row(NET_EXC, totalFunding="0")) == (0.0, True, ZERO)

    def test_the_vendored_venue_sample_reads_as_a_zero(self):
        """The payload in this repo's own copy of the venue's docs."""
        uta = {"cumRealisedPnl": "-0.116", "netProfit": "-45.588",
               "totalFunding": "0", "openFeeTotal": "-22.7360116",
               "closeFeeTotal": "-22.7359884"}
        assert funding_on_row(uta).basis == ZERO

    def test_no_funding_field_is_unstated_and_carries_no_figure(self):
        row = _row(NET_INC)
        row.pop("totalFunding")
        assert funding_on_row(row) == (None, None, UNSTATED)

    def test_a_flag_is_not_a_cost(self):
        """`float(True)` is 1.0, and a bool read as a funding is a dollar."""
        assert funding_on_row(_row(NET_INC, totalFunding=True)).usd is None

    @pytest.mark.parametrize("drop", ["pnl", "netProfit"])
    def test_a_row_missing_either_side_cannot_place_it(self, drop):
        row = _row(NET_INC)
        row.pop(drop)
        read = funding_on_row(row)
        assert read.usd == FUND and read.in_net is None and read.basis == UNPLACED

    def test_an_arithmetic_that_closes_to_neither_is_unplaced(self):
        read = funding_on_row(_row(999.0))
        assert read.usd == FUND and read.in_net is None

    @pytest.mark.parametrize("drop", [("openFee",), ("closeFee",),
                                      ("openFee", "closeFee")])
    def test_a_fee_the_row_does_not_state_is_not_a_fee_of_zero(self, drop):
        """The UTA payload spells the fees `openFeeTotal`/`closeFeeTotal`.

        Reading the gap as zero fees decides the question off an arithmetic
        missing a term, so a row that does not state BOTH legs cannot say.

        The ONE-leg cases are what make this measurable. The first draft
        skipped an unstated leg and left the total at its 0.0 start -- the same
        number -- so a mutation replacing the guard with `abs(fee or 0.0)`
        survived a green suite, and this test's own claim was unchecked.
        """
        row = _row(NET_INC)
        for k in drop:
            row.pop(k)
        read = funding_on_row(row)
        assert read.usd == FUND and read.in_net is None and read.basis == UNPLACED

    def test_one_stated_leg_is_not_a_round_trip(self):
        """A row whose net closes on ONE leg still cannot be placed.

        Built so the fee-only candidate fits exactly with the close leg alone:
        under the skip-and-carry-on reading this row DECIDED, off half the fees.
        """
        one_leg = GROSS - abs(CLOSE_FEE)
        row = _row(one_leg)
        row.pop("openFee")
        assert funding_on_row(row).basis == UNPLACED

    def test_the_two_candidates_differ_by_exactly_the_funding(self):
        """Why one row can decide: the readings are one term apart."""
        assert NET_INC == pytest.approx(NET_EXC + FUND)

    def test_a_funding_inside_the_tolerance_decides_nothing(self):
        """Both candidates fit, so neither is confirmed.

        The two readings are one funding apart, so a funding smaller than the
        tolerance leaves them indistinguishable -- and a rule that picked one
        there would be claiming a measurement it did not make. Planning the
        mutation round is what found this: with the corpus's 0.84 funding, both
        `d_exc > tol` clauses could be deleted and no verdict moved.
        """
        tiny = -0.004
        row = _row(GROSS - FEES, totalFunding=str(tiny))
        read = funding_on_row(row)
        assert read.usd == tiny and read.in_net is None and read.basis == UNPLACED

    @pytest.mark.parametrize("junk", ["nan", "inf", "-inf"])
    def test_a_funding_that_is_not_a_finite_number_is_unstated(self, junk):
        """`float("nan")` parses, and every comparison against it is False --
        so a NaN funding read as a figure would fold a NaN into the net."""
        assert funding_on_row(_row(NET_INC, totalFunding=junk)).usd is None

    def test_the_gross_is_read_under_the_other_name_the_payload_uses(self):
        """`achievedProfits` is the row's second spelling of the gross.

        No fixture reached that fallback until the mutation round was planned:
        deleting it changed no verdict, because every row in the corpus spells
        the field `pnl`.
        """
        row = _row(NET_INC)
        row["achievedProfits"] = row.pop("pnl")
        assert funding_on_row(row) == (FUND, True, NET_INCLUDES)

    def test_a_cent_alone_is_too_tight_on_a_large_position(self):
        """The relative term, driven -- the docstring's own claim.

        A close is the average of several partial fills and the venue spells
        its numbers to eight decimals, so on a four-figure net the arithmetic
        can miss by more than a cent and still be the same reading. Under
        `ABS_TOL` alone this row decides nothing.

        The whole row scales, because funding is a RATE on notional: the first
        draft of this fixture multiplied the gross by a thousand and left the
        funding at 0.84, which is a position no venue can produce and which
        read UNPLACED for the reason the test below states.
        """
        row = _row(str((GROSS + FUND - FEES) * 1000 + 0.9),
                   pnl=str(GROSS * 1000), openFee=str(OPEN_FEE * 1000),
                   closeFee=str(CLOSE_FEE * 1000),
                   totalFunding=str(FUND * 1000))
        assert funding_on_row(row).in_net is True

    def test_a_funding_under_the_tolerance_cannot_be_placed_at_any_size(self):
        """A stated cost of the tolerance's own scale, driven and recorded.

        The two candidates are exactly one funding apart, so a row can only
        decide when the tolerance is SMALLER than the funding -- and the
        relative term grows with the net. A big winner whose funding is under a
        tenth of a percent of its net therefore reads UNPLACED, the funding is
        never folded, and the card says the content is unknown.

        That is the conservative failure rather than a fabricated figure, and it
        is the cost of the relative term: `ABS_TOL` alone would place this row.
        Driven here so the exclusion is a recorded limit rather than something
        the next reader discovers from a card.
        """
        big = GROSS * 1000
        row = _row(str(big + FUND - FEES), pnl=str(big), totalFunding=str(FUND))
        read = funding_on_row(row)
        assert read.usd == FUND and read.in_net is None and read.basis == UNPLACED

    def test_a_relative_term_alone_is_too_tight_on_a_small_one(self):
        """And the absolute floor, driven, on a net of a few cents."""
        row = _row(-0.316, pnl="0.5", openFee="-0.3", closeFee="-0.35",
                   totalFunding="-0.17")
        assert funding_on_row(row).in_net is True

    def test_a_non_dict_row_is_unstated_rather_than_a_raise(self):
        assert funding_on_row(None) == (None, None, UNSTATED)
        assert funding_on_row("row") == (None, None, UNSTATED)


class TestTheDocsThisModuleQuotes:
    """The vendored venue docs still say what the module reasons from.

    A doc update that changed either sentence would leave the module's stated
    basis false, so the two sentences are read out of the file rather than
    remembered.
    """

    DOC = pathlib.Path("docs/bitget-uta/trade.md")

    def test_the_gross_twin_excludes_funding(self):
        assert "Excluding fees and funding costs" in self.DOC.read_text()

    def test_the_net_twin_includes_funding(self):
        assert "Including fees and funding costs" in self.DOC.read_text()

    def test_a_stated_zero_means_no_funding_was_charged(self):
        assert ("If the value is zero, it indicates no fees have been charged"
                in self.DOC.read_text())


class TestPlacingIt:
    """Four inputs, one arm each -- and the reconcile ASKS, never restates.

    The first draft of this slice spelled these four arms out inside
    `_reconcile_exchange_close_pnl` and left the leaf with no production caller
    at all: a second answer to one question, and the fifth granularity beside
    it. `test_no_new_unreachable_functions` named it before the gate did.
    """

    def test_a_funding_inside_the_reported_figure_comes_out_of_the_base(self):
        """So the recorded gross stays the price move, not the move plus it."""
        assert place_funding(NET_INC, FUND, True) == (NET_INC - FUND, FUND)

    def test_a_funding_outside_it_leaves_the_base_alone_and_is_still_added(self):
        assert place_funding(NET_EXC, FUND, False) == (NET_EXC, FUND)

    def test_an_unplaceable_funding_is_never_added(self):
        """Folding on a None would be a guess in the flattering direction."""
        assert place_funding(NET_EXC, FUND, None) == (NET_EXC, None)

    def test_an_unstated_funding_is_never_added(self):
        assert place_funding(NET_EXC, None, None) == (NET_EXC, None)

    def test_a_stated_zero_is_placed_and_adds_nothing(self):
        """`ZERO` reads in-net, and by arithmetic it already is."""
        assert place_funding(NET_EXC, 0.0, True) == (NET_EXC, 0.0)

    def test_the_addend_is_signed_as_the_venue_states_it(self):
        """A position that PAID funding carries a negative figure."""
        assert place_funding(NET_EXC, FUND, False).add_to_net < 0.0

    def test_the_reconcile_asks_this_leaf_rather_than_restating_it(self):
        """Patch the leaf and read what the reconcile answers.

        A byte-identical copy of these four arms agrees with every fixture in
        this file and diverges on the first edit to either, which is what a
        second copy looks like from outside. So the walk is DRIVEN: a planted
        placement the arms could never produce has to reach the figures.
        """
        import bot.core.live_executor as le
        from bot.core.close_funding import FundingPlacement

        sentinel = 999.0
        real = le.place_funding
        le.place_funding = lambda *_a, **_k: FundingPlacement(sentinel, 0.5)
        try:
            acct = LiveExecutor._reconcile_exchange_close_pnl(
                NET_INC, FEES, True, entry_notional=None, entry_fee_pct=0.06,
                funding=FUND, funding_in_pnl=True)
        finally:
            le.place_funding = real
        assert acct.gross_pnl == pytest.approx(sentinel + FEES)
        assert acct.net_pnl == pytest.approx(sentinel + 0.5)


class TestTheGrossStopsAbsorbingIt:
    """`gross + fees` made the recorded gross the price move MINUS the funding."""

    R = staticmethod(LiveExecutor._reconcile_exchange_close_pnl)

    def test_the_price_move_is_the_gross_when_the_net_carries_funding(self):
        acct = self.R(NET_INC, FEES, True, entry_notional=None, entry_fee_pct=0.06,
                      funding=FUND, funding_in_pnl=True)
        assert acct.gross_pnl == pytest.approx(GROSS)
        assert acct.net_pnl == pytest.approx(NET_INC)
        assert acct.commission == pytest.approx(FEES)
        assert acct.funding_usd == FUND and acct.funding_in_net is True

    def test_the_triple_closes(self):
        acct = self.R(NET_INC, FEES, True, entry_notional=None, entry_fee_pct=0.06,
                      funding=FUND, funding_in_pnl=True)
        assert (acct.gross_pnl - acct.commission + acct.funding_usd
                == pytest.approx(acct.net_pnl))

    def test_the_two_branches_land_on_one_net(self):
        """The venue's own net, and a net derived from its gross, agree."""
        from_net = self.R(NET_INC, FEES, True, entry_notional=None,
                          entry_fee_pct=0.06, funding=FUND, funding_in_pnl=True)
        from_gross = self.R(NET_EXC, FEES, True, entry_notional=None,
                            entry_fee_pct=0.06, funding=FUND, funding_in_pnl=False)
        assert from_net.net_pnl == pytest.approx(from_gross.net_pnl)
        assert from_net.gross_pnl == pytest.approx(from_gross.gross_pnl)

    def test_an_unplaceable_funding_changes_no_figure(self):
        acct = self.R(NET_INC, FEES, True, entry_notional=None, entry_fee_pct=0.06,
                      funding=FUND, funding_in_pnl=None)
        assert acct.net_pnl == pytest.approx(NET_INC)
        assert acct.funding_usd == FUND and acct.funding_in_net is None

    def test_an_unstated_funding_is_byte_identical_to_before_this_slice(self):
        """Every fill-priced and ticker-priced close takes this path."""
        acct = self.R(NET_INC, FEES, True, entry_notional=None, entry_fee_pct=0.06)
        assert (acct.gross_pnl, acct.net_pnl, acct.commission) == (
            pytest.approx(NET_INC + FEES), pytest.approx(NET_INC),
            pytest.approx(FEES))
        assert acct.funding_usd is None and acct.funding_in_net is None

    def test_an_unpriceable_close_records_no_funding_placement(self):
        acct = self.R(GROSS, FEES, False, entry_notional=None, entry_fee_pct=0.06,
                      funding=FUND, funding_in_pnl=False)
        assert acct.net_pnl is None and acct.funding_in_net is None

    def test_it_is_a_named_row_so_the_next_figure_moves_nothing(self):
        acct = self.R(1.0, 0.1, True, entry_notional=None, entry_fee_pct=0.06)
        assert isinstance(acct, CloseAccounting)
        assert acct._fields == ("gross_pnl", "net_pnl", "commission",
                                "funding_usd", "funding_in_net")


class TestTheStageReportsIt:
    """Driven through the real lookup against a planted venue."""

    @pytest.mark.asyncio
    async def test_the_venues_net_carries_the_row_s_verdict(self):
        ex = _executor()
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [_row(NET_INC)]}})
        data = await ex._fetch_bitget_close_data(_pos())
        assert data["funding"] == FUND
        assert data["funding_in_pnl"] is True
        assert data["funding_basis"] == NET_INCLUDES
        assert data["pnl"] == pytest.approx(NET_INC)

    @pytest.mark.asyncio
    async def test_a_net_derived_from_the_gross_is_told_it_has_no_funding(self):
        """The branch that flagged `gross - fees` as net and dropped funding."""
        ex = _executor()
        row = _row(NET_INC, netProfit="0")
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [row]}})
        data = await ex._fetch_bitget_close_data(_pos())
        assert data["pnl"] == pytest.approx(NET_EXC)
        assert data["funding_in_pnl"] is False
        assert data["funding"] == FUND

    @pytest.mark.asyncio
    async def test_a_netprofit_row_that_cannot_place_it_says_so(self):
        """The row's OWN verdict, not `True` because the branch is the net one.

        `_fund_in_pnl = True` hard-coded on this branch survived a whole round,
        because every fixture here planted a row whose arithmetic really did
        put the funding inside the net. A row whose net closes to NEITHER
        candidate is the input that separates them -- and reading it as in-net
        would strip a funding out of a base that may never have carried it.
        """
        ex = _executor()
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [_row(999.0)]}})
        data = await ex._fetch_bitget_close_data(_pos())
        assert data["pnl"] == pytest.approx(999.0)
        assert data["pnl_is_net"] is True
        assert data["funding"] == FUND
        assert data["funding_in_pnl"] is None
        assert data["funding_basis"] == UNPLACED

    @pytest.mark.asyncio
    async def test_a_row_with_no_funding_field_says_unstated(self):
        ex = _executor()
        row = _row(NET_INC)
        row.pop("totalFunding")
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [row]}})
        data = await ex._fetch_bitget_close_data(_pos())
        assert data["funding"] is None
        assert data["funding_basis"] == UNSTATED


class TestEveryCloseSiteCarriesItEndToEnd:
    """The CABLE, driven -- which four mutations walked through.

    The stage reports the funding and the reconcile places it, and nothing
    checked the wire between them at either real call site: dropping the
    `funding=`/`funding_in_pnl=` arguments, and dropping the two lines that
    write the placement onto the position, each survived a whole round. That is
    `capability_answer`'s `extras` shape -- a socket with no cable -- on the two
    paths that book every live close.

    Driven through the real lookup against a planted history row, so the chain
    is stage -> reconcile -> position -> card with nothing stubbed between.
    """

    HIST = {"openAvgPrice": "100", "closeAvgPrice": "105", "closeType": "tp",
            "pnl": "50.0", "netProfit": "48.16", "openFee": "-0.60",
            "closeFee": "-0.63", "totalFunding": "-0.61"}
    #: 50.00 - 0.61 - 1.23 == 48.16, so the row places the funding INSIDE its
    #: net and the recorded gross must come back the bare price move.

    def _driven(self, path):
        # Imported here rather than aliased at module scope: this file has an
        # `_executor` of its own, and the two spellings of one module cannot be
        # sorted into one import block without an unused-import suppression on
        # the wrong statement.
        from tests.test_an_unread_entry_is_an_unpriced_close import (
            _executor as wired,
        )

        ex, _x = wired(history=[self.HIST])
        p = _pos100()
        if path == "already_closed":
            msg = asyncio.run(ex._handle_already_closed_position(p))
        else:
            ex._positions = {p.trade_id: p}
            ex._verify_position_closed = AsyncMock(return_value={
                "confirmed": True, "fill_price": 105.0, "fill_qty": 10.0,
                "fees": 0.0, "remaining_qty": 0.0, "failure_stage": ""})
            msg = asyncio.run(ex.close_position(p.trade_id, reason="TP"))
        return p, msg

    @pytest.mark.parametrize("path", ["own_close", "already_closed"])
    def test_the_position_carries_what_the_row_stated(self, path):
        p, _msg = self._driven(path)
        assert p.funding_usd == pytest.approx(-0.61), (
            "the stage read the funding and the position never got it")
        assert p.funding_in_net is True

    @pytest.mark.parametrize("path", ["own_close", "already_closed"])
    def test_the_gross_is_the_price_move_and_the_triple_closes(self, path):
        p, _msg = self._driven(path)
        assert p.gross_pnl == pytest.approx(50.0), (
            "the recorded gross absorbed the funding")
        assert (p.gross_pnl - p.commission + p.funding_usd
                == pytest.approx(p.pnl_usd))

    @pytest.mark.parametrize("path", ["own_close", "already_closed"])
    def test_the_card_prints_the_row(self, path):
        _p, msg = self._driven(path)
        assert "Funding: -$0.6100" in msg, msg

    @pytest.mark.parametrize("path", ["own_close", "already_closed"])
    def test_the_closed_record_carries_it(self, path):
        p, _msg = self._driven(path)
        row = closed_trade_row(p)
        assert row["funding_usd"] == pytest.approx(-0.61)
        assert row["funding_in_net"] is True


class TestTheRecordCarriesIt:
    def test_the_row_writes_both_fields(self):
        pos = _pos(status="closed", pnl_usd=NET_INC, gross_pnl=GROSS,
                   commission=FEES, funding_usd=FUND, funding_in_net=True)
        row = closed_trade_row(pos)
        assert row["funding_usd"] == FUND and row["funding_in_net"] is True

    def test_funding_is_its_own_field_and_not_a_fee(self):
        """A card that prints funding as a fee says the venue charged one."""
        pos = _pos(status="closed", commission=FEES, funding_usd=FUND,
                   funding_in_net=True)
        row = closed_trade_row(pos)
        assert row["commission"] == FEES
        assert row["funding_usd"] != row["commission"]

    def test_a_row_written_before_this_says_nothing_rather_than_zero(self):
        """Every close the bot ever booked is such a row.

        Read as 0.0 they would all report a position that crossed no
        settlement, which is the measured-zero-for-an-absence shape this whole
        slice removes.
        """
        old = closed_trade_row(_pos(status="closed", pnl_usd=1.0))
        old.pop("funding_usd")
        old.pop("funding_in_net")
        restored = LiveExecutor._closed_row_to_position(old)
        assert restored.funding_usd is None
        assert restored.funding_in_net is None

    def test_a_row_written_after_this_round_trips(self):
        pos = _pos(status="closed", pnl_usd=NET_INC, funding_usd=FUND,
                   funding_in_net=True)
        restored = LiveExecutor._closed_row_to_position(closed_trade_row(pos))
        assert restored.funding_usd == FUND
        assert restored.funding_in_net is True

    def test_a_junk_placement_flag_is_not_read_as_a_verdict(self):
        """The field is a bool or it is unknown: a string is neither."""
        row = closed_trade_row(_pos(status="closed", funding_in_net=True))
        row["funding_in_net"] = "yes"
        assert LiveExecutor._closed_row_to_position(row).funding_in_net is None


class TestTheCardSaysItOnlyWhereItBites:
    def test_a_paid_funding_inside_the_net_prints_the_figure(self):
        assert close_funding_text(FUND, True) == "Funding: -$0.8400"

    def test_a_received_funding_keeps_its_sign(self):
        assert close_funding_text(1.37, True) == "Funding: +$1.3700"

    def test_an_unplaceable_funding_says_the_content_is_unknown(self):
        text = close_funding_text(FUND, None)
        assert "unknown" in text and "-$0.8400" in text

    def test_it_never_claims_the_net_lacks_it(self):
        """None is UNKNOWN, not absent -- the first draft of this line said
        "NOT in the net above", which is the claim the tri-state exists to
        avoid making."""
        assert "NOT in" not in (close_funding_text(FUND, None) or "")

    def test_a_stated_zero_prints_no_row(self):
        assert close_funding_text(0.0, True) is None

    def test_an_unstated_funding_prints_no_row(self):
        assert close_funding_text(None, None) is None


class TestBothCloseCardsCarryTheRow:
    """A SCAN, and the reason is stated rather than left to a reader.

    Both close cards are composed inside async methods hundreds of lines long,
    behind an engine, an exchange and a notifier, so driving them to read one
    row would stand up most of the executor. What a scan can answer here is the
    one thing at risk: the row is COMPOSED into each card. What it says is
    driven above, on the pure renderer.
    """

    #: Comments and docstrings blanked, because the rule below forbids a
    #: spelling this module's own prose uses to EXPLAIN the rule -- the comment
    #: over the reconcile says `gross - commission + funding == net`. Read raw,
    #: the assertion matched my own explanation and failed on correct code,
    #: which is this repo's "strip comments first" arriving from the author's
    #: side rather than the scanner's.
    SRC = code_only(pathlib.Path("bot/core/live_executor.py").read_text())

    def test_every_card_that_prints_the_fees_line_prints_the_funding_row(self):
        fee_lines = self.SRC.count(
            'f"PnL: {pnl_str} ({pnl_pct_str}) | Fees: {fee_str} | Hold: {hold_str}')
        assert fee_lines == 2, fee_lines
        assert self.SRC.count('f"{_funding_row}"') == fee_lines

    def test_each_card_builds_the_row_from_the_position_s_own_fields(self):
        assert self.SRC.count(
            "close_funding_text(pos.funding_usd, pos.funding_in_net)") == 2

    def test_nothing_folds_funding_into_the_commission(self):
        """A card printing funding as a fee says the venue charged one."""
        assert "commission + funding" not in self.SRC
        assert "funding + commission" not in self.SRC

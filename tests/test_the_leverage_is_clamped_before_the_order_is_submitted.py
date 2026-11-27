"""The leverage an order is submitted at is one the venue can set.

A venue holds a maximum leverage per symbol and refuses a set call above it.
The refusal leaves the symbol at whatever leverage it held before, so the
order filled at a leverage nobody chose, with a margin computed for one the
venue never applied, and the fill was reported as a leverage mismatch. The
non-Bitget path clamped to the market's maximum inside its own set call,
after the order's margin had been computed; the Bitget path did not clamp.

`leverage.clamp_for_submit` is `min(requested, symbol_max, account_cap)`,
made once on the pair every reader of an order's leverages asks for
(`LiveExecutor._order_leverage_reading`), and `execute` says LEVERAGE_CLAMPED
when it lowered anything. It is reduce-only: a user's preference, the quality
ladder and the margin-risk cap all still bind, and nothing here raises a
leverage. The post-fill overshoot guard is not touched.

Driven through the real executor against a Bitget stand-in whose market row
states a maximum and whose set call refuses anything above it.
"""

from __future__ import annotations

import copy
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import leverage as lv
from bot.core import live_executor as le
from bot.core.leverage import LeveragePair, clamp_for_submit, symbol_max_leverage
from bot.core.live_executor import LiveExecutor
from tests.test_an_estimated_entry_says_so import MARKET, TICKER, Venue
from tests.test_an_estimated_entry_says_so import _drive as _execute

SPLIT = LeveragePair(5, 10)
SWAP = "ETH/USDT:USDT"


@pytest.fixture(autouse=True)
def _shipped_standards():
    old = RUNTIME.leverage_override
    RUNTIME.leverage_override = None
    try:
        assert lv.order_standards(CONFIG.exchange, None) == SPLIT, "the shipped standards"
        assert lv.leverage_ceiling(CONFIG.exchange) == 10, "the shipped ceiling"
        yield
    finally:
        RUNTIME.leverage_override = old


@pytest.fixture
def clamp_off():
    cfg = CONFIG.execution
    assert cfg.leverage_presubmit_clamp_enabled is True, "the clamp ships on"
    object.__setattr__(cfg, "leverage_presubmit_clamp_enabled", False)
    try:
        yield
    finally:
        object.__setattr__(cfg, "leverage_presubmit_clamp_enabled", True)


def _market(max_leverage, *, info=None) -> dict:
    m = copy.deepcopy(MARKET)
    m["limits"]["leverage"]["max"] = max_leverage
    if info is not None:
        m["info"] = info
    return m


def _executor(tmp_path, max_leverage=None, *, markets=None) -> LiveExecutor:
    ex = LiveExecutor(state_dir=str(tmp_path))
    if markets is None and max_leverage is not None:
        markets = {SWAP: _market(max_leverage)}
    ex._exchange = SimpleNamespace(markets=markets)
    return ex


# ── the arithmetic ───────────────────────────────────────────────────────────

class TestTheClampIsAMinimum:
    def test_a_leverage_inside_every_limit_is_submitted_as_requested(self):
        r = clamp_for_submit(SPLIT, 125, 10)
        assert r.pair == SPLIT and r.requested == SPLIT
        assert not r.clamped and r.bound_by == ""
        assert (r.symbol_max, r.account_cap) == (125, 10)

    def test_the_symbols_maximum_lowers_the_venue_leverage(self):
        r = clamp_for_submit(SPLIT, 8, 10)
        assert r.pair == (5, 8), "the venue at the symbol's 8x, sized at 5 as before"
        assert r.clamped and r.bound_by == lv.CLAMP_SYMBOL_MAX
        assert r.requested == SPLIT

    def test_a_maximum_under_the_sizing_lowers_the_sized_leverage_with_it(self):
        """The venue never locks more margin than the sizing figure: with the
        symbol at 3x the order is sized at 3x, a smaller notional, rather
        than sized at 5x on a venue that can only take 3."""
        r = clamp_for_submit(SPLIT, 3, 10)
        assert r.pair == (3, 3) and r.bound_by == lv.CLAMP_SYMBOL_MAX
        assert r.pair.venue >= r.pair.sized

    def test_the_account_cap_lowers_a_request_above_it(self):
        r = clamp_for_submit(LeveragePair(5, 15), 125, 10)
        assert r.pair == (5, 10) and r.clamped and r.bound_by == lv.CLAMP_ACCOUNT_CAP

    def test_the_lower_of_the_two_limits_is_the_one_named(self):
        both = LeveragePair(5, 15)
        assert clamp_for_submit(both, 8, 10).bound_by == lv.CLAMP_SYMBOL_MAX
        assert clamp_for_submit(both, 8, 10).pair == (5, 8)
        assert clamp_for_submit(both, 12, 10).bound_by == lv.CLAMP_ACCOUNT_CAP
        assert clamp_for_submit(both, 12, 10).pair == (5, 10)

    def test_an_unread_maximum_is_not_a_maximum_of_anything(self):
        """Unread is never zero and never one: with no symbol maximum the
        clamp holds the account cap alone, which is the order as it went out
        before the clamp existed."""
        r = clamp_for_submit(SPLIT, None, 10)
        assert r.pair == SPLIT and not r.clamped and r.symbol_max is None
        capped = clamp_for_submit(LeveragePair(5, 15), None, 10)
        assert capped.pair == (5, 10) and capped.bound_by == lv.CLAMP_ACCOUNT_CAP

    def test_it_never_raises_a_leverage_and_never_breaks_the_pair(self):
        """Every request, maximum and cap in range: the answer is at or under
        the request on both leverages, at or under each limit, and still a
        pair whose venue leverage is not below the sized one."""
        requests = [LeveragePair(s, v) for s in range(1, 8) for v in range(s, 21)]
        for req, smax, cap in itertools.product(
                requests, (None, 1, 2, 3, 5, 8, 10, 20, 125), (1, 3, 5, 10, 20, 125)):
            got = clamp_for_submit(req, smax, cap).pair
            assert 1 <= got.sized <= got.venue, (req, smax, cap, got)
            assert got.venue <= req.venue and got.sized <= req.sized, (req, smax, cap, got)
            assert got.venue <= cap, (req, smax, cap, got)
            if smax is not None:
                assert got.venue <= smax, (req, smax, cap, got)
            assert got.venue == min(req.venue, cap, smax if smax is not None else req.venue)


class TestTheSymbolMaximumIsReadOffTheVenuesContractRow:
    def test_the_unified_limit(self):
        assert symbol_max_leverage(_market(125)) == 125
        assert symbol_max_leverage(_market(20.0)) == 20

    @pytest.mark.parametrize("info,want", [
        ({"maxLever": "75"}, 75),            # Bitget classic contracts row
        ({"maxLeverage": "50"}, 50),         # Bitget unified instruments row
        ({"maxLever": "", "maxLeverage": "25"}, 25),
    ])
    def test_the_venues_raw_row_when_the_unified_limit_is_empty(self, info, want):
        assert symbol_max_leverage(_market(None, info=info)) == want

    def test_the_unified_limit_wins_over_the_raw_row(self):
        assert symbol_max_leverage(_market(20, info={"maxLever": "75"})) == 20

    @pytest.mark.parametrize("market", [
        None, "ETH/USDT:USDT", 7, MagicMock(), {}, {"limits": None},
        {"limits": {"leverage": None}}, {"limits": {"leverage": {"max": None}}},
        {"limits": {"leverage": {"max": True}}},       # a bool is not a leverage
        {"limits": {"leverage": {"max": "abc"}}},
        {"limits": {"leverage": {"max": 0}}},          # zero is not a maximum
        {"limits": {"leverage": {"max": float("nan")}}},
        {"limits": {"leverage": {"max": float("inf")}}},
        {"limits": {"leverage": {"max": MagicMock()}}},  # int(MagicMock()) is 1
        {"limits": {"leverage": {"max": None}}, "info": {"maxLever": MagicMock()}},
    ])
    def test_a_row_that_states_no_maximum_answers_none(self, market):
        assert symbol_max_leverage(market) is None


# ── the executor's one reading ───────────────────────────────────────────────

class TestTheOrdersLeveragesAreTheClampedPair:
    def test_normal_leverage_is_untouched(self, tmp_path):
        ex = _executor(tmp_path, 125)
        reading = ex._order_leverage_reading(SWAP)
        assert reading.pair == SPLIT and not reading.clamped
        assert ex._order_leverages(SWAP) == SPLIT
        assert ex._compute_target_leverage(SWAP) == 10
        assert ex._compute_sized_leverage(SWAP) == 5

    def test_the_symbol_maximum_reaches_every_reader(self, tmp_path):
        """The venue set, the sizing and the margin conversion read one
        answer: the pair, the venue's half and the sized half all move."""
        ex = _executor(tmp_path, 8)
        assert ex._order_leverages(SWAP) == (5, 8)
        assert ex._compute_target_leverage(SWAP) == 8
        assert ex._compute_sized_leverage(SWAP) == 5
        low = _executor(tmp_path / "low", 3)
        assert low._order_leverages(SWAP) == (3, 3)
        assert low._compute_sized_leverage(SWAP) == 3

    def test_the_account_cap_is_the_max_leverage_ceiling(self, tmp_path):
        """`/leverage 15` under MAX_LEVERAGE=10 is submitted at 10, whatever
        the symbol would take; a symbol that takes less lowers it further."""
        RUNTIME.leverage_override = 15
        ex = _executor(tmp_path, 125)
        reading = ex._order_leverage_reading(SWAP)
        assert reading.pair == (5, 10) and reading.account_cap == 10
        assert _executor(tmp_path / "b", 6)._order_leverages(SWAP) == (5, 6)

    def test_the_account_cap_is_read_where_the_ceiling_is_set(self, tmp_path):
        ex = _executor(tmp_path, 125)
        saved = CONFIG.exchange.max_leverage
        object.__setattr__(CONFIG.exchange, "max_leverage", 7)
        try:
            reading = ex._order_leverage_reading(SWAP)
        finally:
            object.__setattr__(CONFIG.exchange, "max_leverage", saved)
        assert reading.account_cap == 7 and reading.pair.venue <= 7

    def test_a_spot_form_symbol_finds_its_perp_row(self, tmp_path):
        ex = _executor(tmp_path, 8)
        assert ex._order_leverages("ETH/USDT") == (5, 8)

    def test_a_users_preference_under_the_maximum_still_binds(self, tmp_path):
        ex = _executor(tmp_path, 8)
        ex._user_leverage_pref = 3
        assert ex._order_leverages(SWAP) == (3, 3)
        ex._user_leverage_pref = 7
        assert ex._order_leverages(SWAP) == (5, 7), "their 7, under the symbol's 8"
        ex._user_leverage_pref = 9
        assert ex._order_leverages(SWAP) == (5, 8), "the symbol's 8, under their 9"


class TestTheQualityLadderStillBinds:
    """The ladder (and the margin-risk cap) lower the SIZED leverage on the
    idea, and the venue follows in proportion. The clamp comes after them and
    only lowers, so a rung is never undone by a roomy symbol and a tight
    symbol tightens it further."""

    @staticmethod
    def _idea(cap):
        idea = SimpleNamespace(asset="ETH/USDT")
        lv.tighten_leverage_cap(idea, cap)
        return idea

    def test_a_rung_is_not_raised_to_the_symbols_maximum(self, tmp_path):
        ex = _executor(tmp_path, 125)
        reading = ex._order_leverage_reading(SWAP, self._idea(2))
        assert reading.pair == (2, 4), "sized at the rung's 2x, the venue at its 4x"
        assert not reading.clamped

    def test_a_tight_symbol_lowers_the_venue_under_the_rung(self, tmp_path):
        ex = _executor(tmp_path, 3)
        reading = ex._order_leverage_reading(SWAP, self._idea(2))
        assert reading.requested == (2, 4)
        assert reading.pair == (2, 3), "the rung's sizing kept, the venue at the symbol's 3x"
        assert reading.bound_by == lv.CLAMP_SYMBOL_MAX

    def test_a_symbol_under_the_rung_lowers_both(self, tmp_path):
        ex = _executor(tmp_path, 1)
        assert ex._order_leverages(SWAP, self._idea(2)) == (1, 1)

    @pytest.mark.parametrize("rung,symbol_max", list(itertools.product(
        (1, 2, 3, 4, 5), (1, 2, 3, 5, 8, 10, 20, 125))))
    def test_no_combination_exceeds_the_rung_or_the_symbol(self, tmp_path, rung, symbol_max):
        ex = _executor(tmp_path, symbol_max)
        unclamped = _executor(tmp_path / "u", 125)._order_leverages(SWAP, self._idea(rung))
        got = ex._order_leverages(SWAP, self._idea(rung))
        assert got.sized <= rung, "the ladder's sized cap"
        assert got.sized <= unclamped.sized and got.venue <= unclamped.venue
        assert got.venue <= symbol_max and got.venue <= 10
        assert 1 <= got.sized <= got.venue


class TestTheMaximumIsCachedWithASafeFallback:
    def test_a_client_whose_markets_are_gone_answers_the_last_figure_read(self, tmp_path):
        ex = _executor(tmp_path, 8)
        assert ex._order_leverages(SWAP) == (5, 8)
        ex._exchange = SimpleNamespace(markets=None)       # a fresh client, nothing loaded
        assert ex._symbol_max_leverage(SWAP) == 8
        assert ex._order_leverages(SWAP) == (5, 8)
        ex._exchange = None
        assert ex._order_leverages(SWAP) == (5, 8)

    def test_a_fresh_reading_replaces_the_cached_one(self, tmp_path):
        ex = _executor(tmp_path, 8)
        assert ex._order_leverages(SWAP) == (5, 8)
        ex._exchange = SimpleNamespace(markets={SWAP: _market(6)})   # the venue lowered it
        assert ex._order_leverages(SWAP) == (5, 6)

    def test_never_read_and_unreadable_is_the_order_as_requested(self, tmp_path):
        for exchange in (None, SimpleNamespace(markets=None), SimpleNamespace(markets={}),
                         SimpleNamespace(markets={SWAP: _market(None)}), MagicMock(),
                         AsyncMock()):
            ex = LiveExecutor(state_dir=str(tmp_path))
            ex._exchange = exchange
            reading = ex._order_leverage_reading(SWAP)
            assert reading.symbol_max is None, exchange
            assert reading.pair == SPLIT and not reading.clamped, exchange

    def test_one_symbols_maximum_is_not_anothers(self, tmp_path):
        ex = _executor(tmp_path, markets={SWAP: _market(8)})
        assert ex._order_leverages(SWAP) == (5, 8)
        assert ex._order_leverages("BTC/USDT:USDT") == SPLIT

    def test_off_is_the_order_as_requested(self, tmp_path, clamp_off):
        ex = _executor(tmp_path, 3)
        reading = ex._order_leverage_reading(SWAP)
        assert reading.pair == SPLIT and not reading.clamped


# ── the order ────────────────────────────────────────────────────────────────

class CappedVenue(Venue):
    """A Bitget stand-in that states a maximum for its one symbol and, like
    the venue, refuses a set call above it and stays where it was."""

    def __init__(self, max_leverage: int, resting: int = 5):
        super().__init__()
        self.bg.set_markets([_market(max_leverage)])
        self.markets = self.bg.markets
        self.max_leverage = max_leverage
        self.lev = min(resting, max_leverage)
        self.asked: list[int] = []
        self.refused: list[int] = []

    async def set_leverage(self, leverage=None, symbol=None, params=None, **k):
        self.asked.append(int(leverage))
        if int(leverage) > self.max_leverage:
            self.refused.append(int(leverage))
            raise ccxt.BadRequest("the leverage exceeds the maximum for this symbol")
        self.lev = int(leverage)
        return {}


def _audits(audits, **match):
    return [a for a in audits if all(a.get(k) == v for k, v in match.items())]


class TestAnOrderIsSubmittedAtTheClampedLeverage:
    def test_a_normal_order_says_nothing_and_changes_nothing(self, tmp_path):
        venue = CappedVenue(125)
        _ex, pos, _card, audits, *_ = _execute(venue, tmp_path)
        assert venue.lev == 10 and not venue.refused
        assert pos.leverage == 10 and le.sized_leverage_on_record(pos) == 5
        assert not _audits(audits, result="LEVERAGE_CLAMPED")

    def test_the_venue_is_never_asked_for_more_than_the_symbol_takes(self, tmp_path):
        venue = CappedVenue(8)
        _ex, pos, _card, audits, *_ = _execute(venue, tmp_path)
        assert venue.asked and max(venue.asked) == 8 and not venue.refused
        assert venue.lev == 8 and pos.leverage == 8
        assert le.sized_leverage_on_record(pos) == 5
        # The fill is at the leverage that was submitted: nothing to report
        # as a mismatch, and nothing for the overshoot guard to weigh.
        assert not _audits(audits, action="leverage_mismatch_on_fill")
        assert not _audits(audits, action="leverage_overshoot_guard")
        assert pos.status == "open"

    def test_the_clamp_is_said_once_with_what_bound_it(self, tmp_path):
        venue = CappedVenue(8)
        _ex, _pos, _card, audits, *_ = _execute(venue, tmp_path)
        said = _audits(audits, result="LEVERAGE_CLAMPED")
        assert len(said) == 1
        one = said[0]
        assert one["action"] == "leverage_clamp"
        assert one["message"].startswith("LEVERAGE_CLAMPED for ETH/USDT")
        assert one["data"] == {
            "trade_id": "TI-EST", "symbol": "ETH/USDT",
            "requested_leverage": 10, "requested_sized_leverage": 5,
            "leverage": 8, "sized_leverage": 5,
            "symbol_max_leverage": 8, "account_cap": 10, "bound_by": "symbol_max"}

    def test_the_trade_is_the_same_trade_at_a_lower_leverage(self, tmp_path):
        """A maximum between the sizing and the venue standard moves margin
        and nothing else: the same quantity, so the same dollar loss at the
        stop, with the venue locking the margin that notional needs at 8x."""
        roomy, tight = CappedVenue(125), CappedVenue(8)
        _, pos10, *_ = _execute(roomy, tmp_path / "a")
        _, pos8, *_ = _execute(tight, tmp_path / "b")
        assert roomy.orders == tight.orders, "the same quantity"
        # The stand-in's position row states the size it holds, and the record
        # takes the venue's figure, so the margin is read off the record's own
        # quantity: the same notional, over 8 on one venue and 10 on the other.
        assert pos8.quantity == pos10.quantity
        notional = pos8.quantity * TICKER
        assert pos8.cost_usd == pytest.approx(notional / 8)
        assert pos10.cost_usd == pytest.approx(notional / 10)
        assert le.sized_margin(pos8) == pytest.approx(le.sized_margin(pos10))

    def test_a_maximum_under_the_sizing_is_a_smaller_order_not_more_margin(self, tmp_path):
        roomy, tight = CappedVenue(125), CappedVenue(3)
        _, pos10, *_ = _execute(roomy, tmp_path / "a")
        _, pos3, _card, audits, *_ = _execute(tight, tmp_path / "b")
        assert tight.lev == 3 and pos3.leverage == 3 and not tight.refused
        assert le.sized_leverage_on_record(pos3) == 3
        # $100 of sized margin (the per-position cap) on a $4,000 ticker, cut
        # to the market's 0.01 step: 100 x 5 / 4000 = 0.125 and 100 x 3 / 4000
        # = 0.075. Three fifths of the notional, not the same notional at 3x.
        assert roomy.orders[0]["amount"] == pytest.approx(0.12)
        assert tight.orders[0]["amount"] == pytest.approx(0.07)
        # Sized and placed at one leverage, so the margin the venue locks IS
        # the sized margin, where sizing at 5x on a 3x venue would have locked
        # five thirds of it. (Read on the one record: the stand-in's position
        # row states a fixed size, so two venues' records are not comparable.)
        assert pos3.cost_usd == pytest.approx(le.sized_margin(pos3))
        assert pos10.cost_usd == pytest.approx(le.sized_margin(pos10) / 2)
        assert _audits(audits, result="LEVERAGE_CLAMPED")[0]["data"]["sized_leverage"] == 3

    def test_without_the_clamp_the_venue_refuses_the_set_and_the_fill_mismatches(
            self, tmp_path, clamp_off):
        """The other arm, on the same venue: the order asks for 10x, the venue
        refuses every set and stays at its 8x, and the fill is reported as a
        mismatch against a leverage the venue could never have applied."""
        venue = CappedVenue(8, resting=8)
        _ex, pos, _card, audits, *_ = _execute(venue, tmp_path)
        assert venue.refused and set(venue.refused) == {10}
        assert not _audits(audits, result="LEVERAGE_CLAMPED")
        mismatch = _audits(audits, action="leverage_mismatch_on_fill")
        assert len(mismatch) == 1
        assert mismatch[0]["data"]["requested"] == 10 and mismatch[0]["data"]["actual"] == 8
        assert pos.leverage == 8

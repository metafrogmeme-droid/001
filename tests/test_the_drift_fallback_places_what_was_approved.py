"""The drift market fallback places what the caps and the envelope approved,
and never chases a hand-typed ticket.

A resting limit that drifts more than `LIMIT_DRIFT_CANCEL_PCT` in the trade's
direction with momentum behind it is cancelled and its REMAINDER is marketed
at the current price, up to `DRIFT_MARKET_MAX_CHASE_PCT` (5%) past the limit.
The limit was sized, capped and authorized at ITS price, and nothing asked
again at the market's. Driven against the unfixed tree, a per-user executor
holding a $20 approval (qty 1 at $100, 5x) under a $20 `PER_USER_MAX_FUNDS_USD`
cap, the market at $104.90:

    orders placed: [market buy 1.0]      placed margin: $20.98 (cap $20.00)
    stop/target 98/106 -> 102.80/111.19  entry 100 -> 104.9

The same fallback carried an order the Authority Envelope authorized at $100
of notional to $104.90, because the envelope's figure is stamped on the IDEA
and the idea is gone by the time a resting order drifts. And a hand-typed
ticket -- whose levels the placement path keeps as typed -- was chased up to
5% past the price the person typed, with their stop and target moved to
match.

Three things now. The idea's provenance rides on the position (`idea_source`,
`authorized_notional_usd`), persisted with it. The fallback asks the hard caps
at the margin the market order really places and compares the whole
position's notional with the envelope's figure, refusing by name; the limit
is already cancelled, so a refusal opens nothing and says what a partial fill
left on the venue. And a typed ticket is never chased: the drift cancels it
and the person is told why.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG
from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from tests.source_scan import code_only

SECRET = "apiKey=SECRETVALUE"


class _Venue:
    """Records every order; answers a fill at ``fill_px``."""

    def __init__(self, fill_px: float):
        self.fill_px = fill_px
        self.orders: list[dict] = []
        self.cancels: list[str] = []
        self.tickers: list[str] = []
        self.last = fill_px

    async def cancel_order(self, oid, symbol=None, params=None):
        self.cancels.append(oid)
        return {"id": oid}

    async def fetch_ticker(self, symbol, *a, **k):
        self.tickers.append(symbol)
        return {"symbol": symbol, "last": self.last}

    async def create_order(self, symbol, typ, side, qty, price=None, params=None):
        self.orders.append({"symbol": symbol, "type": typ, "side": side,
                            "qty": float(qty), "price": price})
        return {"id": "M1", "average": self.fill_px, "filled": float(qty)}


def _resting(direction="LONG", entry=100.0, qty=1.0, lev=5, cost=None, **extra):
    pos = LivePosition(
        trade_id="T1", symbol="SOL/USDT", direction=direction, entry_price=entry,
        quantity=qty, cost_usd=(entry * qty / lev if cost is None else cost),
        stop_loss=(98.0 if direction == "LONG" else 102.0),
        take_profit=(106.0 if direction == "LONG" else 94.0),
        leverage=lev, is_spot=False, status="pending_fill", order_type="limit",
        limit_order_id="L1")
    for k, v in extra.items():
        setattr(pos, k, v)
    return pos


def _executor(tmp_path, user_id="7", *, final=None, held=()):
    """A real executor whose venue reads are stubbed at their seams."""
    ex = LiveExecutor(state_dir=str(tmp_path), user_id=user_id)
    ex._fetch_order = AsyncMock(return_value=final or {"status": "canceled", "filled": 0,
                                                       "average": None})
    ex.available_margin = AsyncMock(return_value=None)          # flat bounds
    ex._venue_market_price = AsyncMock(return_value=None)
    ex._place_sl_tp = AsyncMock(return_value=("S1", "TP1"))
    ex._reattempt_post_fill_sl = AsyncMock(
        side_effect=lambda exchange, pos, direction, qty, sl, tp, tid: (sl, tp, None))
    ex._guard_fill_leverage = AsyncMock(return_value=None)
    for i, cost in enumerate(held):
        tid = f"OLD-{i}"
        ex._positions[tid] = LivePosition(
            trade_id=tid, symbol="BTC/USDT", direction="LONG", entry_price=50_000.0,
            quantity=cost * 5 / 50_000.0, cost_usd=cost, leverage=5,
            stop_loss=45_000.0, take_profit=55_000.0, status="open")
    return ex


def _fallback(ex, pos, cur_price, venue=None):
    ex._positions[pos.trade_id] = pos
    venue = venue or _Venue(cur_price)
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(le, "trading_halted", lambda: False):
        msg = asyncio.run(ex._execute_drift_market_fallback(venue, pos.trade_id, pos, cur_price))
    return msg, venue, audits


def _refusals(audits):
    return [a for a in audits if a.get("action") == "market_fallback"
            and a.get("result") == "REFUSED"]


# ── the caps, at the margin the market order places ─────────────────────

class TestTheCapsAreAskedAtTheMarketPrice:

    def test_a_chase_past_the_per_user_cap_is_refused_and_places_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20")
        ex = _executor(tmp_path)
        pos = _resting()                                  # $20 approved at $100
        msg, venue, audits = _fallback(ex, pos, 104.9)    # $20.98 at the market
        assert venue.orders == [], "a refused chase sends no market order"
        assert venue.cancels == ["L1"], "the drift cancel still happens: it is a reducing action"
        assert "REFUSED" in msg and "$20.98" in msg and "approved $20.00" in msg
        assert "PER_USER_MAX_FUNDS_USD" in msg
        assert msg.endswith("No position was opened.")
        assert pos.status == "pending_fill", "the row waits for the next sweep to read the cancel"
        assert (pos.entry_price, pos.stop_loss, pos.take_profit) == (100.0, 98.0, 106.0)
        (row,) = _refusals(audits)
        assert row["data"]["reason"] == "over_cap"

    def test_a_chase_past_the_operators_total_cap_is_refused(self, tmp_path):
        ex = _executor(tmp_path, user_id=None,
                       held=(le.MICRO_MAX_TOTAL_EXPOSURE - 20.0,))   # $480 committed
        pos = _resting()                                              # + $20 = the cap exactly
        msg, venue, _ = _fallback(ex, pos, 104.9)                     # + $20.98 is over it
        assert venue.orders == []
        assert "REFUSED" in msg and "$20.98" in msg

    def test_a_chase_past_the_per_trade_bound_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "1000")
        ex = _executor(tmp_path)
        per_trade = le.MICRO_MAX_POSITION_USD
        pos = _resting(qty=per_trade * 5 / 100.0)          # exactly the per-trade bound
        msg, venue, _ = _fallback(ex, pos, 104.9)
        assert venue.orders == []
        assert "per-trade margin limit" in msg

    def test_a_chase_inside_every_cap_still_places_at_the_market(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "25")
        ex = _executor(tmp_path)
        pos = _resting()
        msg, venue, audits = _fallback(ex, pos, 104.9)
        assert [(o["type"], o["side"], o["qty"]) for o in venue.orders] == [("market", "buy", 1.0)]
        assert msg.startswith("LIMIT → MARKET FALLBACK")
        assert pos.status == "open" and pos.entry_price == 104.9
        assert pos.cost_usd == pytest.approx(20.98)
        assert _refusals(audits) == []

    def test_the_rows_own_resting_margin_is_not_counted_against_it(self, tmp_path, monkeypatch):
        """A pending row counts toward committed margin (a resting order holds
        cap room). Counted here it would refuse every fallback on its own
        approval: $20 resting + $20.98 chase = $40.98 against a $21 cap."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "21")
        ex = _executor(tmp_path)
        pos = _resting()
        msg, venue, _ = _fallback(ex, pos, 104.9)
        assert len(venue.orders) == 1, msg

    def test_a_chase_that_lowers_the_margin_asks_the_caps_nothing(self, tmp_path, monkeypatch):
        """A short drifting DOWN markets less margin than was approved: the
        caps were met at the larger figure, as `execute` asks nothing of a
        quantity the round-up did not raise."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20")
        ex = _executor(tmp_path)
        pos = _resting(direction="SHORT")                  # $20 approved at $100
        with patch.object(LiveExecutor, "_hard_cap_refusal", autospec=True,
                          wraps=LiveExecutor._hard_cap_refusal) as spy:
            msg, venue, _ = _fallback(ex, pos, 95.1)       # $19.02 at the market
        assert spy.call_count == 0
        assert [(o["side"], o["qty"]) for o in venue.orders] == [("sell", 1.0)]
        assert pos.cost_usd == pytest.approx(19.02)

    def test_a_partial_fills_margin_counts_toward_the_cap(self, tmp_path, monkeypatch):
        """The whole position's margin is what the cap sees: 0.4 filled at $100
        (margin $8) plus 0.6 marketed at $104.90 ($12.59) is $20.59 against a
        $20.50 cap. The remainder alone ($12.59) would have passed."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20.5")
        ex = _executor(tmp_path, final={"status": "canceled", "filled": 0.4, "average": 100.0})
        msg, venue, audits = _fallback(ex, _resting(), 104.9)
        assert venue.orders == []
        assert "$20.59" in msg and _refusals(audits)[0]["data"]["reason"] == "over_cap"

    def test_the_bounds_are_read_off_this_accounts_balance(self, tmp_path, monkeypatch):
        """The per-trade bound is the balance-relative one when the feature is
        armed: the fallback hands `size_bounds_for` the executor's own
        available-margin reading, not a flat figure of its own."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "1000")
        ex = _executor(tmp_path)
        ex.available_margin = AsyncMock(return_value=123.0)
        seen: list = []
        real = le.size_bounds_for

        def _spy(available_usd=None):
            seen.append(available_usd)
            return real(available_usd)

        with patch.object(le, "size_bounds_for", _spy):
            _fallback(ex, _resting(), 104.9)
        assert seen == [123.0]


# ── the envelope, on the whole position's notional ──────────────────────

class TestTheEnvelopeIsAskedOnTheNotional:

    def test_a_chase_over_the_authorized_notional_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "25")            # the caps pass
        ex = _executor(tmp_path)
        pos = _resting(authorized_notional_usd=100.0)                 # qty 1 at $100
        msg, venue, audits = _fallback(ex, pos, 104.9)                # $104.90 of notional
        assert venue.orders == []
        assert "$104.90" in msg and "$100.00" in msg and "Authority Envelope" in msg
        (row,) = _refusals(audits)
        assert row["data"]["reason"] == "over_authorized"

    def test_no_envelope_figure_asks_no_envelope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "25")
        ex = _executor(tmp_path)
        msg, venue, _ = _fallback(ex, _resting(), 104.9)
        assert len(venue.orders) == 1, msg

    @pytest.mark.parametrize("junk", [0.0, -1.0, "abc", float("nan")],
                             ids=["zero", "negative", "text", "nan"])
    def test_a_figure_that_is_not_one_bounds_nothing_and_is_never_zero(self, tmp_path, monkeypatch, junk):
        """`price_on_record`'s reading: a stamp that is not a positive money
        figure authorizes nothing and refuses nothing -- it is never read as
        an authorization of $0, which would refuse every chase."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "25")
        ex = _executor(tmp_path)
        msg, venue, _ = _fallback(ex, _resting(authorized_notional_usd=junk), 104.9)
        assert len(venue.orders) == 1, msg

    def test_the_envelope_is_asked_even_when_the_margin_fell(self, tmp_path, monkeypatch):
        """A short drifting down lowers the margin and the caps are not asked;
        the envelope still is, on the notional the position would carry."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20")
        ex = _executor(tmp_path)
        pos = _resting(direction="SHORT", authorized_notional_usd=90.0)
        msg, venue, _ = _fallback(ex, pos, 95.1)                       # $95.10 > $90
        assert venue.orders == [] and "Authority Envelope" in msg

    def test_a_partial_fill_counts_toward_the_notional_and_the_sentence_says_it_is_live(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "25")
        ex = _executor(tmp_path, final={"status": "canceled", "filled": 0.4, "average": 100.0})
        pos = _resting(authorized_notional_usd=100.0)
        msg, venue, _ = _fallback(ex, pos, 104.9)      # 0.4 x 100 + 0.6 x 104.9 = 102.94
        assert venue.orders == []
        assert "remaining 0.600000" in msg and "$102.94" in msg
        assert "0.400000 that filled before the cancel is live on the venue" in msg
        assert "adopted on the next sweep" in msg
        assert "No position was opened" not in msg


# ── a margin the record cannot measure ──────────────────────────────────

class TestAnUnreadLeverageIsNotAMarginOfZero:

    def test_an_adopted_limits_unread_leverage_refuses_by_name(self, tmp_path, monkeypatch):
        """An adopted limit order records leverage 0 and margin 0.0 (the venue
        states neither for an unfilled order). `margin_at_fill` answers 0.0 for
        that leverage -- its own spelling of unread -- and 0.0 > 0.0 is False,
        so the caps would never have been asked."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "1")
        ex = _executor(tmp_path)
        pos = _resting(lev=0, cost=0.0)
        msg, venue, audits = _fallback(ex, pos, 104.9)
        assert venue.orders == []
        assert "leverage on record is unread" in msg
        (row,) = _refusals(audits)
        assert row["data"]["reason"] == "leverage_unread"

    def test_a_reclaimed_orders_chase_is_measured_at_its_standard(self, tmp_path, monkeypatch):
        """A reclaimed order is the bot's own, re-tracked after a restart lost
        the record: its approved leverage is not on record, and the standard
        the executor sets for the symbol is what the fill guard checks it
        against. The chase's margin is measured there: $104.90 at 5x is
        $20.98, over a $20 cap, so it is refused on the CAP and not as
        unread."""
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20")
        ex = _executor(tmp_path)
        ex._standard_leverage = lambda symbol: 5
        pos = _resting(lev=0, cost=0.0, origin="reclaimed")
        msg, venue, audits = _fallback(ex, pos, 104.9)
        assert venue.orders == []
        assert "$20.98" in msg
        (row,) = _refusals(audits)
        assert row["data"]["reason"] == "over_cap"


# ── a refusal says what the venue holds ─────────────────────────────────

class TestTheRefusalSaysWhatIsLeftOnTheVenue:

    def test_the_halt_refusal_names_a_partial_fill_too(self, tmp_path):
        ex = _executor(tmp_path, final={"status": "canceled", "filled": 0.4, "average": 100.0})
        pos = _resting()
        ex._positions["T1"] = pos
        with patch.object(le, "audit", lambda *a, **k: None), \
             patch.object(le, "trading_halted", lambda: True):
            msg = asyncio.run(ex._execute_drift_market_fallback(_Venue(104.9), "T1", pos, 104.9))
        assert "halted" in msg and "0.400000 that filled before the cancel" in msg
        assert "No position was opened" not in msg

    def test_the_tail_is_one_reading(self):
        assert LiveExecutor._fallback_refused_tail(0.0) == "No position was opened."
        assert "adopted on the next sweep" in LiveExecutor._fallback_refused_tail(0.25)


# ── a typed ticket is never chased ──────────────────────────────────────

def _pending_check(ex, pos, venue, *, momentum=True):
    ex._positions[pos.trade_id] = pos
    ex._fetch_order = AsyncMock(side_effect=[{"status": "open", "filled": 0},
                                             {"status": "canceled", "filled": 0, "average": None}])
    ex._check_drift_market_fallback = AsyncMock(return_value=momentum)
    ex._execute_drift_market_fallback = AsyncMock(return_value="FALLBACK RAN")
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        msg = asyncio.run(ex._check_pending_limit(venue, pos.trade_id, pos))
    return msg, audits


class TestATypedTicketIsNeverChased:

    @pytest.fixture(autouse=True)
    def _premises(self):
        assert CONFIG.limit_orders.price_drift_cancel_pct > 0
        assert CONFIG.limit_orders.drift_market_fallback is True

    def test_an_engine_idea_that_drifts_with_momentum_reaches_the_fallback(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="unknown")
        msg, _ = _pending_check(ex, pos, _Venue(104.9))
        assert msg == "FALLBACK RAN"
        assert ex._check_drift_market_fallback.await_count == 1

    def test_a_typed_ticket_that_drifts_keeps_resting_and_is_never_chased(self, tmp_path):
        """The first cure cancelled a drifted typed ticket rather than chase
        it; the decision of 2026-09-28 is that the drift rule is the engine's
        freshness rule and does not read a person's ticket at all
        (`tests/test_a_typed_ticket_rests_on_its_own_clock.py`). What this
        class still owns: momentum is never asked and nothing is placed."""
        ex = _executor(tmp_path)
        pos = _resting(idea_source="manual")
        venue = _Venue(104.9)
        msg, audits = _pending_check(ex, pos, venue)
        assert ex._check_drift_market_fallback.await_count == 0, "momentum is never even asked"
        assert ex._execute_drift_market_fallback.await_count == 0
        assert venue.cancels == [] and venue.orders == []
        assert msg is None and pos.status == "pending_fill"
        assert not any(a.get("action") == "limit_drift_cancel" for a in audits)

    def test_a_typed_ticket_inside_the_drift_band_keeps_resting(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="manual")
        venue = _Venue(101.0)                                  # 1% away, under the 2% band
        msg, _ = _pending_check(ex, pos, venue)
        assert msg is None and venue.cancels == [] and pos.status == "pending_fill"

    def test_the_typed_check_sits_above_the_drift_read(self):
        """A scan, stated as one: `_check_pending_limit` is a 400-line
        coroutine and the claim is an ORDER -- the typed test is read before
        the drift band is, and the drift block is entered only for an
        untyped row, so the momentum read is never reached for a ticket."""
        src = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
        typed = src.index('_typed = getattr(pos, "idea_source", None) == "manual"')
        drift = src.index("if drift_pct > 0 and pos.entry_price > 0 and not _typed:")
        momentum = src.index("await self._check_drift_market_fallback(")
        assert typed < drift < momentum


# ── the provenance rides on the position, and survives a restart ────────

class TestTheProvenanceIsPersisted:

    def test_both_fields_survive_a_restart(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="manual", authorized_notional_usd=100.0)
        ex._positions["T1"] = pos
        ex._save_positions()
        again = LiveExecutor(state_dir=str(tmp_path), user_id="7")
        back = again._positions["T1"]
        assert getattr(back, "idea_source", None) == "manual"
        assert getattr(back, "authorized_notional_usd", None) == 100.0

    def test_an_older_row_reads_as_absent(self, tmp_path):
        ex = _executor(tmp_path)
        ex._positions["T1"] = _resting()
        ex._save_positions()
        (f,) = [p for p in tmp_path.iterdir() if p.name.startswith("live_positions")]
        rows = json.loads(f.read_text())
        for row in rows.values() if isinstance(rows, dict) else rows:
            row.pop("idea_source", None)
            row.pop("authorized_notional_usd", None)
        f.write_text(json.dumps(rows))
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions["T1"]
        assert getattr(back, "idea_source", None) is None
        assert getattr(back, "authorized_notional_usd", None) is None

    def test_a_saved_figure_that_is_not_one_is_dropped_not_read_as_zero(self, tmp_path):
        ex = _executor(tmp_path)
        ex._positions["T1"] = _resting(authorized_notional_usd=100.0)
        ex._save_positions()
        (f,) = [p for p in tmp_path.iterdir() if p.name.startswith("live_positions")]
        rows = json.loads(f.read_text())
        for row in rows.values() if isinstance(rows, dict) else rows:
            row["authorized_notional_usd"] = "abc"
        f.write_text(json.dumps(rows))
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions["T1"]
        assert getattr(back, "authorized_notional_usd", None) is None

    def test_the_construction_in_execute_copies_both_off_the_idea(self, tmp_path):
        """Driven through the real `execute` against the placed-order suite's
        venue, filling at the market: the position it constructs carries the
        idea's own source and the envelope's figure. (The first draft was an
        AST pin on the two `setattr` calls, and `if False:` around either
        survived it: a call that exists is not a call that runs.)"""
        from tests.test_the_placed_order_is_the_checked_order import _idea, _market, _Venue

        class _Filling(_Venue):
            async def create_order(self, symbol=None, type=None, side=None, amount=None,
                                   price=None, params=None, **k):
                self.orders.append({"amount": float(amount), "type": type})
                return {"id": "M1", "status": "closed", "filled": float(amount),
                        "average": self.price, "cost": float(amount) * self.price}

            async def fetch_order(self, oid, symbol=None, params=None):
                return {"id": oid, "status": "closed", "filled": self.orders[-1]["amount"],
                        "average": self.price}

            async def fetch_my_trades(self, *a, **k):
                return []

        from bot.config import RUNTIME
        from bot.core import bounds_shadow
        was = RUNTIME.leverage_override
        RUNTIME.leverage_override = None
        venue = _Filling(_market(), 4000.0)
        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._exchange = venue
        ex._place_sl_tp = AsyncMock(return_value=("S1", "T1"))
        ex._reattempt_post_fill_sl = AsyncMock(
            side_effect=lambda exchange, pos, direction, qty, sl, tp, tid: (sl, tp, None))
        ex.sync_positions_from_exchange = AsyncMock(return_value=None)
        ex._guard_fill_leverage = AsyncMock(return_value=None)
        idea = _idea(4000.0)
        idea.source = "manual"
        idea.authorized_notional_usd = 250.0
        try:
            with patch.object(le, "audit", lambda *a, **k: None), \
                 patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
                 patch.object(type(CONFIG), "is_live", return_value=True):
                result = asyncio.run(ex.execute(idea, size_usd=50.0, order_type="market"))
        finally:
            RUNTIME.leverage_override = was
        assert "LIVE BUY" in result, result
        (pos,) = ex._positions.values()
        assert pos.status == "open"
        assert getattr(pos, "idea_source", None) == "manual"
        assert getattr(pos, "authorized_notional_usd", None) == 250.0


# ── the fallback asks the seam, and the seam asks the caps ──────────────

def test_the_fallback_asks_the_refusal_between_the_halt_and_the_order():
    src = code_only(inspect.getsource(LiveExecutor._execute_drift_market_fallback))
    halt = src.index("if trading_halted():")
    ask = src.index("await self._drift_fallback_size_refusal(")
    order = src.index('await exchange.create_order(')
    assert halt < ask < order


def test_the_refusal_asks_the_one_cap_reading():
    src = code_only(inspect.getsource(LiveExecutor._drift_fallback_size_refusal))
    assert "self._hard_cap_refusal(" in src
    assert "size_bounds_for(await self.available_margin())" in src
    assert "price_on_record(" in src
    assert "PER_USER_MAX_FUNDS_USD" not in src, "the per-user cap is the reading's, not a second copy"

"""A resting limit is sized and checked at the price it fills at.

`LiveExecutor.execute` sized every order at the market price
(`quantity = size_usd * leverage / current_price`) and a limit that rests on
the book fills at its own. Driven through the real `execute`, $20 at 5x with
ETH at 4000:

    typed SHORT limit at 4400 -> 0.025 sent, $22.00 of margin at fill
    typed LONG  limit at 3600 -> 0.025 sent, $18.00 of margin at fill

The SHORT placed 10% more than the approval, and the hard caps, the Authority
Envelope's authorized notional and the notional ceiling had all been asked at
the market price, so none of them saw it. A hand-typed ticket rests on its own
24h clock at any distance the person typed, so the gap is whatever they typed.

`order_fill_price` is the one reading (its own price when the limit rests, the
market's when it crosses, and the market's for a market order), and the
minimum, the caps and the ceiling are asked there, once, after the entry tier
and the tick grid have decided the order's price. The post-only retry re-prices
an engine limit after those checks, so a retry re-priced above the checked
price is sent the amount that keeps the checked notional, never more.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from unittest.mock import patch

import ccxt
import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core.limit_entry import EntryResult, order_fill_price
from bot.core.live_executor import LiveExecutor
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only
from tests.test_the_placed_order_is_the_checked_order import _market, _Sent, _Venue

PRICE = 4000.0


class _Recording(_Venue):
    """The placed-order venue, keeping each order's type and price, and able to
    answer an order (a resting one, or a post-only refusal first)."""

    def __init__(self, market, price, *, answer=None, post_only_first=False):
        super().__init__(market, price)
        self.sent: list[dict] = []
        self.answer = answer
        self.post_only_first = post_only_first

    async def fetch_closed_orders(self, *a, **k):
        return []

    async def create_order(self, symbol=None, type=None, side=None, amount=None,
                           price=None, params=None, **k):
        if self.post_only_first and not self.sent:
            self.sent.append({"refused": True, "amount": float(amount), "price": price})
            raise ccxt.InvalidOrder("post only order failed: would immediately match")
        self.sent.append({"type": type, "side": side, "amount": float(amount),
                          "price": None if price is None else float(price)})
        if self.answer is None:
            raise _Sent()
        return dict(self.answer)


def _typed(direction, entry, *, source="manual", authorized=None):
    if direction == Direction.SHORT:
        sl, tp = entry * 1.02, entry * 0.94
    else:
        sl, tp = entry * 0.98, entry * 1.06
    idea = TradeIdea(id="TI-REST-1", asset="ETH/USDT", direction=direction,
                     entry_price=entry, stop_loss=sl, take_profit=tp,
                     confidence=1.0 if source == "manual" else 0.8,
                     reasoning="fixture", source=source)
    if authorized is not None:
        idea.authorized_notional_usd = authorized
    return idea


def _run(venue, idea, size_usd, tmp_path, *, order_type="limit", user_id=None,
         atr=None, tier=None):
    creds = {"api_key": "k", "api_secret": "s", "passphrase": "p"} if user_id else None
    ex = LiveExecutor(user_id=user_id, credentials=creds, state_dir=tmp_path)
    ex._exchange = venue
    ex._user_leverage_pref = None
    audits: list[dict] = []
    patches = [patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})),
               patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None),
               patch.object(type(CONFIG), "is_live", return_value=True)]
    if tier is not None:
        patches.append(patch.object(le, "calculate_entry", lambda **k: tier))
    for p in patches:
        p.start()
    try:
        kw = {"order_type": order_type}
        if atr is not None:
            kw["atr_value"] = atr
        try:
            result = asyncio.run(ex.execute(idea, size_usd=size_usd, **kw))
        except _Sent:
            result = "<sent>"
    finally:
        for p in reversed(patches):
            p.stop()
    return result, [o for o in venue.sent if not o.get("refused")], audits, ex


@pytest.fixture(autouse=True)
def _five_x():
    RUNTIME.leverage_override = 5
    yield
    RUNTIME.leverage_override = None


def _margin_at_fill(order, lev=5):
    return order["amount"] * order["price"] / lev


# ── the reading ──────────────────────────────────────────────────────────

class TestTheFillPrice:

    @pytest.mark.parametrize("side,limit,want", [
        ("sell", 4400.0, 4400.0),     # rests above: fills at its own price
        ("buy", 3600.0, 3600.0),      # rests below
        ("sell", 3900.0, PRICE),      # crosses: fills at the market
        ("buy", 4100.0, PRICE),
        ("buy", PRICE, PRICE),        # AT the market crosses
        ("sell", PRICE, PRICE),
        ("LONG", 3600.0, 3600.0),     # the direction vocabulary reads the same
        ("SHORT", 4400.0, 4400.0),
    ])
    def test_a_resting_limit_is_its_own_price_and_a_crossing_one_the_markets(self, side, limit, want):
        assert order_fill_price(side, limit, PRICE) == want

    @pytest.mark.parametrize("limit", [None, 0.0, -5.0, "abc", float("nan"), float("inf")])
    def test_no_readable_limit_is_the_market(self, limit):
        assert order_fill_price("sell", limit, PRICE) == PRICE

    def test_a_side_it_does_not_know_is_the_market(self):
        assert order_fill_price("hold", 4400.0, PRICE) == PRICE

    def test_it_answers_a_float_for_a_numeric_string(self):
        out = order_fill_price("sell", "4400", PRICE)
        assert out == 4400.0 and isinstance(out, float)


# ── a typed ticket, through the real execute ─────────────────────────────

class TestATypedTicket:

    def test_a_short_resting_above_places_the_approved_margin_not_more(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE)
        result, orders, audits, _ = _run(venue, _typed(Direction.SHORT, 4400.0), 20.0, tmp_path)
        assert result == "<sent>", result
        (o,) = orders
        assert (o["type"], o["side"], o["price"]) == ("limit", "sell", 4400.0)
        assert o["amount"] == pytest.approx(0.0227)          # 20 x 5 / 4400, truncated
        assert _margin_at_fill(o) <= 20.0
        assert _margin_at_fill(o) == pytest.approx(20.0, abs=0.0001 * 4400 / 5)
        row = next(a for a in audits if a.get("action") == "limit_sizing")
        assert row["result"] == "AT_FILL_PRICE"
        assert row["data"]["fill_price"] == 4400.0 and row["data"]["market_price"] == PRICE

    def test_a_long_resting_below_places_the_approved_margin_not_less(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE)
        result, orders, _, _ = _run(venue, _typed(Direction.LONG, 3600.0), 20.0, tmp_path)
        assert result == "<sent>", result
        (o,) = orders
        assert (o["side"], o["price"]) == ("buy", 3600.0)
        assert o["amount"] == pytest.approx(0.0277)          # 20 x 5 / 3600, truncated
        assert _margin_at_fill(o) == pytest.approx(20.0, abs=0.0001 * 3600 / 5)

    def test_a_crossing_typed_limit_is_sized_at_the_market(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE)
        result, orders, audits, _ = _run(venue, _typed(Direction.LONG, 4050.0), 20.0, tmp_path)
        assert result == "<sent>", result
        (o,) = orders
        assert o["price"] == 4050.0 and o["amount"] == pytest.approx(0.025)
        assert not [a for a in audits if a.get("action") == "limit_sizing"]

    def test_a_market_order_is_sized_at_the_market(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE)
        result, orders, audits, _ = _run(venue, _typed(Direction.SHORT, 4400.0), 20.0, tmp_path,
                                         order_type="market")
        assert result == "<sent>", result
        (o,) = orders
        assert o["type"] == "market" and o["amount"] == pytest.approx(0.025)
        assert not [a for a in audits if a.get("action") == "limit_sizing"]

    def test_the_resting_order_is_recorded_at_the_approved_margin(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE,
                           answer={"id": "o-rest", "status": "open", "filled": 0})
        result, orders, _, ex = _run(venue, _typed(Direction.SHORT, 4400.0), 20.0, tmp_path)
        (o,) = orders
        pending = [p for p in ex._positions.values() if p.status == "pending_fill"]
        assert len(pending) == 1, result
        pos = pending[0]
        assert pos.quantity == pytest.approx(o["amount"])
        assert pos.cost_usd <= 20.0 and pos.cost_usd == pytest.approx(20.0, abs=0.1)


class TestTheChecksAreAskedAtTheFillPrice:

    def test_a_round_up_at_the_fill_price_is_asked_the_caps(self, tmp_path, monkeypatch):
        # 20 x 5 / 4400 = 0.02273, under the 0.025 minimum: rounded up, 0.025 at
        # 4400 is $22 of margin against a $20 cap. Measured at the market it was
        # exactly 0.025, no round-up, no cap asked, and $22 filled anyway.
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20")
        venue = _Recording(_market(amount_step=0.001, amount_min=0.025), PRICE)
        result, orders, audits, _ = _run(venue, _typed(Direction.SHORT, 4400.0), 20.0,
                                         tmp_path, user_id="u1")
        assert orders == [], orders
        assert "rounded up to the venue minimum is $22.00" in result, result
        assert any(a.get("result") == "ROUNDUP_OVER_CAP" for a in audits), audits

    def test_a_round_up_past_the_envelope_is_measured_at_the_fill_price(self, tmp_path):
        venue = _Recording(_market(amount_step=0.001, amount_min=0.025), PRICE)
        idea = _typed(Direction.SHORT, 4400.0, authorized=100.0)     # $20 x 5
        result, orders, audits, _ = _run(venue, idea, 20.0, tmp_path)
        assert orders == [], orders
        assert "would place $110.00 of notional" in result, result
        assert any(a.get("result") == "ROUNDUP_OVER_AUTHORIZED" for a in audits)

    def test_the_notional_ceiling_is_measured_at_the_fill_price(self, tmp_path):
        # At the 10x ceiling a $100 LONG resting 10% below is 0.277 ETH: $997 of
        # notional at its fill, $1,108 at the market. Measured at the market the
        # ceiling ($1,050) refused an order that fills inside it.
        RUNTIME.leverage_override = 10
        venue = _Recording(_market(amount_step=0.001, amount_min=0.001), PRICE)
        result, orders, _, _ = _run(venue, _typed(Direction.LONG, 3600.0), 100.0, tmp_path)
        assert result == "<sent>", result
        (o,) = orders
        assert o["amount"] == pytest.approx(0.277)
        assert o["amount"] * o["price"] <= 100.0 * le.leverage_ceiling(CONFIG.exchange) * 1.05


class TestAnEngineLimit:

    def test_a_re_priced_engine_short_is_sized_at_the_price_it_rests_at(self, tmp_path):
        tier = EntryResult(limit_price=4040.0, tier="A", confluence_count=3, size_multiplier=1.0)
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE)
        idea = _typed(Direction.SHORT, 3990.0, source="unknown")    # crosses: re-priced
        result, orders, _, _ = _run(venue, idea, 20.0, tmp_path, atr=40.0, tier=tier)
        assert result == "<sent>", result
        (o,) = orders
        assert o["price"] == 4040.0
        assert o["amount"] == pytest.approx(0.0247)          # 20 x 5 / 4040, truncated

    def test_a_post_only_retry_above_the_checked_price_keeps_the_checked_notional(self, tmp_path):
        # Rests at 4040, checked there; the venue refuses it post-only and the
        # retry re-prices to 4000 + 1 ATR = 4080. The same 0.0247 at 4080 is
        # $20.16 of margin; the retry sends the amount that keeps $19.96, and
        # the pending record carries the amount that was sent.
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE,
                           answer={"id": "o-retry", "status": "open", "filled": 0},
                           post_only_first=True)
        idea = _typed(Direction.SHORT, 4040.0, source="unknown")    # rests: kept
        result, orders, audits, ex = _run(venue, idea, 20.0, tmp_path, atr=80.0)
        (o,) = orders
        assert o["price"] == 4080.0
        assert o["amount"] == pytest.approx(0.0244)          # 0.0247 x 4040 / 4080, truncated
        assert o["amount"] * 4080.0 <= 0.0247 * 4040.0
        row = next(a for a in audits if a.get("result") == "AMOUNT_BOUNDED")
        assert row["data"]["checked_price"] == 4040.0 and row["data"]["retry_price"] == 4080.0
        pending = [p for p in ex._positions.values() if p.status == "pending_fill"]
        assert len(pending) == 1, result
        assert pending[0].quantity == pytest.approx(0.0244)

    def test_a_retry_whose_kept_notional_is_under_the_minimum_places_nothing(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0247), PRICE,
                           answer={"id": "never", "status": "open"}, post_only_first=True)
        idea = _typed(Direction.SHORT, 4040.0, source="unknown")
        result, orders, audits, ex = _run(venue, idea, 20.0, tmp_path, atr=80.0)
        assert orders == [], orders
        assert result.startswith("BLOCKED:") and "Nothing was placed" in result, result
        assert any(a.get("result") == "UNDER_MINIMUM" for a in audits), audits
        assert not ex._positions


# ── the retry's amount ───────────────────────────────────────────────────

class _Grid:
    def __init__(self, step=0.0001, raises=False, none=False):
        self.bg = ccxt.bitget()
        self.bg.set_markets([_market(amount_step=step, amount_min=step)])
        self.raises, self.none = raises, none

    def amount_to_precision(self, symbol, amount):
        if self.raises:
            raise ccxt.InvalidOrder("amount must be greater than minimum")
        if self.none:
            return None
        return self.bg.amount_to_precision(symbol, amount)


class TestTheRetryAmount:
    SYM = "ETH/USDT:USDT"

    def _cut(self, grid, market, amount, checked, retry):
        return LiveExecutor.__new__(LiveExecutor)._retry_amount_within(
            grid, market, self.SYM, amount, checked, retry)

    def test_at_or_below_the_checked_price_the_amount_is_kept(self):
        grid = _Grid(raises=True)                  # never asked
        assert self._cut(grid, None, 0.0247, 4040.0, 4040.0) == 0.0247
        assert self._cut(grid, None, 0.0247, 4040.0, 3920.0) == 0.0247

    def test_above_it_the_amount_is_cut_to_the_checked_notional_and_truncated(self):
        out = self._cut(_Grid(), _market(amount_step=0.0001, amount_min=0.0001),
                        0.0247, 4040.0, 4080.0)
        assert out == pytest.approx(0.0244)
        assert out * 4080.0 <= 0.0247 * 4040.0

    def test_a_grid_that_refuses_or_answers_nothing_cannot_place_it(self):
        assert self._cut(_Grid(raises=True), None, 0.0247, 4040.0, 4080.0) is None
        assert self._cut(_Grid(none=True), None, 0.0247, 4040.0, 4080.0) is None

    def test_a_cut_to_zero_cannot_place_it(self):
        assert self._cut(_Grid(step=0.01), None, 0.0005, 4040.0, 4080.0) is None

    def test_under_the_minimum_amount_or_cost_it_cannot_be_placed(self):
        grid = _Grid()
        assert self._cut(grid, _market(amount_step=0.0001, amount_min=0.0245),
                         0.0247, 4040.0, 4080.0) is None
        assert self._cut(grid, _market(amount_step=0.0001, amount_min=0.0001, cost_min=100.0),
                         0.0247, 4040.0, 4080.0) is None
        assert self._cut(grid, _market(amount_step=0.0001, amount_min=0.0244),
                         0.0247, 4040.0, 4080.0) == pytest.approx(0.0244)


def test_one_reading_of_a_markets_minimums():
    """The minimum gate and the retry read the one set; driven, and the gate
    spells no floor of its own."""
    floor, step, cost = le.venue_minimums(_market(amount_step=0.001, amount_min=0.02, cost_min=5.0))
    assert (floor, step, cost) == (0.02, 0.001, 5.0)
    assert le.venue_minimums(_market(amount_step=0.01, amount_min=None, cost_min=None)) == (0.01, 0.01, 0.0)
    gate = code_only(inspect.getsource(LiveExecutor._exchange_minimum_gate))
    assert "venue_minimums(market)" in gate and "min_amount_step(" not in gate


def test_execute_hands_the_checked_price_in_and_takes_the_quantity_back():
    """A scan, stated as one, of the two ends of a cable the retry drive above
    runs through: the keyword and the returned quantity."""
    src = inspect.getsource(LiveExecutor.execute)
    tree = ast.parse(textwrap.dedent(src))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await):
            call = node.value.value
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "_submit_entry_order":
                hits.append(node)
    (node,) = hits
    names = [e.id for e in node.targets[0].elts]
    assert names[-1] == "quantity", names
    kws = {k.arg: k.value for k in node.value.value.keywords}
    assert isinstance(kws.get("checked_price"), ast.Name) and kws["checked_price"].id == "_fill_px"

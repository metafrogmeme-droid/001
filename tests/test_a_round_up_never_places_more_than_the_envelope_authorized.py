"""A venue-minimum round-up never places more than the envelope authorized.

The web-live authorization asks the user's enforce-mode Authority Envelope
about THIS order's notional -- margin x the leverage it places at -- and
records that figure against the day. `_exchange_minimum_gate` then raises a
quantity under the venue's minimum, up to `EXCHANGE_MIN_ROUNDUP_MAX_MULT`
(1.5x, on by default), and nothing asked the envelope again. Driven through
the real `execute` against a venue whose minimum is 0.001 BTC at $63,000:

    $10 margin at 5x    authorized and recorded $50.00
    placed 0.001 BTC    $63.00 of notional, 1.26x what was authorized

A per-trade cap of $60 was breached and the day under-counted by $13, on the
one order shape a small live account near a venue minimum produces. The
authorization stamps the idea with what it authorized
(`authorized_notional_usd`), the gate bounds a round-up by it the way it is
bounded by the multiplier, and an order over it is refused by name -- the
confirm handler then takes the recorded spend back. Only the round-up is
bounded: without one the placed notional is at most the approved size times
the leverage, which is the figure the envelope was asked about.
"""
from __future__ import annotations

import asyncio
import inspect
import tempfile
import time
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.core.live_executor as le
from bot.config import CONFIG
from bot.core import bounds_shadow
from bot.core.live_executor import LiveExecutor, execution_indicates_failure
from bot.guardian import user_authority_store as uas
from bot.guardian.authority import compile_envelope
from bot.guardian.authority_ledger import AuthoritySpendLedger
from bot.utils.models import Direction, TradeIdea
from bot.web import user_gateway as ug
from tests.source_scan import code_only
from tests.test_the_placed_order_is_the_checked_order import _market, _Sent, _Venue

PRICE = 63_000.0
UID = "web:5"


def _bitcoin_min():
    return _market(amount_step=0.001, amount_min=0.001, cost_min=5.0)


def _idea(authorized=None):
    return TradeIdea(id="TI-RU", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=PRICE, stop_loss=PRICE * 0.98, take_profit=PRICE * 1.06,
                     confidence=0.8, reasoning="fixture", source="manual",
                     authorized_notional_usd=authorized)


def _place(idea, size_usd=10.0):
    venue = _Venue(_bitcoin_min(), PRICE)
    ex = LiveExecutor(state_dir=tempfile.mkdtemp())
    ex._exchange = venue
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        try:
            result = asyncio.run(ex.execute(idea, size_usd=size_usd, order_type="market"))
        except _Sent:
            result = "<sent>"
    return result, venue.orders, audits


@pytest.fixture(autouse=True)
def _round_up_on(monkeypatch):
    assert CONFIG.exchange.exchange_min_roundup_enabled is True, "the premise: the round-up ships on"
    assert CONFIG.exchange.exchange_min_roundup_max_mult >= 1.26, "the premise: 1.26x is within the cap"


class TestThePlacedOrder:

    def test_the_premise_a_round_up_with_no_authorization_still_places(self):
        result, orders, audits = _place(_idea(None))
        assert result == "<sent>" and orders[0]["amount"] == 0.001
        assert "ROUNDED_TO_MIN" in [a["result"] for a in audits if a.get("action") == "live_execute"]

    def test_a_round_up_over_the_authorization_is_refused_and_nothing_is_sent(self):
        result, orders, audits = _place(_idea(50.0))
        assert orders == [], "an order reached the venue"
        assert result.startswith("BLOCKED:")
        assert "$63.00 of notional" in result and "$50.00 your Authority Envelope authorized" in result
        assert "Nothing was placed" in result
        assert execution_indicates_failure(result), "the handler must read it as placed nothing"
        row = [a for a in audits if a.get("result") == "ROUNDUP_OVER_AUTHORIZED"][0]
        assert row["data"]["placed_notional"] == 63.0 and row["data"]["authorized_notional"] == 50.0

    def test_a_round_up_inside_the_authorization_places(self):
        result, orders, _ = _place(_idea(70.0))
        assert result == "<sent>" and orders[0]["amount"] == 0.001

    def test_a_round_up_exactly_at_the_authorization_places(self):
        result, orders, _ = _place(_idea(63.0))
        assert result == "<sent>" and orders[0]["amount"] == 0.001

    def test_an_order_that_needs_no_round_up_is_not_bounded_by_the_stamp(self):
        """A stamp below the order's own notional is not this gate's subject:
        without a round-up the placed notional is the approved size times the
        leverage, and the envelope was asked about that figure."""
        result, orders, audits = _place(_idea(50.0), size_usd=12.6)   # 0.001 exactly
        assert result == "<sent>" and orders[0]["amount"] == 0.001
        assert not [a for a in audits if a.get("result") == "ROUNDUP_OVER_AUTHORIZED"]

    @pytest.mark.parametrize("stamp", [0.0, -5.0, float("nan"), "junk"], ids=["zero", "negative", "nan", "junk"])
    def test_a_stamp_that_is_not_a_figure_bounds_nothing(self, stamp):
        idea = _idea(None)
        object.__setattr__(idea, "authorized_notional_usd", stamp)
        result, orders, _ = _place(idea)
        assert result == "<sent>" and orders[0]["amount"] == 0.001


# ── the gate itself ──────────────────────────────────────────────────────

class _Fx:
    def amount_to_precision(self, symbol, amount):
        return str(amount)

    def price_to_precision(self, symbol, price):
        return str(price)


def _gate(authorized, qty=0.0008):
    ex = LiveExecutor(state_dir=tempfile.mkdtemp())
    seen: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: seen.append({"message": msg, **kw})):
        block, q = ex._exchange_minimum_gate(_Fx(), _bitcoin_min(), "BTC/USDT:USDT", qty, PRICE, 5,
                                             10.0, authorized_notional_usd=authorized)
    return block, q, seen


class TestTheGate:

    def test_the_refusal_keeps_the_quantity_it_was_handed(self):
        block, q, seen = _gate(50.0)
        assert block and "Authority Envelope" in block and q == 0.0008
        assert [s["result"] for s in seen] == ["ROUNDUP_OVER_AUTHORIZED"]

    def test_the_refusal_names_what_the_venue_needs(self):
        block, _, _ = _gate(50.0)
        assert "requires >= $63.00" in block and "$12.60 of margin at 5x" in block

    def test_the_multiplier_bound_still_refuses_first_when_it_bites(self, monkeypatch):
        block, _, seen = _gate(50.0, qty=0.0005)   # 2.0x, over the 1.5x cap
        assert block and "exceeds 1.5x cap" in block
        assert [s["result"] for s in seen] == ["BELOW_EXCHANGE_MIN"]

    def test_the_one_call_in_execute_hands_the_stamp_in(self):
        """A scan, stated as one. There were two calls, the second a tier-C
        re-ask; the gate runs once now, after the entry tier and the tick grid
        have decided the order's size and price, so one call carries the
        keyword and a second call without it would be the gap."""
        src = code_only(inspect.getsource(LiveExecutor.execute))
        calls = src.count("self._exchange_minimum_gate(")
        assert calls == 1
        assert src.count('authorized_notional_usd=getattr(idea, "authorized_notional_usd", None)') == 1


# ── the stamp ────────────────────────────────────────────────────────────

@pytest.fixture
def wired(monkeypatch, tmp_path):
    store = uas.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(uas, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: types.SimpleNamespace(get_venue=lambda uid: "bitget"))
    store.bind(UID, compile_envelope({
        "mode": "enforce", "label": "t", "allowed_venues": ["bitget"],
        "symbol_allowlist": ["SOL"], "max_notional_per_trade_usd": 300,
        "max_notional_daily_usd": 2000}))
    return store, ledger


def _engine(idea, margin=50):
    return SimpleNamespace(
        _pending_ideas={"T1": idea},
        _manual_margin_override={"T1": margin} if margin is not None else {},
        live_executor=object(),
        _executor_for=lambda tg_id, venue=None: SimpleNamespace(
            _compute_target_leverage=lambda symbol, idea=None: 5))


class TestTheStamp:

    def test_an_allow_stamps_the_pending_idea_with_what_it_authorized(self, wired):
        _, ledger = wired
        idea = SimpleNamespace(asset="SOL/USDT")
        ok, _, recorded = ug._authorize_web_live_trade({}, _engine(idea), UID, "T1")
        assert ok is True and recorded is True
        assert idea.authorized_notional_usd == 250.0
        assert ledger.spent(UID, time.time()) == 250.0

    def test_a_real_idea_takes_the_stamp(self, wired):
        idea = TradeIdea(id="T1", asset="SOL/USDT", direction=Direction.LONG, entry_price=100,
                         stop_loss=98, take_profit=106, confidence=1.0, reasoning="m", source="manual")
        ok, _, _ = ug._authorize_web_live_trade({}, _engine(idea), UID, "T1")
        assert ok is True and idea.authorized_notional_usd == 250.0

    def test_a_deny_stamps_nothing(self, wired):
        idea = SimpleNamespace(asset="SOL/USDT")
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(idea, margin=100), UID, "T1")  # 500 > 300
        assert ok is False and any("per-trade" in r for r in reasons)
        assert not hasattr(idea, "authorized_notional_usd")

    def test_an_auto_sized_order_stamps_nothing(self, wired):
        idea = SimpleNamespace(asset="SOL/USDT")
        ok, _, _ = ug._authorize_web_live_trade({}, _engine(idea, margin=None), UID, "T1")
        assert ok is False
        assert not hasattr(idea, "authorized_notional_usd")

    def test_a_stamp_that_cannot_be_written_denies_and_records_nothing(self, wired):
        _, ledger = wired

        class _Sealed:
            __slots__ = ("asset",)

            def __init__(self):
                self.asset = "SOL/USDT"

        ok, reasons, recorded = ug._authorize_web_live_trade({}, _engine(_Sealed()), UID, "T1")
        assert ok is False and recorded is False
        assert reasons == ["the authorized notional could not be recorded on the order"]
        assert ledger.spent(UID, time.time()) == 0.0

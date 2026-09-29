"""A hand-typed resting limit rests on its own clock, not the engine's.

`_check_pending_limit` cancels a resting limit when the price drifts 2% away
with momentum behind it (the engine's setup is stale) and when it has rested
4h (the analysis is stale). Both rules were written for the engine's own
ideas, and both read a hand-typed ticket the same way. Driven on the unfixed
tree: "buy ETH at 3000" typed at a market of 3090 was cancelled the moment
the market moved 2% further away -- a limit UNDER the market is exactly
"wait for the pullback" -- and a ticket that rested 4h01m was cancelled as
`LIMIT EXPIRED ... cancelled after 4.0h`, an engine sentence about a level
the engine did not choose.

Decided 2026-09-28 (delegated): the drift rule never reads a typed ticket,
and a typed ticket rests on `MANUAL_LIMIT_EXPIRE_SEC` (24h by default), a
backstop against a forgotten ticket holding cap room and a stale stop for
ever rather than a freshness rule; when that clock runs out the person is
told which clock it was and that nothing was placed. The hard timeout that
closes a row nobody can READ is twice the clock that applies, so a typed
ticket's is 48h. The engine's ideas keep both rules exactly as they were.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, limit_expiry_seconds
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only
from tests.test_the_drift_fallback_places_what_was_approved import (
    _executor,
    _pending_check,
    _resting,
    _Venue,
)
from tests.test_the_placed_order_is_the_checked_order import _market as _placed_market
from tests.test_the_placed_order_is_the_checked_order import _Venue as _PlacedVenue

ROOT = Path(__file__).resolve().parents[1]
ENGINE_H = CONFIG.limit_orders.expire_seconds / 3600
TYPED_H = CONFIG.limit_orders.manual_expire_seconds / 3600


@pytest.fixture(autouse=True)
def _premises():
    assert CONFIG.limit_orders.price_drift_cancel_pct > 0
    assert CONFIG.limit_orders.drift_market_fallback is True
    assert ENGINE_H == 4.0 and TYPED_H == 24.0, "the shipped clocks"


def _aged(pos, hours: float):
    pos.opened_at = datetime.now(UTC) - timedelta(hours=hours)
    return pos


# ── the clock ────────────────────────────────────────────────────────────

class TestWhichClock:

    def test_a_typed_ticket_rests_on_the_typed_horizon(self):
        assert limit_expiry_seconds(_resting(idea_source="manual")) == 86400

    @pytest.mark.parametrize("source", ["unknown", "scan_skill", None], ids=["engine", "scan", "none"])
    def test_everything_else_rests_on_the_engines(self, source):
        pos = _resting() if source is None else _resting(idea_source=source)
        assert limit_expiry_seconds(pos) == 14400

    def test_a_config_without_the_typed_horizon_is_the_engines_clock(self, monkeypatch):
        """A hand-written stand-in that forgot the field, or a horizon that
        is not a positive number, must not loosen anything: the engine's
        clock is the stricter of the two and is what every ticket had."""
        stand_in = SimpleNamespace(expire_seconds=14400)
        monkeypatch.setattr(le, "CONFIG", SimpleNamespace(limit_orders=stand_in))
        assert limit_expiry_seconds(_resting(idea_source="manual")) == 14400
        for bad in (0, -5, True, "86400", None):
            stand_in.manual_expire_seconds = bad
            assert limit_expiry_seconds(_resting(idea_source="manual")) == 14400, bad

    def test_the_hard_timeout_is_twice_the_clock_that_applies(self):
        """A scan, stated as one: the hard timeout and the expiry are read off
        one figure in `_check_pending_limit`, so a typed ticket's unreadable
        row is closed at 48h and an engine idea's at 8h."""
        src = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
        assert "_expire = limit_expiry_seconds(pos)" in src
        assert "hard_timeout = 2 * _expire" in src
        assert "age_sec > _expire:" in src
        assert "2 * CONFIG.limit_orders.expire_seconds" not in src


# ── the drift rule does not read a typed ticket ──────────────────────────

class TestTheDriftRuleIsTheEngines:

    def test_a_typed_ticket_the_market_ran_from_keeps_resting(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="manual")
        venue = _Venue(104.9)                                    # 4.9% away, momentum on
        msg, audits = _pending_check(ex, pos, venue)
        assert msg is None and pos.status == "pending_fill"
        assert venue.cancels == [] and venue.orders == []
        assert ex._check_drift_market_fallback.await_count == 0
        assert not any(a.get("action", "").startswith("limit_drift") for a in audits)

    def test_the_ticker_is_not_even_asked_for_a_typed_ticket(self, tmp_path):
        ex = _executor(tmp_path)
        venue = _Venue(104.9)
        _pending_check(ex, _resting(idea_source="manual"), venue)
        assert venue.tickers == [], "no drift read, no ticker read"

    def test_an_engine_idea_that_drifts_still_meets_the_rule(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(idea_source="unknown")
        msg, _ = _pending_check(ex, pos, _Venue(104.9))
        assert msg == "FALLBACK RAN"


# ── the typed clock ──────────────────────────────────────────────────────

class TestTheTypedClock:

    def test_a_typed_ticket_past_the_engines_clock_keeps_resting(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _aged(_resting(idea_source="manual"), 5.0)
        venue = _Venue(100.5)
        msg, _ = _pending_check(ex, pos, venue)
        assert msg is None and pos.status == "pending_fill" and venue.cancels == []

    def test_a_typed_ticket_past_its_own_clock_is_cancelled_and_told_which_clock(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _aged(_resting(idea_source="manual"), 25.0)
        venue = _Venue(100.5)
        msg, audits = _pending_check(ex, pos, venue)
        assert venue.cancels == ["L1"] and venue.orders == []
        assert pos.status == "closed" and pos.close_reason == "expired"
        assert msg.startswith("LIMIT EXPIRED: LONG SOL/USDT — your ticket rested 25.0h")
        assert "the typed-ticket horizon (MANUAL_LIMIT_EXPIRE_SEC, 24h)" in msg
        assert msg.endswith("Nothing was placed.")
        row = next(a for a in audits if a.get("action") == "limit_expire")
        assert row["result"] == "EXPIRED"
        assert row["data"]["typed"] is True and row["data"]["horizon_sec"] == 86400

    def test_an_engine_idea_past_the_engines_clock_expires_as_before(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _aged(_resting(idea_source="unknown"), 5.0)
        venue = _Venue(100.5)
        msg, audits = _pending_check(ex, pos, venue)
        assert venue.cancels == ["L1"]
        assert msg == "LIMIT EXPIRED: LONG SOL/USDT — cancelled after 5.0h"
        row = next(a for a in audits if a.get("action") == "limit_expire")
        assert row["data"]["typed"] is False and row["data"]["horizon_sec"] == 14400

    def test_the_typed_sentence_never_claims_a_fill(self, tmp_path):
        """A partial fill is adopted above the expiry sentence, so the
        sentence is reached only when nothing filled -- and says so."""
        ex = _executor(tmp_path)
        pos = _aged(_resting(idea_source="manual"), 25.0)
        msg, _ = _pending_check(ex, pos, _Venue(100.5))
        assert "Nothing was placed" in msg and "filled" not in msg.lower()


# ── the documents ────────────────────────────────────────────────────────

class TestTheDocumentsNameTheClock:

    def test_the_config_declares_the_typed_horizon_beside_the_engines(self):
        src = code_only(inspect.getsource(CONFIG.limit_orders.__class__))
        assert '_env_float("MANUAL_LIMIT_EXPIRE_SEC", 86400)' in src
        assert dataclasses.fields(CONFIG.limit_orders)[0] is not None

    def test_the_env_example_names_both_clocks(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "MANUAL_LIMIT_EXPIRE_SEC" in text and "LIMIT_ORDER_EXPIRE_SEC" in text


# ── the placement card names the clock the resting order is on ───────────
class _RestingVenue(_PlacedVenue):
    """The placed-order suite's venue, answering a RESTING limit order rather
    than raising: the card is built only after the venue answered."""

    async def create_order(self, symbol=None, type=None, side=None, amount=None,
                           price=None, params=None, **k):
        self.orders.append({"type": type, "side": side, "amount": float(amount),
                            "price": price, "params": dict(params or {})})
        return {"id": "O-REST", "status": "open", "filled": 0.0, "price": price}


def _placed_card(tmp_path, source):
    entry = 3990.0  # under the 4000 market: a limit that RESTS
    idea = TradeIdea(id=f"TI-REST-{source}", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=entry, stop_loss=entry * 0.98, take_profit=entry * 1.06,
                     confidence=0.8, reasoning="fixture", source=source, order_type="limit")
    venue = _RestingVenue(_placed_market(), 4000.0)
    ex = LiveExecutor(state_dir=tmp_path)
    ex._exchange = venue
    with patch.object(le, "audit", lambda log, msg, **kw: None), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        msg = asyncio.run(ex.execute(idea, size_usd=100.0, order_type="limit"))
    assert venue.orders, "the order never reached the venue"
    assert "LIMIT ORDER BUY" in msg and "PENDING FILL" in msg, msg
    return msg


class TestThePlacementCardNamesTheClock:
    """A card that says an order rests and not for how long, or on whose
    clock, leaves the person to guess whether the engine's 4h and drift rules
    apply to the ticket they typed. Driven through the real `execute` to a
    venue that answers a resting order, because the card is composed only
    after the venue answered and a scan of the f-string cannot see that."""

    def test_a_typed_tickets_card_names_its_own_clock_and_no_drift(self, tmp_path):
        msg = _placed_card(tmp_path, "manual")
        rest_h = CONFIG.limit_orders.manual_expire_seconds / 3600
        want = (f"- Rests: up to {rest_h:g}h (your ticket's clock, MANUAL_LIMIT_EXPIRE_SEC; "
                "never cancelled for drift)")
        assert want in msg, msg
        assert "LIMIT_ORDER_EXPIRE_SEC" not in msg, "the engine's clock, named on a typed ticket"

    def test_an_engine_ideas_card_names_the_engines_clock_and_its_drift(self, tmp_path):
        msg = _placed_card(tmp_path, "scan_skill")
        rest_h = CONFIG.limit_orders.expire_seconds / 3600
        drift = CONFIG.limit_orders.price_drift_cancel_pct
        want = (f"- Rests: up to {rest_h:g}h (LIMIT_ORDER_EXPIRE_SEC), or until the market "
                f"drifts {drift:g}% away")
        assert want in msg, msg
        assert "MANUAL_LIMIT_EXPIRE_SEC" not in msg, "the ticket's clock, named on the engine's idea"

    def test_the_card_reads_the_one_clock_reading(self, monkeypatch, tmp_path):
        """Planted: the card asks `limit_expiry_seconds(typed=...)`; a second
        spelling of the clock in the f-string agrees with every honest fixture."""
        seen = []
        real = le.limit_expiry_seconds

        def _planted(pos=None, *, typed=None):
            seen.append(typed)
            return 7200 if typed else real(pos, typed=typed)

        monkeypatch.setattr(le, "limit_expiry_seconds", _planted)
        msg = _placed_card(tmp_path, "manual")
        assert seen == [True]
        assert "- Rests: up to 2h (your ticket's clock" in msg, msg

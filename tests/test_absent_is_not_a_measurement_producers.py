"""Three producers that answered a question nobody had asked them.

Each manufactures a number where it has none, and in every case the CONSUMER
downstream was already written correctly — so the producer was not filling a
gap, it was overriding a cure.

  M-24  exchange_flow returned `oi_change_pct: 0.0` when the venue could not
        be reached, and on the first observation of a symbol. That is the
        reading for "open interest did not move". `smart_money` already tests
        `sig.oi_change_pct is not None` before acting on it, and
        `order_flow.OrderFlowSignal` already declares the field Optional.

  M-25  the exchange position-count cache was seeded `{"count": 0,
        "timestamp": 0.0}` and its freshness compared against
        `time.monotonic()`, whose zero point is arbitrary. Wherever
        `monotonic()` starts below the 30s TTL, the first call returns the
        SEED — "you have 0 open positions" — to the check deciding whether
        another may be opened. The venue is never asked.

  M-05  the MCP Shield's `confidence` defaulted to 0.65 against a 0.60 floor,
        so a caller that omitted it was handed a pass — and the reply echoed
        `"confidence": 0.65` back as though measured. An MCP client is a
        program; omitting a field is the most ordinary thing it does.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest


# ── M-24 ─────────────────────────────────────────────────────────────────
def _provider(entry, exchange=None):
    from bot.core.exchange_flow import ExchangeFlowProvider
    p = ExchangeFlowProvider.__new__(ExchangeFlowProvider)
    p._lock = threading.RLock()
    p._oi_ttl = 60.0
    p._cache = {}
    p._entry = lambda sym: entry

    async def _get():
        return exchange
    p._get_exchange = _get
    p._prune = lambda: None
    return p


def _fresh(**over):
    e = {"oi_usd": 1000.0, "oi_prev_usd": None, "oi_updated_at": time.time(),
         "funding_rate": None, "funding_updated_at": 0.0}
    e.update(over)
    return e


def test_a_first_observation_has_no_change_to_report():
    """There is no previous level to difference against. 0.0 here is not a
    small change; it is no change having been computed at all."""
    out = asyncio.run(_provider(_fresh()).get_open_interest("BTC/USDT"))
    assert out["oi_usd"] == 1000.0
    assert out["oi_change_pct"] is None


class _Venue:
    def __init__(self, value=1000.0):
        self.value = value

    async def fetch_open_interest(self, *_a, **_k):
        return {"openInterestValue": self.value}


def test_a_first_FETCH_has_no_change_to_report():
    """The cached path and the fetch path each compute this separately, and a
    test of one says nothing about the other — the fetch path survived a
    mutation back to 0.0 while the cached path's test passed."""
    empty = _fresh(oi_usd=None, oi_updated_at=0.0)
    out = asyncio.run(_provider(empty, exchange=_Venue()).get_open_interest("BTC/USDT"))
    assert out["oi_usd"] == 1000.0
    assert out["oi_change_pct"] is None, "first fetch of a symbol has no prior"


def test_a_second_fetch_reports_the_real_change():
    entry = _fresh(oi_usd=800.0, oi_updated_at=0.0)
    out = asyncio.run(_provider(entry, exchange=_Venue(1000.0)).get_open_interest("BTC/USDT"))
    assert out["oi_change_pct"] == pytest.approx(25.0)


def test_a_real_change_is_still_reported():
    out = asyncio.run(_provider(_fresh(oi_prev_usd=800.0)).get_open_interest("BTC/USDT"))
    assert out["oi_change_pct"] == pytest.approx(25.0)


def test_a_genuinely_flat_reading_is_still_zero_not_none():
    """The point is to separate unknown from flat, which means flat must
    survive. Both prior and current present and equal is a measurement."""
    out = asyncio.run(_provider(_fresh(oi_prev_usd=1000.0)).get_open_interest("BTC/USDT"))
    assert out["oi_change_pct"] == 0.0


def test_an_unreachable_venue_reports_the_level_but_not_a_change():
    stale = _fresh(oi_updated_at=0.0, oi_prev_usd=800.0)   # cache too old to serve
    out = asyncio.run(_provider(stale, exchange=None).get_open_interest("BTC/USDT"))
    assert out["oi_usd"] == 1000.0, "the cached LEVEL is a real past reading"
    assert out["oi_change_pct"] is None, "nothing was fetched to compare against"


def test_a_raising_fetch_reports_the_level_but_not_a_change():
    class _Boom:
        async def fetch_open_interest(self, *_a, **_k):
            raise RuntimeError("venue down")
    stale = _fresh(oi_updated_at=0.0, oi_prev_usd=800.0)
    out = asyncio.run(_provider(stale, exchange=_Boom()).get_open_interest("BTC/USDT"))
    assert out["oi_change_pct"] is None


def test_the_consumer_that_was_already_correct_still_works():
    """smart_money guards `is not None`. The producer's 0.0 defeated it: an
    unreachable venue read as "OI flat", which is a market state."""
    src = open("bot/core/smart_money.py", encoding="utf-8").read()
    assert "oi_change_pct is not None" in src


# ── M-25 ─────────────────────────────────────────────────────────────────
def test_an_unfilled_position_count_cache_is_never_served_as_a_reading():
    """The seed was `{"count": 0, "timestamp": 0.0}` against time.monotonic().
    Any process whose monotonic clock starts below the TTL served that 0 to the
    open-positions limit check without asking the venue once."""
    import bot.core.exchange_sync as es
    assert es._position_count_cache["count"] is None
    assert es._position_count_cache["timestamp"] is None


def test_a_fresh_process_asks_the_venue_rather_than_reading_the_seed(monkeypatch):
    import bot.core.exchange_sync as es
    monkeypatch.setattr(es, "_position_count_cache", {"count": None, "timestamp": None})
    # A monotonic clock that has only just started — the exact condition.
    monkeypatch.setattr(es.time, "monotonic", lambda: 3.0)
    asked = []

    async def _fetch(engine):
        asked.append(1)
        return [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
    monkeypatch.setattr(es, "_fetch_exchange_positions", _fetch)

    n = asyncio.run(es.get_exchange_position_count(NS()))
    assert asked, "served the seed instead of asking the venue"
    assert n == 2


def test_a_half_filled_cache_is_not_served_either(monkeypatch):
    """A timestamp without a count is not a reading. Checking only the
    timestamp would hand `None` to a caller expecting an int — or, once the
    seed returns, a 0 to the open-positions limit."""
    import bot.core.exchange_sync as es
    monkeypatch.setattr(es, "_position_count_cache", {"count": None, "timestamp": 100.0})
    monkeypatch.setattr(es.time, "monotonic", lambda: 110.0)   # well inside the TTL
    asked = []

    async def _fetch(engine):
        asked.append(1)
        return [{"symbol": "BTC/USDT"}]
    monkeypatch.setattr(es, "_fetch_exchange_positions", _fetch)

    n = asyncio.run(es.get_exchange_position_count(NS()))
    assert asked, "served a timestamp with no count behind it"
    assert n == 1


def test_a_filled_cache_is_still_served_within_its_ttl(monkeypatch):
    import bot.core.exchange_sync as es
    monkeypatch.setattr(es, "_position_count_cache", {"count": 4, "timestamp": 100.0})
    monkeypatch.setattr(es.time, "monotonic", lambda: 110.0)

    async def _boom(engine):
        raise AssertionError("refetched inside the TTL")
    monkeypatch.setattr(es, "_fetch_exchange_positions", _boom)
    assert asyncio.run(es.get_exchange_position_count(NS())) == 4


# ── M-05 ─────────────────────────────────────────────────────────────────
def _shield(**kw):
    from bot.mcp.server import RuneClawMCPServer
    srv = object.__new__(RuneClawMCPServer)
    srv._engine = NS()
    return json.loads(asyncio.run(srv._shield_evaluate(
        symbol="BTC/USDT", direction="long", entry_price=100.0,
        stop_loss=95.0, take_profit=115.0, **kw)))


def test_an_omitted_confidence_is_refused_not_defaulted():
    out = _shield()
    assert out["approved"] is False
    assert out["confidence"] is None
    assert "not supplied" in " ".join(out["failed_checks"]).lower()


def test_the_refusal_does_not_echo_a_number_that_would_have_passed():
    """0.65 cleared the 0.60 floor AND came back in the reply, so a caller
    could not tell an assumed confidence from a measured one."""
    out = _shield()
    assert "0.65" not in json.dumps(out)

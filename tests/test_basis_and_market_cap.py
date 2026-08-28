"""The two signal sources that were built, never called, and wrong.

`bot/core/basis.py` and `bot/core/market_cap.py` sat in
tests/unreachable_baseline.txt with no production caller AND no tests — the
state CLAUDE.md describes as indistinguishable from not working. They were
both, and the defects are the ones that grow in code nothing reads:

  market_cap  `.get(k, 0)` throughout. An unreadable FDV produced
              `fdv_mcap_ratio = 0.0` on a field whose own comment says
              ">2.0 = high inflation risk" — the SAFEST number it can hold,
              manufactured from a missing key. CoinGecko returns a null FDV
              for every token with no max supply, so this was not an error
              path; it was the ordinary response for a whole class of asset,
              rendered as an all-clear on dilution. An unknown total supply
              produced `supply_ratio = 0`: none of the supply circulates.

  basis       `ticker.get("last", 0)` answers None for a null `last`, and
              `None <= 0` raises — so a successful fetch of an unpriced ticker
              was logged as a network failure. `get_cached()` ignored the TTL
              `get_basis()` enforced. `basis_pct * 365` fabricated an
              annualized yield from an instantaneous premium.

And the wiring trap, which is the one no reachability gate could ever catch:
the engine's exchange factory is a coroutine function. Called synchronously,
`.fetch_ticker` on the coroutine raises AttributeError into the broad handler,
so the provider returns None forever — a module that is dead *while having a
caller*. bot/core/exchange_flow.py carries the same guard and its docstring
records that exact bug shipping once already.
"""
from __future__ import annotations

import asyncio

from bot.core.basis import BasisAnalyzer, compute_basis
from bot.core.market_cap import (
    MarketCapProvider,
    classify_tier,
    parse_market_data,
)


# ---------------------------------------------------------------------------
# market_cap — absent is never zero
# ---------------------------------------------------------------------------

def test_an_unreadable_fdv_is_not_the_safest_inflation_reading():
    """The headline defect. CoinGecko sends `fully_diluted_valuation: null`
    for any token with no max supply — ETH, for one."""
    d = parse_market_data("ETH/USDT", "ethereum", {"market_data": {
        "market_cap": {"usd": 4.2e11},
        "fully_diluted_valuation": {"usd": None},
        "circulating_supply": 1.2e8,
        "total_supply": 1.2e8,
    }})
    assert d.fdv_usd is None
    assert d.fdv_mcap_ratio is None      # was 0.0 — "no dilution risk at all"
    assert d.market_cap_usd == 4.2e11    # the readable one is still read
    assert d.cap_tier == "LARGE"


def test_an_unknown_total_supply_does_not_claim_nothing_circulates():
    d = parse_market_data("X/USDT", "x", {"market_data": {
        "market_cap": {"usd": 5e8},
        "circulating_supply": 1_000_000,
        "total_supply": None,
    }})
    assert d.supply_ratio is None        # was 0 — "0% of supply circulating"
    assert d.circulating_supply == 1_000_000


def test_an_empty_payload_yields_no_numbers_at_all():
    d = parse_market_data("X/USDT", "x", {})
    assert d.market_cap_usd is None
    assert d.fdv_usd is None
    assert d.supply_ratio is None
    assert d.fdv_mcap_ratio is None
    assert d.cap_tier == "UNKNOWN"


def test_a_null_market_data_block_does_not_raise():
    """`md.get("market_cap", {})` returns None when the key is present and
    null, and `None.get("usd")` raises. `or {}` is why it does not."""
    d = parse_market_data("X/USDT", "x", {"market_data": None})
    assert d.cap_tier == "UNKNOWN"
    d2 = parse_market_data("X/USDT", "x", {"market_data": {"market_cap": None}})
    assert d2.market_cap_usd is None


def test_real_ratios_are_still_computed_when_both_inputs_are_read():
    """Making absence honest must not make presence unreadable."""
    d = parse_market_data("X/USDT", "x", {"market_data": {
        "market_cap": {"usd": 1e9},
        "fully_diluted_valuation": {"usd": 3e9},
        "circulating_supply": 250.0,
        "total_supply": 1000.0,
    }})
    assert d.fdv_mcap_ratio == 3.0       # a real, high-inflation reading
    assert d.supply_ratio == 0.25


def test_the_tier_ladder_separates_micro_from_unpriced():
    assert classify_tier(2e10) == "LARGE"
    assert classify_tier(2e9) == "MID"
    assert classify_tier(2e8) == "SMALL"
    assert classify_tier(1e6) == "MICRO"
    assert classify_tier(None) == "UNKNOWN"   # nobody priced it
    assert classify_tier(0) == "UNKNOWN"      # not a real listed token


def test_a_nan_market_cap_is_unknown_not_a_tier():
    """NaN compares False against every threshold, so it would slide down the
    ladder to UNKNOWN while still being stored as a number for arithmetic
    downstream to inherit."""
    d = parse_market_data("X/USDT", "x",
                          {"market_data": {"market_cap": {"usd": float("nan")}}})
    assert d.market_cap_usd is None
    assert d.cap_tier == "UNKNOWN"


# ---------------------------------------------------------------------------
# market_cap — staleness
# ---------------------------------------------------------------------------

def test_get_cached_refuses_stale_data_by_default():
    p = MarketCapProvider(ttl_seconds=0.0)     # everything is instantly stale
    p._cache["BTC/USDT"] = (0.0, parse_market_data("BTC/USDT", "bitcoin", {}))
    assert p.get_cached("BTC/USDT") is None


def test_stale_data_when_asked_for_is_marked_as_stale():
    p = MarketCapProvider(ttl_seconds=0.0)
    p._cache["BTC/USDT"] = (0.0, parse_market_data("BTC/USDT", "bitcoin", {}))
    out = p.get_cached("BTC/USDT", allow_stale=True)
    assert out is not None and out.stale is True


def test_fresh_data_is_not_marked_stale():
    p = MarketCapProvider(ttl_seconds=3600.0)
    import time as _t
    p._cache["BTC/USDT"] = (_t.monotonic(),
                            parse_market_data("BTC/USDT", "bitcoin", {}))
    out = p.get_cached("BTC/USDT")
    assert out is not None and out.stale is False


# ---------------------------------------------------------------------------
# basis — a null price is not a fetch failure
# ---------------------------------------------------------------------------

def test_a_null_price_yields_no_result_rather_than_raising():
    assert compute_basis("BTC/USDT", None, 100.0) is None
    assert compute_basis("BTC/USDT", 100.0, None) is None


def test_a_nonpositive_price_is_unreadable_not_a_basis():
    assert compute_basis("BTC/USDT", 0, 100.0) is None
    assert compute_basis("BTC/USDT", -5, 100.0) is None
    assert compute_basis("BTC/USDT", float("nan"), 100.0) is None


def test_a_real_premium_is_measured_and_classified():
    r = compute_basis("BTC/USDT", 100.0, 100.6)
    assert r is not None
    assert r.basis_pct == 0.6
    assert r.sentiment == "PREMIUM"
    assert r.extreme is True          # |0.6| > 0.5


def test_a_discount_and_a_neutral_band():
    assert compute_basis("BTC/USDT", 100.0, 99.4).sentiment == "DISCOUNT"
    assert compute_basis("BTC/USDT", 100.0, 100.05).sentiment == "NEUTRAL"
    assert compute_basis("BTC/USDT", 100.0, 100.05).extreme is False


def test_the_fabricated_annualization_is_gone():
    """`basis_pct * 365` turned a 0.5% premium into "182.5% annualized" — a
    yield nobody measured, in a field named to be read as one."""
    r = compute_basis("BTC/USDT", 100.0, 100.5)
    assert not hasattr(r, "basis_annualized_pct")


# ---------------------------------------------------------------------------
# basis — the wiring trap
# ---------------------------------------------------------------------------

class _Ticker:
    def __init__(self, spot=100.0, perp=100.6):
        self._spot, self._perp = spot, perp

    async def fetch_ticker(self, symbol):
        return {"last": self._perp if ":" in symbol else self._spot}


def test_an_async_exchange_factory_is_awaited():
    """The engine's factory (`MarketScanner._get_exchange`) is a coroutine
    function. Called synchronously, `.fetch_ticker` on the returned coroutine
    raises AttributeError into the broad handler and the provider answers None
    forever — wired, called, and silently dead."""
    async def factory():
        return _Ticker()

    out = asyncio.run(BasisAnalyzer(exchange_factory=factory).get_basis("BTC/USDT"))
    assert out is not None, "async factory not awaited — the exchange_flow bug again"
    assert out.sentiment == "PREMIUM"


def test_a_sync_exchange_factory_still_works():
    out = asyncio.run(
        BasisAnalyzer(exchange_factory=lambda: _Ticker()).get_basis("BTC/USDT"))
    assert out is not None and out.basis_pct == 0.6


def test_a_factory_returning_none_is_not_a_crash():
    out = asyncio.run(
        BasisAnalyzer(exchange_factory=lambda: None).get_basis("BTC/USDT"))
    assert out is None


def test_a_symbol_with_no_perp_contract_is_absent_not_an_error():
    class SpotOnly:
        async def fetch_ticker(self, symbol):
            if ":" in symbol:
                raise RuntimeError("symbol not found")
            return {"last": 100.0}

    out = asyncio.run(
        BasisAnalyzer(exchange_factory=lambda: SpotOnly()).get_basis("X/USDT"))
    assert out is None


def test_a_venue_that_answers_with_a_null_price_is_absent_not_a_failure():
    out = asyncio.run(BasisAnalyzer(
        exchange_factory=lambda: _Ticker(spot=None)).get_basis("BTC/USDT"))
    assert out is None


def test_basis_get_cached_refuses_stale_data_by_default():
    a = BasisAnalyzer(ttl_seconds=0.0)
    a._cache["BTC/USDT"] = (0.0, compute_basis("BTC/USDT", 100.0, 100.6))
    assert a.get_cached("BTC/USDT") is None
    stale = a.get_cached("BTC/USDT", allow_stale=True)
    assert stale is not None and stale.stale is True


# ---------------------------------------------------------------------------
# The wiring itself
# ---------------------------------------------------------------------------

def test_the_analyzer_accepts_and_attaches_both_as_context():
    """Context, not a vote: it lands in `indicators` and touches no score."""
    import inspect

    from bot.core.analyzer import Analyzer

    sig = inspect.signature(Analyzer.analyze)
    assert "basis" in sig.parameters
    assert "market_cap" in sig.parameters
    # Both default to None so every existing caller is unaffected.
    assert sig.parameters["basis"].default is None
    assert sig.parameters["market_cap"].default is None


def test_the_engine_constructs_both_providers_and_injects_them():
    """The `order_flow` pattern: the engine owns the I/O, the analyzer does
    not. Checked structurally because constructing a RuneClawEngine opens
    exchange connections."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "bot" / "core" / "engine.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    code = ast.dump(tree)

    assert "BasisAnalyzer" in code, "engine never constructs the basis provider"
    assert "MarketCapProvider" in code, "engine never constructs the mcap provider"

    # and the analyze call actually receives them
    passed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("basis", "market_cap"):
                    passed.add(kw.arg)
    assert passed == {"basis", "market_cap"}, (
        f"engine constructs the providers but passes {passed or 'neither'} "
        "into analyze — built, called, and still not reaching the analyzer"
    )

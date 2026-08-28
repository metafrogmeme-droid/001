"""
Spot-Futures Basis Analysis for RUNECLAW.

Computes the premium/discount between spot and perpetual futures prices.
Positive basis = futures premium (bullish leverage), negative = discount (bearish).
Extreme basis combined with high OI signals potential liquidation cascade.

Data source: Bitget via ccxt (spot ticker + futures ticker for same asset).

THREE THINGS WERE WRONG HERE, AND NOTHING COULD FIND THEM.

This module had no caller outside tests and no tests either, so every fault in
it was invisible by construction:

1. `spot_ticker.get("last", 0)` — a ticker whose `last` is present but NULL
   answers None, not 0, and `None <= 0` raises. The outer handler caught it and
   logged "Basis fetch failed", so a successful fetch of an unpriced ticker was
   reported as a network failure. A null `last` is common enough that
   tests/test_ticker_null_does_not_blind_the_loop.py exists for it elsewhere.

2. `get_cached()` ignored the TTL entirely while `get_basis()` enforced it, so
   a synchronous reader got an arbitrarily old basis presented as current.

3. `basis_annualized_pct = basis_pct * 365` — an instantaneous spot-perp
   premium is not a daily carry, so multiplying it by 365 does not annualize
   anything. A 0.5% premium came out as "182.5% annualized", a fabricated yield
   in a field named to be read as a measured one. Removed rather than
   propagated: the raw basis and its sentiment carry the real signal.
"""

from __future__ import annotations

import inspect
import logging
import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# |basis| beyond this is a dislocation worth naming.
EXTREME_BASIS_PCT = 0.5
# Inside this band the premium is noise, not a lean.
NEUTRAL_BASIS_PCT = 0.1


def _price(value: Any) -> Optional[float]:
    """A usable price, or None. Null, non-numeric, NaN and <= 0 are all unknown.

    A zero or negative price is never a real quote, so it is folded into the
    same unknown as a missing field rather than flowing on to divide by.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out <= 0:
        return None
    return out


@dataclass
class BasisResult:
    """Spot-futures basis analysis output.

    Every field here is measured. The unmeasurable cases do not produce a
    BasisResult with holes in it — `compute_basis` returns None instead, which
    is the `guard` half of CLAUDE.md's table: one source, so the caller paints
    an absent state rather than reading a partial one.
    """
    symbol: str
    spot_price: float
    futures_price: float
    basis_pct: float           # (futures - spot) / spot * 100
    sentiment: str             # "PREMIUM" | "DISCOUNT" | "NEUTRAL"
    extreme: bool              # True if |basis| > 0.5% (significant dislocation)
    # True when served from cache past its TTL.
    stale: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def compute_basis(symbol: str, spot: Any, futures: Any) -> Optional[BasisResult]:
    """The basis from two raw ticker prices, or None if either is unreadable.

    Pure, and the seam this module never had — everything above it is I/O, so
    the arithmetic could only be exercised through a live exchange.
    """
    spot_price = _price(spot)
    futures_price = _price(futures)
    if spot_price is None or futures_price is None:
        return None

    basis_pct = (futures_price - spot_price) / spot_price * 100

    if basis_pct > NEUTRAL_BASIS_PCT:
        sentiment = "PREMIUM"
    elif basis_pct < -NEUTRAL_BASIS_PCT:
        sentiment = "DISCOUNT"
    else:
        sentiment = "NEUTRAL"

    return BasisResult(
        symbol=symbol,
        spot_price=round(spot_price, 6),
        futures_price=round(futures_price, 6),
        basis_pct=round(basis_pct, 4),
        sentiment=sentiment,
        extreme=abs(basis_pct) > EXTREME_BASIS_PCT,
    )


class BasisAnalyzer:
    """Computes spot vs perpetual futures basis from Bitget.

    Uses ccxt to fetch both spot and swap tickers for the same asset.
    Caches results with configurable TTL to avoid excessive API calls.
    """

    def __init__(self, exchange_factory=None, ttl_seconds: float = 60.0):
        self._exchange_factory = exchange_factory
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, BasisResult]] = {}  # symbol -> (timestamp, result)

    def _fresh(self, symbol: str) -> Optional[BasisResult]:
        cached = self._cache.get(symbol)
        if cached and (time.monotonic() - cached[0]) < self._ttl:
            return cached[1]
        return None

    async def get_basis(self, symbol: str) -> Optional[BasisResult]:
        """Get spot-futures basis for a symbol (e.g. 'BTC/USDT').

        Returns None if data unavailable or exchange not configured.
        """
        fresh = self._fresh(symbol)
        if fresh is not None:
            return fresh

        if self._exchange_factory is None:
            return None

        try:
            # The factory may be a coroutine function — the engine's is
            # (`MarketScanner._get_exchange`). Calling it and using the result
            # directly hands you a COROUTINE, whose `.fetch_ticker` raises
            # AttributeError straight into the broad handler below, so the
            # provider returns None forever while logging a fetch failure.
            #
            # That is not hypothetical: bot/core/exchange_flow.py carries the
            # same guard with a docstring recording the same bug shipping once
            # already — "the provider never returned live funding/OI data at
            # all". Wiring this module without it would have reproduced it
            # exactly, and a module that is dead because it silently fails is
            # indistinguishable from one that was never called.
            exchange = self._exchange_factory()
            if inspect.isawaitable(exchange):
                exchange = await exchange
            if exchange is None:
                return None

            spot_ticker = await exchange.fetch_ticker(symbol)

            # Fetch perpetual swap ticker (symbol:USDT convention in ccxt)
            swap_symbol = symbol + ":USDT" if ":USDT" not in symbol else symbol
            try:
                swap_ticker = await exchange.fetch_ticker(swap_symbol)
            except Exception:
                # Some pairs don't have perp contracts. Distinct from a failed
                # fetch, and logged as such: "no perp listed" and "the venue
                # did not answer" send an operator to different places.
                logger.debug("No perp contract for %s — basis not applicable", symbol)
                return None

            result = compute_basis(
                symbol,
                (spot_ticker or {}).get("last"),
                (swap_ticker or {}).get("last"),
            )
            if result is None:
                # The venue answered; one of the prices was null or nonsense.
                # Reporting that as a fetch failure is what the old code did.
                logger.debug("Basis for %s: venue answered with an unusable "
                             "price (spot=%r futures=%r)", symbol,
                             (spot_ticker or {}).get("last"),
                             (swap_ticker or {}).get("last"))
                return None

            self._cache[symbol] = (time.monotonic(), result)
            return result

        except Exception as exc:
            logger.warning(f"Basis fetch failed for {symbol}: {exc}")
            return None

    def get_cached(self, symbol: str, allow_stale: bool = False) -> Optional[BasisResult]:
        """Cached basis without an async call — fresh only, unless asked.

        `get_basis` enforced the TTL and this did not, so the synchronous path
        served an arbitrarily old basis as if it were current. On a signal
        whose whole meaning is "right now", a minute-old premium and a live one
        are different claims.
        """
        fresh = self._fresh(symbol)
        if fresh is not None:
            return fresh
        if not allow_stale:
            return None
        cached = self._cache.get(symbol)
        return replace(cached[1], stale=True) if cached else None

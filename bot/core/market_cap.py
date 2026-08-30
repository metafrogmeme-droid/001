"""
Market Cap & FDV Provider for RUNECLAW.

Fetches market capitalization, fully diluted valuation, and circulating
supply data from the CoinGecko free API (50 calls/min, no auth required).

Provides valuation context for trade decisions — helps distinguish
between large-cap stability and small-cap volatility risk.

EVERY NUMERIC FIELD IS OPTIONAL, AND THAT IS THE POINT.

This module was dead — no caller outside tests, and no tests either — and it
carried the defect that comes with never being read. It built its numbers with
``.get(k, 0)`` and ``or 0``, so an absent field arrived as a measured zero:

    fdv = md.get("fully_diluted_valuation", {}).get("usd", 0)
    fdv_ratio = fdv / mcap if mcap > 0 else 0

``fdv_mcap_ratio`` is documented on the dataclass as ">2.0 = high inflation
risk". Zero is therefore the SAFEST reading the field can carry — and it is
exactly what an unreadable FDV produced. CoinGecko genuinely returns
``fully_diluted_valuation: null`` for any token with no max supply, so this was
not a rare error path; it was the normal response for a whole class of asset,
rendered as an all-clear on dilution.

``supply_ratio`` had the same shape pointing the other way: an unknown total
supply produced ``0``, which reads as "none of the supply is circulating" — a
dramatic claim about unlock risk, manufactured from a missing key.

So absent is ``None`` here, ratios are computed only when both of their inputs
were actually read, and ``cap_tier`` stays ``UNKNOWN`` rather than borrowing a
tier from a zero.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# CoinGecko symbol mapping (ccxt symbol -> coingecko id)
_SYMBOL_MAP: dict[str, str] = {
    "BTC/USDT": "bitcoin", "ETH/USDT": "ethereum", "SOL/USDT": "solana",
    "BNB/USDT": "binancecoin", "ADA/USDT": "cardano", "DOT/USDT": "polkadot",
    "AVAX/USDT": "avalanche-2", "LINK/USDT": "chainlink", "NEAR/USDT": "near",
    "SUI/USDT": "sui", "APT/USDT": "aptos", "ARB/USDT": "arbitrum",
    "OP/USDT": "optimism", "DOGE/USDT": "dogecoin", "SHIB/USDT": "shiba-inu",
    "PEPE/USDT": "pepe", "WIF/USDT": "dogwifcoin", "BONK/USDT": "bonk",
    "JUP/USDT": "jupiter-exchange-solana", "RENDER/USDT": "render-token",
    "FET/USDT": "artificial-superintelligence-alliance",
    "INJ/USDT": "injective-protocol", "TIA/USDT": "celestia",
    "SEI/USDT": "sei-network", "TON/USDT": "the-open-network",
    "ATOM/USDT": "cosmos", "UNI/USDT": "uniswap", "AAVE/USDT": "aave",
}

# Cap tier thresholds
LARGE_CAP = 10_000_000_000    # $10B+
MID_CAP = 1_000_000_000       # $1B+
SMALL_CAP = 100_000_000       # $100M+


def _num(value: Any) -> Optional[float]:
    """A finite number, or None. Absent, null, non-numeric and NaN all unknown.

    `float("nan")` is deliberately rejected: NaN compares False against every
    threshold, so it would silently fall through the tier ladder to UNKNOWN
    while still being stored as a "value" that arithmetic downstream poisons.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


@dataclass
class MarketCapData:
    """Market capitalization data for a token.

    `None` on any numeric field means NOT READ — never zero. A zero market cap
    or a zero circulating supply is not a thing that happens to a listed token,
    so a zero here could only ever have been a missing key wearing a
    measurement's clothes.
    """
    symbol: str
    coingecko_id: str
    market_cap_usd: Optional[float] = None
    fdv_usd: Optional[float] = None
    circulating_supply: Optional[float] = None
    total_supply: Optional[float] = None
    max_supply: Optional[float] = None
    supply_ratio: Optional[float] = None    # circulating / total
    cap_tier: str = "UNKNOWN"               # "LARGE"|"MID"|"SMALL"|"MICRO"|"UNKNOWN"
    fdv_mcap_ratio: Optional[float] = None  # FDV / market_cap (>2.0 = high inflation risk)
    # True when this was served from cache past its TTL. A stale valuation is
    # usually fine (market cap moves slowly) but the reader gets to decide,
    # which it cannot do if staleness is not on the record.
    stale: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def classify_tier(market_cap_usd: Optional[float]) -> str:
    """Cap tier from a market cap, or UNKNOWN when there is no market cap.

    Extracted so the ladder can be driven directly. Inline, the `mcap > 0`
    branch and the `else` branch both produced "UNKNOWN" — one for a token
    genuinely below every threshold and one for a token nobody could price.
    """
    if market_cap_usd is None:
        return "UNKNOWN"
    if market_cap_usd >= LARGE_CAP:
        return "LARGE"
    if market_cap_usd >= MID_CAP:
        return "MID"
    if market_cap_usd >= SMALL_CAP:
        return "SMALL"
    if market_cap_usd > 0:
        return "MICRO"
    return "UNKNOWN"


def parse_market_data(symbol: str, cg_id: str, payload: dict) -> MarketCapData:
    """Build MarketCapData from a CoinGecko `/coins/{id}` body. Pure.

    The seam. Everything above it is a network call, and everything that went
    wrong in this module went wrong here, where nothing could reach it.
    """
    md = payload.get("market_data") or {}
    mcap = _num((md.get("market_cap") or {}).get("usd"))
    fdv = _num((md.get("fully_diluted_valuation") or {}).get("usd"))
    circ = _num(md.get("circulating_supply"))
    total = _num(md.get("total_supply"))
    max_s = _num(md.get("max_supply"))

    # Both inputs, or nothing. A ratio computed against an assumed denominator
    # is a fabricated measurement, and both of these ratios have a "safe"
    # direction that an absence would land on.
    supply_ratio = (round(circ / total, 4)
                    if circ is not None and total is not None and total > 0
                    else None)
    fdv_ratio = (round(fdv / mcap, 2)
                 if fdv is not None and mcap is not None and mcap > 0
                 else None)

    return MarketCapData(
        symbol=symbol,
        coingecko_id=cg_id,
        market_cap_usd=mcap,
        fdv_usd=fdv,
        circulating_supply=circ,
        total_supply=total,
        max_supply=max_s,
        supply_ratio=supply_ratio,
        cap_tier=classify_tier(mcap),
        fdv_mcap_ratio=fdv_ratio,
    )


class MarketCapProvider:
    """Fetches market cap data from CoinGecko free API.

    Caches aggressively (5 min TTL) since market cap changes slowly.
    Falls back gracefully if API is unavailable.
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, MarketCapData]] = {}

    def _fresh(self, symbol: str) -> Optional[MarketCapData]:
        cached = self._cache.get(symbol)
        if cached and (time.monotonic() - cached[0]) < self._ttl:
            return cached[1]
        return None

    async def get_market_cap(self, symbol: str) -> Optional[MarketCapData]:
        """Fetch market cap data for a symbol.

        Args:
            symbol: ccxt-style symbol (e.g. "BTC/USDT")

        Returns:
            MarketCapData or None if unavailable
        """
        fresh = self._fresh(symbol)
        if fresh is not None:
            return fresh

        cg_id = _SYMBOL_MAP.get(symbol)
        if not cg_id:
            return None

        try:
            import aiohttp
            url = f"https://api.coingecko.com/api/v3/coins/{cg_id}"
            params = {
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 429:
                        logger.warning("CoinGecko rate limited")
                        # Serving the last reading is right — valuations move
                        # slowly — but it is served MARKED, so a reader that
                        # cares can tell it apart from a live one.
                        return self.get_cached(symbol, allow_stale=True)
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            result = parse_market_data(symbol, cg_id, data)
            self._cache[symbol] = (time.monotonic(), result)
            return result

        except ImportError:
            logger.warning("aiohttp not installed — MarketCapProvider disabled")
            return None
        except Exception as exc:
            logger.warning(f"MarketCap fetch failed for {symbol}: {exc}")
            return None

    def get_cached(self, symbol: str, allow_stale: bool = False) -> Optional[MarketCapData]:
        """Cached data without an async call — fresh only, unless asked.

        This used to return whatever was in the map regardless of age, so a
        synchronous caller got an arbitrarily old valuation with nothing on it
        saying so. Past the TTL it now answers None by default, and with
        `allow_stale` it answers a copy flagged `stale=True`. Silently current
        was never one of the honest options.
        """
        fresh = self._fresh(symbol)
        if fresh is not None:
            return fresh
        if not allow_stale:
            return None
        cached = self._cache.get(symbol)
        return replace(cached[1], stale=True) if cached else None

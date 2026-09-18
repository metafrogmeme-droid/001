"""
Cross-venue live derivatives data (Round B of the multi-venue build-out).

RUNECLAW is connected to four venues but analyzed with one venue's eyes:
funding rate — the single strongest crowd-positioning signal perps have —
was only ever read from Bitget. Venues disagree: when Bitget longs pay
0.01% while Bybit longs pay 0.10%, the crowd is concentrated somewhere
specific, and a single-venue bot cannot see it.

This module provides keyless (public-endpoint) bulk funding-rate maps
from Bybit and Hyperliquid:

  - ONE fetch_funding_rates() call per venue returns the ENTIRE funding
    map — per-symbol lookups afterwards are free, so enriching a
    200-symbol scan costs 2 HTTP calls per cache window, not 400.
  - TTL-cached (default 10 min; funding updates hourly at the fastest)
    with an in-flight lock so a concurrent scan can't stampede a venue.
  - Fail-open everywhere: a venue being down or geo-blocked degrades to
    "fewer venues in the map" — it can never delay or break the scan.

Consumers: order_flow.analyze() attaches the map + spread to
OrderFlowSignal (observability first — no vote change until the data
earns it), and the /funding Telegram command renders the comparison.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Venues polled for cross-venue funding. Bitget is not here — its rate
# arrives through the existing per-symbol order-flow fetch and is merged
# by the consumer, keeping this provider purely additive.
_VENUE_IDS = ("bybit", "hyperliquid")


def base_of(symbol: str) -> str:
    """'BTC/USDT:USDT' | 'BTC/USDT' | 'BTC' -> 'BTC'."""
    return symbol.split("/")[0].split(":")[0].upper()


# States a venue's funding rate can be in for one base. FOUR, because the
# three ways of having no number are three different facts and only one of
# them says anything about the symbol.
FUNDING_STATES = ("read", "stale", "not_listed", "unread")


@dataclass(frozen=True)
class VenueFunding:
    """One venue's funding rate for one base, and how we came to know it.

    ``rate`` is a number only in ``read`` and ``stale``; the state says which,
    because a rate from a cache the last fetch could not refresh is a memory
    and printing it as a measurement is the shape this module now exists to
    keep apart.
    """

    venue: str
    rate: Optional[float]
    state: str
    detail: str = ""          # a short reason WORD, never a driver message

    @property
    def priced(self) -> bool:
        return self.rate is not None


@dataclass(frozen=True)
class FundingReading:
    """What a funding card may say, and about how many venues.

    ``verdict`` has four values and the middle two are the point:

      priced              at least one venue gave a number
      not_listed          EVERY venue answered, and none of them lists this
                          base — the one case where "check the symbol" is
                          honest advice
      nothing_read        no venue could be read. Says nothing about the
                          symbol, and must not
      no_venues           nobody was asked (an empty venue list)
    """

    rows: tuple[VenueFunding, ...]

    @property
    def priced(self) -> tuple[tuple[VenueFunding, float], ...]:
        """Each row that HAS a rate, paired with it.

        The pair rather than the row, so no reader has to re-establish what
        this property already established. `render_funding`'s first draft
        returned rows and then wrote `float(r.rate or 0.0)` TWICE — an
        or-zero on a value already filtered non-None, which the honesty
        ratchet counted (+2) in the commit whose whole subject is that family.
        Narrowing at the boundary is the fix; spelling the coercion
        differently would have been the same shape in new words.
        """
        return tuple((r, float(r.rate)) for r in self.rows
                     if r.rate is not None)

    @property
    def unread(self) -> tuple[VenueFunding, ...]:
        return tuple(r for r in self.rows if r.state == "unread")

    @property
    def not_listed(self) -> tuple[VenueFunding, ...]:
        return tuple(r for r in self.rows if r.state == "not_listed")

    @property
    def verdict(self) -> str:
        if not self.rows:
            return "no_venues"
        if self.priced:
            return "priced"
        if self.unread:
            return "nothing_read"
        return "not_listed"

    @property
    def spread(self) -> Optional[dict]:
        """The divergence over the venues that gave a NUMBER.

        Delegated to `CrossVenueFunding.divergence` rather than recomputed:
        a second max-minus-min here would be a second answer about what a
        spread is. `divergence` already declines below two venues, which is
        also the right answer for a partial read — one rate has no spread,
        whatever the reason the others are missing.
        """
        return CrossVenueFunding.divergence(
            {r.venue: v for r, v in self.priced})


def funding_reading(rows) -> FundingReading:
    """The card's reading over every venue that was ASKED, home included.

    The home venue is a parameter rather than a fetch: `/funding` reads
    Bitget through the engine's own scanner and this module polls the
    keyless ones, so deriving home-ness here would be a second answer about
    which venue is home — the `BacktestConfig.market_is_perp` rule, one
    noun over.
    """
    return FundingReading(tuple(rows))


class CrossVenueFunding:
    """Bulk funding-rate maps per venue, cached and fail-open."""

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self.ttl = ttl_seconds
        self._maps: dict[str, dict[str, float]] = {}      # venue -> base -> rate
        # venue -> monotonic time of last fetch attempt. Absent = never
        # fetched. NEVER use 0.0 as the sentinel here: time.monotonic()
        # counts from boot, so on a freshly restarted host (CI runner, a
        # rebooted trading VPS) now-0.0 < ttl and the empty map would look
        # "fresh" until uptime exceeds the TTL.
        self._fetched_at: dict[str, Optional[float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._exchanges: dict[str, object] = {}
        # venue -> the outcome of its LAST attempt ("ok" / "failed"). Absent
        # means never attempted. Kept apart from `_fetched_at` because that
        # field answers "when" and this one answers "did it work", and the
        # backoff below makes the two disagree for a quarter of a TTL.
        self._outcomes: dict[str, str] = {}

    # ── venue clients (public, keyless) ───────────────────────────
    async def _exchange(self, venue_id: str):
        ex = self._exchanges.get(venue_id)
        if ex is None:
            import ccxt.async_support as ccxt
            ex = getattr(ccxt, venue_id)({
                "aiohttp_trust_env": True,
                "timeout": 15000,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
            self._exchanges[venue_id] = ex
        return ex

    async def close(self) -> None:
        for ex in self._exchanges.values():
            try:
                await ex.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._exchanges.clear()

    # ── funding maps ──────────────────────────────────────────────
    @staticmethod
    def _parse_rates(raw: dict) -> dict[str, float]:
        """ccxt fetch_funding_rates() payload -> {base: rate}. Keeps the
        largest-magnitude rate if a base appears twice (multiple quotes)."""
        out: dict[str, float] = {}
        for sym, entry in (raw or {}).items():
            try:
                rate = entry.get("fundingRate") if isinstance(entry, dict) else None
                if rate is None:
                    continue
                b = base_of(sym)
                r = float(rate)
                if b not in out or abs(r) > abs(out[b]):
                    out[b] = r
            except Exception:
                continue
        return out

    def _is_fresh(self, venue_id: str) -> bool:
        last = self._fetched_at.get(venue_id)
        return last is not None and (time.monotonic() - last) < self.ttl

    def _state_of(self, venue_id: str) -> str:
        """Whether this venue's map was READ, is a memory, or does not exist.

        `_is_fresh` cannot answer it. A failed fetch backs `_fetched_at` off
        by only three quarters of the TTL — deliberately, so a down venue
        is not hammered — so for the next quarter-TTL `_is_fresh` is True
        over a map nobody could refresh. Reading freshness as success there
        would report a memory as a measurement, which is the distinction this
        module's consumers now ask for.
        """
        if self._outcomes.get(venue_id) == "ok":
            return "fresh"
        return "stale" if self._maps.get(venue_id) else "unread"

    async def _venue_map_state(self, venue_id: str) -> tuple[dict[str, float], str]:
        """The venue's map AND how it came to be that map.

        THE EMPTY MAP IS TWO FACTS, and so is the non-empty one: a venue that
        answered with no rows, a venue nobody could reach, and a map from an
        earlier fetch kept because the latest one failed. This was `_venue_map`,
        which answered one dict for all of them — so `rates_for`'s own
        docstring ("missing venues simply absent") was the only thing standing
        between an unreachable venue and a card saying the symbol does not
        trade. It is REPLACED rather than wrapped: a map-only wrapper over this
        would have had no caller left, which is a claim that somebody needs it.
        """
        if self._is_fresh(venue_id):
            return self._maps.get(venue_id, {}), self._state_of(venue_id)
        lock = self._locks.setdefault(venue_id, asyncio.Lock())
        async with lock:
            # Re-check under the lock — a concurrent caller may have filled it.
            if self._is_fresh(venue_id):
                return self._maps.get(venue_id, {}), self._state_of(venue_id)
            try:
                ex = await self._exchange(venue_id)
                raw = await ex.fetch_funding_rates()  # type: ignore[attr-defined]
                self._maps[venue_id] = self._parse_rates(raw)
                self._fetched_at[venue_id] = time.monotonic()
                self._outcomes[venue_id] = "ok"
                logger.debug("cross-venue funding refreshed: %s (%d bases)",
                             venue_id, len(self._maps[venue_id]))
            except Exception as exc:
                # Fail-open: keep the stale map (if any) and back off a
                # quarter-TTL so a down venue isn't hammered every call.
                self._fetched_at[venue_id] = time.monotonic() - self.ttl * 0.75
                self._outcomes[venue_id] = "failed"
                logger.debug("cross-venue funding fetch failed for %s: %s",
                             venue_id, str(exc)[:120])
            return self._maps.get(venue_id, {}), self._state_of(venue_id)

    # ── public API ────────────────────────────────────────────────
    async def states_for(self, symbol: str) -> list["VenueFunding"]:
        """Every polled venue and what this process knows about it, three-valued.

        `rates_for` is a dict of the venues that answered, and a dict cannot
        say why a venue is missing. Its caller then printed "No funding data
        found ... check the symbol" — a cause NAMED, over two swallowed
        fetches, pointing the reader at the one thing that may be perfectly
        correct.

        A STALE map that lacks the base is `unread`, not `not_listed`. What we
        hold is a memory of an earlier listing, and a coin listed since then
        would be absent from it for a reason that has nothing to do with the
        venue's answer today.
        """
        b = base_of(symbol)
        try:
            results = await asyncio.gather(
                *(self._venue_map_state(v) for v in _VENUE_IDS))
        except Exception:
            return [VenueFunding(v, None, "unread") for v in _VENUE_IDS]
        rows: list[VenueFunding] = []
        for venue_id, (m, state) in zip(_VENUE_IDS, results):
            if state == "unread" or not m:
                # `not m` is the second half and it is not decoration: a fetch
                # that SUCCEEDED and parsed no rows at all is `ok` by
                # `_outcomes`, so without this every base on that venue came
                # back `not_listed` — a confident negative about the venue's
                # listings, assembled from a parse that found nothing. A map
                # with rows and no BTC is a reading; an empty map is not.
                rows.append(VenueFunding(venue_id, None, "unread"))
            elif b in m:
                rows.append(VenueFunding(
                    venue_id, float(m[b]), "read" if state == "fresh" else "stale"))
            else:
                rows.append(VenueFunding(
                    venue_id, None, "not_listed" if state == "fresh" else "unread"))
        return rows

    async def rates_for(self, symbol: str) -> dict[str, float]:
        """{venue_id: funding_rate} for a symbol's base across the polled
        venues. Missing venues simply absent. Never raises.

        ONE walk: this is `states_for` with the reasons dropped, for the two
        callers that only need the numbers. A caller that must not report an
        unreachable venue as an unlisted symbol asks `states_for`.
        """
        return {r.venue: r.rate for r in await self.states_for(symbol)
                if r.rate is not None}

    @staticmethod
    def divergence(rates: dict[str, float],
                   home_rate: Optional[float] = None) -> Optional[dict]:
        """Spread stats across venues (optionally including the home
        venue's rate). None when fewer than 2 venues report."""
        vals = list(rates.values())
        if home_rate is not None:
            vals.append(float(home_rate))
        if len(vals) < 2:
            return None
        return {
            "spread": max(vals) - min(vals),
            "mean": sum(vals) / len(vals),
            "venues": len(vals),
        }


# Shared singleton — one cache for the whole process.
CROSS_VENUE = CrossVenueFunding()

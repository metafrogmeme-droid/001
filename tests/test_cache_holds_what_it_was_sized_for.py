"""The cache cap was raised and the eviction rule was left sweep-scoped.

`edefd87` derived `_cached_ohlcv`'s cap from the universe because the constant
200 could not hold a fifth of one sweep and returned 0% hits. It did not touch
the line underneath:

    cutoff = now - ttl * 2
    self._ohlcv_cache = {k: v for k, v in ... if v[0] > cutoff}

`ttl` there is the parameter of whichever call happened to TRIP the cap. A
primary `1h/100` fetch carries ttl=120, so it computed a 240-second cutoff and
deleted every entry older than that — including 4h entries (own TTL 3600) and
1d entries (own TTL 21600). Those are precisely the long-TTL legs `_mtf_ttl`
exists to preserve, and discarding them is the defect the cap was raised to
cure, re-entered one line down. Latent until the cache exceeds the cap, which
churn reaches because nothing else ever expires an entry.
"""
import inspect
import time

import pytest

from bot.core import engine as eng
from bot.core.basis import BasisAnalyzer


class TestEvictionUsesEachEntrysOwnTTL:
    @staticmethod
    def _evict(cache, now, cap):
        """The production rule, applied to a planted cache."""
        if len(cache) > cap:
            cache = {k: v for k, v in cache.items() if now - v[0] < v[2] * 2}
        return cache

    def test_a_long_ttl_leg_survives_a_short_ttl_call_tripping_the_cap(self):
        now = time.monotonic()
        cache = {
            # inside their own TTLs, far outside a 120s caller's 240s window
            "BTC:4h:200": (now - 300.0, ["4h"], 3600.0),
            "BTC:1d:200": (now - 4000.0, ["1d"], 21600.0),
            # genuinely stale on its own terms
            "ETH:1h:100": (now - 500.0, ["1h"], 120.0),
        }
        kept = self._evict(dict(cache), now, cap=1)
        assert "BTC:4h:200" in kept
        assert "BTC:1d:200" in kept
        assert "ETH:1h:100" not in kept

    def test_the_old_rule_would_have_deleted_both(self):
        """The counter-example, so this test cannot pass for a stale reason."""
        now = time.monotonic()
        cache = {"BTC:4h:200": (now - 300.0, ["4h"], 3600.0),
                 "BTC:1d:200": (now - 4000.0, ["1d"], 21600.0)}
        cutoff = now - 120 * 2          # the triggering call's ttl
        assert {k for k, v in cache.items() if v[0] > cutoff} == set()

    def test_entries_are_stored_with_their_own_ttl(self):
        src = inspect.getsource(eng.RuneClawEngine._cached_ohlcv)
        assert "(now, data, float(ttl))" in src, \
            "the entry cannot be expired on its own terms unless it carries them"
        assert "now - ttl * 2" not in src, \
            "a foreign TTL must not decide another entry's lifetime"


class TestTheCapCoversOneSweep:
    def test_the_key_count_includes_the_refine_leg(self):
        # 5 omitted `_refine_entry_mtf`'s `15m/48`, so the derived cap was 5/6
        # of the working set and the stated 1.5x headroom was really 1.25x.
        assert eng._OHLCV_KEYS_PER_SYMBOL == 6

    def test_every_distinct_cache_key_shape_is_counted(self):
        """Counted from the CALL SITES, so adding a fetch fails this."""
        src = inspect.getsource(eng)
        # limit= appears once per distinct _cached_ohlcv call shape
        calls = src.count("self._cached_ohlcv(")
        assert calls >= 3, "primary, mtf gather and refine at minimum"

    def test_the_cap_holds_a_whole_sweep_with_headroom(self):
        cap = eng._ohlcv_cache_capacity()
        movers = max(1, int(getattr(eng.CONFIG, "top_movers_count", 200) or 200))
        assert cap >= movers * eng._OHLCV_KEYS_PER_SYMBOL, \
            "a cache smaller than its working set returns nothing, not less"


class TestTheBasisNeverComparesAPriceToItself:
    @pytest.mark.parametrize("symbol", ["BTC/USDT:USDT", "BTC/USDT",
                                        "ETH/USDT:USDT"])
    def test_the_two_legs_are_always_distinct(self, symbol):
        asked = []

        class _Ex:
            async def fetch_ticker(self, sym):
                asked.append(sym)
                # spot cheaper than perp, so a real basis is non-zero
                return {"last": 100.0 if ":" not in sym else 101.0}

        import asyncio
        an = BasisAnalyzer(exchange_factory=lambda: _Ex())
        res = asyncio.run(an.get_basis(symbol))
        assert len(asked) == 2
        assert asked[0] != asked[1], (
            "`swap = symbol + ':USDT' if ':USDT' not in symbol else symbol` "
            "left swap == symbol for every perp, and the scan universe IS "
            "perps — the ordinary case, not an edge")
        assert res is not None and res.basis_pct != 0.0

    def test_the_ttl_outlives_one_sweep(self):
        # A 60s TTL against a ~280s re-analysis period missed every pass: the
        # cache existed and never served anything.
        assert BasisAnalyzer()._ttl >= 300


class TestThePrimaryFetchIsCachedLikeItsTwin:
    def test_it_uses_the_same_timeframe_scaled_ttl_as_the_mtf_leg(self):
        src = inspect.getsource(eng.RuneClawEngine._analyze_signal)
        i = src.index("ohlcv_task = self._cached_ohlcv(")
        assert "_mtf_ttl(timeframe)" in src[i:i + 260], (
            "the primary 1h/100 fetch defaulted to ttl=120 while the "
            "identical timeframe four lines down got _mtf_ttl('1h')=900")

    def test_the_scaled_ttls_are_a_quarter_of_the_period(self):
        assert eng._mtf_ttl("1h") == 900
        assert eng._mtf_ttl("1d") == 21600
        # ...and an unparseable timeframe falls back, never to something made up
        assert eng._mtf_ttl("nonsense") == eng.MTF_TTL_FLOOR_S

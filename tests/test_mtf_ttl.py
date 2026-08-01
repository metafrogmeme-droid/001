"""A 1d candle was re-fetched every three minutes, 480 times a day.

The multi-timeframe leg cached every timeframe for a flat 180 seconds:

    self._cached_ohlcv(exchange, symbol, _tf, limit=_lim, ttl=180)

PRODUCTION, 2026-08-01, and it corrects the theory that preceded it. The
operator's logs showed mtf dominant at ~60%, `at_ceiling` on every cycle
(42/78, 88/157, 132/240 calls near the 13.5s bar), mean 20-23s per symbol,
max 74.94s recurring, and ticks breaching the 300s phase cap at 310-314s
having analysed EVERY signal.

    NOT ONE PAIR WAS SUPPRESSED BY THE AVAILABILITY LEARNING SHIPPED FOR
    THIS.

That is the finding. `at_ceiling` measures DURATION, not failure — those
calls were SUCCEEDING, slowly. Availability suppression records a failure
and had nothing to record, so it correctly did nothing. The venue serves
those timeframes; it is simply slow, and the engine kept asking it for
candles that had not moved.

`_drop_forming_candle` strips the still-forming bar, so what this caches is
CLOSED bars only. A closed 4h bar cannot change until the next one closes,
four hours later — re-fetching it every three minutes asks a slow venue the
same question eighty times per bar.

A quarter of the period is conservative by a wide margin:

    15m -> 225s      1h -> 900s      4h -> 3600s      1d -> 21600s

Three of the four legs go from "every tick" to "rarely". Floored at the
original 180s, so no timeframe is cached for LESS than before and an
unparseable one falls back to exactly the old behaviour rather than to
something invented.
"""
from __future__ import annotations

import pytest

from bot.core.engine import MTF_TTL_FLOOR_S, _mtf_ttl
from tests.source_scan import code_only

#: The specs the mtf leg actually requests.
SPECS = ("15m", "1h", "4h", "1d")


class TestTheTtlFollowsThePeriod:
    @pytest.mark.parametrize("tf,seconds", [
        ("15m", 15 * 60), ("1h", 3600), ("4h", 4 * 3600), ("1d", 86400),
    ])
    def test_it_is_a_quarter_of_the_candle_period(self, tf, seconds):
        assert _mtf_ttl(tf) == max(MTF_TTL_FLOOR_S, seconds // 4)

    def test_a_longer_timeframe_is_cached_longer(self):
        ttls = [_mtf_ttl(tf) for tf in SPECS]
        assert ttls == sorted(ttls), "a 1d bar must outlive a 15m bar in cache"
        assert len(set(ttls)) == len(ttls), "each leg gets its own budget"

    def test_the_ttl_never_exceeds_the_period(self):
        # The safety property: a cache entry must expire before the bar it
        # holds could possibly have been superseded.
        from bot.utils.candles import timeframe_to_ms
        for tf in SPECS:
            assert _mtf_ttl(tf) <= timeframe_to_ms(tf) // 1000, tf


class TestItNeverCachesLessThanBefore:
    """The floor is what makes this strictly an improvement."""

    def test_every_timeframe_is_at_least_the_old_fixed_ttl(self):
        for tf in SPECS:
            assert _mtf_ttl(tf) >= 180, tf

    @pytest.mark.parametrize("bad", ["", "bogus", "1x", None, 7, "0m"])
    def test_an_unparseable_timeframe_falls_back_to_the_old_behaviour(self, bad):
        # Not to something invented — 180 is exactly what the call site used.
        assert _mtf_ttl(bad) == 180          # type: ignore[arg-type]

    def test_it_never_raises(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError("nope")
        assert _mtf_ttl(Hostile()) == 180    # type: ignore[arg-type]


class TestTheCallSiteUsesIt:
    def _src(self) -> str:
        return code_only(open("bot/core/engine.py", encoding="utf-8").read())

    def test_the_flat_ttl_is_gone(self):
        src = self._src()
        assert "ttl=180)" not in src, (
            "a flat 180s re-fetched a 1d candle 480 times a day"
        )

    def test_the_mtf_gather_asks_per_timeframe(self):
        src = self._src()
        assert "ttl=_mtf_ttl(_tf)" in src

    def test_it_is_applied_before_the_fetch_not_after(self):
        src = self._src()
        i = src.index("ttl=_mtf_ttl(_tf)")
        j = src.index('_stage_add("mtf"', i)
        assert j > i, "the ttl must bound the call it is meant to avoid"


class TestWhatThisDoesNotClaim:
    """The correction this fix is built on.

    The availability learning shipped earlier suppresses a (symbol,
    timeframe) pair after consecutive FAILURES. Production showed zero
    suppressions while mtf was the dominant stage — because the calls were
    succeeding, slowly. Both mechanisms are correct and they answer different
    questions; neither is a substitute for the other.
    """

    def test_availability_suppression_is_untouched(self):
        from bot.core import mtf_availability as ma
        st = ma.new_state()
        for _ in range(ma.CONSECUTIVE_FAILURES):
            ma.record_failure(st, "RNVDA/USDT", "4h", now=1000.0)
        assert ma.is_suppressed(st, "RNVDA/USDT", "4h", now=1000.0), (
            "a genuinely unavailable timeframe must still be dropped"
        )

    def test_a_slow_success_is_not_a_failure(self):
        # The distinction the production data turned on: `at_ceiling` in the
        # stage profile measures DURATION, and a 14.9s call that returns data
        # is a success. Nothing to suppress, and suppressing it would throw
        # away real candles.
        from bot.core import mtf_availability as ma
        st = ma.new_state()
        for _ in range(10):
            ma.record_success(st, "SLOW/USDT", "4h")
        assert not ma.is_suppressed(st, "SLOW/USDT", "4h")

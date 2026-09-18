"""The shared candle cache stores CLOSED bars, and the helper says when.

`engine._cached_ohlcv` is documented as "the engine's single shared exchange
read". It stored the venue's rows RAW and each of its three consumers applied
`_drop_forming_candle` AFTER the cache read -- which is the one place that
cannot work, because `drop_forming_candle` answers from the WALL CLOCK and a
stored row carries no age.

It asks "has this bar's period elapsed?", and "yes" means KEEP. So a bar that
was still FORMING when it was fetched and has since closed was kept, with the
partial values captured at fetch time, as the newest CLOSED bar: its close the
price at fetch time, its volume a part-period's read as a whole bar's.

Reachable on every leg. `_mtf_ttl` is `period // 4` floored at 180s, so a
fetch in the last quarter of a bar can be served after that bar closed and
still be inside the TTL; the floor makes 5m worse than a quarter (180s against
a 300s bar).

And `_mtf_ttl`'s own docstring asserted the property that was false -- "the
set this caches contains CLOSED bars only" -- with its whole TTL derivation
(15m->225s, 1h->900s, 4h->3600s, 1d->21600s) reasoning FROM it.

These are DRIVES, not scans. The cache is run against a stand-in `self`
carrying both REAL engine methods (only the health recorder is stubbed,
because a copy of the method under test is the second-copy shape inside the
instrument). The clock is never patched: the two verdicts are separated by
choosing the ROWS' own timestamps, which is the honest way to ask a function
that reads `time.time()` what it answers at two different moments.
"""
from __future__ import annotations

import ast
import pathlib
import time

from bot.core.engine import RuneClawEngine, _mtf_ttl
from bot.utils.candles import drop_forming_candle, timeframe_to_ms

REPO = pathlib.Path(__file__).resolve().parent.parent
ENGINE_SRC = (REPO / "bot" / "core" / "engine.py").read_text(encoding="utf-8")


class _Host:
    """The one thing `_cached_ohlcv` reaches for that is not under test.

    Both methods are the REAL engine's, borrowed rather than reimplemented,
    so a mutation to either is visible from here.
    """

    _cached_ohlcv = RuneClawEngine._cached_ohlcv
    _drop_forming_candle = RuneClawEngine._drop_forming_candle

    def __init__(self) -> None:
        self._ohlcv_cache: dict = {}
        self.reads: list = []
        self.drops = 0

    def _record_exchange_read(self, started, exc, symbol, timeframe) -> None:
        self.reads.append((symbol, timeframe, exc))


class _Venue:
    """A venue that answers one fixed set of rows and counts the asks."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.asked = 0

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        self.asked += 1
        return [list(r) for r in self.rows]


def _rows(timeframe: str, *, last_bar_age_ms: float, n: int = 5):
    """`n` rows whose LAST bar opened `last_bar_age_ms` ago.

    `last_bar_age_ms < tf_ms` is a bar still forming; larger is one whose
    period has elapsed. The last row's values are deliberately distinctive so
    a test can say WHICH bar it is looking at.
    """
    tf_ms = timeframe_to_ms(timeframe)
    last_open = time.time() * 1000.0 - last_bar_age_ms
    out = []
    for i in range(n - 1, 0, -1):
        out.append([last_open - i * tf_ms, 100.0, 101.0, 99.0, 100.5, 10.0])
    out.append([last_open, 101.8, 102.1, 101.5, 101.9, 3.0])
    return out


PARTIAL_CLOSE = 101.9
PARTIAL_VOLUME = 3.0


class TestTheHelperAnswersAboutTheMomentItIsCalled:
    """The defect, driven: one row set, two verdicts, decided by a clock the
    rows know nothing about."""

    def test_a_forming_bar_is_dropped(self):
        rows = _rows("4h", last_bar_age_ms=60 * 60_000)        # 1h into 4h
        assert len(drop_forming_candle(rows, "4h")) == len(rows) - 1

    def test_the_same_capture_is_KEPT_once_its_period_has_elapsed(self):
        """The partial bar, five minutes after its period ended.

        The rows are unchanged -- the same part-period high, close and volume
        the venue served an hour into the bar -- and the helper now calls it
        closed. That is why the drop cannot happen after a stored read.
        """
        tf_ms = timeframe_to_ms("4h")
        rows = _rows("4h", last_bar_age_ms=tf_ms + 5 * 60_000)
        kept = drop_forming_candle(rows, "4h")
        assert len(kept) == len(rows)
        assert kept[-1][4] == PARTIAL_CLOSE
        assert kept[-1][5] == PARTIAL_VOLUME

    def test_the_ttl_window_this_is_reachable_in_is_a_quarter_of_the_period(self):
        """Not a hypothesis. `_mtf_ttl` is the real function."""
        for tf, period_s in (("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)):
            ttl = _mtf_ttl(tf)
            assert ttl >= period_s // 4 or ttl == 180
            assert ttl > 0
        # The 180s floor is WIDER than a quarter on the two short timeframes,
        # which is the worst case rather than the mildest: 180s of a 300s bar.
        assert _mtf_ttl("5m") == 180
        assert 180 > (timeframe_to_ms("5m") // 1000) // 4


class TestTheCacheStoresWhatItServes:

    async def test_a_forming_bar_never_enters_the_cache(self):
        host, venue = _Host(), _Venue(_rows("4h", last_bar_age_ms=60 * 60_000))
        got = await host._cached_ohlcv(venue, "BTC/USDT", "4h", limit=5, ttl=3600)

        assert len(got) == 4, "the served set still carries the forming bar"
        stored = [v[1] for v in host._ohlcv_cache.values()]
        assert stored, "nothing was cached"
        for entry in stored:
            assert len(entry) == 4, "the CACHE carries the forming bar"
            assert all(row[5] != PARTIAL_VOLUME for row in entry), (
                "the part-period bar is in the cache and will be served as "
                "closed once its period elapses")

    async def test_a_cache_HIT_serves_the_same_closed_set(self):
        """A hit is where the defect fired, so the hit is driven."""
        host, venue = _Host(), _Venue(_rows("1h", last_bar_age_ms=10 * 60_000))
        first = await host._cached_ohlcv(venue, "ETH/USDT", "1h", limit=5, ttl=3600)
        second = await host._cached_ohlcv(venue, "ETH/USDT", "1h", limit=5, ttl=3600)
        assert venue.asked == 1, "the second read was not a cache hit"
        assert second == first
        assert all(row[5] != PARTIAL_VOLUME for row in second)

    async def test_a_feed_that_already_excludes_the_forming_bar_is_left_intact(self):
        """The helper's own stated property, through the cache: nothing is
        dropped from a set whose last bar really has closed."""
        tf_ms = timeframe_to_ms("1h")
        rows = _rows("1h", last_bar_age_ms=tf_ms + 10 * 60_000)
        host, venue = _Host(), _Venue(rows)
        got = await host._cached_ohlcv(venue, "SOL/USDT", "1h", limit=5, ttl=600)
        assert len(got) == len(rows)

    async def test_the_read_is_still_recorded_and_a_raise_still_propagates(self):
        """Moving the drop must not move the health recording or swallow a
        venue fault -- `_record_exchange_read`'s own docstring says a swallow
        here lets the feeder stop feeding silently."""
        host = _Host()

        class _Dead:
            async def fetch_ohlcv(self, *a, **k):
                raise TimeoutError("venue timed out")

        try:
            await host._cached_ohlcv(_Dead(), "BTC/USDT", "4h", limit=5)
        except TimeoutError:
            pass
        else:
            raise AssertionError("the fetch failure was swallowed")
        assert host.reads and isinstance(host.reads[-1][2], TimeoutError)
        assert not host._ohlcv_cache, "a failed read was cached"


class TestTheDropHappensOnceAndOnlyAtTheBoundary:

    async def test_it_runs_on_a_miss_and_not_on_a_hit(self):
        """Driven by patching the borrowed method on the instance, so this
        counts REACHES rather than matching a literal."""
        host, venue = _Host(), _Venue(_rows("4h", last_bar_age_ms=60 * 60_000))
        calls: list = []

        def _counting(ohlcv, timeframe):
            calls.append(timeframe)
            return drop_forming_candle(ohlcv, timeframe)

        host._drop_forming_candle = _counting          # type: ignore[assignment]
        await host._cached_ohlcv(venue, "BTC/USDT", "4h", limit=5, ttl=3600)
        assert calls == ["4h"], f"hygiene reached {len(calls)} time(s) on a miss"
        await host._cached_ohlcv(venue, "BTC/USDT", "4h", limit=5, ttl=3600)
        assert calls == ["4h"], "hygiene ran again on a cache HIT"

    def test_no_consumer_in_the_engine_applies_it_a_second_time(self):
        """A second application cannot change the answer -- the cached set's
        last bar closed before it was stored -- so a consumer call would be a
        line no input can reach, which is a claim that there is a check.

        The rule is the SHAPE (any call to the method), and the one permitted
        caller is `_cached_ohlcv` itself.
        """
        tree = ast.parse(ENGINE_SRC)
        sites: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "_drop_forming_candle"):
                    sites.append((node.name, call.lineno))
        assert [s[0] for s in sites] == ["_cached_ohlcv"], (
            "hygiene is applied outside the cache: " + repr(sites))

    def test_the_derivation_no_longer_reasons_from_a_property_it_lacks(self):
        """`_mtf_ttl`'s docstring is load-bearing: its arithmetic is only
        sound over closed bars, and it USED to credit the consumers with the
        drop. It must name where the property actually holds."""
        doc = _mtf_ttl.__doc__ or ""
        assert "CLOSED bars only" in doc
        assert "_cached_ohlcv" in doc, (
            "the derivation names no owner for the property it depends on")
        assert "_drop_forming_candle` removes" not in doc, (
            "the docstring still credits the consumer-side drop that was the "
            "defect")


class TestTheHelperStatesItsPrecondition:
    """A helper whose answer depends on WHEN it is called must say so, or the
    next cache is the same defect in a new place."""

    def test_the_docstring_says_to_call_it_at_the_fetch(self):
        doc = drop_forming_candle.__doc__ or ""
        assert "CALL IT AT THE FETCH" in doc
        assert "carry no age" in doc or "no age" in doc
        assert "_cached_ohlcv" in doc, (
            "the one caller that had to learn this is not named")

    def test_it_records_why_its_sibling_needs_no_such_warning(self):
        """`resample_ohlcv`, twelve lines up, derives its own boundary from
        the DATA, so its answer is the same however old the rows are. That
        difference is the reason this one needs a clock at all."""
        doc = drop_forming_candle.__doc__ or ""
        assert "resample_ohlcv" in doc
        assert "DATA" in doc or "data" in doc

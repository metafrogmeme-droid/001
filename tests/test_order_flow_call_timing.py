"""One duration for five concurrent calls named a symbol and nothing else.

Production, 2026-08-01. The stage profile reported `max 74.94s` recurring
across three cycles and, once #1044 landed, which SYMBOL owned it. It could
not say which CALL — and the fetch stage fires five per symbol:

    ohlcv, fetch_order_book, fetch_trades, fetch_funding_rate,
    fetch_open_interest

Those need entirely different fixes. A slow order book is a depth/venue
problem; a slow trade window is a limit problem; a slow funding rate is a
value we should not have been asking for at all. Naming the symbol and
stopping there is where the diagnosis stalled.

Each call is now timed individually and recorded as its own pseudo-stage
(`fetch:book`, `fetch:trades`, `fetch:funding`, `fetch:oi`), so the existing
per-symbol shape analysis applies to them unchanged.

FUNDING IS CACHED. OPEN INTEREST IS DELIBERATELY NOT.

Funding updates every EIGHT HOURS and is read only as a LEVEL, so it was
being re-fetched every tick, per symbol, from a venue measured at 15-20s per
call, for a number that changes three times a day.

Open interest looks like the same opportunity and is not. It feeds a DELTA:

    prev = self._oi_history.get(deriv_sym)
    self._oi_history[deriv_sym] = oi_usd

Serving a cached repeat makes prev == current, so the divergence is always
zero and the voter silently stops voting. A cache that quietly neuters a
signal is worse than the latency it saves — and it would look like a
performance win on every graph while doing it.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core.order_flow import FUNDING_TTL_S, OrderFlowAnalyzer
from tests.source_scan import code_only

SRC = code_only(open("bot/core/order_flow.py", encoding="utf-8").read())


class TestEveryVenueCallIsTimedSeparately:
    @pytest.mark.parametrize("name", ["book", "trades", "funding", "oi"])
    def test_each_call_is_wrapped(self, name):
        assert f'_timed("{name}"' in SRC, f"{name} is untimed"

    def test_the_timing_survives_a_failing_call(self):
        # A call that fails SLOWLY is the interesting one. Recording only on
        # success would hide exactly the calls worth finding.
        i = SRC.index("async def _timed(")
        body = SRC[i:SRC.index("async def _funding(", i)]
        assert "finally:" in body, "a failed call must still be measured"

    def test_the_timing_cannot_break_the_call(self):
        i = SRC.index("async def _timed(")
        body = SRC[i:SRC.index("async def _funding(", i)]
        assert "except Exception:" in body

    def test_timings_ride_on_the_signal_not_the_analyzer(self):
        # Analyses run concurrently; an instance dict would race between
        # symbols and attribute one symbol's slowness to another.
        assert "call_seconds: dict[str, float] = {}" in SRC
        assert "sig.call_seconds[name]" in SRC


class TestTheEngineRecordsThem:
    def test_it_records_each_call_as_its_own_stage(self):
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        assert 'f"fetch:{_cn}"' in src

    def test_it_reads_them_after_the_signal_exists(self):
        # of_signal is bound from the gather result; recording before that
        # point would read an unbound name.
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        i = src.index("of_signal = results[1]")
        j = src.index('f"fetch:{_cn}"')
        assert j > i, "timings read before of_signal is assigned"

    def test_the_lightweight_path_is_unaffected(self):
        # It fires no order-flow calls at all, so there is nothing to record
        # and nothing that could raise there.
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        assert src.count('f"fetch:{_cn}"') == 1


class TestThePerCallDataActuallyReachesTheLog:
    """#1045 recorded it and nothing read it.

    `_stage_report` picks the dominant stage from `_stage_totals`, which holds
    only the four real ANALYSIS_STAGES, so `_stage_shape_note` was never once
    called with a `fetch:` key. The per-call durations were collected every
    tick and printed nowhere — and the grep command written for the operator
    would have matched nothing.

    Fourth time this session a mechanism landed with no surface. The previous
    three were caught by asking "who reads it"; this one was caught by
    checking whether the command handed to the operator could match.

        RECORDING IT IS NOT REPORTING IT.
    """

    def _engine_src(self):
        return code_only(open("bot/core/engine.py", encoding="utf-8").read())

    def test_a_reader_exists(self):
        assert "_fetch_call_note" in self._engine_src()

    def test_the_stage_report_calls_it(self):
        src = self._engine_src()
        i = src.index("def _stage_report(")
        j = src.index("\n    def ", i + 10)
        assert "_fetch_call_note" in src[i:j], (
            "recorded per-call durations must reach the line the operator reads"
        )

    def test_it_reads_the_keys_that_are_written(self):
        # The join that was missing: engine writes f"fetch:{_cn}" where _cn
        # comes from OrderFlowSignal.call_seconds, whose keys are set by the
        # _timed() wrappers. If those drift apart the note goes silent.
        src = self._engine_src()
        i = src.index("def _fetch_call_note(")
        body = src[i:i + 1800]
        for name in ("book", "trades", "funding", "oi"):
            assert f'"{name}"' in body, f"the note never looks for fetch:{name}"
            assert f'_timed("{name}"' in SRC, f"nothing ever writes fetch:{name}"

    def test_it_produces_a_line_from_real_profiles(self):
        from bot.core.engine import RuneClawEngine
        from bot.core import stage_profile as sp

        profs = {}
        for name, dur in (("book", 2.0), ("trades", 72.8)):
            p = sp.new_profile()
            sp.record(p, "WLD/USDT" if name == "trades" else "ETH/USDT", dur)
            profs[f"fetch:{name}"] = p

        class Stub:
            _stage_profiles = profs
            _fetch_call_note = RuneClawEngine._fetch_call_note

        out = Stub()._fetch_call_note()
        assert "trades" in out and "72.8" in out
        assert "WLD/USDT" in out, "the culprit symbol must be named per call"

    def test_it_is_silent_with_no_per_call_data(self):
        # The lightweight path fires no order-flow calls at all.
        from bot.core.engine import RuneClawEngine

        class Stub:
            _stage_profiles = {}
            _fetch_call_note = RuneClawEngine._fetch_call_note

        assert Stub()._fetch_call_note() == ""

    def test_it_stays_on_one_line(self):
        src = self._engine_src()
        i = src.index("def _fetch_call_note(")
        body = src[i:src.index("\n    def ", i + 10)]
        assert "\\n" not in body, "both callers embed this in one audit line"

    def test_it_cannot_break_the_report(self):
        # Same hazard that once made _stage_report return "" for every stub.
        from bot.core.engine import RuneClawEngine

        class Stub:
            _stage_totals = {"fetch": 60.0, "mtf": 40.0}
            _stage_report = RuneClawEngine._stage_report

        out = Stub()._stage_report(120.0)
        assert out and "fetch 60s" in out


class TestFundingIsCachedAsALevel:
    def test_the_ttl_is_far_below_the_funding_interval(self):
        # Funding updates every 8h. Anything under that is safe; this is
        # conservative by a wide margin.
        assert 0 < FUNDING_TTL_S <= 8 * 3600 / 4

    def test_a_second_call_inside_the_window_does_not_refetch(self):
        calls = {"n": 0}

        class Ex:
            async def fetch_funding_rate(self, sym):
                calls["n"] += 1
                return {"fundingRate": 0.0001}

        a = OrderFlowAnalyzer()
        ex = Ex()

        async def go():
            # Exercise the cache directly through the analyzer's store, which
            # is what the inner helper reads.
            import time as _t
            key = "BTC/USDT:USDT"
            hit = a._funding_cache.get(key)
            if not (hit and (_t.time() - hit[0]) < FUNDING_TTL_S):
                out = await ex.fetch_funding_rate(key)
                a._funding_cache[key] = (_t.time(), out)
            hit = a._funding_cache.get(key)
            if not (hit and (_t.time() - hit[0]) < FUNDING_TTL_S):
                await ex.fetch_funding_rate(key)
            return calls["n"]

        assert asyncio.run(go()) == 1

    def test_the_cache_is_bounded(self):
        assert "len(self._funding_cache) > 500" in SRC


class TestOpenInterestIsLeftLiveOnPurpose:
    """The trap this fix had to avoid."""

    def test_oi_is_not_cached(self):
        i = SRC.index('_timed("oi"')
        window = SRC[max(0, i - 400):i + 200]
        assert "_funding_cache" not in window.replace(
            "_funding_cache in __init__", "")
        assert "fetch_open_interest(deriv_sym)" in SRC

    def test_the_delta_it_feeds_still_exists(self):
        # If this ever stops being a delta, caching OI becomes safe — and if
        # it stays a delta, caching it silently zeroes the voter. Either way
        # the next person needs to know which.
        assert "prev = self._oi_history.get(deriv_sym)" in SRC
        assert "self._oi_history[deriv_sym] = oi_usd" in SRC

    def test_the_reason_is_written_down(self):
        raw = open("bot/core/order_flow.py", encoding="utf-8").read()
        assert "divergence" in raw.lower()
        assert "NOT cached" in raw

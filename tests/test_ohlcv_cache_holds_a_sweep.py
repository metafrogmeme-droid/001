"""A cache smaller than its working set returns NOTHING, not less.

`_cached_ohlcv` capped itself at the constant 200 entries. One sweep at
`TOP_MOVERS_COUNT=200` asks for 200 x 5 = 1,000 distinct
`symbol:timeframe:limit` keys — the primary `1h/100` plus four MTF legs — so
the cap could hold a fifth of a single pass.

That is not a 20%-effective cache. Eviction is oldest-first and the sweep is
CYCLIC, so the entries discarded are exactly the ones the next pass asks for
first: the hit rate is **0%**, at every level of universe churn. Every candle
fetch in every tick went to the exchange, and `fetch`+`mtf` are the dominant
stages of the analyze phase (engine.py's own `_stage_shape_note`: "fetch and
mtf are both _cached_ohlcv against the same rate-limited ccxt instance").

It also silently undid the work directly above it. `_mtf_ttl` gives a closed
1d candle set a six-hour TTL specifically so it is not re-fetched 480 times a
day — and that entry was evicted inside the same tick that created it.

The cap read like a memory guard from when the universe was 80 symbols;
`top_movers_count`'s own comment records the 80 -> 200 raise. The universe
grew and the cap did not, so it is DERIVED now and this file pins the
relationship.
"""

import pytest

from bot.core.engine import (
    _OHLCV_CACHE_HEADROOM,
    _OHLCV_KEYS_PER_SYMBOL,
    _ohlcv_cache_capacity,
)


def _hit_rate(cap, n_symbols, churn=0.0, sweeps=5, universe=600, seed=11):
    """Replay the real access pattern against a cap; return the last rate."""
    import random
    rng = random.Random(seed)
    cache, clock, last, rate = {}, 0, None, 0.0
    for _ in range(sweeps):
        if last is None:
            sel = list(range(n_symbols))
        else:
            keep = last[:int(n_symbols * (1 - churn))]
            pool = [x for x in range(universe) if x not in set(keep)]
            sel = keep + rng.sample(pool, n_symbols - len(keep))
        last = sel
        hits = reads = 0
        for sym in sel:
            for spec in range(_OHLCV_KEYS_PER_SYMBOL):
                reads += 1
                k = (sym, spec)
                if k in cache:
                    hits += 1
                else:
                    clock += 1
                    cache[k] = clock
                    if len(cache) > cap:
                        for old in sorted(cache, key=cache.get)[:len(cache) - cap]:
                            del cache[old]
        rate = hits / reads
    return rate


class TestTheCapacityIsDerived:
    def test_it_holds_more_than_one_full_sweep(self):
        from bot.config import CONFIG
        working_set = CONFIG.top_movers_count * _OHLCV_KEYS_PER_SYMBOL
        assert _ohlcv_cache_capacity() > working_set

    def test_the_headroom_survives_a_symbol_leaving_and_returning(self):
        # A 4h/1d leg has a TTL of hours. Sizing to exactly one sweep would
        # evict it while its symbol is briefly out of the top-N.
        assert _OHLCV_CACHE_HEADROOM > 1.0

    def test_an_operator_can_cap_it_on_a_small_box(self, monkeypatch):
        monkeypatch.setenv("OHLCV_CACHE_MAX_ENTRIES", "300")
        assert _ohlcv_cache_capacity() == 300

    def test_the_override_cannot_go_below_the_old_behaviour(self, monkeypatch):
        monkeypatch.setenv("OHLCV_CACHE_MAX_ENTRIES", "5")
        assert _ohlcv_cache_capacity() == 200

    def test_junk_in_the_override_falls_back_to_the_derived_value(self, monkeypatch):
        monkeypatch.setenv("OHLCV_CACHE_MAX_ENTRIES", "lots")
        assert _ohlcv_cache_capacity() == _ohlcv_cache_capacity()
        assert _ohlcv_cache_capacity() > 200


class TestTheOldCapReturnedNothing:
    """The property that makes this a bug rather than a tuning question."""

    @pytest.mark.parametrize("churn", [0.0, 0.15, 0.30, 0.50])
    def test_a_200_entry_cap_never_hits_at_all(self, churn):
        assert _hit_rate(200, n_symbols=200, churn=churn) == 0.0

    def test_a_cap_above_the_working_set_hits(self):
        """The robust half of the claim.

        Only churn=0 is asserted, deliberately. Under churn the improvement
        depends on HOW the top-N is modelled to turn over, and a first draft
        of this file asserted `> 0.5` at three churn levels and produced a
        non-monotonic result — 10% at 30% churn, 65% at 50% — which is a
        property of the toy churn model, not of the cache. The 0% above needs
        no model (a working set larger than the cache under FIFO returns
        nothing, always); the exact improvement is for the running bot's own
        stage profile to measure, not for a simulation to assert.
        """
        assert _hit_rate(_ohlcv_cache_capacity(), n_symbols=200, churn=0.0) == 1.0

    def test_it_is_strictly_better_than_the_old_cap_under_churn(self):
        cap = _ohlcv_cache_capacity()
        for churn in (0.0, 0.15, 0.30, 0.50):
            assert _hit_rate(cap, 200, churn) > _hit_rate(200, 200, churn)

    def test_the_old_cap_was_adequate_at_the_OLD_universe_size(self):
        # 80 symbols against 200 — still short, and still 0%. The cap was
        # never right; the raise to 200 movers made it worse.
        assert _hit_rate(200, n_symbols=80, churn=0.0) == 0.0
        # The size that DOES fit is derived, not typed: this asserted 40,
        # which fit only while the key count was believed to be 5. It is 6 —
        # `_refine_entry_mtf`'s `15m/48` was never counted — so 40 symbols
        # needed 240 keys and the old cap failed there too. A hard-coded
        # number here restates the constant instead of checking it.
        _fits = 200 // _OHLCV_KEYS_PER_SYMBOL
        assert _hit_rate(200, n_symbols=_fits, churn=0.0) == 1.0
        assert _hit_rate(200, n_symbols=_fits + 1, churn=0.0) == 0.0


def test_the_key_count_matches_what_the_engine_actually_requests():
    """6 keys/symbol: `1h/100` + the four MTF legs + refine's `15m/48`.

    This said 5 and asserted the primary call as a LITERAL, which pinned the
    miscount and broke the moment that call gained a ttl argument. The refine
    leg was missing the whole time — so the derived cap was 5/6 of the working
    set and the stated 1.5x headroom was really 1.25x. Counted from the call
    sites now, so ADDING a fetch fails this instead of silently shrinking the
    effective headroom.
    """
    import io
    import re

    from tests.source_scan import code_only
    code = code_only(io.open("bot/core/engine.py", encoding="utf-8").read())
    assert '_tf_specs = (("15m", 200), ("1h", 200), ("4h", 200), ("1d", 200))' in code

    # The primary fetch, however it is spelled and wrapped.
    assert re.search(r"_cached_ohlcv\(\s*exchange,\s*signal\.symbol,\s*timeframe,",
                     code), "the primary 1h/100 fetch is gone or renamed"
    # Refine's own leg — the one the count forgot.
    assert re.search(r'_cached_ohlcv\(\s*exchange,\s*symbol,\s*"15m",\s*limit=48',
                     code), "the refine leg is a sixth distinct cache key"

    _mtf_legs = code.count('("15m", 200)')      # the gather, one call, four keys
    assert _OHLCV_KEYS_PER_SYMBOL == 1 + 4 * _mtf_legs + 1


def test_the_constant_200_is_gone_from_the_eviction():
    import io

    from tests.source_scan import code_only
    code = code_only(io.open("bot/core/engine.py", encoding="utf-8").read())
    # Sliced def-to-next-def, not by a fixed character window: a 2200-char
    # window stopped short of the lines under test the moment the function
    # grew, and failed in the direction that looks like a code bug. CLAUDE.md
    # records the same mistake in test_daily_report_risk_is_measured.
    i = code.index("async def _cached_ohlcv")
    j = code.index("\n    def ", i)
    _k = code.find("\n    async def ", i + 20)
    if _k != -1:
        j = min(j, _k)
    block = code[i:j]
    assert "> 200" not in block
    assert "_cap = _ohlcv_cache_capacity()" in block
    # Both eviction passes must judge against the DERIVED cap. Counting
    # occurrences pinned one spelling of the body — the TTL pass was rewritten
    # to expire on each entry's own terms and dropped one mention while doing
    # strictly more work.
    assert block.count("len(self._ohlcv_cache) > _cap") == 2
    # ...and the TTL pass must use the ENTRY's ttl, not the caller's.
    assert "now - ttl * 2" not in block
    assert "v[2]" in block, "each entry carries the ttl it was stored with"

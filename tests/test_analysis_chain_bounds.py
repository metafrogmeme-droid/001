"""The worst case of a single analysis must fit inside the cap over it.

#970 bounded each analysis at ANALYSIS_TIMEOUT_SEC so one that never returns
could not park the batch and kill the tick. That stops the bleeding. It does
not answer the question underneath it: why would one analysis take 300s?

Reading the chain, it does not need to hang. A full analysis makes ~9 requests
through the scanner's keyless market-data clients, and those three clients
were the ONLY ccxt clients in the codebase at a 30s per-request cap — every
other one (exchange_credentials x8, scan_skill, cross_venue) is 15s. Worse,
the four MTF timeframe fetches were awaited ONE AFTER ANOTHER, so four socket
timeouts landed end to end:

    OHLCV + order flow   parallel   ->  1 x cap
    MTF 15m/1h/4h/1d     SEQUENTIAL ->  4 x cap      <-- the long pole
    LLM turn             bounded    ->  15s
    _refine_entry_mtf    sequential ->  1 x cap

At a 30s cap that is 6 x 30 + 15 = 195s of pure timeout inside ONE analysis,
under a 300s phase cap, with eleven concurrent peers competing for the same
rate limiter. No hang required to blow it.

So: the MTF fetches are gathered like the order-flow fetches beside them
already were, and the data clients come into line with every other client at
15s (MARKET_DATA_TIMEOUT_MS). The serial worst case drops to 3 x 15 + 15 =
60s, which fits under the 90s per-analysis cap instead of dwarfing it.

HONESTY: this is a mechanism consistent with the evidence, not a confirmed
diagnosis of the 2026-07-29 incident. #970's audit line names the symbols it
skips; whether they are many-at-once (exchange-wide, this fix) or the same one
repeatedly (that symbol's endpoints, a different fix) is what settles it.
These changes are worth making either way — a serial fetch loop next to a
parallel one, and a lone 30s outlier among 15s peers, are defects on their own
terms.
"""

import asyncio
from pathlib import Path

from bot.config import CONFIG


ENGINE = Path("bot/core/engine.py").read_text(encoding="utf-8")
SCANNER = Path("bot/core/market_scanner.py").read_text(encoding="utf-8")


# ── the MTF fetches are parallel ──────────────────────────────────────────

def _mtf_block() -> str:
    start = ENGINE.index("if not lightweight and (CONFIG.analyzer.elliott_mtf_enabled")
    return ENGINE[start:ENGINE.index("idea = await self.analyzer.analyze(", start)]


def test_the_mtf_timeframes_are_fetched_together():
    block = _mtf_block()
    assert "asyncio.gather(" in block, (
        "four independent fetches of the same symbol have no reason to be "
        "serial, and serial put four socket timeouts end to end")
    assert "for _tf, _lim in _tf_specs" in block


def test_no_await_remains_inside_a_timeframe_loop():
    """The regression guard. A single stray `await` back inside the per-tf
    loop silently restores the serial chain."""
    block = _mtf_block()
    tail = block[block.index("for (_tf, _lim), _c in zip("):]
    assert "await" not in tail, "the result loop must be pure post-processing"


def test_a_failed_timeframe_still_degrades_alone():
    """Fail-open per timeframe, exactly as the sequential loop did — gather
    without return_exceptions would let one bad timeframe raise out of the
    whole analysis."""
    block = _mtf_block()
    assert "return_exceptions=True" in block
    assert "isinstance(_c, BaseException)" in block
    assert "continue" in block


def test_all_four_timeframes_survive_the_rewrite():
    block = _mtf_block()
    for tf in ('"15m", 200', '"1h", 200', '"4h", 200', '"1d", 200'):
        assert tf in block, f"lost timeframe spec {tf}"


def test_gather_preserves_the_pairing_of_spec_to_result():
    """asyncio.gather returns results in ARGUMENT order, not completion
    order — the property the zip() depends on. Exercised, not assumed."""
    async def _run():
        async def _slow(v, d):
            await asyncio.sleep(d)
            return v
        # deliberately inverted: the first finishes last
        return await asyncio.gather(_slow("a", 0.03), _slow("b", 0.02),
                                    _slow("c", 0.01), _slow("d", 0.0))

    assert asyncio.run(_run()) == ["a", "b", "c", "d"]


def test_a_raising_leg_does_not_take_its_peers_with_it():
    async def _run():
        async def _ok(v):
            return v

        async def _bad():
            raise RuntimeError("venue said no")

        return await asyncio.gather(_ok(1), _bad(), _ok(3),
                                    return_exceptions=True)

    got = asyncio.run(_run())
    assert got[0] == 1 and got[2] == 3
    assert isinstance(got[1], BaseException)


# ── the data clients are no longer the outlier ────────────────────────────

def test_the_scanner_clients_read_the_configured_cap():
    assert '"timeout": 30000' not in SCANNER, (
        "the hardcoded 30s outlier is what made a timing-out symbol able to "
        "eat most of the phase budget")
    assert SCANNER.count('"timeout": CONFIG.market_data_timeout_ms,') == 4, (
        "spot, futures, the active-venue client and the per-venue /scan "
        "client must all use it")


def test_the_default_matches_every_other_client_in_the_codebase():
    assert CONFIG.market_data_timeout_ms == 15000


def test_the_cap_cannot_be_set_absurdly_low():
    """Below a few seconds this would start cutting requests that would have
    succeeded — a worse failure than the one it prevents."""
    src = Path("bot/config.py").read_text(encoding="utf-8")
    assert '"MARKET_DATA_TIMEOUT_MS", 15000, 2000, 60000' in src


def test_it_is_the_data_clients_only_not_the_trading_ones():
    """Order placement is not on the analysis hot path and must keep its own
    timeout — a cap tuned for a market-data GET has no business bounding a
    live order."""
    creds = Path("bot/core/exchange_credentials.py").read_text(encoding="utf-8")
    assert "market_data_timeout_ms" not in creds


# ── the arithmetic the two changes exist to satisfy ───────────────────────

def test_the_serial_worst_case_now_fits_under_the_per_analysis_cap():
    """Three serial legs at the data cap, plus the LLM's own bound.

    Before: OHLCV/OF (1) + MTF SEQUENTIAL (4) + refine (1) = 6 legs at 30s,
    plus 15s of LLM = 195s, against a 300s phase cap shared with 11 peers.
    """
    leg_s = CONFIG.market_data_timeout_ms / 1000.0
    llm_s = float(CONFIG.llm.timeout_seconds)
    worst = 3 * leg_s + llm_s
    assert worst <= CONFIG.analysis_timeout_sec, (
        f"an analysis in which every request times out ({worst:.0f}s) must "
        f"still finish inside its own cap ({CONFIG.analysis_timeout_sec}s), "
        "or the cap fires on a merely-degraded exchange instead of a hang")
    # And the old shape must NOT have fit — otherwise this test proves nothing.
    assert 6 * 30.0 + llm_s > CONFIG.analysis_timeout_sec

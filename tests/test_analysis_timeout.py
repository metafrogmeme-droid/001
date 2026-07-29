"""One hanging analysis must not take the tick with it.

Production, 2026-07-29, 02:06 UTC:

    - Tick phase timed out: analyze (exceeded its 300s, ×37)

Thirty-seven consecutive ticks died in the analyze phase, and each failure fed
the warning-rate breaker until it tripped and started REJECTING trades. The
engine was protecting the account correctly; the thing it was protecting
against was its own hung loop.

_analyze_signals_batched bounds CONCURRENCY with a semaphore and wraps each
analysis in try/except. Its docstring says "one failure never sinks the
batch". That is true of failures and false of hangs: asyncio.gather waits for
every task, and a signal whose LLM call or exchange fetch never returns is not
an exception — it simply never arrives. One parked analysis parks the batch,
the batch parks the phase, and the phase cap kills the tick.

The same shape as the migration retry loop earlier the same day: retrying, or
in this case catching, only helps if the thing can finish.

Each analysis is now bounded individually. A hang becomes what every other
failed analysis already was — this signal yields no idea — and the other
199 complete normally. It is NAMED rather than silently skipped, because a
quiet tick hiding a hanging dependency is how this stayed invisible for a day.
"""

import asyncio
import inspect
from pathlib import Path

import pytest

from bot.config import CONFIG


SRC = Path("bot/core/engine.py").read_text(encoding="utf-8")
BATCH = SRC[SRC.index("async def _analyze_signals_batched"):
            SRC.index("async def _analyze_signal(self")]


@pytest.fixture(autouse=True)
def _restore():
    cap = getattr(CONFIG, "analysis_timeout_sec", 90.0)
    yield
    object.__setattr__(CONFIG, "analysis_timeout_sec", cap)


# ── the shape ─────────────────────────────────────────────────────────────

def test_each_analysis_is_bounded_individually():
    assert "asyncio.wait_for(_coro, timeout=_cap)" in BATCH, (
        "the semaphore caps how many run AT ONCE; it does nothing about one "
        "that never returns")


def test_a_hang_is_caught_separately_from_an_exception():
    """They are different failures. The try/except already covered exceptions
    and that is exactly why the hang went unnoticed."""
    assert "except asyncio.TimeoutError:" in BATCH
    ti = BATCH.index("except asyncio.TimeoutError:")
    ei = BATCH.index("except Exception as exc:")
    assert ti < ei, "TimeoutError must be caught before the broad handler"


def test_a_skipped_analysis_yields_no_idea_rather_than_a_fake_one():
    block = BATCH[BATCH.index("except asyncio.TimeoutError:"):
                  BATCH.index("except Exception as exc:")]
    assert "return None" in block, "never fabricate an idea for a signal we could not analyse"


def test_the_skip_is_named_not_silent():
    assert "_timed_out.append" in BATCH
    assert 'action="analysis_timeout"' in BATCH
    assert "of {len(signals)}" in BATCH, "report the share, not just a count"
    assert "The tick continues" in BATCH, "state the blast radius honestly"


def test_the_cap_is_configurable_and_disablable():
    assert "_cap > 0" in BATCH, "0 must disable the cap, not mean 'instant'"
    cfg = Path("bot/config.py").read_text(encoding="utf-8")
    assert '_env_float_bounded(\n        "ANALYSIS_TIMEOUT_SEC", 90.0, 0.0, 600.0)' in cfg


def test_the_default_is_generous_enough_for_real_work():
    # Several OHLCV fetches, order flow, MTF and an LLM turn. The cap exists
    # to bound the pathological case, not the merely slow one.
    assert CONFIG.analysis_timeout_sec >= 60.0
    # …and well under the 300s phase cap it exists to keep from firing.
    assert CONFIG.analysis_timeout_sec < 300.0


def test_it_is_still_gathered_so_one_slow_signal_does_not_serialise_the_rest():
    assert "asyncio.gather(*[_one(s) for s in signals])" in BATCH


# ── the behaviour ─────────────────────────────────────────────────────────

def test_a_hanging_task_is_bounded_while_its_peers_complete():
    """The property that matters, exercised directly: one task that never
    returns must not prevent the others from finishing."""
    async def _run():
        sem = asyncio.Semaphore(4)
        cap = 0.2
        done, timed_out = [], []

        async def _one(name, delay):
            async with sem:
                try:
                    return await asyncio.wait_for(asyncio.sleep(delay), timeout=cap)
                except asyncio.TimeoutError:
                    timed_out.append(name)
                    return None
                finally:
                    done.append(name)

        # one hangs far beyond the cap; the rest are quick
        results = await asyncio.gather(*[
            _one("HANGS", 30.0), _one("a", 0.01), _one("b", 0.01), _one("c", 0.01),
        ])
        return results, done, timed_out

    results, done, timed_out = asyncio.run(asyncio.wait_for(_run(), timeout=10))
    assert timed_out == ["HANGS"]
    assert len(done) == 4, "every peer completed despite the hang"
    assert results[0] is None, "the hung task yields None, like any other failure"
    assert all(r is None for r in results[1:]), "asyncio.sleep returns None on success"
    assert len(results) == 4, "order is preserved, one entry per signal"


def test_the_batch_helper_is_async_and_takes_signals():
    from bot.core.engine import RuneClawEngine
    fn = RuneClawEngine._analyze_signals_batched
    assert inspect.iscoroutinefunction(fn)
    params = list(inspect.signature(fn).parameters)
    assert params[:2] == ["self", "signals"]

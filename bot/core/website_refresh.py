"""The engine keeps the website's scan, signal stream and Deep Scan fresh.

Before this, three website surfaces moved only when a person asked:

- the setup cards and the signal stream: filled by /scan (Telegram) and the
  chat's scan, never by the engine's own cycle, which pushes a summary and
  no cards. On 18 October the heartbeat was minutes old while the last
  cards and the last signals were two and a half hours old.
- the Deep Scan page: filled by /deepscan or "deep scan" in AI Chat. Nothing
  ran one on its own, and the page waited for a run nothing scheduled.

The refresher runs the same two reads those doors run, on a cadence, from
the bot process only (`RuneClawEngine.run` starts it, `stop` ends it). It
changes no trading state: the scan read is the rule engine's (no LLM), its
push is the website payload and the signal rows /scan already publishes, and
the deep scan measures patterns and decides no direction.

Cadence, in minutes, from the environment; 0 turns a refresh off:

- ``WEBSITE_AUTO_SCAN_MIN`` (default 15): the setup cards and signal rows.
- ``WEBSITE_AUTO_DEEPSCAN_MIN`` (default 180): the Deep Scan block, inside the
  six hours the website keeps one (`DEEPSCAN_TTL_MS` in app/routes/sync.js).

Each run is recorded on the engine (`_website_refresh`): when it ran and what
it did, ``ok`` with a count, ``empty`` (nothing read, nothing pushed) or
``failed`` with the exception's class. A refresh that fails is said, never
taken for a fresh page.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

SCAN_ENV = "WEBSITE_AUTO_SCAN_MIN"
DEEPSCAN_ENV = "WEBSITE_AUTO_DEEPSCAN_MIN"
SCAN_DEFAULT_MIN = 15.0
DEEPSCAN_DEFAULT_MIN = 180.0
#: The first runs wait for startup (reconcile, the first tick) to settle.
FIRST_SCAN_DELAY_S = 90.0
FIRST_DEEPSCAN_DELAY_S = 300.0
#: How often the loop looks at its clock. Not a cadence: a floor on lateness.
POLL_S = 30.0


def interval_s(key: str, default_min: float, env: Optional[dict] = None) -> Optional[float]:
    """The cadence for ``key`` in seconds, or None when it is turned off.

    Unset or unreadable is the default, never off: a typo must not quietly
    stop the website moving. ``0`` (or below) is off, said in so many words.
    """
    e = env if env is not None else os.environ
    raw = str(e.get(key, "") or "").strip()
    if not raw:
        return default_min * 60.0
    try:
        minutes = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number of minutes; using %.0f", key, raw, default_min)
        return default_min * 60.0
    if minutes != minutes:  # NaN
        return default_min * 60.0
    if minutes <= 0:
        return None
    return minutes * 60.0


async def _scan(engine) -> Optional[int]:
    from bot.skills.scan_skill import website_scan
    return await website_scan(engine)


async def _deepscan(engine) -> Optional[int]:
    from bot.config import CONFIG
    from bot.skills.skill_registry import DeepScanSkill
    await asyncio.wait_for(DeepScanSkill().execute(engine, timeframe="4h"),
                           timeout=float(CONFIG.deepscan_timeout_sec))
    hits = getattr(engine, "_last_deepscan_hits", None)
    return len(hits) if isinstance(hits, list) else None


async def run_once(engine, name: str, job: Callable[[Any], Awaitable[Optional[int]]],
                   clock: Callable[[], float] = time.time) -> dict:
    """Run one refresh and record what it did on the engine."""
    try:
        count = await job(engine)
        rec = {"at": clock(), "outcome": "empty" if count is None else "ok", "count": count}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a failed refresh is recorded, never raised
        rec = {"at": clock(), "outcome": "failed", "error": type(exc).__name__}
        logger.warning("website %s refresh failed: %s", name, type(exc).__name__)
    state = getattr(engine, "_website_refresh", None)
    if not isinstance(state, dict):
        state = {}
        try:
            engine._website_refresh = state
        except Exception:
            pass
    state[name] = rec
    return rec


async def refresh_loop(engine, env: Optional[dict] = None,
                       sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                       monotonic: Callable[[], float] = time.monotonic,
                       jobs: Optional[dict] = None) -> None:
    """Run each enabled refresh on its cadence until cancelled.

    One run at a time, scan before deep scan when both are due, so the two
    never compete for the exchange with each other.
    """
    jobs = jobs or {"scan": _scan, "deepscan": _deepscan}
    # name -> [every_s, due_monotonic]
    plan: dict[str, list[float]] = {}
    for name, key, default, first in (("scan", SCAN_ENV, SCAN_DEFAULT_MIN, FIRST_SCAN_DELAY_S),
                                      ("deepscan", DEEPSCAN_ENV, DEEPSCAN_DEFAULT_MIN,
                                       FIRST_DEEPSCAN_DELAY_S)):
        every = interval_s(key, default, env)
        if every is None:
            logger.info("website %s refresh is off (%s=0)", name, key)
            continue
        plan[name] = [every, monotonic() + first]
    if not plan:
        return
    while True:
        now = monotonic()
        for name, slot in plan.items():
            if now >= slot[1]:
                await run_once(engine, name, jobs[name])
                slot[1] = monotonic() + slot[0]
        await sleep(POLL_S)

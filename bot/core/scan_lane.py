"""Run a scan batch off the loop that serves the website gateway.

``bot.main`` starts the engine and the aiohttp gateway on one event loop.
A batch that spends minutes on that loop is the loop: the gateway cannot
answer, the market socket cannot pong, and a heartbeat stamped only when
the batch returns reads as stale the whole time the scan is working.

The seam is the one this process already uses for blocking work — a
thread, with a queue of jobs and a loop of its own (``asyncio.to_thread``
and ``website_sync``'s background threads are the same idea). It is not a
second bot. The job is the scan the tick already runs; the thread only
changes where it runs, and the result comes back to the caller. A
cancellation on the caller cancels the job. Nothing is dropped on the floor.

Exchange clients are loop-affine. The scanner keeps one client per loop
(``MarketScanner._client_on_this_loop``), so the worker's session is not
the gateway's and a website read is not queued behind the batch's throttle.
"""
from __future__ import annotations

import asyncio
import math
import threading
from typing import Any, Awaitable, Callable, Optional

#: How often a batch that is still working stamps the heartbeat. Well under
#: the 15-minute window the status page treats as fresh, so a scan that is
#: in progress does not age out between stamps.
SCAN_HEARTBEAT_CADENCE_S = 20.0

#: Same window the website's engine_scan line uses (15 minutes). A stamp
#: older than this is a scan that has stopped. A missing stamp is not a
#: fresh one.
SCAN_HEARTBEAT_FRESH_S = 15 * 60.0


def scan_heartbeat_state(stamped_at: object, now: float, fresh_s: float) -> str:
    """``fresh`` / ``stale`` / ``unknown``.

    A missing, unreadable or non-finite stamp is ``unknown``. That is not
    "just scanned": the caller has no reading, and unknown must not be
    rendered as a recent success. A stamp inside the window is a scan that
    is still checking in. A stamp older than the window is a scan that has
    stopped.
    """
    if isinstance(stamped_at, bool) or not isinstance(stamped_at, (int, float)):
        return "unknown"
    stamp = float(stamped_at)
    if not math.isfinite(stamp) or not math.isfinite(now) or not math.isfinite(fresh_s):
        return "unknown"
    if fresh_s <= 0:
        return "unknown"
    age = now - stamp
    if not math.isfinite(age) or age < 0:
        return "unknown"
    return "fresh" if age <= fresh_s else "stale"


def batch_blocks_a_read(engine: object) -> bool:
    """True when a scan batch is in progress or the scan lock is held.

    ``locked()`` does not wait. A read that called ``async with`` here would
    sit until the batch released the lock, which is the request the website
    then cancels.
    """
    if getattr(engine, "_scan_batch_running", False):
        return True
    if scan_lane.busy():
        return True
    lock = getattr(engine, "_scan_lock", None)
    locked = getattr(lock, "locked", None)
    return bool(callable(locked) and locked())


def gateway_scan_read(engine: object) -> Optional[dict]:
    """A website read of the scan card while a batch is in the way.

    Returns ``None`` when nothing is in the way: the caller may scan.
    Otherwise it does not take the scan lock and does not touch an exchange.
    A card already on file is that card. Nothing on file is ``not_ready``,
    which is not an empty market.
    """
    if not batch_blocks_a_read(engine):
        return None
    cache = getattr(engine, "_gateway_scan_cache", None)
    html: Optional[str] = None
    if isinstance(cache, dict):
        raw = cache.get("reply_html")
        if isinstance(raw, str) and raw.strip():
            html = raw
    if html is not None:
        return {"ready": True, "reason": "cached", "reply_html": html}
    return {
        "ready": False,
        "reason": "not_ready",
        "reply_html": (
            "A scan is still running and nothing is on file yet. "
            "This is not an empty market."
        ),
    }


class ScanLane:
    """One worker thread, one loop, a queue of scan jobs."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._jobs = 0
        self._jobs_lock = threading.Lock()

    def busy(self) -> bool:
        with self._jobs_lock:
            return self._jobs > 0

    def _start_thread(self) -> None:
        """Start the worker if it is not already running. Safe to call twice."""
        with self._jobs_lock:
            thread = self._thread
            loop = self._loop
            if thread is not None and thread.is_alive() and loop is not None:
                return
            self._ready.clear()

            def _main() -> None:
                worker = asyncio.new_event_loop()
                asyncio.set_event_loop(worker)
                self._loop = worker
                # Set once the loop is actually running, so the first job is
                # not scheduled against a loop that has not started.
                worker.call_soon(self._ready.set)
                worker.run_forever()

            self._thread = threading.Thread(
                target=_main, name="runeclaw-scan-lane", daemon=True)
            self._thread.start()

    async def _ensure(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        thread = self._thread
        if thread is not None and thread.is_alive() and loop is not None and loop.is_running():
            return loop
        # The wait is off this loop. A blocking wait here would be the
        # gateway stall the lane exists to avoid, on the first scan.
        await asyncio.to_thread(self._start_thread)
        ready = await asyncio.to_thread(self._ready.wait, 10)
        if not ready:
            raise RuntimeError("scan lane did not start")
        loop = self._loop
        if loop is None:
            raise RuntimeError("scan lane has no loop")
        return loop

    async def run(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``factory()`` on the worker loop and return its result.

        ``factory`` is called on the worker, so the coroutine and any
        exchange client it opens belong to that loop. Cancelling the caller
        cancels the job.
        """
        loop = await self._ensure()
        caller = asyncio.get_running_loop()
        done: asyncio.Future = caller.create_future()
        box: list[asyncio.Task] = []

        async def _wrap() -> None:
            try:
                result = await factory()
            except BaseException as exc:
                # Bound into the default: the name from `except ... as` is
                # cleared when the clause ends, and this callback runs later.
                err = exc

                def _fail(err: BaseException = err) -> None:
                    if not done.done():
                        done.set_exception(err)
                caller.call_soon_threadsafe(_fail)
            else:
                def _ok() -> None:
                    if not done.done():
                        done.set_result(result)
                caller.call_soon_threadsafe(_ok)

        def _start() -> None:
            box.append(asyncio.ensure_future(_wrap(), loop=loop))

        with self._jobs_lock:
            self._jobs += 1
        try:
            loop.call_soon_threadsafe(_start)
            try:
                return await done
            except asyncio.CancelledError:
                def _cancel() -> None:
                    for task in box:
                        task.cancel()
                if loop.is_running():
                    loop.call_soon_threadsafe(_cancel)
                raise
        finally:
            with self._jobs_lock:
                self._jobs = max(0, self._jobs - 1)

    async def close_scanner(self, scanner: object) -> None:
        """Close exchange clients the worker opened. No-op if it never started."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        close = getattr(scanner, "close_this_loop", None)
        if not callable(close):
            close = getattr(scanner, "close", None)
        if not callable(close):
            return
        await self.run(close)


scan_lane = ScanLane()

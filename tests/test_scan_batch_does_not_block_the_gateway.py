"""A long scan must not be the loop that serves the website gateway.

The engine and the aiohttp gateway share one event loop (bot/main.py). A
batch that blocks that loop cancels gateway reads and lets the market
socket's keepalive time out, and a heartbeat stamped only when the batch
finishes reads as stale the whole time the scan is still working.

Driven here: a batch that sleeps on the scan lane does not delay a gateway
read; the heartbeat advances during the batch and goes stale after it
stops; a missing stamp is unknown; a gateway read does not wait on the
scan lock.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from aiohttp import web

from bot.core.scan_lane import (
    gateway_scan_read,
    scan_heartbeat_state,
    scan_lane,
)


def test_a_missing_heartbeat_is_unknown_not_just_scanned():
    assert scan_heartbeat_state(None, now=100.0, fresh_s=10.0) == "unknown"
    assert scan_heartbeat_state("not a time", now=100.0, fresh_s=10.0) == "unknown"
    assert scan_heartbeat_state(float("nan"), now=100.0, fresh_s=10.0) == "unknown"
    assert scan_heartbeat_state(True, now=100.0, fresh_s=10.0) == "unknown"


def test_a_recent_stamp_is_fresh_and_an_old_one_is_stale():
    assert scan_heartbeat_state(95.0, now=100.0, fresh_s=10.0) == "fresh"
    assert scan_heartbeat_state(80.0, now=100.0, fresh_s=10.0) == "stale"
    # The window boundary is still fresh. One step past it is stale.
    assert scan_heartbeat_state(90.0, now=100.0, fresh_s=10.0) == "fresh"
    assert scan_heartbeat_state(89.9, now=100.0, fresh_s=10.0) == "stale"


@pytest.mark.asyncio
async def test_a_sleeping_scan_batch_does_not_block_a_gateway_read():
    """The batch blocks its own thread. The gateway loop keeps answering."""
    async def handle(_request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/ping", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async def slow_batch():
        # Blocking sleep, on purpose: an await would yield even on the
        # gateway loop and would not show the stall this lane exists to end.
        time.sleep(0.6)
        return "done"

    batch = asyncio.create_task(scan_lane.run(slow_batch))
    await asyncio.sleep(0.05)
    from aiohttp import ClientSession
    started = time.monotonic()
    async with ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/ping") as resp:
            assert resp.status == 200
            body = await resp.json()
    elapsed = time.monotonic() - started
    assert body == {"ok": True}
    assert elapsed < 0.4, (
        f"gateway read took {elapsed:.2f}s while a scan batch was sleeping")
    assert await batch == "done"
    await runner.cleanup()


@pytest.mark.asyncio
async def test_heartbeat_advances_during_the_batch_and_stales_after_it_stops():
    from bot.core.engine import RuneClawEngine

    class _Host:
        _stamp_scan_heartbeat = RuneClawEngine._stamp_scan_heartbeat
        _scan_heartbeat_while = RuneClawEngine._scan_heartbeat_while
        _scan_heartbeat_mono = None
        _scan_heartbeat_cadence_s = 0.05
        _scan_batch_running = True

    engine = _Host()
    stop = asyncio.Event()

    async def slow_batch():
        await asyncio.sleep(0.35)
        return "done"

    batch = asyncio.create_task(scan_lane.run(slow_batch))
    beat = asyncio.create_task(RuneClawEngine._scan_heartbeat_while(engine, stop))
    await asyncio.sleep(0.12)
    mid = engine._scan_heartbeat_mono
    assert mid is not None
    assert scan_heartbeat_state(mid, time.monotonic(), fresh_s=0.2) == "fresh"
    await asyncio.sleep(0.08)
    assert engine._scan_heartbeat_mono > mid, "the heartbeat did not advance during the batch"
    assert await batch == "done"
    stop.set()
    await beat
    last = engine._scan_heartbeat_mono
    await asyncio.sleep(0.25)
    assert engine._scan_heartbeat_mono == last, "a stopped scan kept stamping"
    assert scan_heartbeat_state(last, time.monotonic(), fresh_s=0.2) == "stale"


@pytest.mark.asyncio
async def test_a_gateway_read_does_not_wait_on_the_scan_lock():
    engine = SimpleNamespace(
        _scan_batch_running=False,
        _gateway_scan_cache={"reply_html": "Scan on file — BTC/USDT"},
    )
    engine._scan_lock = asyncio.Lock()
    await engine._scan_lock.acquire()
    started = time.monotonic()
    held = gateway_scan_read(engine)
    elapsed = time.monotonic() - started
    assert elapsed < 0.1, f"the read waited {elapsed:.2f}s on the scan lock"
    assert held is not None
    assert held["ready"] is True
    assert held["reason"] == "cached"
    assert "BTC/USDT" in held["reply_html"]

    engine._gateway_scan_cache = None
    missing = gateway_scan_read(engine)
    assert missing is not None
    assert missing["ready"] is False
    assert missing["reason"] == "not_ready"
    assert "not an empty market" in missing["reply_html"]
    engine._scan_lock.release()

    # Idle, nothing on file: the caller may scan. This is not "not ready".
    assert gateway_scan_read(engine) is None


@pytest.mark.asyncio
async def test_a_running_batch_returns_the_cache_without_the_lock():
    engine = SimpleNamespace(
        _scan_batch_running=True,
        _scan_lock=asyncio.Lock(),
        _gateway_scan_cache={"reply_html": "Scan on file — ETH/USDT"},
    )
    held = gateway_scan_read(engine)
    assert held is not None and held["reason"] == "cached"
    assert not engine._scan_lock.locked()

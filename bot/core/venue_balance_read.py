"""One venue balance read per caller, shared by every surface that asks.

A connected trader's balance is one read-only ``fetch_balance`` per venue
(`exchange_credentials.balance_snapshot`). Three surfaces ask for it: the
website's net-worth card and its funds-by-venue card (both through the
gateway), and Telegram's ``/networth``. A dashboard that opens both cards, a
reload, or a second tab each made their own venue call, so one person's page
views spent their venue's rate budget, and with registration open that is
many people's page views spending one bot's sockets.

So the read is shared:

* a reading younger than ``TTL_S`` is answered from memory, with the time
  the venue answered (``read_at``), never the time it was served;
* a read already in flight is joined, not repeated;
* only a reading the venue answered (``ok``) is kept. A failed or timed-out
  read is never served again from memory: the next ask reads again, so an
  outage is not remembered past its end and a recovery is not hidden behind
  it.

IDENTITY DECIDES WHOSE BOOK. The key is the caller, the venue, the
environment the venue trades in, and a salted digest of the credentials
themselves. Two callers never share a reading, whatever their keys; a rotated
key or a switch between demo and live is a new reading at once. The digest is
salted per process and never leaves it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional

#: How long a reading the venue answered is served from memory. Short: a
#: balance is the reader's own money, and the cards carry `read_at`.
TTL_S = 15.0
#: Readings held at most; the oldest is dropped first.
MAX_READINGS = 2048
#: The venue's own budget for one read (the 25 s the net-worth chain is sized
#: around, `bot/core/networth_reading.py`).
DEFAULT_TIMEOUT_S = 25.0

_SALT = os.urandom(32)
_now = time.monotonic           # patched by the tests to pin the clock
_readings: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_inflight: dict[tuple, "asyncio.Future[dict]"] = {}


def _digest(fields: dict) -> str:
    blob = json.dumps(fields or {}, sort_keys=True, default=str).encode("utf-8")
    return hmac.new(_SALT, blob, hashlib.sha256).hexdigest()


def _sandbox(venue: str) -> Optional[bool]:
    try:
        from bot.core.exchange_credentials import venue_sandbox
        return bool(venue_sandbox(venue))
    except Exception:  # noqa: BLE001
        return None


def _key(user_id: Any, venue: str, fields: dict, sandbox: Optional[bool]) -> tuple:
    return (str(user_id), str(venue).lower().strip(), sandbox, _digest(fields))


def _fresh(key: tuple) -> Optional[dict]:
    held = _readings.get(key)
    if held is None:
        return None
    at, reading = held
    if _now() - at >= TTL_S:
        _readings.pop(key, None)
        return None
    _readings.move_to_end(key)
    return dict(reading)


def _keep(key: tuple, reading: dict) -> None:
    _readings[key] = (_now(), dict(reading))
    _readings.move_to_end(key)
    while len(_readings) > MAX_READINGS:
        _readings.popitem(last=False)


async def _read(venue: str, fields: dict, timeout: float) -> dict:
    # The snapshot reads the venue's environment itself, as it always has;
    # the key carries the same reading so a demo/live switch is a new key.
    from bot.core.exchange_credentials import balance_snapshot
    try:
        snap = await asyncio.wait_for(balance_snapshot(venue, fields), timeout=timeout)
    except asyncio.TimeoutError:
        snap = {"ok": False, "venue": venue, "equity_usd": None,
                "detail": "venue timeout"}
    reading = dict(snap)
    reading["read_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return reading


async def read_balance(user_id: Any, venue: str, fields: dict, *,
                       timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """``balance_snapshot`` for one caller's venue, shared as above.

    The answer is the snapshot's own shape plus ``read_at``, the moment the
    venue answered. A reading served from memory carries the ``read_at`` of
    the read it came from.
    """
    key = _key(user_id, venue, fields, _sandbox(venue))
    held = _fresh(key)
    if held is not None:
        return held
    fut = _inflight.get(key)
    if fut is not None and fut.get_loop() is not asyncio.get_running_loop():
        fut = None                  # another loop's read; this loop reads its own
    if fut is None:
        task = asyncio.ensure_future(_read(venue, fields, timeout))
        _inflight[key] = task

        def _settle(t: "asyncio.Future[dict]", key: tuple = key) -> None:
            if _inflight.get(key) is t:
                _inflight.pop(key, None)
            if not t.cancelled() and t.exception() is None and t.result().get("ok") is True:
                _keep(key, t.result())

        task.add_done_callback(_settle)
        fut = task
    # Shielded: a caller that gives up (its own budget, a closed page) does
    # not cancel the read the others are waiting on.
    return dict(await asyncio.shield(fut))


def _reset() -> None:
    """Test seam: hold nothing."""
    _readings.clear()
    _inflight.clear()

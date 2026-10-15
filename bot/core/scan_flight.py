"""Many people, one scan: identical on-demand reads share one run.

THE SHAPE BEFORE. No on-demand scan shared anything with another caller. The
Telegram limiter is per person, so twenty people sending /scan in the same
second passed it twenty times and ran twenty full market sweeps: forty
`fetch_tickers` through the same two clients, twenty appends to every symbol's
volume history (a twenty-sample window, so one burst replaced the baseline the
spike detector reads), and twenty cards that each fetched eighteen candle
series through the operator's authenticated TRADING client, 360 requests
queued in front of whatever order that client had to place next. /deepscan
and /fullscan did the same at 115 and 67 symbols a call. Only the per-strategy
boards (`strategy_scan`) shared a result.

THE RULE NOW. A read that is the same for everyone who asks (the market
sweep, a deep scan of one timeframe, the full-scan market read) runs once per
key at a time:

* a caller who arrives while it runs JOINS it and gets the same result;
* a caller who arrives within `ttl_s` of it finishing gets that result, with
  its age, and the card says how old it is;
* a failed read is never kept: the next caller reads again;
* at most `max_concurrent` distinct reads run at once, so a burst of
  different scans queues instead of stacking on the venue.

What a caller adds on top (their book, their entry gate, their buttons) is
still computed per caller, after the shared read. Nothing here decides who
may scan; the doors still do.

A shared run is a task, not the first caller's coroutine, so a caller who
gives up (a timeout, a closed chat) does not cancel it for the others.
Entries are per event loop: a task belongs to the loop that made it, and the
bot runs scans on more than one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional


@dataclass(frozen=True)
class Shared:
    """One caller's answer from a flight.

    ``how`` is "ran" (this caller started the read), "joined" (it was already
    running) or "cached" (it had finished ``age_s`` seconds ago).
    """
    value: Any
    how: str
    age_s: float

    @property
    def reused(self) -> bool:
        return self.how != "ran"


def shared_note(shared: Optional[Shared], ttl_s: float) -> str:
    """The line a card adds when its result was shared, or "" when it was not.
    A reused result says how old it is, because a scan is a reading of a
    moment and the moment is part of the reading."""
    if shared is None or not shared.reused:
        return ""
    if shared.how == "joined":
        return ("♻️ Joined a scan that was already running, so you get the "
                "same read as the person who started it.")
    return (f"♻️ From a scan {int(round(shared.age_s))}s old: identical scans "
            f"are shared for {int(ttl_s)}s so a crowd does not run one each.")


class ScanFlights:
    """Single-flight with a short result cache and a cap on distinct runs."""

    def __init__(self, max_concurrent: int = 3, max_entries: int = 128,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max_concurrent = max(1, int(max_concurrent))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._running: dict[tuple, asyncio.Task] = {}
        self._done: dict[tuple, tuple[float, Any]] = {}
        self._gates: dict[int, asyncio.Semaphore] = {}
        self.started = 0        # reads actually run, for tests and /status

    def _gate(self, loop_id: int) -> asyncio.Semaphore:
        g = self._gates.get(loop_id)
        if g is None:
            g = self._gates[loop_id] = asyncio.Semaphore(self._max_concurrent)
        return g

    def _remember(self, k: tuple, value: Any) -> None:
        self._done[k] = (self._clock(), value)
        while len(self._done) > self._max_entries:
            self._done.pop(next(iter(self._done)))

    def forget(self, key: Any = None) -> None:
        """Drop the cached result for ``key`` on every loop, or everything."""
        if key is None:
            self._done.clear()
            return
        for k in [k for k in self._done if k[1] == key]:
            self._done.pop(k, None)

    async def run(self, key: Any, factory: Callable[[], Awaitable[Any]], *,
                  ttl_s: float,
                  keep: Optional[Callable[[Any], bool]] = None) -> Shared:
        """The value for ``key``: cached, joined, or read now by ``factory``.

        ``keep(value)`` says whether a finished value may be served to the
        next caller (default: yes). A read that raised is never kept, and
        every caller waiting on it gets the exception.
        """
        loop = asyncio.get_running_loop()
        k = (id(loop), key)
        hit = self._done.get(k)
        if hit is not None:
            age = self._clock() - hit[0]
            if age <= ttl_s:
                return Shared(hit[1], "cached", max(0.0, age))
            self._done.pop(k, None)
        task = self._running.get(k)
        if task is not None and not task.done():
            value = await asyncio.shield(task)
            return Shared(value, "joined", 0.0)

        gate = self._gate(id(loop))

        async def _read() -> Any:
            async with gate:
                self.started += 1
                return await factory()

        task = loop.create_task(_read())
        self._running[k] = task

        def _settle(t: asyncio.Task) -> None:
            if self._running.get(k) is t:
                self._running.pop(k, None)
            if t.cancelled() or t.exception() is not None:
                return
            value = t.result()
            if keep is None or keep(value):
                self._remember(k, value)

        task.add_done_callback(_settle)
        value = await asyncio.shield(task)
        return Shared(value, "ran", 0.0)


class RoomScans:
    """One scan card per group chat at a time.

    Sharing the read is not enough in a group: five members who send /scan
    together all get the same result, and the chat then shows the same card
    five times. Here the first member's scan posts the card; a member who
    asks while it is running is told it is coming, and one who asks within
    `ttl_s` of it being posted is pointed at it. Private chats are not
    collapsed: each person's card is in their own chat.

    A claim that was never settled (a handler that died) goes stale after
    `stale_s`, so a chat cannot be locked out of scanning by a lost task.
    """

    def __init__(self, stale_s: float = 180.0, max_rooms: int = 1024,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._stale_s = float(stale_s)
        self._max_rooms = int(max_rooms)
        self._clock = clock
        self._state: dict[tuple, tuple[str, float]] = {}

    def claim(self, chat_id: Any, kind: str, ttl_s: float) -> tuple[str, float]:
        """``("run", 0)`` when this member's scan should run and post;
        ``("running", seconds)`` when another member's is under way;
        ``("posted", age)`` when one was posted in this chat ``age`` seconds
        ago, inside ``ttl_s``."""
        k = (str(chat_id), kind)
        now = self._clock()
        cur = self._state.get(k)
        if cur is not None:
            state, at = cur
            age = now - at
            if state == "running" and age < self._stale_s:
                return "running", age
            if state == "posted" and age <= ttl_s:
                return "posted", age
        self._state[k] = ("running", now)
        while len(self._state) > self._max_rooms:
            self._state.pop(next(iter(self._state)))
        return "run", 0.0

    def posted(self, chat_id: Any, kind: str) -> None:
        self._state[(str(chat_id), kind)] = ("posted", self._clock())

    def release(self, chat_id: Any, kind: str) -> None:
        """A claimed scan that posted nothing: the next member may run."""
        k = (str(chat_id), kind)
        if self._state.get(k, ("", 0.0))[0] == "running":
            self._state.pop(k, None)


def room_sentence(state: str, seconds: float, ttl_s: float, what: str) -> str:
    """What a group member is told instead of a second card."""
    if state == "running":
        return (f"⏳ A {what} for this chat is already running; its card "
                f"follows here shortly. One card per chat, so the chat is not "
                f"flooded with copies.")
    wait = max(1, int(ttl_s - seconds) + 1)
    return (f"⬆️ The {what} posted in this chat {int(seconds)}s ago is still "
            f"current: scans are shared for {int(ttl_s)}s. Scroll up, or ask "
            f"again in {wait}s.")


#: The bot's group chats, one scan card at a time each.
ROOMS = RoomScans()

#: The bot's one set of flights. Three distinct scans at once: the market
#: sweep, a deep scan and a full scan can run side by side, and a fourth kind
#: waits its turn rather than stacking more load on the venue.
FLIGHTS = ScanFlights(max_concurrent=3)

#: How long a finished market sweep is served to the next caller. A sweep is
#: 24h change and volume per pair; forty-five seconds is inside the noise of
#: both, and long enough that a group asking together is served by one read.
MARKET_TTL_S = 45.0
#: A deep scan reads closed bars of one timeframe; ninety seconds is well
#: inside the shortest bar it reads (15m).
DEEPSCAN_TTL_S = 90.0
#: The full scan reads 4h bars.
FULLSCAN_TTL_S = 60.0

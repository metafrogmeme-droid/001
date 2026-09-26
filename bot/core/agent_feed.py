"""
RUNECLAW — Agent mind-stream feed emitter.

Collects the agent's public "what am I doing" moments — scan cycles, trade
theses, opens/closes, trailing-stop moves, proactive alerts, stance changes —
and ships them to the website in small background batches, where they are
stored and re-broadcast to connected dashboards over SSE.

Strictly fail-soft and non-blocking: ``emit()`` only appends to a bounded
in-memory queue; a lazily-started daemon thread flushes every few seconds.
A dead website, a slow network, or a bug anywhere in here must NEVER touch
the trading path — every public entry point swallows its own exceptions.

Privacy contract: the feed is PUBLIC (it powers the landing page, the
unauthenticated SSE stream, web push and an MCP tool). Only OPERATOR-account
activity may be emitted (callers guard per-user paths), and events must carry
no dollar amount of the account: no balance, equity, position size or P&L.
Prices are public market facts and may appear. A closed trade is told in
percent of its margin (`close_event`).

This paragraph used to say realized P&L in dollars was fine because "it is
already published on the public track-record page". It is not:
`app/routes/track.js` states that the public track record is percent, ratio
and count only, and indexes its equity curve to 100 so no account size
escapes. The false reason is what let every operator close publish its dollar
result to anonymous readers.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Optional

from bot.utils.leveraged_return import realized_margin_return_pct

log = logging.getLogger(__name__)

ALLOWED_TYPES = frozenset(
    {"scan", "thesis", "trade_open", "trade_close", "sl_move",
     "alert", "stance", "info"})
_SEVERITIES = frozenset({"info", "success", "warning", "critical"})

MAX_QUEUE = 200          # drop-oldest bound; the feed is best-effort telemetry
MAX_BATCH = 40           # events per POST to the website
FLUSH_INTERVAL_S = 4.0   # feels live without hammering the web app
MAX_RETRIES = 3          # re-queue a failed batch this many times before dropping
TITLE_MAX = 200
BODY_MAX = 500


def _enabled() -> bool:
    return os.getenv("AGENT_FEED_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off")


def _finite(value: object) -> Optional[float]:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def close_event(symbol: object, pnl_usd: object, margin_usd: object,
                reason: object = "") -> Optional[dict[str, Any]]:
    """The public ``trade_close`` event for an operator close, or None.

    The figure is the return on margin, net of fees
    (`realized_margin_return_pct`), which is the same reading the public close
    line prints as "on margin" (`public_text.public_close_line` reads it as
    `pnl_pct_margin_net`), so the feed and the channel state one number. The
    dollar P&L is read for its sign and never sent.

    Three outcomes:

    * P&L and margin read: "Closed BTC/USDT -20.60% on margin".
    * P&L read, margin not recorded (an adopted position the venue did not
      size): "Closed BTC/USDT" with no figure, and the body says the return
      was not recorded. A percent computed some other way would be a second
      answer, and a 0.00% would read as a measured break-even.
    * P&L not read: None. An unpriced close was never announced on the feed
      and still is not; whether it should be is a decision about the public
      page, not a wording fix.

    The severity follows the sign: a measured break-even is "info", neither a
    success nor a loss.
    """
    sym = str(symbol or "")
    pnl = _finite(pnl_usd)
    if not sym or pnl is None:
        return None
    why = str(reason or "")
    ret = realized_margin_return_pct(pnl, margin_usd)
    if ret is None:
        title = f"Closed {sym}"
        unread = "return on margin not recorded"
        body = f"Exit: {why} · {unread}" if why else unread.capitalize()
    else:
        title = f"Closed {sym} {ret:+.2f}% on margin"
        body = f"Exit: {why}" if why else ""
    severity = "success" if pnl > 0 else "warning" if pnl < 0 else "info"
    return {
        "title": title,
        "body": body,
        "symbol": sym,
        "severity": severity,
        "data": {"return_on_margin_pct": None if ret is None else round(ret, 2),
                 "reason": why},
    }


class AgentFeed:
    """Bounded queue + background flusher for public agent-feed events."""

    def __init__(self) -> None:
        self._queue: deque[dict] = deque(maxlen=MAX_QUEUE)
        self._lock = threading.Lock()
        self._flusher: threading.Thread | None = None

    def emit(self, etype: str, title: str, *, body: str = "", symbol: str = "",
             severity: str = "info", data: dict | None = None) -> None:
        """Queue one feed event. Never raises; never blocks on I/O."""
        try:
            if not _enabled() or not title:
                return
            ev = {
                "event_type": etype if etype in ALLOWED_TYPES else "info",
                "severity": severity if severity in _SEVERITIES else "info",
                "symbol": str(symbol or "")[:32],
                "title": str(title)[:TITLE_MAX],
                "body": str(body or "")[:BODY_MAX],
                "data": data if isinstance(data, dict) else {},
                "ts": datetime.now(UTC).isoformat(),
            }
            with self._lock:
                self._queue.append(ev)
            self._ensure_flusher()
        except Exception as exc:  # noqa: BLE001 — telemetry must never propagate
            log.debug("agent feed emit skipped: %s", exc)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    # ── flushing ─────────────────────────────────────────────────────

    def _ensure_flusher(self) -> None:
        if self._flusher is not None and self._flusher.is_alive():
            return
        with self._lock:
            if self._flusher is not None and self._flusher.is_alive():
                return
            t = threading.Thread(
                target=self._run, name="agent-feed-flush", daemon=True)
            self._flusher = t
        t.start()

    def _drain(self) -> list[dict]:
        with self._lock:
            batch: list[dict] = []
            while self._queue and len(batch) < MAX_BATCH:
                batch.append(self._queue.popleft())
            return batch

    def _run(self) -> None:
        while True:
            time.sleep(FLUSH_INTERVAL_S)
            try:
                self.flush_once()
            except Exception as exc:  # noqa: BLE001
                log.debug("agent feed flush error: %s", exc)

    def flush_once(self) -> int:
        """Drain up to one batch and POST it to the website.

        Returns the number of events sent (0 on empty queue or failed POST). On
        a failed POST the batch is RE-QUEUED for up to ``MAX_RETRIES`` attempts
        (the flusher sleeps ``FLUSH_INTERVAL_S`` between tries, so this naturally
        rate-limits) so a transient web outage doesn't silently lose the feed;
        events past the retry cap are dropped (telemetry, not trades). The retry
        bookkeeping (``_retries``) is internal and never sent on the wire. Split
        out for tests.
        """
        batch = self._drain()
        if not batch:
            return 0
        from bot.utils.website_sync import sync_agent_events
        wire = [{k: v for k, v in ev.items() if k != "_retries"} for ev in batch]
        if sync_agent_events(wire):
            return len(batch)
        self._requeue(batch)
        return 0

    def _requeue(self, batch: list[dict]) -> None:
        """Prepend a failed batch (in original order) for a bounded retry.
        Events that have exhausted MAX_RETRIES are dropped. The maxlen bound
        still caps memory: if the queue is saturated, appendleft drops the
        newest queued events in favour of retrying these."""
        keep: list[dict] = []
        for ev in batch:
            n = int(ev.get("_retries", 0)) + 1
            if n <= MAX_RETRIES:
                ev["_retries"] = n
                keep.append(ev)
        if not keep:
            return
        with self._lock:
            for ev in reversed(keep):
                self._queue.appendleft(ev)


FEED = AgentFeed()

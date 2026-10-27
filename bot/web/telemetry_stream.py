"""The API bridge's live telemetry stream: orderbook metrics and risk alerts.

`api_bridge.py` mounts one authenticated WebSocket (`/ws/telemetry`) over this
module. The module holds everything that is not FastAPI, so every rule below is
driven by a test with a fake socket and the endpoint itself is driven end to end
by `tests/test_telemetry_stream.py`.

WHAT IS STREAMED, AND FROM WHERE

  orderbook   The microstructure the order-flow analyzer reads for a symbol the
              client named: spread in basis points, book imbalance, the ratio
              of top-of-book depth, the aggressor ratio, the whale bias. RATIOS
              AND BASIS POINTS ONLY. The depth in dollars, the mid price and the
              traded volume are on the signal and never leave it.
  risk        Rejections and warnings from the risk audit log the BOT writes
              (`logs/risk.jsonl`): which gates a trade failed, on which asset.
              This process is not the bot and has no risk engine whose
              rejections mean anything (`api_bridge._bot_breaker_fields`), so
              the stream tails what the bot recorded. The reason text is NOT
              forwarded: it carries amounts ("equity $4,012 < ...").

THE RULES, EACH ONE A CLASS OF BUG THIS REPO HAS ALREADY HAD

  * Unreadable is never zero. A book the venue did not answer is
    `{"read": false}`, not a spread of 0 bps; an imbalance that needs depth on
    both sides is omitted when either side is empty (`book_imbalance_read`). A
    risk log that cannot be read is announced once as `source_unreadable` and
    again as `source_recovered`, never as a quiet stretch with no alerts.
  * Dollar-free. `dollar_free` drops any key that names an amount and any
    string that carries one, on every frame, as the last step before it is
    encoded. Builders already leave the amounts out; this is the net under
    them, so a future field added to a builder cannot reach a socket.
  * Every frame passes `scrub_secrets` after it is encoded, so a credential or
    a chat id that reached a string field is redacted whole-frame.
  * Auth is `hmac.compare_digest`, on bytes, so a non-ASCII candidate is a
    mismatch and not a TypeError that closes the socket with a stack trace.
    The token is the dashboard token `require_dashboard_token` checks. It is
    accepted from the `Authorization: Bearer` header or from a first
    `{"op":"auth","token":...}` message, and NEVER from the URL: uvicorn's
    access log and every proxy record a WebSocket's path with its query string,
    and the same token gates the bridge's other endpoints. A connection that
    puts one in the URL is closed, so the client stops sending it.
  * A slow client is dropped, not waited for. Each connection has its own
    bounded queue; a queue that fills closes THAT connection (1013) and the
    others never notice. A send that takes longer than `SEND_TIMEOUT_S` is the
    same fault. A client that talks too much is closed too (1008).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

from bot.utils.secret_shapes import scrub_secrets

log = logging.getLogger("runeclaw.telemetry_stream")

#: The topics a client may ask for.
TOPICS: tuple[str, ...] = ("orderbook", "risk")

#: Frames a connection may have waiting before it is dropped as a slow client.
QUEUE_SIZE = 64
#: Simultaneous stream connections. Each can hold a queue and a symbol list.
MAX_SUBSCRIBERS = 16
#: Symbols one connection may watch, and symbols the whole process will poll.
MAX_SYMBOLS_PER_CLIENT = 3
MAX_POLLED_SYMBOLS = 8
#: A socket that cannot take a frame in this long is not keeping up.
SEND_TIMEOUT_S = 5.0
#: How long a connection has to authenticate by first message.
AUTH_TIMEOUT_S = 5.0
#: What one connection may say: a size per message and a count per window.
MAX_CLIENT_MESSAGE_CHARS = 1024
CLIENT_MESSAGES_PER_WINDOW = 20
CLIENT_MESSAGE_WINDOW_S = 10.0
#: The biggest frame a socket will be handed.
MAX_FRAME_BYTES = 4096
#: Seconds between orderbook polls and between risk-log reads.
ORDERBOOK_INTERVAL_S = 5.0
ORDERBOOK_CALL_TIMEOUT_S = 15.0
RISK_LOG_INTERVAL_S = 1.0
#: The most risk-log bytes one read consumes, and alerts one read forwards.
RISK_LOG_MAX_READ_BYTES = 256 * 1024
RISK_LOG_MAX_ALERTS_PER_READ = 50
RISK_LOG_MAX_LINE_BYTES = 64 * 1024

#: WebSocket close codes this module uses.
CLOSE_POLICY = 1008
CLOSE_TRY_LATER = 1013


# ── dollar-free ──────────────────────────────────────────────────────────────

#: A KEY that names an amount. `size` and `price` are here on purpose: this
#: stream is ratios and basis points, and a field named like a quantity of
#: money is one nobody should add to it without removing the net. `balance` is
#: matched except inside `imbalance`, which is a ratio and the stream's own
#: headline field: the first draft of this pattern ate `book_imbalance`.
_DOLLAR_KEY_RE = re.compile(
    r"(usd|usdt|usdc|dollar|pnl|equity|(?<!im)balance|notional|margin|fee|profit|cost"
    r"|capital|price|amount|size|worth|nav)",
    re.IGNORECASE,
)
#: A STRING that carries an amount. A symbol such as `BTC/USDT` is not one: the
#: unit must follow a number, or the string must hold a dollar sign.
_DOLLAR_TEXT_RE = re.compile(
    r"\$|\d[\d,.]*\s?(?:usd[tc]?|dollars?)\b", re.IGNORECASE)

_DROP = object()
_MAX_DEPTH = 6


def _clean(value: Any, depth: int) -> Any:
    """``value`` without amounts, or `_DROP` when nothing of it survives."""
    if depth > _MAX_DEPTH:
        return _DROP
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # NaN and inf are not JSON; a frame holding one would be rejected by
        # the client's parser, and "not a number" is not a reading anyway.
        return value if math.isfinite(value) else _DROP
    if isinstance(value, str):
        return _DROP if _DOLLAR_TEXT_RE.search(value) else value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str) or _DOLLAR_KEY_RE.search(k):
                continue
            cleaned = _clean(v, depth + 1)
            if cleaned is not _DROP:
                out[k] = cleaned
        return out
    if isinstance(value, (list, tuple)):
        items = [_clean(v, depth + 1) for v in value]
        return [v for v in items if v is not _DROP]
    return _DROP


def dollar_free(message: dict) -> dict:
    """``message`` with every amount-named key and amount-bearing string removed.

    A dropped field is ABSENT from the result, not zeroed: absent is the
    spelling of "not published", and 0 would be a figure."""
    out = _clean(message, 0)
    return out if isinstance(out, dict) else {}


def encode_frame(message: dict) -> Optional[str]:
    """The text a socket is handed for ``message``, or None when it cannot be
    made safe: dollar-free, finite, secret-scrubbed, valid JSON, bounded.

    None means REFUSED, and the caller counts it; a frame that fails any step is
    never sent in a lesser form."""
    try:
        text = json.dumps(dollar_free(message), separators=(",", ":"),
                          sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    text = scrub_secrets(text)
    if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
        return None
    try:
        json.loads(text)
    except ValueError:
        return None
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── auth ─────────────────────────────────────────────────────────────────────


def token_matches(candidate: object, expected: str) -> bool:
    """Whether ``candidate`` is the dashboard token. Constant time, and False
    for anything that is not a non-empty string or when no token is set."""
    if not expected or not isinstance(candidate, str) or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def bearer_from_header(value: Optional[str]) -> Optional[str]:
    """The token in an ``Authorization: Bearer <token>`` header, or None."""
    if not value:
        return None
    scheme, _, rest = value.partition(" ")
    token = rest.strip()
    return token if scheme.lower() == "bearer" and token else None


# ── the hub ──────────────────────────────────────────────────────────────────


class Subscriber:
    """One connection's place in the hub: its topics, its symbols and its own
    bounded queue. A `None` in the queue is the instruction to close."""

    def __init__(self, sid: int, topics: set[str], symbols: set[str], queue_size: int) -> None:
        self.sid = sid
        self.topics = set(topics)
        self.symbols = set(symbols)
        self.queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue(maxsize=queue_size)
        self.closed_reason: Optional[str] = None


class TelemetryHub:
    """Fan-out to bounded per-connection queues. Single event loop only:
    `asyncio.Queue` is not thread-safe, and nothing here pretends otherwise."""

    def __init__(self, *, queue_size: int = QUEUE_SIZE,
                 max_subscribers: int = MAX_SUBSCRIBERS) -> None:
        self._queue_size = queue_size
        self._max = max_subscribers
        self._subs: dict[int, Subscriber] = {}
        self._next = 0
        self.delivered = 0
        self.dropped_slow = 0
        self.refused_frames = 0

    @property
    def subscribers(self) -> int:
        """How many connections are subscribed. A property and not `__len__`:
        an empty hub with a length of 0 is falsy, and `hub or TelemetryHub()`
        would quietly build a second one."""
        return len(self._subs)

    def attach(self, topics: set[str], symbols: set[str]) -> Optional[Subscriber]:
        """A new subscriber, or None when the hub is at capacity."""
        if len(self._subs) >= self._max:
            return None
        self._next += 1
        sub = Subscriber(self._next, topics, symbols, self._queue_size)
        self._subs[sub.sid] = sub
        return sub

    def detach(self, sub: Subscriber) -> None:
        self._subs.pop(sub.sid, None)

    def drop(self, sub: Subscriber, reason: str) -> None:
        """Remove ``sub`` and tell its writer to close. Whatever it had queued
        is discarded: a connection being dropped for not keeping up is not
        owed the backlog it could not take."""
        if self._subs.pop(sub.sid, None) is None:
            return
        sub.closed_reason = reason
        while True:
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        sub.queue.put_nowait(None)
        if reason in ("slow_client", "send_timeout"):
            self.dropped_slow += 1

    def _named_symbols(self) -> list[str]:
        """Every symbol any orderbook subscriber named, sorted."""
        names: set[str] = set()
        for sub in self._subs.values():
            if "orderbook" in sub.topics:
                names |= sub.symbols
        return sorted(names)

    def watched_symbols(self) -> list[str]:
        """Symbols any orderbook subscriber named, sorted, process-capped."""
        return self._named_symbols()[:MAX_POLLED_SYMBOLS]

    def unpolled_symbols(self) -> list[str]:
        """Symbols an orderbook subscriber named that `watched_symbols` cut off.
        They are said, not left silent: a client watching one would otherwise
        read the absence of frames as a quiet book."""
        return self._named_symbols()[MAX_POLLED_SYMBOLS:]

    def has_topic(self, topic: str) -> bool:
        return any(topic in s.topics for s in self._subs.values())

    def publish(self, topic: str, message: dict, *, symbol: Optional[str] = None) -> int:
        """Encode ``message`` once and queue it for every subscriber of
        ``topic`` (and, for a symbol-scoped frame, that symbol). Returns how
        many queues took it. A full queue drops its connection."""
        frame = encode_frame(message)
        if frame is None:
            self.refused_frames += 1
            return 0
        sent = 0
        for sub in list(self._subs.values()):
            if topic not in sub.topics:
                continue
            if symbol is not None and symbol not in sub.symbols:
                continue
            try:
                sub.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self.drop(sub, "slow_client")
                continue
            sent += 1
        self.delivered += sent
        return sent

    def send_to(self, sub: Subscriber, message: dict) -> bool:
        """Queue a frame for one subscriber (a reply). False when it was
        refused or the queue was full, which drops the connection."""
        frame = encode_frame(message)
        if frame is None:
            self.refused_frames += 1
            return False
        try:
            sub.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.drop(sub, "slow_client")
            return False
        return True

    def stats(self) -> dict:
        """Counters only, safe to print."""
        return {"subscribers": len(self._subs), "delivered": self.delivered,
                "dropped_slow": self.dropped_slow, "refused_frames": self.refused_frames}


# ── orderbook metrics ────────────────────────────────────────────────────────


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def orderbook_metrics(sig: Any, symbol: str) -> dict:
    """The orderbook frame for one `OrderFlowSignal`, ratios and bps only.

    Every field is present only when the signal's own component list says it
    was read. The signal's numeric fields default to 0.0, and a 0.0 spread or a
    0.0 imbalance from a book the venue never answered is the defect this
    function exists to keep off the wire."""
    from bot.core.order_flow import book_imbalance_read, whale_flow_read

    comps = [str(c) for c in (getattr(sig, "components_ok", None) or [])]
    frame: dict[str, Any] = {"type": "orderbook", "symbol": symbol, "ts": _now_iso()}
    if "book" not in comps:
        frame["read"] = False
        return frame
    frame["read"] = True
    spread = _finite(getattr(sig, "spread_bps", None))
    if spread is not None:
        frame["spread_bps"] = round(spread, 3)
    if book_imbalance_read(sig, comps):
        imbalance = _finite(getattr(sig, "book_imbalance", None))
        if imbalance is not None:
            frame["book_imbalance"] = round(imbalance, 4)
    bid_top = _finite(getattr(sig, "bid_depth_top_usd", None))
    ask_top = _finite(getattr(sig, "ask_depth_top_usd", None))
    if bid_top is not None and ask_top is not None and bid_top > 0 and ask_top > 0:
        frame["depth_ratio_top"] = round(bid_top / ask_top, 4)
    if "trades" in comps:
        aggressor = _finite(getattr(sig, "aggressor_ratio", None))
        if aggressor is not None:
            frame["aggressor_ratio"] = round(aggressor, 4)
        trend = getattr(sig, "cvd_trend", None)
        if trend in ("rising", "falling", "flat"):
            frame["cvd_trend"] = trend
        if whale_flow_read(sig, comps):
            bias = getattr(sig, "whale_bias", None)
            if bias in ("accumulation", "distribution", "neutral"):
                frame["whale_bias"] = bias
    confidence = _finite(getattr(sig, "confidence", None))
    if confidence is not None:
        frame["confidence"] = round(confidence, 3)
    frame["components"] = comps[:12]
    return frame


# ── risk alerts from the bot's risk audit log ────────────────────────────────

_ASSET_IN_MESSAGE_RE = re.compile(r"^Risk \w+ for ([A-Z0-9]{1,15}(?:/[A-Z0-9]{1,15})?) \[")
_MAX_GATES_PER_ALERT = 8
_MAX_GATE_CHARS = 48


def _gate_names(checks_failed: Any) -> list[str]:
    """The gate names in a rejection's failed checks: the text before the
    first colon, which is how the risk engine's own gate counters key them. The
    detail after the colon carries amounts and is not forwarded."""
    if not isinstance(checks_failed, list):
        return []
    names: list[str] = []
    for entry in checks_failed:
        if not isinstance(entry, str):
            continue
        name = entry.split(":", 1)[0].strip()[:_MAX_GATE_CHARS]
        if name and name not in names:
            names.append(name)
        if len(names) >= _MAX_GATES_PER_ALERT:
            break
    return names


def risk_alert_from_line(line: str) -> Optional[dict]:
    """A risk alert for one `risk.jsonl` line, or None when the line is not one.

    Two kinds qualify: a risk evaluation that REJECTED (`action=risk_check`),
    and any WARNING-or-worse line that names an action (a tripped breaker, a
    paused equity curve). The free-text `message` is read for the asset only,
    through a strict pattern, and is otherwise discarded."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    action = str(rec.get("action") or "")
    result = str(rec.get("result") or "")
    level = str(rec.get("level") or "")
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    alert: dict[str, Any]
    if action == "risk_check":
        if result != "REJECTED":
            return None
        alert = {"kind": "rejection", "gates": _gate_names(data.get("checks_failed"))}
        m = _ASSET_IN_MESSAGE_RE.match(str(rec.get("message") or ""))
        if m:
            alert["asset"] = m.group(1)
    elif action and level in ("WARNING", "ERROR", "CRITICAL"):
        alert = {"kind": "warning", "action": action[:48], "result": result[:32]}
    else:
        return None
    alert["type"] = "risk"
    alert["level"] = level or "INFO"
    source_ts = rec.get("ts")
    if isinstance(source_ts, str):
        alert["source_ts"] = source_ts[:32]
    return alert


class RiskLogTailer:
    """Reads the lines the bot appends to the risk log, from where it stood.

    It starts at the END of the file: the stream is for what happens while a
    client is connected, not a replay of history. It follows a rotation (a new
    inode, or a file shorter than the offset) from the top of the new file, and
    consumes only whole lines so a write in progress is read next time.

    ``state`` is "ok", "absent" (no file yet, which is not a fault) or
    "unreadable" (the file is there and cannot be read, which is)."""

    def __init__(self, path: "str | os.PathLike[str]",
                 max_read_bytes: int = RISK_LOG_MAX_READ_BYTES,
                 max_alerts: int = RISK_LOG_MAX_ALERTS_PER_READ) -> None:
        self._path = Path(path)
        self._max_read = max_read_bytes
        self._max_alerts = max_alerts
        self._offset: Optional[int] = None
        self._inode: Optional[int] = None
        self.state = "absent"

    def poll(self) -> list[dict]:
        """New alerts since the last call. Never raises."""
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            self.state = "absent"
            self._offset = None
            self._inode = None
            return []
        except OSError:
            self.state = "unreadable"
            return []
        try:
            if self._offset is None:
                self._offset, self._inode = st.st_size, st.st_ino
                self.state = "ok"
                return []
            if st.st_ino != self._inode or st.st_size < self._offset:
                self._offset, self._inode = 0, st.st_ino
            if st.st_size == self._offset:
                self.state = "ok"
                return []
            with open(self._path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read(self._max_read)
        except OSError:
            self.state = "unreadable"
            return []
        self.state = "ok"
        end = chunk.rfind(b"\n")
        if end < 0:
            # No whole line in a full read: one line longer than the read.
            # Skip it rather than stall on it forever.
            if len(chunk) >= self._max_read:
                self._offset += len(chunk)
            return []
        self._offset += end + 1
        alerts: list[dict] = []
        skipped = 0
        for raw in chunk[:end].split(b"\n"):
            if not raw or len(raw) > RISK_LOG_MAX_LINE_BYTES:
                continue
            alert = risk_alert_from_line(raw.decode("utf-8", "replace"))
            if alert is None:
                continue
            if len(alerts) >= self._max_alerts:
                skipped += 1
                continue
            alerts.append(alert)
        if skipped:
            # Said, not silently shortened: a burst this large is the news.
            alerts.append({"type": "risk", "kind": "overflow", "skipped": skipped})
        return alerts


# ── the feeds ────────────────────────────────────────────────────────────────

#: ``fetch(symbol)`` returns an `OrderFlowSignal`-like object, or None when the
#: venue could not be read. It may also raise; both are "unreadable".
OrderbookFetch = Callable[[str], Awaitable[Any]]


class TelemetryFeeds:
    """The two background readers behind the hub. Each runs only while someone
    is subscribed to its topic, and ends by itself afterwards, so an idle
    bridge makes no venue call and reads no log."""

    def __init__(self, hub: TelemetryHub, fetch: OrderbookFetch, tailer: RiskLogTailer, *,
                 orderbook_interval_s: float = ORDERBOOK_INTERVAL_S,
                 call_timeout_s: float = ORDERBOOK_CALL_TIMEOUT_S,
                 risk_interval_s: float = RISK_LOG_INTERVAL_S) -> None:
        self._hub = hub
        self._fetch = fetch
        self._tailer = tailer
        self._ob_interval = orderbook_interval_s
        self._call_timeout = call_timeout_s
        self._risk_interval = risk_interval_s
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_source_state: Optional[str] = None

    def ensure_running(self) -> None:
        """Start whichever feed has a subscriber and no task."""
        loop = asyncio.get_running_loop()
        for topic, runner in (("orderbook", self._run_orderbook), ("risk", self._run_risk)):
            task = self._tasks.get(topic)
            if self._hub.has_topic(topic) and (task is None or task.done()):
                self._tasks[topic] = loop.create_task(runner(), name=f"telemetry-{topic}")

    def running(self) -> dict[str, bool]:
        return {t: (k is not None and not k.done()) for t, k in self._tasks.items()}

    async def stop_all(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - a feed's death is not the caller's
                pass
        self._tasks.clear()

    async def poll_orderbooks_once(self) -> None:
        """One pass over every watched symbol. Public so a test drives it
        without a timer."""
        for symbol in self._hub.watched_symbols():
            try:
                async with asyncio.timeout(self._call_timeout):
                    sig = await self._fetch(symbol)
            except Exception as exc:  # noqa: BLE001 - class only, never the message
                log.warning("orderbook read failed for %s: %s", symbol, type(exc).__name__)
                sig = None
            if sig is None:
                frame = {"type": "orderbook", "symbol": symbol, "read": False, "ts": _now_iso()}
            else:
                try:
                    frame = orderbook_metrics(sig, symbol)
                except Exception as exc:  # noqa: BLE001
                    log.warning("orderbook frame failed for %s: %s", symbol, type(exc).__name__)
                    frame = {"type": "orderbook", "symbol": symbol, "read": False, "ts": _now_iso()}
            self._hub.publish("orderbook", frame, symbol=symbol)
        for symbol in self._hub.unpolled_symbols():
            # Over the process cap: not read, and the client is told so. It is
            # `read: false`, never a spread, and `polled: false` says why.
            self._hub.publish(
                "orderbook",
                {"type": "orderbook", "symbol": symbol, "read": False, "polled": False,
                 "reason": "over_capacity", "ts": _now_iso()},
                symbol=symbol)

    def poll_risk_once(self) -> None:
        """One read of the risk log. Public for the same reason."""
        alerts = self._tailer.poll()
        state = self._tailer.state
        if state != self._last_source_state:
            previous, self._last_source_state = self._last_source_state, state
            if state == "unreadable":
                self._hub.publish("risk", {"type": "risk", "kind": "source_unreadable", "ts": _now_iso()})
            elif previous == "unreadable":
                self._hub.publish("risk", {"type": "risk", "kind": "source_recovered", "ts": _now_iso()})
        for alert in alerts:
            alert.setdefault("ts", _now_iso())
            self._hub.publish("risk", alert)

    async def _run_orderbook(self) -> None:
        while self._hub.has_topic("orderbook") and self._hub.watched_symbols():
            await self.poll_orderbooks_once()
            await asyncio.sleep(self._ob_interval)

    async def _run_risk(self) -> None:
        while self._hub.has_topic("risk"):
            self.poll_risk_once()
            await asyncio.sleep(self._risk_interval)


# ── one connection ───────────────────────────────────────────────────────────


class WebSocketLike(Protocol):
    """What a session needs from a socket. Starlette's `WebSocket` has all of
    it; the tests use a fake."""

    headers: Any
    query_params: Any

    async def accept(self) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None: ...


async def _close(ws: WebSocketLike, code: int, reason: str) -> None:
    try:
        await ws.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - already gone
        pass


def _parse_subscription(msg: dict, symbol_ok: Callable[[str], bool]
                        ) -> "tuple[Optional[set[str]], Optional[set[str]], Optional[str]]":
    """(topics, symbols, error) from a subscribe message; either of the first
    two is None when the message did not name it."""
    topics: Optional[set[str]] = None
    symbols: Optional[set[str]] = None
    if "topics" in msg:
        raw = msg["topics"]
        if not isinstance(raw, list) or not raw or not all(isinstance(t, str) for t in raw):
            return None, None, "bad_topics"
        if any(t not in TOPICS for t in raw):
            return None, None, "unknown_topic"
        topics = set(raw)
    if "symbols" in msg:
        raw = msg["symbols"]
        if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
            return None, None, "bad_symbols"
        if len(raw) > MAX_SYMBOLS_PER_CLIENT:
            return None, None, "too_many_symbols"
        if not all(symbol_ok(s) for s in raw):
            return None, None, "bad_symbol"
        symbols = set(raw)
    return topics, symbols, None


async def serve_session(
    ws: WebSocketLike,
    *,
    hub: TelemetryHub,
    feeds: TelemetryFeeds,
    expected_token: str,
    symbol_ok: Callable[[str], bool],
    auth_timeout_s: float = AUTH_TIMEOUT_S,
    send_timeout_s: float = SEND_TIMEOUT_S,
) -> None:
    """Run one authenticated connection to its end.

    The caller has already rate-limited the handshake by address. Nothing is
    subscribed, queued or polled until the token has matched."""
    await ws.accept()
    if not expected_token:
        # require_dashboard_token's 503: a bridge with no token has no
        # authenticated surface, and an open stream is not a substitute.
        await _close(ws, CLOSE_POLICY, "not configured")
        return

    # 1. authenticate: the header, then a first message. Never the URL.
    if ws.query_params is not None and "token" in ws.query_params:
        await _close(ws, CLOSE_POLICY, "token in url refused")
        return
    candidate = bearer_from_header(ws.headers.get("authorization")) if ws.headers is not None else None
    if candidate is None:
        try:
            async with asyncio.timeout(auth_timeout_s):
                first = await ws.receive_text()
            msg = json.loads(first) if len(first) <= MAX_CLIENT_MESSAGE_CHARS else None
        except Exception:  # noqa: BLE001 - timeout, disconnect or junk: all unauthenticated
            msg = None
        candidate = msg.get("token") if isinstance(msg, dict) and msg.get("op") == "auth" else None
    if not token_matches(candidate, expected_token):
        await _close(ws, CLOSE_POLICY, "unauthorized")
        return

    # 2. subscribe, from the query string or the default (risk alerts only).
    topics: set[str] = {"risk"}
    symbols: set[str] = set()
    query = ws.query_params
    if query is not None:
        raw_topics = query.get("topics")
        raw_symbols = query.get("symbols")
        initial: dict[str, Any] = {}
        if raw_topics:
            initial["topics"] = [t for t in raw_topics.split(",") if t]
        if raw_symbols:
            initial["symbols"] = [s for s in raw_symbols.split(",") if s]
        t, s, err = _parse_subscription(initial, symbol_ok)
        if err is not None:
            await _close(ws, CLOSE_POLICY, err)
            return
        topics = t or topics
        symbols = s or symbols
    sub = hub.attach(topics, symbols)
    if sub is None:
        await _close(ws, CLOSE_TRY_LATER, "at capacity")
        return
    hub.send_to(sub, {"type": "hello", "v": 1, "topics": sorted(sub.topics),
                      "symbols": sorted(sub.symbols), "ts": _now_iso()})
    feeds.ensure_running()

    async def writer() -> None:
        while True:
            frame = await sub.queue.get()
            if frame is None:
                return
            try:
                # asyncio.timeout, not wait_for: on 3.11 a wait_for whose inner
                # call completes in the same tick as an outer cancel swallows
                # the cancel, and this loop then blocks on an empty queue for
                # ever -- the session never ends.
                async with asyncio.timeout(send_timeout_s):
                    await ws.send_text(frame)
            except TimeoutError:
                hub.drop(sub, "send_timeout")
                return

    async def reader() -> None:
        stamps: list[float] = []
        while True:
            text = await ws.receive_text()
            now = time.monotonic()
            stamps = [t for t in stamps if now - t < CLIENT_MESSAGE_WINDOW_S]
            stamps.append(now)
            if len(stamps) > CLIENT_MESSAGES_PER_WINDOW:
                hub.drop(sub, "client_rate")
                return
            if len(text) > MAX_CLIENT_MESSAGE_CHARS:
                hub.drop(sub, "message_too_large")
                return
            try:
                msg = json.loads(text)
            except ValueError:
                msg = None
            op = msg.get("op") if isinstance(msg, dict) else None
            if op == "ping":
                hub.send_to(sub, {"type": "pong", "ts": _now_iso()})
            elif op == "subscribe" and isinstance(msg, dict):
                t, s, err = _parse_subscription(msg, symbol_ok)
                if err is not None:
                    hub.send_to(sub, {"type": "error", "code": err})
                    continue
                if t is not None:
                    sub.topics = t
                if s is not None:
                    sub.symbols = s
                hub.send_to(sub, {"type": "subscribed", "topics": sorted(sub.topics),
                                  "symbols": sorted(sub.symbols)})
                feeds.ensure_running()
            else:
                hub.send_to(sub, {"type": "error", "code": "bad_op"})

    tasks = [asyncio.ensure_future(writer()), asyncio.ensure_future(reader())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        reason = sub.closed_reason
        hub.detach(sub)
    if reason in ("slow_client", "send_timeout"):
        await _close(ws, CLOSE_TRY_LATER, reason)
    elif reason is not None:
        await _close(ws, CLOSE_POLICY, reason)
    else:
        await _close(ws, 1000, "bye")

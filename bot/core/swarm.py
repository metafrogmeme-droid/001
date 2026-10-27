"""
RUNECLAW multi-agent swarm: an asyncio queue bus with one worker per role.

Roles (one worker task each):
  - SCANNER:  validates a market observation, hands it to the Analyst
  - ANALYST:  turns an observation that carries levels into a trade thesis
  - RISK:     gates the thesis through ``RiskEngine.evaluate``
  - EXECUTOR: ENQUEUES a ticket into the engine's existing confirm path. It
              never calls an executor and never places an order
  - SENTINEL: reads anomalies; a critical or unreadable one HALTS the swarm
  - COORDINATOR: consumes heartbeats and reports; the supervisor watches it

THE BUS. One bounded ``asyncio.PriorityQueue`` per role. A message's priority
comes from its type (HALT first, HEARTBEAT last) and ties are first in, first
out. ``publish`` runs on the loop; ``publish_threadsafe`` is the door for
every other thread and goes through ``loop.call_soon_threadsafe``.

FAIL CLOSED, in three ways, and each one HALTS rather than drops:
  - a queue overflows (the consumer is not keeping up);
  - a worker misses its heartbeat window (it is stuck or dead);
  - a worker handler raises (the reason names the exception class only).
A halt is a latch on the bus, set at once from any thread. It does not depend
on the HALT message reaching a queue, so a full queue cannot swallow it.
While halted, the stages that advance a trade (SIGNAL, TRADE_IDEA,
RISK_VERDICT) are refused at the door.

THE ENGINE OWNS THE HALT. The coordinator never stops trading itself: it hands
the reason to a ``halt_sink`` the engine supplies, and that sink is the
engine's existing halt path. Nothing here opens a second one.

ABSENT IS NOT A MEASUREMENT. An observation with no readable price or momentum
is counted unreadable and goes no further. The Analyst fabricates no levels:
a thesis needs a stop and a target the caller or a ``thesis_provider`` stated.
An anomaly whose severity cannot be read is treated as critical.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field

from bot.compat import UTC
from bot.utils.models import AgentState, Direction, RiskVerdict, TradeIdea

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Swarm message protocol
# ---------------------------------------------------------------------------

class SwarmRole(str, Enum):
    """Agent roles in the swarm."""
    SCANNER = "SCANNER"
    ANALYST = "ANALYST"
    RISK = "RISK"
    EXECUTOR = "EXECUTOR"
    SENTINEL = "SENTINEL"
    COORDINATOR = "COORDINATOR"


class SwarmMessageType(str, Enum):
    """Message types exchanged between agents."""
    SIGNAL = "SIGNAL"               # Scanner → Analyst: market signal detected
    TRADE_IDEA = "TRADE_IDEA"       # Analyst → Risk: trade thesis generated
    RISK_VERDICT = "RISK_VERDICT"   # Risk → Executor: approved/rejected
    EXECUTION = "EXECUTION"         # Executor → Coordinator: ticket queued/refused
    ANOMALY = "ANOMALY"             # anomaly report → Sentinel (and its record)
    HALT = "HALT"                   # Sentinel/Coordinator → All: stop trading
    HEARTBEAT = "HEARTBEAT"         # Any → Coordinator: alive signal
    STATUS = "STATUS"               # engine FSM report / coordinator broadcast


class SwarmMessage(BaseModel):
    """A message in the swarm protocol."""
    id: str = Field(default_factory=lambda: f"MSG-{uuid4().hex[:12]}")
    msg_type: SwarmMessageType
    sender: SwarmRole
    recipient: SwarmRole
    payload: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def __str__(self) -> str:
        return f"[{self.sender.value}→{self.recipient.value}] {self.msg_type.value}: {list(self.payload.keys())}"


#: Lower number is served first. HALT outranks everything; a heartbeat never
#: delays a trade stage. Every SwarmMessageType has a row (a test walks the
#: enum), so a new type cannot arrive without a priority.
MESSAGE_PRIORITY: dict[SwarmMessageType, int] = {
    SwarmMessageType.HALT: 0,
    SwarmMessageType.ANOMALY: 1,
    SwarmMessageType.RISK_VERDICT: 2,
    SwarmMessageType.EXECUTION: 3,
    SwarmMessageType.TRADE_IDEA: 4,
    SwarmMessageType.SIGNAL: 5,
    SwarmMessageType.STATUS: 6,
    SwarmMessageType.HEARTBEAT: 7,
}

#: The stages that advance a trade toward a ticket. Refused while halted.
_TRADE_STAGES = frozenset({
    SwarmMessageType.SIGNAL,
    SwarmMessageType.TRADE_IDEA,
    SwarmMessageType.RISK_VERDICT,
})

#: Not counted as in-flight work: they never advance a trade, and a steady
#: heartbeat would keep ``drain`` from ever seeing the swarm idle.
_UNTRACKED = frozenset({
    SwarmMessageType.HEARTBEAT,
    SwarmMessageType.STATUS,
    SwarmMessageType.HALT,
})

#: Which role is active in each engine FSM state. Exhaustive over AgentState
#: (a test walks the enum). The engine's `_transition` stays the only FSM
#: mutator; the coordinator only OBSERVES it through this map.
ROLE_FOR_STATE: dict[AgentState, SwarmRole] = {
    AgentState.IDLE: SwarmRole.COORDINATOR,
    AgentState.SCANNING: SwarmRole.SCANNER,
    AgentState.ANALYZING: SwarmRole.ANALYST,
    AgentState.RISK_CHECK: SwarmRole.RISK,
    AgentState.CONFIRMING: SwarmRole.EXECUTOR,
    AgentState.EXECUTING: SwarmRole.EXECUTOR,
    AgentState.MONITORING: SwarmRole.EXECUTOR,
    AgentState.COOLING_DOWN: SwarmRole.SENTINEL,
    AgentState.HALTED: SwarmRole.SENTINEL,
}

DEFAULT_QUEUE_SIZE = 256
DEFAULT_HEARTBEAT_INTERVAL_S = 2.0
DEFAULT_HEARTBEAT_TIMEOUT_S = 15.0
#: Same figure as `bot.core.black_swan._HALT_SEVERITY`; a test asserts they
#: agree, so the Sentinel and the detector cannot hold two answers.
DEFAULT_ANOMALY_HALT_SEVERITY = 0.8
MESSAGE_LOG_MAX = 1000

HALT_ENGINE = "engine_halted"
HALT_OVERFLOW = "queue_overflow"
HALT_HEARTBEAT = "heartbeat_gap"
HALT_WORKER = "worker_exception"
HALT_ANOMALY = "anomaly"
HALT_ANOMALY_UNREADABLE = "anomaly_unreadable"

_REASON_MAX = 80


def _clean_reason(reason: Any) -> str:
    """A halt reason that is short, single-line text. It reaches audit rows."""
    text = " ".join(str(reason or "halt").split())
    return text[:_REASON_MAX] or "halt"


def _finite(value: Any) -> Optional[float]:
    """A finite float, or None. A bool is not a number here, and neither is
    NaN: None is how this module says unreadable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# ---------------------------------------------------------------------------
# Swarm communication bus
# ---------------------------------------------------------------------------

class SwarmBus:
    """Bounded per-role priority queues plus the halt latch."""

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        self._queue_size = queue_size
        self._queues: dict[SwarmRole, asyncio.PriorityQueue] = {
            role: asyncio.PriorityQueue(maxsize=queue_size) for role in SwarmRole
        }
        self._seq = itertools.count()
        self._log: deque[SwarmMessage] = deque(maxlen=MESSAGE_LOG_MAX)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        self._halt_reason: Optional[str] = None
        self._halt_generation = 0
        self._halt_event = asyncio.Event()
        self._in_flight = 0
        self._idle = asyncio.Event()
        self._idle.set()

    # -- state --

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    @property
    def halt_reason(self) -> Optional[str]:
        return self._halt_reason

    @property
    def halt_generation(self) -> int:
        """How many halts have latched. A watcher compares it, so a halt that
        lands right after a reset is not mistaken for the one it already saw."""
        return self._halt_generation

    @property
    def message_count(self) -> int:
        return len(self._log)

    @property
    def queue_size(self) -> int:
        return self._queue_size

    def depth(self, role: SwarmRole) -> int:
        return self._queues[role].qsize()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Name the loop the queues are consumed on. `publish_threadsafe`
        schedules onto it."""
        self._loop = loop

    def _adopt_loop(self) -> None:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def _on_bound_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    # -- publishing --

    def publish(self, message: SwarmMessage) -> bool:
        """Queue a message for its recipient. ON the loop only.

        True when it was queued (or, for HALT, latched). False when it was
        refused: the swarm is halted and this stage advances a trade, or the
        recipient's queue is full, which HALTS the swarm (fail closed)."""
        self._adopt_loop()
        if message.msg_type is SwarmMessageType.HALT:
            self.halt(str(message.payload.get("reason") or "halt"),
                      sender=message.sender)
            return True
        return self._enqueue(message.recipient, message)

    def publish_threadsafe(self, message: SwarmMessage) -> bool:
        """The door for any thread that is not the loop.

        HALT latches immediately, whatever the loop is doing. Anything else is
        scheduled with `call_soon_threadsafe`; False means it was NOT
        scheduled (no loop, or the loop is closed), so the caller knows."""
        if message.msg_type is SwarmMessageType.HALT:
            self.halt(str(message.payload.get("reason") or "halt"),
                      sender=message.sender)
            return True
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(self._deliver, message)
        except RuntimeError:
            return False
        return True

    def _deliver(self, message: SwarmMessage) -> None:
        self._enqueue(message.recipient, message)

    def broadcast(self, message: SwarmMessage) -> None:
        """Send a message to EVERY role's queue (a HALT latches the halt)."""
        self._adopt_loop()
        if message.msg_type is SwarmMessageType.HALT:
            self.halt(str(message.payload.get("reason") or "halt"),
                      sender=message.sender)
            return
        self._fan_out(message)

    def _fan_out(self, message: SwarmMessage) -> None:
        priority = MESSAGE_PRIORITY[message.msg_type]
        self._log.append(message)
        for queue in self._queues.values():
            try:
                queue.put_nowait((priority, next(self._seq), message))
            except asyncio.QueueFull:
                # A full queue cannot take a notice. The halt flag, not this
                # message, is what stops the swarm.
                continue

    def _enqueue(self, recipient: SwarmRole, message: SwarmMessage) -> bool:
        if self.halted and message.msg_type in _TRADE_STAGES:
            return False
        try:
            self._queues[recipient].put_nowait(
                (MESSAGE_PRIORITY[message.msg_type], next(self._seq), message))
        except asyncio.QueueFull:
            self.halt(f"{HALT_OVERFLOW}:{recipient.value}")
            return False
        self._log.append(message)
        if message.msg_type not in _UNTRACKED:
            self._in_flight += 1
            self._idle.clear()
        return True

    async def get(self, role: SwarmRole) -> SwarmMessage:
        """The highest-priority message waiting for `role`."""
        _prio, _seq, message = await self._queues[role].get()
        assert isinstance(message, SwarmMessage)
        return message

    def done(self, message: SwarmMessage) -> None:
        """A consumer finished `message`. Pairs with a tracked publish."""
        if message.msg_type in _UNTRACKED:
            return
        self._in_flight = max(0, self._in_flight - 1)
        if self._in_flight == 0:
            self._idle.set()

    async def wait_idle(self, timeout: float) -> bool:
        """True once every tracked message has been handled."""
        try:
            async with asyncio.timeout(timeout):
                await self._idle.wait()
        except TimeoutError:
            return False
        return True

    # -- halting --

    def halt(self, reason: str, sender: SwarmRole = SwarmRole.SENTINEL,
             severity: Optional[float] = None) -> bool:
        """Latch the halt. Any thread. True only for the call that latched it;
        the first reason wins and later ones are ignored."""
        text = _clean_reason(reason)
        with self._lock:
            if self._halt_reason is not None:
                return False
            self._halt_reason = text
            self._halt_generation += 1
        message = SwarmMessage(
            msg_type=SwarmMessageType.HALT, sender=sender,
            recipient=SwarmRole.COORDINATOR,
            payload={"reason": text, "severity": severity})
        loop = self._loop
        if loop is None or self._on_bound_loop() or loop.is_closed():
            self._announce_halt(message)
        else:
            try:
                loop.call_soon_threadsafe(self._announce_halt, message)
            except RuntimeError:
                self._announce_halt(message)
        return True

    def _announce_halt(self, message: SwarmMessage) -> None:
        self._halt_event.set()
        self._fan_out(message)

    async def wait_halted(self) -> None:
        await self._halt_event.wait()

    def reset(self) -> None:
        """Lift the halt and drop everything still queued."""
        with self._lock:
            self._halt_reason = None
        self._halt_event.clear()
        for queue in self._queues.values():
            while True:
                try:
                    _prio, _seq, message = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self.done(message)


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class SwarmWorker(ABC):
    """One task, one role. Every handled message is followed by a heartbeat; an
    idle worker beats once per interval; a handler that raises HALTS the
    swarm and the worker carries on (discarding while halted) so a reset needs
    no restart."""

    role: SwarmRole = SwarmRole.COORDINATOR

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float) -> None:
        self._bus = bus
        self._interval = heartbeat_interval_s
        self._last_beat = 0.0
        self._task: Optional[asyncio.Task] = None
        self.handled = 0

    def start(self) -> asyncio.Task:
        self._task = asyncio.get_running_loop().create_task(
            self.run(), name=f"swarm-{self.role.value.lower()}")
        return self._task

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # a worker that died by itself: nothing to add
            logger.debug("swarm worker %s ended with %s", self.role.value, type(exc).__name__)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _beat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_beat < self._interval / 2:
            return
        self._last_beat = now
        self.send(SwarmMessageType.HEARTBEAT, SwarmRole.COORDINATOR, {})

    def send(self, msg_type: SwarmMessageType, recipient: SwarmRole,
             payload: Optional[dict] = None) -> bool:
        return self._bus.publish(SwarmMessage(
            msg_type=msg_type, sender=self.role, recipient=recipient,
            payload=payload or {}))

    async def run(self) -> None:
        self._beat(force=True)
        while True:
            message: Optional[SwarmMessage] = None
            try:
                async with asyncio.timeout(self._interval):
                    message = await self._bus.get(self.role)
            except TimeoutError:
                self._beat(force=True)
                continue
            try:
                if message.msg_type is SwarmMessageType.HALT or self._bus.halted:
                    continue
                await self.handle(message)
                self.handled += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The class only: the message of an exception can carry a
                # secret or a path, and this reason reaches the audit log.
                self._bus.halt(f"{HALT_WORKER}:{self.role.value}:{type(exc).__name__}")
            finally:
                self._bus.done(message)
                self._beat()

    @abstractmethod
    async def handle(self, msg: SwarmMessage) -> None:
        """Process one message. Raising HALTS the swarm."""


class ScannerWorker(SwarmWorker):
    """Validates an observation and forwards it. An unreadable one stops here."""
    role = SwarmRole.SCANNER

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float) -> None:
        super().__init__(bus, heartbeat_interval_s)
        self.signals_seen = 0
        self.signals_unreadable = 0

    async def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type is not SwarmMessageType.SIGNAL:
            return
        self.signals_seen += 1
        payload = msg.payload
        symbol = payload.get("symbol")
        price = _finite(payload.get("price"))
        momentum = _finite(payload.get("momentum_score"))
        if not isinstance(symbol, str) or not symbol.strip() or price is None or price <= 0 or momentum is None:
            self.signals_unreadable += 1
            return
        self.send(SwarmMessageType.SIGNAL, SwarmRole.ANALYST,
                  {**payload, "symbol": symbol.strip(), "price": price,
                   "momentum_score": momentum})


#: (signal payload) -> {"stop_loss": float, "take_profit": float, "atr": float?}
#: or None. Sync or async.
ThesisProvider = Callable[[dict], Union[Optional[dict], Awaitable[Optional[dict]]]]


class AnalystWorker(SwarmWorker):
    """Builds a thesis ONLY from levels somebody stated. No level is invented."""
    role = SwarmRole.ANALYST

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float,
                 thesis_provider: Optional[ThesisProvider] = None) -> None:
        super().__init__(bus, heartbeat_interval_s)
        self._provider = thesis_provider
        self.ideas_generated = 0
        self.no_thesis = 0

    async def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type is not SwarmMessageType.SIGNAL:
            return
        payload = msg.payload
        levels: Optional[dict] = None
        if payload.get("stop_loss") is not None and payload.get("take_profit") is not None:
            levels = payload
        elif self._provider is not None:
            found = self._provider(payload)
            if asyncio.iscoroutine(found) or isinstance(found, asyncio.Future):
                found = await found
            levels = found if isinstance(found, dict) else None
        idea = self._build(payload, levels)
        if idea is None:
            self.no_thesis += 1
            return
        self.ideas_generated += 1
        atr = _finite((levels or {}).get("atr"))
        self.send(SwarmMessageType.TRADE_IDEA, SwarmRole.RISK, {
            "trade_id": idea.id, "asset": idea.asset, "idea": idea,
            "atr": atr if atr is not None and atr > 0 else None,
            "source_signal_id": msg.id,
        })

    @staticmethod
    def _build(payload: dict, levels: Optional[dict]) -> Optional[TradeIdea]:
        if levels is None:
            return None
        price = _finite(payload.get("price"))
        momentum = _finite(payload.get("momentum_score"))
        stop = _finite(levels.get("stop_loss"))
        target = _finite(levels.get("take_profit"))
        symbol = payload.get("symbol")
        # Momentum 0.0 has no direction, and "no direction" is not LONG.
        if (price is None or momentum is None or momentum == 0 or stop is None
                or target is None or not isinstance(symbol, str)):
            return None
        try:
            return TradeIdea(
                asset=symbol,
                direction=Direction.LONG if momentum > 0 else Direction.SHORT,
                entry_price=price,
                stop_loss=stop,
                take_profit=target,
                confidence=min(abs(momentum), 1.0),
                reasoning=(f"swarm momentum score {momentum:+.2f}; the confidence "
                           "is that score clamped to 1, not a calibrated figure"),
                signals_used=["swarm_momentum"],
                source="swarm",
            )
        except ValueError:
            # A model-level sanity failure (levels on the wrong side, non-finite
            # prices): the thesis is unreadable, so there is no thesis.
            return None


class RiskWorker(SwarmWorker):
    """Gates every thesis through the real RiskEngine. With none, approves nothing."""
    role = SwarmRole.RISK

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float,
                 risk_engine: Optional[Any] = None) -> None:
        super().__init__(bus, heartbeat_interval_s)
        self._risk_engine = risk_engine
        self.approved = 0
        self.rejected = 0

    async def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type is not SwarmMessageType.TRADE_IDEA:
            return
        idea = msg.payload.get("idea")
        approved = False
        reason = "no_risk_engine"
        if self._risk_engine is not None and isinstance(idea, TradeIdea):
            # An exception here is a broken gate: it propagates and HALTS.
            check = self._risk_engine.evaluate(idea, atr=msg.payload.get("atr"))
            approved = check.verdict == RiskVerdict.APPROVED
            reason = "approved" if approved else "risk_rejected"
        elif self._risk_engine is not None:
            reason = "idea_unreadable"
        if approved:
            self.approved += 1
        else:
            self.rejected += 1
        self.send(
            SwarmMessageType.RISK_VERDICT,
            SwarmRole.EXECUTOR if approved else SwarmRole.COORDINATOR,
            {"trade_id": msg.payload.get("trade_id"),
             "verdict": "APPROVED" if approved else "REJECTED",
             "reason": reason, "asset": msg.payload.get("asset"),
             "idea": idea if approved else None, "atr": msg.payload.get("atr")})


#: (idea, atr) -> True when the ticket was queued into the engine's confirm
#: path. The sink never places anything: a person (or the engine's own gates)
#: still confirms.
TicketSink = Callable[[TradeIdea, Optional[float]], bool]


class ExecutorWorker(SwarmWorker):
    """Enqueues an approved thesis as a TICKET. It places nothing."""
    role = SwarmRole.EXECUTOR

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float,
                 ticket_sink: Optional[TicketSink] = None) -> None:
        super().__init__(bus, heartbeat_interval_s)
        self._ticket_sink = ticket_sink
        self.tickets_queued = 0
        self.tickets_refused = 0

    async def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type is not SwarmMessageType.RISK_VERDICT:
            return
        if msg.payload.get("verdict") != "APPROVED":
            return
        idea = msg.payload.get("idea")
        queued = False
        if self._ticket_sink is not None and isinstance(idea, TradeIdea):
            queued = bool(self._ticket_sink(idea, msg.payload.get("atr")))
        if queued:
            self.tickets_queued += 1
        else:
            self.tickets_refused += 1
        self.send(SwarmMessageType.EXECUTION, SwarmRole.COORDINATOR, {
            "trade_id": msg.payload.get("trade_id"),
            "asset": msg.payload.get("asset"),
            "status": "TICKET_QUEUED" if queued else "TICKET_REFUSED"})


class SentinelWorker(SwarmWorker):
    """A critical, or unreadable, anomaly halts the swarm."""
    role = SwarmRole.SENTINEL

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float,
                 halt_severity: float) -> None:
        super().__init__(bus, heartbeat_interval_s)
        self._halt_severity = halt_severity
        self.anomalies = 0
        self.halts_raised = 0

    async def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type is not SwarmMessageType.ANOMALY:
            return
        self.anomalies += 1
        kind = " ".join(str(msg.payload.get("anomaly_type") or "unknown").split())[:40]
        severity = _finite(msg.payload.get("severity"))
        if severity is None or not 0.0 <= severity <= 1.0:
            # An unreadable severity is not a mild one.
            if self._bus.halt(f"{HALT_ANOMALY_UNREADABLE}:{kind}", sender=self.role):
                self.halts_raised += 1
        elif severity >= self._halt_severity:
            if self._bus.halt(f"{HALT_ANOMALY}:{kind}", sender=self.role, severity=severity):
                self.halts_raised += 1
        self.send(SwarmMessageType.ANOMALY, SwarmRole.COORDINATOR, dict(msg.payload))


class _CoordinatorWorker(SwarmWorker):
    """Consumes the coordinator's queue: heartbeats, reports, engine state."""
    role = SwarmRole.COORDINATOR

    def __init__(self, bus: SwarmBus, heartbeat_interval_s: float,
                 on_message: Callable[[SwarmMessage], None],
                 on_beat: Callable[[], None]) -> None:
        super().__init__(bus, heartbeat_interval_s)
        self._on_message = on_message
        self._on_beat = on_beat

    def _beat(self, force: bool = False) -> None:
        self._on_beat()

    async def handle(self, msg: SwarmMessage) -> None:
        self._on_message(msg)


# ---------------------------------------------------------------------------
# Swarm coordinator
# ---------------------------------------------------------------------------

HaltSink = Callable[[str], Union[None, Awaitable[None]]]


class SwarmCoordinator:
    """Owns the bus, the workers and the supervisor.

    ``halt_sink(reason)`` is the engine's existing halt path. It is called
    once per halt, on the loop, and never for a halt the engine itself
    announced (``HALT_ENGINE``)."""

    def __init__(
        self,
        *,
        risk_engine: Optional[Any] = None,
        ticket_sink: Optional[TicketSink] = None,
        thesis_provider: Optional[ThesisProvider] = None,
        halt_sink: Optional[HaltSink] = None,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        anomaly_halt_severity: float = DEFAULT_ANOMALY_HALT_SEVERITY,
    ) -> None:
        interval = max(0.005, float(heartbeat_interval_s))
        # A window narrower than three beats halts a healthy swarm.
        self._timeout = max(float(heartbeat_timeout_s), 3 * interval)
        self._interval = interval
        self._halt_sink = halt_sink
        self.bus = SwarmBus(queue_size=queue_size)
        self.scanner = ScannerWorker(self.bus, interval)
        self.analyst = AnalystWorker(self.bus, interval, thesis_provider)
        self.risk = RiskWorker(self.bus, interval, risk_engine)
        self.executor = ExecutorWorker(self.bus, interval, ticket_sink)
        self.sentinel = SentinelWorker(self.bus, interval, anomaly_halt_severity)
        self._coordinator = _CoordinatorWorker(
            self.bus, interval, self._on_message, self._on_coordinator_beat)
        self._workers: tuple[SwarmWorker, ...] = (
            self.scanner, self.analyst, self.risk, self.executor,
            self.sentinel, self._coordinator)
        self._last_seen: dict[SwarmRole, float] = {}
        self._supervisors: list[asyncio.Task] = []
        self._started = False
        self.engine_state: Optional[str] = None
        self.engine_role: Optional[str] = None
        self.reports_rejected = 0

    # -- lifecycle --

    async def start(self) -> None:
        if self._started:
            return
        loop = asyncio.get_running_loop()
        self.bus.bind(loop)
        now = time.monotonic()
        self._last_seen = {worker.role: now for worker in self._workers}
        for worker in self._workers:
            worker.start()
        self._supervisors = [
            loop.create_task(self._watch_halt(), name="swarm-halt-watch"),
            loop.create_task(self._watch_heartbeats(), name="swarm-heartbeat-watch"),
        ]
        self._started = True

    async def stop(self, drain_timeout_s: float = 1.0) -> None:
        if not self._started:
            return
        # Let in-flight work finish, bounded; then cancel. A halted swarm is
        # not waited on: its queued work is discarded.
        if not self.bus.halted:
            await self.drain(drain_timeout_s)
        for task in self._supervisors:
            task.cancel()
        for task in self._supervisors:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._supervisors = []
        for worker in self._workers:
            await worker.stop()
        self._started = False

    async def __aenter__(self) -> "SwarmCoordinator":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def drain(self, timeout_s: float = 5.0) -> bool:
        """Wait until every queued trade message has been handled. Yields once
        first, so a message scheduled with `publish_threadsafe` from the loop
        is already counted."""
        await asyncio.sleep(0)
        return await self.bus.wait_idle(timeout_s)

    # -- ingress --

    async def process_signal(self, symbol: str, price: float, change_pct: float,
                             volume: float, momentum: float, *,
                             stop_loss: Optional[float] = None,
                             take_profit: Optional[float] = None,
                             atr: Optional[float] = None) -> dict:
        """Run one observation through Scanner, Analyst, Risk and Executor and
        wait for it to settle. Levels are stated by the caller (or a thesis
        provider); with none, the observation ends at the Analyst as
        `no_thesis`. Returns the swarm's counters."""
        if self.bus.halted:
            return {"status": "HALTED", "reason": self.bus.halt_reason}
        accepted = self.bus.publish(SwarmMessage(
            msg_type=SwarmMessageType.SIGNAL, sender=SwarmRole.COORDINATOR,
            recipient=SwarmRole.SCANNER,
            payload={"symbol": symbol, "price": price,
                     "change_pct_24h": change_pct, "volume_24h": volume,
                     "momentum_score": momentum, "stop_loss": stop_loss,
                     "take_profit": take_profit, "atr": atr}))
        settled = await self.drain()
        if self.bus.halted:
            return {"status": "HALTED", "reason": self.bus.halt_reason}
        snapshot = self.status()
        snapshot["status"] = "PROCESSED" if accepted and settled else "UNSETTLED"
        return snapshot

    def report_anomaly(self, anomaly_type: str, severity: Any, symbol: str,
                       description: str) -> bool:
        """Hand the Sentinel an anomaly. Any thread. False when it was not
        scheduled (the swarm is not running)."""
        return self.bus.publish_threadsafe(SwarmMessage(
            msg_type=SwarmMessageType.ANOMALY, sender=SwarmRole.COORDINATOR,
            recipient=SwarmRole.SENTINEL,
            payload={"anomaly_type": anomaly_type, "severity": severity,
                     "symbol": symbol, "description": description}))

    def on_transition(self, old: AgentState, new: AgentState, reason: str = "") -> None:
        """Observer for the engine's `_transition`. Reports the active role
        and mirrors an engine halt into the swarm. It never mutates the FSM."""
        role = ROLE_FOR_STATE[new]
        self.bus.publish_threadsafe(SwarmMessage(
            msg_type=SwarmMessageType.STATUS, sender=role,
            recipient=SwarmRole.COORDINATOR,
            payload={"state": new.value, "from": old.value, "reason": reason[:80]}))
        if new is AgentState.HALTED:
            self.bus.halt(HALT_ENGINE, sender=SwarmRole.COORDINATOR)

    def reset(self) -> None:
        """Resume after a halt. The workers never exited, so there is nothing
        to restart; the heartbeat window starts over."""
        now = time.monotonic()
        self._last_seen = {worker.role: now for worker in self._workers}
        self.bus.reset()
        self.bus.broadcast(SwarmMessage(
            msg_type=SwarmMessageType.STATUS, sender=SwarmRole.COORDINATOR,
            recipient=SwarmRole.COORDINATOR, payload={"event": "reset"}))

    def status(self) -> dict:
        """Counters only: nothing financial."""
        return {
            "halted": self.bus.halted,
            "halt_reason": self.bus.halt_reason,
            "running": self._started,
            "workers": {w.role.value: w.running for w in self._workers},
            "engine_state": self.engine_state,
            "stats": {
                "total_messages": self.bus.message_count,
                "signals_seen": self.scanner.signals_seen,
                "signals_unreadable": self.scanner.signals_unreadable,
                "ideas_generated": self.analyst.ideas_generated,
                "no_thesis": self.analyst.no_thesis,
                "risk_approved": self.risk.approved,
                "risk_rejected": self.risk.rejected,
                "tickets_queued": self.executor.tickets_queued,
                "tickets_refused": self.executor.tickets_refused,
                "anomalies": self.sentinel.anomalies,
            },
        }

    # -- supervision --

    def _on_coordinator_beat(self) -> None:
        self._last_seen[SwarmRole.COORDINATOR] = time.monotonic()

    def _on_message(self, msg: SwarmMessage) -> None:
        if msg.msg_type is SwarmMessageType.HEARTBEAT:
            self._last_seen[msg.sender] = time.monotonic()
        elif msg.msg_type is SwarmMessageType.STATUS:
            state = msg.payload.get("state")
            if isinstance(state, str):
                self.engine_state = state
                self.engine_role = msg.sender.value
        elif msg.msg_type is SwarmMessageType.RISK_VERDICT:
            self.reports_rejected += 1

    async def _watch_halt(self) -> None:
        """Hand each halt to the engine's halt path, once."""
        while True:
            await self.bus.wait_halted()
            generation = self.bus.halt_generation
            reason = self.bus.halt_reason
            if reason is not None:
                if self._halt_sink is not None and not reason.startswith(HALT_ENGINE):
                    try:
                        outcome = self._halt_sink(reason)
                        if asyncio.iscoroutine(outcome):
                            await outcome
                    except Exception as exc:
                        logger.error("swarm halt sink failed: %s", type(exc).__name__)
            while self.bus.halted and self.bus.halt_generation == generation:
                await asyncio.sleep(0.05)

    async def _watch_heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            if self.bus.halted:
                continue
            now = time.monotonic()
            for role, seen in list(self._last_seen.items()):
                if now - seen > self._timeout:
                    self.bus.halt(f"{HALT_HEARTBEAT}:{role.value}")
                    break

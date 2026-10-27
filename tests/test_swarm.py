"""The async swarm: a bounded queue bus, one worker per role, and fail-closed.

Driven, not scanned: real bus, real worker tasks, the real RiskEngine where a
verdict matters, and a real `RuneClawEngine` for the wiring. Every claim is
asserted on both arms (the refusal and the case that must still work).

What is pinned:

* PRIORITY. HALT outranks every other message type, ties are FIFO, and every
  `SwarmMessageType` has a priority row.
* FAIL CLOSED. A queue overflow, a missed heartbeat and a handler that raises
  each HALT the swarm; the raised exception's MESSAGE never reaches the reason.
* THREADS. `publish_threadsafe` delivers from a plain thread and reports False
  when there is no loop to deliver to; a HALT from a thread latches at once.
* NOTHING INVENTED. No levels means no thesis; no RiskEngine means no approval;
  an unreadable severity halts rather than reading as mild.
* THE ENGINE. The swarm is off by default; on, it OBSERVES `_transition`
  (never mutates it), mirrors a halt in both directions through the engine's
  existing HaltSkill, and the Executor only puts a TICKET in the pending book
  where the auto-confirm batch cannot take it.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading

import pytest

import bot.core.swarm as swarm
from bot.config import CONFIG
from bot.core.black_swan import _HALT_SEVERITY, AnomalyAlert, AnomalyType
from bot.core.engine import RuneClawEngine
from bot.core.swarm import (
    MESSAGE_PRIORITY,
    ROLE_FOR_STATE,
    SwarmBus,
    SwarmCoordinator,
    SwarmMessage,
    SwarmMessageType,
    SwarmRole,
)
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import AgentState, Direction, TradeIdea

T = SwarmMessageType
R = SwarmRole


def _msg(kind: SwarmMessageType, to: SwarmRole = R.ANALYST, **payload) -> SwarmMessage:
    return SwarmMessage(msg_type=kind, sender=R.COORDINATOR, recipient=to, payload=payload)


def _real_risk() -> RiskEngine:
    return RiskEngine(PortfolioTracker(initial_balance=10000.0),
                      state_file=os.path.join(tempfile.mkdtemp(prefix="rc-swarm-"), "risk_state.json"))


async def _until(predicate, timeout: float = 3.0) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# ── the protocol tables ─────────────────────────────────────────────────────

def test_every_message_type_has_a_priority_and_halt_is_first():
    assert set(MESSAGE_PRIORITY) == set(SwarmMessageType)
    assert MESSAGE_PRIORITY[T.HALT] == min(MESSAGE_PRIORITY.values())
    assert MESSAGE_PRIORITY[T.HALT] < MESSAGE_PRIORITY[T.ANOMALY] < MESSAGE_PRIORITY[T.SIGNAL]
    assert MESSAGE_PRIORITY[T.HEARTBEAT] == max(MESSAGE_PRIORITY.values())


def test_every_fsm_state_maps_to_a_role():
    assert set(ROLE_FOR_STATE) == set(AgentState)
    assert ROLE_FOR_STATE[AgentState.SCANNING] is R.SCANNER
    assert ROLE_FOR_STATE[AgentState.RISK_CHECK] is R.RISK
    assert ROLE_FOR_STATE[AgentState.HALTED] is R.SENTINEL


def test_the_sentinel_threshold_is_the_detectors_threshold():
    assert swarm.DEFAULT_ANOMALY_HALT_SEVERITY == _HALT_SEVERITY


# ── the bus ─────────────────────────────────────────────────────────────────

async def test_halt_is_served_before_everything_and_ties_are_fifo():
    bus = SwarmBus()
    first = _msg(T.SIGNAL, seq=1)
    second = _msg(T.SIGNAL, seq=2)
    beat = _msg(T.HEARTBEAT)
    for m in (beat, first, second):
        bus.publish(m)
    bus.halt("test")  # HALT reaches every queue at priority 0
    got = [await bus.get(R.ANALYST) for _ in range(4)]
    assert got[0].msg_type is T.HALT
    assert [m.id for m in got[1:3]] == [first.id, second.id]
    assert got[3].id == beat.id  # published first, served last: priority, not arrival
    assert bus.depth(R.COORDINATOR) == 1  # the HALT notice


async def test_an_overflow_halts_and_is_not_dropped_silently():
    bus = SwarmBus(queue_size=2)
    assert bus.publish(_msg(T.SIGNAL)) and bus.publish(_msg(T.SIGNAL))
    assert not bus.halted
    assert bus.publish(_msg(T.SIGNAL)) is False
    assert bus.halted and bus.halt_reason == "queue_overflow:ANALYST"


async def test_a_halted_bus_refuses_trade_stages_but_takes_observability():
    bus = SwarmBus()
    bus.halt("x")
    assert bus.publish(_msg(T.SIGNAL)) is False
    assert bus.publish(_msg(T.TRADE_IDEA, R.RISK)) is False
    assert bus.publish(_msg(T.RISK_VERDICT, R.EXECUTOR)) is False
    assert bus.publish(_msg(T.HEARTBEAT, R.COORDINATOR)) is True
    assert bus.publish(_msg(T.ANOMALY, R.SENTINEL)) is True


async def test_the_first_halt_reason_wins_and_reset_lifts_it():
    bus = SwarmBus()
    assert bus.halt("first") is True
    assert bus.halt("second") is False
    assert bus.halt_reason == "first"
    bus.publish(_msg(T.HEARTBEAT, R.COORDINATOR))
    bus.reset()
    assert not bus.halted and bus.depth(R.COORDINATOR) == 0
    assert bus.publish(_msg(T.SIGNAL)) is True
    assert bus.halt("again") is True


async def test_publish_threadsafe_delivers_from_a_thread():
    bus = SwarmBus()
    bus.bind(asyncio.get_running_loop())
    msg = _msg(T.SIGNAL, symbol="BTC/USDT")
    ok = await asyncio.to_thread(bus.publish_threadsafe, msg)
    assert ok is True
    got = await asyncio.wait_for(bus.get(R.ANALYST), 2)
    assert got.id == msg.id


def test_publish_threadsafe_says_false_with_no_loop():
    bus = SwarmBus()
    assert bus.publish_threadsafe(_msg(T.SIGNAL)) is False  # never bound
    loop = asyncio.new_event_loop()
    bus.bind(loop)
    loop.close()
    assert bus.publish_threadsafe(_msg(T.SIGNAL)) is False  # bound, then closed


async def test_a_halt_from_a_thread_latches_before_the_loop_runs():
    bus = SwarmBus()
    bus.bind(asyncio.get_running_loop())
    done = threading.Event()

    def off_thread():
        bus.halt("from_thread")
        done.set()

    t = threading.Thread(target=off_thread)
    t.start()
    t.join(2)
    assert done.is_set() and bus.halted and bus.halt_reason == "from_thread"
    await asyncio.wait_for(bus.wait_halted(), 2)  # and the loop side catches up


async def test_a_reset_that_beats_an_off_loop_halt_announce_leaves_no_halt_behind():
    """`halt()` from a thread latches at once and schedules its announce on the
    loop. A `reset()` that runs on the loop first must win: the late announce
    would otherwise set the event with no reason behind it."""
    bus = SwarmBus()
    bus.bind(asyncio.get_running_loop())
    t = threading.Thread(target=lambda: bus.halt("from_thread"))
    t.start()
    t.join(2)
    assert bus.halted                      # latched; the announce is still queued
    bus.reset()                            # runs on the loop, before the announce
    await asyncio.sleep(0.05)              # the queued announce runs now
    assert not bus.halted
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bus.wait_halted(), 0.1)    # no stale wake-up
    assert bus.depth(R.COORDINATOR) == 0                  # and no stale HALT notice
    # The other arm: a halt after the reset is announced as usual.
    t = threading.Thread(target=lambda: bus.halt("second"))
    t.start()
    t.join(2)
    await asyncio.wait_for(bus.wait_halted(), 2)
    assert bus.halt_reason == "second"


async def test_the_halt_watcher_yields_when_woken_with_nothing_latched():
    """Defence in depth: even a set event with no reason must not turn the
    watcher into a loop that never yields (a spin blocks the whole bot)."""
    async with SwarmCoordinator(risk_engine=_real_risk(), heartbeat_interval_s=0.05) as coord:
        calls = 0
        real_wait = coord.bus.wait_halted

        async def counting_wait():
            nonlocal calls
            calls += 1
            if calls > 40:
                raise RuntimeError("spin")      # the watcher died instead of hanging the loop
            await real_wait()

        coord.bus.wait_halted = counting_wait
        watcher = next(t for t in coord._supervisors if t.get_name() == "swarm-halt-watch")
        # Restart the watcher so it picks up the counting wait.
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        coord._supervisors.remove(watcher)
        task = asyncio.get_running_loop().create_task(coord._watch_halt())
        coord._supervisors.append(task)
        coord.bus._halt_event.set()             # set, with no reason latched
        assert not coord.bus.halted
        await asyncio.sleep(0.3)
        assert not task.done() and calls <= 40
        coord.bus._halt_event.clear()


# ── fail closed ─────────────────────────────────────────────────────────────

async def test_a_worker_exception_halts_and_names_only_the_class():
    class Boom:
        def evaluate(self, idea, atr=None):
            raise RuntimeError("sk-live-SECRET-in-the-message")

    async with SwarmCoordinator(risk_engine=Boom(), heartbeat_interval_s=0.05) as coord:
        coord.risk._bus.publish(_msg(T.TRADE_IDEA, R.RISK, idea=_idea(), trade_id="x"))
        assert await _until(lambda: coord.bus.halted)
        reason = coord.bus.halt_reason
    assert reason == "worker_exception:RISK:RuntimeError"
    assert "SECRET" not in reason


async def test_a_stuck_worker_misses_its_heartbeat_and_halts():
    async with SwarmCoordinator(risk_engine=_real_risk(), heartbeat_interval_s=0.02,
                                heartbeat_timeout_s=0.1) as coord:
        assert not coord.bus.halted  # a healthy idle swarm beats and stays up
        await asyncio.sleep(0.4)
        assert not coord.bus.halted

        async def stuck(msg):
            await asyncio.sleep(3600)

        coord.analyst.handle = stuck
        coord.bus.publish(_msg(T.SIGNAL, R.ANALYST, symbol="BTC/USDT"))
        assert await _until(lambda: coord.bus.halted)
        assert coord.bus.halt_reason == "heartbeat_gap:ANALYST"


async def test_a_flooded_stage_halts_the_swarm():
    async with SwarmCoordinator(risk_engine=_real_risk(), queue_size=4,
                                heartbeat_interval_s=0.05) as coord:
        async def stuck(msg):
            await asyncio.sleep(3600)

        coord.analyst.handle = stuck
        for _ in range(12):
            coord.bus.publish(_msg(T.SIGNAL, R.ANALYST, symbol="BTC/USDT"))
        assert coord.bus.halted
        assert coord.bus.halt_reason.startswith("queue_overflow:")


# ── nothing invented ────────────────────────────────────────────────────────

def _idea(**kw) -> TradeIdea:
    base = dict(asset="BTC/USDT", direction=Direction.LONG, entry_price=67000.0,
                stop_loss=65000.0, take_profit=71000.0, confidence=0.8,
                reasoning="t", source="swarm")
    base.update(kw)
    return TradeIdea(**base)


async def test_no_levels_means_no_thesis_and_no_ticket():
    queued = []
    async with SwarmCoordinator(risk_engine=_real_risk(),
                                ticket_sink=lambda i, a: queued.append(i) or True,
                                heartbeat_interval_s=0.05) as coord:
        r = await coord.process_signal("BTC/USDT", 67000.0, 2.5, 1e9, 0.9)
        # ... and with levels, the same signal does reach a ticket.
        r2 = await coord.process_signal("BTC/USDT", 67000.0, 2.5, 1e9, 0.9,
                                        stop_loss=65000.0, take_profit=71000.0, atr=2600.0)
    assert r["stats"]["no_thesis"] == 1 and r["stats"]["ideas_generated"] == 0
    assert r2["stats"]["tickets_queued"] == 1 and len(queued) == 1


async def test_a_thesis_provider_can_supply_the_levels():
    seen = []

    async def provider(payload):
        seen.append(payload["symbol"])
        return {"stop_loss": 65000.0, "take_profit": 71000.0, "atr": 2600.0}

    async with SwarmCoordinator(risk_engine=_real_risk(), thesis_provider=provider,
                                ticket_sink=lambda i, a: True,
                                heartbeat_interval_s=0.05) as coord:
        r = await coord.process_signal("BTC/USDT", 67000.0, 2.5, 1e9, 0.9)
    assert seen == ["BTC/USDT"] and r["stats"]["tickets_queued"] == 1


async def test_a_short_signal_is_a_short_and_a_flat_one_has_no_direction():
    ideas = []
    async with SwarmCoordinator(risk_engine=_real_risk(),
                                ticket_sink=lambda i, a: ideas.append(i) or True,
                                heartbeat_interval_s=0.05) as coord:
        await coord.process_signal("BTC/USDT", 67000.0, -2.5, 1e9, -0.9,
                                   stop_loss=69000.0, take_profit=63000.0, atr=2600.0)
        # Levels a SHORT would accept: a momentum of 0.0 is not a short.
        flat = await coord.process_signal("BTC/USDT", 67000.0, 0.0, 1e9, 0.0,
                                          stop_loss=69000.0, take_profit=63000.0)
    assert [i.direction for i in ideas] == [Direction.SHORT]
    assert flat["stats"]["no_thesis"] == 1


async def test_unreadable_observations_stop_at_the_scanner():
    async with SwarmCoordinator(risk_engine=_real_risk(), heartbeat_interval_s=0.05) as coord:
        for bad in (dict(price=float("nan"), momentum=0.9),
                    dict(price=-1.0, momentum=0.9),
                    dict(price=67000.0, momentum=float("inf")),
                    dict(price=True, momentum=0.9)):
            r = await coord.process_signal("BTC/USDT", bad["price"], 0.0, 1.0, bad["momentum"],
                                           stop_loss=1.0, take_profit=2.0)
    assert r["stats"]["signals_unreadable"] == 4 and r["stats"]["ideas_generated"] == 0


async def test_no_risk_engine_approves_nothing():
    queued = []
    async with SwarmCoordinator(risk_engine=None, ticket_sink=lambda i, a: queued.append(i) or True,
                                heartbeat_interval_s=0.05) as coord:
        r = await coord.process_signal("BTC/USDT", 67000.0, 2.5, 1e9, 0.95,
                                       stop_loss=65000.0, take_profit=71000.0, atr=2600.0)
    assert r["stats"]["risk_approved"] == 0 and r["stats"]["risk_rejected"] == 1
    assert queued == []


async def test_a_refused_ticket_is_reported_refused_not_executed():
    async with SwarmCoordinator(risk_engine=_real_risk(), ticket_sink=lambda i, a: False,
                                heartbeat_interval_s=0.05) as coord:
        r = await coord.process_signal("BTC/USDT", 67000.0, 2.5, 1e9, 0.9,
                                       stop_loss=65000.0, take_profit=71000.0, atr=2600.0)
    assert r["stats"]["tickets_queued"] == 0 and r["stats"]["tickets_refused"] == 1


async def test_the_sentinel_halts_on_critical_and_unreadable_but_not_on_mild():
    reasons = []
    async with SwarmCoordinator(heartbeat_interval_s=0.05,
                                halt_sink=reasons.append) as coord:
        coord.report_anomaly("MILD", 0.3, "BTC/USDT", "x")
        await coord.drain()
        assert not coord.bus.halted and coord.sentinel.anomalies == 1
        coord.report_anomaly("ODD", "high", "BTC/USDT", "severity is not a number")
        await coord.drain()
        assert coord.bus.halt_reason == "anomaly_unreadable:ODD"
    assert reasons == ["anomaly_unreadable:ODD"]


async def test_the_halt_sink_is_called_once_per_halt_and_never_for_the_engines_own():
    reasons = []
    async with SwarmCoordinator(heartbeat_interval_s=0.05, halt_sink=reasons.append) as coord:
        coord.report_anomaly("CRASH", 0.95, "BTC/USDT", "x")
        coord.report_anomaly("CRASH", 0.99, "ETH/USDT", "x")
        assert await _until(lambda: reasons)
        await coord.drain()
        assert reasons == ["anomaly:CRASH"]  # twice reported, one halt, one call
        # A halt that lands right after a reset is a NEW halt and is handed on,
        # even though the watcher has not yet seen the bus clear.
        coord.reset()
        coord.report_anomaly("CRASH2", 0.95, "BTC/USDT", "x")
        assert await _until(lambda: len(reasons) == 2)
        assert reasons == ["anomaly:CRASH", "anomaly:CRASH2"]
        coord.reset()
        coord.on_transition(AgentState.IDLE, AgentState.HALTED, "engine halted it")
        await asyncio.sleep(0.2)
        assert coord.bus.halt_reason == "engine_halted"
    assert reasons == ["anomaly:CRASH", "anomaly:CRASH2"]  # the engine's own is not echoed


# ── the engine ──────────────────────────────────────────────────────────────

_KEYS = ("swarm_enabled", "swarm_heartbeat_interval_s", "swarm_heartbeat_timeout_s")


@pytest.fixture
def swarm_config():
    """CONFIG is frozen; the venue-router suite sets it the same way."""
    prev = {k: getattr(CONFIG, k) for k in _KEYS}
    object.__setattr__(CONFIG, "swarm_enabled", True)
    object.__setattr__(CONFIG, "swarm_heartbeat_interval_s", 0.05)
    object.__setattr__(CONFIG, "swarm_heartbeat_timeout_s", 5.0)
    try:
        yield CONFIG
    finally:
        for k, v in prev.items():
            object.__setattr__(CONFIG, k, v)


@pytest.fixture
def engine(swarm_config):
    return RuneClawEngine()


async def test_the_swarm_is_off_by_default():
    assert CONFIG.swarm_enabled is False  # the shipped default
    eng = RuneClawEngine()
    await eng._start_swarm()
    assert eng._swarm is None and eng._transition_observers == []
    eng._transition(AgentState.SCANNING, "no swarm, no observer")  # still works
    assert eng.state is AgentState.SCANNING


async def test_on_it_observes_every_transition_without_owning_the_fsm(engine):
    await engine._start_swarm()
    try:
        coord = engine._swarm
        assert coord is not None and len(engine._transition_observers) == 1
        engine._transition(AgentState.SCANNING, "scan")
        assert engine.state is AgentState.SCANNING  # the engine's own FSM
        assert await _until(lambda: coord.engine_state == "SCANNING")
        assert coord.engine_role == "SCANNER"
        assert not hasattr(coord, "state")  # no second FSM
    finally:
        await engine._stop_swarm()
    assert engine._swarm is None and engine._transition_observers == []


async def test_a_failing_observer_cannot_break_a_transition(engine):
    def broken(old, new, reason):
        raise RuntimeError("observer fault")

    engine._transition_observers.append(broken)
    engine._transition(AgentState.ANALYZING, "still transitions")
    assert engine.state is AgentState.ANALYZING


async def test_a_critical_anomaly_halts_through_the_engines_halt_path(engine):
    await engine._start_swarm()
    try:
        engine._pending_ideas["keep-me-not"] = _idea()
        alert = AnomalyAlert(anomaly_type=AnomalyType.PRICE_ACCELERATION, severity=0.95,
                             symbol="BTC/USDT", description="flash crash", metric_value=1.0,
                             threshold=0.5, recommended_action="HALT_NEW_TRADES")
        engine._swarm_report_anomalies([alert])
        assert await _until(lambda: engine.state is AgentState.HALTED)
        assert engine.risk.circuit_breaker_active  # HaltSkill's breaker trip
        assert engine._pending_ideas == {}         # and its idea-book clear
        assert engine._swarm.bus.halt_reason == "anomaly:PRICE_ACCELERATION"
    finally:
        engine.risk.reset_circuit_breaker()
        await engine._stop_swarm()


async def test_a_mild_anomaly_halts_nothing(engine):
    await engine._start_swarm()
    try:
        alert = AnomalyAlert(anomaly_type=AnomalyType.VOLUME_COLLAPSE, severity=0.4,
                             symbol="BTC/USDT", description="thin", metric_value=1.0,
                             threshold=0.5, recommended_action="MONITOR")
        engine._swarm_report_anomalies([alert])
        await engine._swarm.drain()
        assert not engine._swarm.bus.halted and not engine.risk.circuit_breaker_active
        assert engine.state is not AgentState.HALTED
    finally:
        await engine._stop_swarm()


async def test_an_engine_halt_is_mirrored_into_the_swarm_and_reset_resumes_it(engine):
    await engine._start_swarm()
    try:
        engine._transition(AgentState.HALTED, "operator /halt")
        assert engine._swarm.bus.halt_reason == "engine_halted"
        assert not engine.risk.circuit_breaker_active  # the swarm added no second halt
        engine.reset_circuit_breaker_all()
        assert not engine._swarm.bus.halted
    finally:
        await engine._stop_swarm()


async def test_the_swarm_halt_path_falls_back_to_the_breaker_when_it_fails(engine, monkeypatch):
    import bot.skills.skill_registry as reg

    async def broken(self, eng, **kw):
        raise RuntimeError("halt skill fault")

    monkeypatch.setattr(reg.HaltSkill, "execute", broken)
    try:
        await engine._swarm_halt("anomaly:TEST")
        assert engine.risk.circuit_breaker_active
    finally:
        engine.risk.reset_circuit_breaker()


async def test_the_executors_ticket_is_pending_but_never_an_engine_idea(engine):
    idea = _idea()
    assert engine._swarm_enqueue_ticket(idea, 2600.0) is True
    assert engine._pending_ideas[idea.id] is idea
    assert engine._pending_atr[idea.id] == 2600.0
    # Not the engine's own: the auto-confirm batch reads only those, so even a
    # confidence of 1.0 is a ticket a person confirms, not an unattended order.
    assert idea.id not in engine._engine_pending_ids()
    idea.confidence = 1.0
    assert engine._auto_confirm_batch(0.0) == []
    # And a halted engine takes no ticket.
    engine._halted = True
    other = _idea()
    assert engine._swarm_enqueue_ticket(other, None) is False
    assert other.id not in engine._pending_ideas
    engine._halted = False


async def test_the_swarm_never_reaches_an_executor():
    """The Executor worker holds a ticket sink and nothing else: no executor,
    no order call, and no import of the order path."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(swarm))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names} | {n.module or "" for n in ast.walk(tree)
                                     if isinstance(n, ast.ImportFrom)}
    assert not any("live_executor" in i or "engine" in i for i in imported)
    assert not names & {"confirm_trade", "execute", "place_order", "live_executor"}

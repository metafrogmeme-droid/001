"""RC-2026-014, the half the honest snapshot could not fix: nothing FED it.

The previous pass made `SystemHealthMonitor` stop claiming HEALTHY off
initialiser values. It could not make the monitor *report anything*, because
`record_api_call`, `set_exchange_status` and `record_scan` had no caller
anywhere outside the class's own docstring. So the four honest outcomes were
one honest outcome: UNKNOWN, forever, on /health, /ready, /metrics and the
Telegram card. DEGRADED and CRITICAL were unreachable, and so were both of
`_is_ready`'s 503 branches.

That is the #999 shape one level down. The code was present, thoroughly
tested, and never reached -- and reachability is a property of the CALLERS, so
no test living inside `system_health.py`'s own module could see it. These
tests therefore drive the REAL engine functions (unbound, against a stand-in
`self`) rather than the monitor directly: a mutation that unwires the feeder
has to fail here, and asserting on `SystemHealthMonitor` alone would not
notice.
"""
import asyncio
import re
import subprocess
import types
from pathlib import Path

import pytest

import bot.web.dashboard_server as ds
from bot.core.engine import RuneClawEngine, _is_connectivity_error
from bot.core.system_health import SystemHealthMonitor

REPO = Path(__file__).resolve().parents[1]


class _Stub:
    """A stand-in `self` carrying the REAL functions under test.

    Constructing a `RuneClawEngine` boots config, the vault and a dozen
    subsystems; binding the two methods gives the same code path with none of
    it. The methods are taken off the class, so editing them in `engine.py`
    changes what runs here.
    """

    _record_exchange_read = RuneClawEngine._record_exchange_read
    _cached_ohlcv = RuneClawEngine._cached_ohlcv
    _record_sweep_complete = RuneClawEngine._record_sweep_complete

    def __init__(self):
        self._ohlcv_cache = {}
        self._last_scan_time = 0.0
        self._last_sweep_duration_s = None
        self.health = SystemHealthMonitor()


class _Exchange:
    """A fake ccxt handle. `fail` is raised instead of returning candles."""

    def __init__(self, fail=None, delay=0.0):
        self.fail = fail
        self.delay = delay
        self.calls = 0

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return [[1, 2, 3, 4, 5, 6]] * limit


# ccxt is not imported by engine.py and must not become a test dependency, so
# the shapes it raises are reconstructed by NAME -- which is exactly what the
# classifier matches on.
class BaseError(Exception):
    pass


class NetworkError(BaseError):
    pass


class RequestTimeout(NetworkError):
    """ccxt's real hierarchy: this SUBCLASSES NetworkError, it is not named it."""


class ExchangeError(BaseError):
    pass


class BadSymbol(ExchangeError):
    """The exchange ANSWERED. Says nothing about connectivity."""


def _drive(stub, exchange, symbol="BTC/USDT", timeframe="1h", **kw):
    return asyncio.run(stub._cached_ohlcv(exchange, symbol, timeframe, **kw))


# ── reachability: the property that can only be checked from outside ──────

def _non_test_callers(name: str) -> list[str]:
    out = subprocess.run(
        ["git", "grep", "-n", rf"\.{name}(", "--", "*.py"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.splitlines()
    return [
        ln for ln in out
        if not ln.startswith("tests/")
        and not ln.split(":", 1)[0].endswith("system_health.py")
    ]


@pytest.mark.parametrize("feeder", ["record_api_call", "set_exchange_status", "record_scan"])
def test_every_feeder_has_a_real_caller(feeder):
    """The finding itself, as an assertion.

    A green suite proved nothing here before: tests were the only caller of
    all three, which is indistinguishable from a monitor that does not work.
    """
    callers = _non_test_callers(feeder)
    assert callers, (
        f"{feeder} is called by nothing outside its own module and the tests; "
        "the monitor is fed by nobody again"
    )


# ── the success path ──────────────────────────────────────────────────────

def test_a_successful_read_is_recorded_as_a_call():
    s = _Stub()
    _drive(s, _Exchange())
    snap = s.health.snapshot()
    assert snap.total_api_calls == 1
    assert snap.total_errors == 0
    assert snap.error_rate_pct == 0.0


def test_a_successful_read_reports_the_exchange_connected():
    s = _Stub()
    assert s.health.snapshot().exchange_connected is None, "nobody has looked yet"
    _drive(s, _Exchange())
    assert s.health.snapshot().exchange_connected is True, (
        "we just read candles off it; that is a measurement, not an assumption"
    )


def test_the_recorded_latency_is_measured_not_stubbed():
    s = _Stub()
    _drive(s, _Exchange(delay=0.05))
    lat = s.health.snapshot().api_latency_ms
    assert lat is not None
    assert lat >= 40.0, f"a 50ms fetch recorded as {lat}ms — the clock is not being read"


def test_the_status_leaves_unknown_once_a_read_lands():
    s = _Stub()
    assert s.health.snapshot().status == "UNKNOWN"
    _drive(s, _Exchange())
    assert s.health.snapshot().status == "HEALTHY"


# ── the cache hit is NOT an API call ──────────────────────────────────────

def test_a_cache_hit_is_not_counted_as_a_fast_success():
    """It never left the process. Counting it dilutes both figures.

    A cached series recorded as a ~0ms success would drag the rolling average
    toward zero and shrink the error rate by inflating the denominator — a
    health reading improved by NOT talking to the exchange.
    """
    s = _Stub()
    ex = _Exchange()
    _drive(s, ex, ttl=999)
    _drive(s, ex, ttl=999)
    _drive(s, ex, ttl=999)
    assert ex.calls == 1, "fixture broken: the cache did not hold"
    assert s.health.snapshot().total_api_calls == 1


# ── the failure path ──────────────────────────────────────────────────────

def test_a_failed_read_still_propagates():
    """Instrumentation must not swallow the fault it is recording."""
    s = _Stub()
    with pytest.raises(RequestTimeout):
        _drive(s, _Exchange(fail=RequestTimeout("gone")))


def test_a_failed_read_is_counted_as_an_error():
    s = _Stub()
    with pytest.raises(BadSymbol):
        _drive(s, _Exchange(fail=BadSymbol("DOGE/USDT does not exist")))
    snap = s.health.snapshot()
    assert snap.total_api_calls == 1
    assert snap.total_errors == 1


def test_a_failed_read_is_not_cached():
    s = _Stub()
    with pytest.raises(RequestTimeout):
        _drive(s, _Exchange(fail=RequestTimeout("gone")))
    assert s._ohlcv_cache == {}, "a failed read must not be served to the next caller"


# ── the previously-dead 503 branches, now reachable ───────────────────────

def test_a_connectivity_failure_reports_the_exchange_down():
    s = _Stub()
    with pytest.raises(RequestTimeout):
        _drive(s, _Exchange(fail=RequestTimeout("timed out")))
    assert s.health.snapshot().exchange_connected is False


def test_a_connectivity_failure_takes_ready_to_503():
    """`_is_ready`'s `exchange_connected is False` branch was unreachable.

    The successful reads first are load-bearing: one failure on its own is a
    100% error rate, which reaches CRITICAL by the AGGREGATE route and would
    let this pass with the connectivity branch still dead. Nine successes hold
    the rate at 10%, below both thresholds, so the only thing that can turn
    this 503 is the reported disconnection.
    """
    s = _Stub()
    assert ds._is_ready(s.health.snapshot()) is True, "boot window still answers 200"
    for i in range(9):
        _drive(s, _Exchange(), symbol=f"S{i}/USDT")
    assert ds._is_ready(s.health.snapshot()) is True
    with pytest.raises(RequestTimeout):
        _drive(s, _Exchange(fail=RequestTimeout("timed out")), symbol="Z/USDT")
    snap = s.health.snapshot()
    assert snap.error_rate_pct == 10.0, "fixture broken: the rate route is open"
    assert snap.exchange_connected is False
    assert snap.status == "CRITICAL"
    assert ds._is_ready(snap) is False


def test_a_run_of_errors_reaches_critical_without_a_connectivity_verdict():
    """The aggregate route to 503: no single call claimed the exchange is down."""
    s = _Stub()
    for _ in range(6):
        with pytest.raises(BadSymbol):
            _drive(s, _Exchange(fail=BadSymbol("nope")))
    snap = s.health.snapshot()
    assert snap.exchange_connected is None, (
        "no call reported connectivity either way; only the rate escalated"
    )
    assert snap.error_rate_pct == 100.0
    assert snap.status == "CRITICAL"
    assert ds._is_ready(snap) is False


def test_slow_reads_reach_degraded():
    s = _Stub()
    for _ in range(2):
        s._record_exchange_read(0.0, None, "BTC/USDT", "1h")
    # A monotonic start of 0.0 is process-start, so the elapsed figure is the
    # uptime — comfortably past the 5s DEGRADED threshold and measured, not
    # asserted.
    snap = s.health.snapshot()
    assert snap.api_latency_ms is not None and snap.api_latency_ms > 5000
    assert snap.status == "DEGRADED"


# ── a heuristic is never a verdict ────────────────────────────────────────

def test_one_refused_symbol_does_not_declare_the_exchange_down():
    """`BadSymbol` is the exchange answering. /ready must not 503 on a delisting."""
    s = _Stub()
    _drive(s, _Exchange())                       # a real, successful read first
    assert s.health.snapshot().exchange_connected is True
    with pytest.raises(BadSymbol):
        _drive(s, _Exchange(fail=BadSymbol("delisted")), symbol="XYZ/USDT")
    assert s.health.snapshot().exchange_connected is True, (
        "a refused symbol overwrote a measured connectivity reading"
    )


def test_an_unlooked_exchange_stays_unlooked_after_a_refusal():
    s = _Stub()
    with pytest.raises(BadSymbol):
        _drive(s, _Exchange(fail=BadSymbol("delisted")))
    assert s.health.snapshot().exchange_connected is None, (
        "nobody established connectivity either way; False would be invented"
    )


@pytest.mark.parametrize("exc", [
    NetworkError("x"),
    RequestTimeout("x"),            # subclass — matched via the MRO, not by name
    TimeoutError("x"),
    ConnectionResetError("x"),       # subclasses OSError
    OSError("x"),
])
def test_connectivity_shapes_are_classified_by_their_whole_mro(exc):
    assert _is_connectivity_error(exc) is True


@pytest.mark.parametrize("exc", [
    BadSymbol("x"),
    ExchangeError("x"),
    ValueError("x"),
    KeyError("x"),
])
def test_answers_and_bugs_are_not_connectivity_faults(exc):
    assert _is_connectivity_error(exc) is False


# ── the error label must not carry the driver's prose ─────────────────────

_SECRET = "sk-RUNECLAW-TESTKEY-9d3f"


def test_the_recorded_error_is_a_class_name_not_the_exception_text():
    """`last_error` is rendered into the Telegram card.

    A ccxt error string can carry the failing request URL, and on some venues
    the API key rides in that URL's query string. Same rule as /readyz's fixed
    reason vocabulary: which KIND of failure, never the driver's message.
    """
    s = _Stub()
    boom = RequestTimeout(f"GET https://api.venue.com/ohlcv?apiKey={_SECRET} timed out")
    with pytest.raises(RequestTimeout):
        _drive(s, _Exchange(fail=boom))
    last = s.health.snapshot().last_error or ""
    assert _SECRET not in last, f"the exception text reached last_error: {last!r}"
    assert "RequestTimeout" in last, "the failure KIND must survive the redaction"
    assert "BTC/USDT" in last, "which symbol failed is not a secret and is useful"


def test_the_telegram_card_cannot_be_made_to_print_the_secret():
    s = _Stub()
    boom = RequestTimeout(f"apiKey={_SECRET}")
    with pytest.raises(RequestTimeout):
        _drive(s, _Exchange(fail=boom))
    assert _SECRET not in s.health.format_telegram()


# ── the scan stamp ────────────────────────────────────────────────────────

def test_a_completed_sweep_stamps_the_monitor():
    s = _Stub()
    assert s.health.snapshot().last_successful_scan is None
    s._record_sweep_complete(0.0)
    assert s.health.snapshot().last_successful_scan is not None


def test_record_scan_is_only_called_from_the_sweep_completion_seam():
    """The seam is right BECAUSE of what does not reach it.

    `_record_sweep_complete` is not called when the scan failed — its own
    docstring is emphatic, and both call sites guard on `signals is not None`
    or sit past a re-raising phase. A second `record_scan()` call anywhere else
    in the engine would let the stamp come to mean "we tried".
    """
    src = (REPO / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
    hits = [
        m.start() for m in re.finditer(r"health\.record_scan\(", src)
        if not src[:m.start()].rstrip().endswith("#")
    ]
    assert len(hits) == 1, f"record_scan called {len(hits)} times in engine.py"
    body_start = src.index("def _record_sweep_complete")
    body_end = src.index("def _record_analyze_throughput", body_start)
    assert body_start < hits[0] < body_end, (
        "record_scan moved out of _record_sweep_complete"
    )


# ── the endpoint docstring stopped promising what it does not do ──────────

def test_handle_ready_no_longer_promises_to_fail_closed():
    doc = ds.handle_ready.__doc__ or ""
    body = doc.split("This said", 1)[0]
    assert "Fails CLOSED" not in body, (
        "the handler documents a contract `_is_ready` deliberately does not "
        "implement; an operator configures a probe off the promise"
    )


def test_ready_body_still_distinguishes_the_boot_window():
    engine = types.SimpleNamespace(health=SystemHealthMonitor())
    snap = engine.health.snapshot()
    assert snap.status == "UNKNOWN"
    assert ds._is_ready(snap) is True
    # The qualifier the 200 is only honest with.
    assert (snap.status != "UNKNOWN") is False

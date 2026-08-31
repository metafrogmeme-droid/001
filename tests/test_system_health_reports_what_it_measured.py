"""An unfed health monitor must not report the good state.

RC-2026-014, SAFE variant. `SystemHealthMonitor` is fed by nothing --
`record_api_call`, `set_exchange_status` and `set_ws_status` have no caller
anywhere in the tree outside this class's own docstring -- and `snapshot()`
resolved that absence into the most reassuring answer available::

    else:
        avg_lat = 0.0
        p99_lat = 0.0
        err_rate = 0.0
    ...
    if not self._exchange_ok or err_rate > 50:   -> False
    elif err_rate > 10 or avg_lat > 5000:        -> False
    else: status = "HEALTHY"

Every threshold is a comparison against a fabricated zero, so the verdict is
structurally HEALTHY, with `exchange_connected=True` from an initialiser no
caller ever writes. That is published on /health, /ready, /metrics and the
Telegram card -- the four places an operator and an uptime checker look first.

WHAT THIS DELIBERATELY DOES NOT DO. Making the snapshot honest would, under the
old predicate, flip /ready from a permanent 200 to a permanent 503: the
monitor is unfed, so "not determined" is its steady state, and a readiness
probe stuck at 503 is an outage that is not happening. The HTTP contract is
therefore unchanged and the body says `health_observed: false` instead. The
endpoint's own "fails CLOSED" promise cannot be honoured until something
actually feeds the monitor; pretending otherwise by flipping the code would
trade a false all-clear for a false alarm.
"""
from bot.core.system_health import SystemHealthMonitor


def _unfed():
    return SystemHealthMonitor()


# ── the snapshot ──────────────────────────────────────────────────────────

def test_an_unfed_monitor_does_not_report_healthy():
    s = _unfed().snapshot()
    assert s.status != "HEALTHY", (
        "a monitor nothing has ever reported to publishes a passing grade"
    )
    assert s.status == "UNKNOWN"


def test_unmeasured_rates_are_none_not_zero():
    s = _unfed().snapshot()
    assert s.api_latency_ms is None
    assert s.api_latency_p99_ms is None
    assert s.error_rate_pct is None, (
        "0.0% error rate is a measurement: it says calls were made and none failed"
    )


def test_exchange_connectivity_nobody_checked_is_not_connected():
    s = _unfed().snapshot()
    assert s.exchange_connected is None, (
        "`exchange_connected=True` came from an initialiser, not from a check"
    )


def test_uptime_is_still_reported_because_it_is_genuinely_measured():
    """The honest fix must not blank the fields that ARE real."""
    s = _unfed().snapshot()
    assert s.uptime_seconds is not None and s.uptime_seconds >= 0.0


# ── once something actually reports ───────────────────────────────────────

def test_a_fed_monitor_reports_normally():
    m = _unfed()
    m.record_api_call(120.0, success=True)
    m.record_api_call(140.0, success=True)
    s = m.snapshot()
    assert s.status == "HEALTHY"
    assert s.error_rate_pct == 0.0, (
        "a measured 0% error rate is real and must survive the change"
    )
    assert s.api_latency_ms is not None and s.api_latency_ms > 0


def test_a_measured_failure_rate_still_escalates():
    m = _unfed()
    for _ in range(6):
        m.record_api_call(100.0, success=False, error_msg="boom")
    m.record_api_call(100.0, success=True)
    s = m.snapshot()
    assert s.status == "CRITICAL"
    assert s.error_rate_pct is not None and s.error_rate_pct > 50


def test_a_reported_exchange_outage_is_still_critical():
    m = _unfed()
    m.record_api_call(100.0, success=True)
    m.set_exchange_status(False)
    assert m.snapshot().status == "CRITICAL"


# ── the Telegram card ─────────────────────────────────────────────────────

def test_the_card_does_not_print_measured_looking_zeros():
    out = _unfed().format_telegram()
    assert "SYSTEM HEALTH: HEALTHY" not in out
    assert "0ms" not in out, "an unmeasured latency printed as 0ms"
    assert "0.0%" not in out, "an unmeasured error rate printed as 0.0%"
    assert "🟢 Connected" not in out, "exchange reported connected by nobody"


def test_the_card_still_reports_a_real_reading():
    m = _unfed()
    m.record_api_call(150.0, success=True)
    m.set_exchange_status(True)
    out = m.format_telegram()
    assert "SYSTEM HEALTH: HEALTHY" in out
    assert "150ms" in out


# ── the endpoints ─────────────────────────────────────────────────────────

class _Engine:
    def __init__(self, monitor):
        self.health = monitor


def test_readiness_does_not_flip_to_a_permanent_503():
    """THE SAFE CHOICE, and it is deliberate.

    The old predicate was `exchange_connected and status != "CRITICAL"`, with a
    docstring promising it "fails CLOSED". Against the honest snapshot that
    reads False forever, because the monitor is unfed -- so an orchestrator or
    uptime checker would see a permanent outage that is not happening. A false
    alarm is not an improvement on a false all-clear.

    Not-observed is therefore not a readiness failure; a REPORTED failure still
    is. The body carries the truth for whoever reads it.
    """
    import bot.web.dashboard_server as ds

    assert ds._is_ready(_unfed().snapshot()) is True


def test_a_reported_failure_still_fails_readiness():
    import bot.web.dashboard_server as ds

    m = _unfed()
    m.record_api_call(100.0, success=True)
    m.set_exchange_status(False)
    assert ds._is_ready(m.snapshot()) is False


def test_metrics_omit_what_was_never_measured():
    """A gauge is a claim, and Prometheus has no value meaning 'no reading'.

    Exporting 0 for an unfed monitor gives every alert on these series a
    permanently-satisfied condition. Absent is the correct representation, and
    `runeclaw_health_observed` says which case it is.
    """
    import bot.web.dashboard_server as ds

    out = ds._render_prometheus(_Engine(_unfed()))
    assert "runeclaw_api_latency_ms" not in out
    assert "runeclaw_api_error_rate_pct" not in out
    assert "runeclaw_health_observed 0" in out
    # Never emit a bare None -- it is not valid exposition and a scraper
    # rejects the whole payload, taking the real metrics down with it.
    assert "None" not in out
    # The genuinely measured ones survive.
    assert "runeclaw_up 1" in out
    assert "runeclaw_uptime_seconds" in out


def test_metrics_report_the_gauges_once_they_are_real():
    import bot.web.dashboard_server as ds

    m = _unfed()
    m.record_api_call(120.0, success=True)
    m.set_exchange_status(True)
    out = ds._render_prometheus(_Engine(m))
    assert "runeclaw_api_latency_ms" in out
    assert "runeclaw_api_error_rate_pct" in out
    assert "runeclaw_health_observed 1" in out

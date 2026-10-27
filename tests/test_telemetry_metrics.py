"""/metrics: scan latency, risk rejections by gate, active swarm workers.

Three series families were added to `bot.web.dashboard_server._render_prometheus`
and the rule they are held to is the repo's: UNREADABLE IS NEVER ZERO. The
exposition format has no spelling for "no reading", so the only honest
representation of an unmeasured quantity is an absent series, and a gauge written
at 0 for it is a permanently satisfied alert condition. Every test below has both
arms: the series is there when the source measured it, and gone when it did not.

The risk-gate arm is driven against a REAL `RiskEngine` that the red-team
scenarios have attacked, so the gate names and counts are the ones the gate
actually keeps rather than a fixture's idea of them.

Non-financial only: /metrics is served unauthenticated, so nothing here may carry
a dollar figure, and the label values are the gate names, not an amount.
"""
from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace

import pytest

import bot.web.dashboard_server as ds
from bot.core.system_health import SystemHealthMonitor


def _series(text: str) -> dict[str, str]:
    """name{labels} -> value for every sample line."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        out[key] = value
    return out


_SAMPLE = re.compile(r'^[a-z_]+(\{.*\})? -?[0-9]+(\.[0-9]+)?(e[+-]?[0-9]+)?$')


def _assert_valid_exposition(text: str) -> None:
    """Every line is a comment or a finite number: a scraper rejects the WHOLE
    payload on one bad line, which would take the real series down with it."""
    for line in text.splitlines():
        assert line.startswith("#") or _SAMPLE.match(line), line


def _engine(**extra):
    eng = SimpleNamespace(
        health=SystemHealthMonitor(),
        portfolio=SimpleNamespace(open_positions=[]),
        risk=SimpleNamespace(circuit_breaker_active=False, consecutive_losses=0, stats={}),
    )
    for k, v in extra.items():
        setattr(eng, k, v)
    return eng


# ── scan latency ─────────────────────────────────────────────────────────────


def test_no_scan_record_means_no_scan_series():
    """An engine that has not completed a scan phase has measured nothing; the
    old way to say that on a gauge is a 0, and a 0 here reads as 'instant'."""
    out = ds._render_prometheus(_engine())
    assert "runeclaw_scan_" not in out


def test_a_completed_scan_reports_last_peak_and_cap():
    rec = {"last_s": 2.5, "peak_s": 7.25, "cap_s": 300.0, "timed_out": False,
           "last_timed_out": False}
    s = _series(ds._render_prometheus(_engine(_phase_durations={"scan": rec})))
    assert float(s["runeclaw_scan_latency_last_seconds"]) == 2.5
    assert float(s["runeclaw_scan_latency_peak_seconds"]) == 7.25
    assert float(s["runeclaw_scan_latency_cap_seconds"]) == 300.0
    assert s["runeclaw_scan_last_timed_out"] == "0.0"
    assert s["runeclaw_scan_timed_out"] == "0.0"


def test_a_scan_that_timed_out_omits_the_last_duration_because_it_is_a_floor():
    """`_record_phase_duration` stores the CAP for a phase it cancelled. That is
    'at least this long', not a reading, so publishing it as the last latency
    would be a measurement nobody took. The flag says why it is missing."""
    rec = {"last_s": 300.0, "peak_s": 300.0, "cap_s": 300.0, "timed_out": True,
           "last_timed_out": True}
    s = _series(ds._render_prometheus(_engine(_phase_durations={"scan": rec})))
    assert "runeclaw_scan_latency_last_seconds" not in s
    assert s["runeclaw_scan_last_timed_out"] == "1.0"
    assert s["runeclaw_scan_timed_out"] == "1.0"
    # The peak survives, with the help text that calls it a lower bound.
    assert "runeclaw_scan_latency_peak_seconds" in s
    assert "lower bound" in ds._render_prometheus(_engine(_phase_durations={"scan": rec}))


def test_a_later_success_does_not_clear_the_sticky_timeout_flag():
    rec = {"last_s": 1.0, "peak_s": 300.0, "cap_s": 300.0, "timed_out": True,
           "last_timed_out": False}
    s = _series(ds._render_prometheus(_engine(_phase_durations={"scan": rec})))
    assert float(s["runeclaw_scan_latency_last_seconds"]) == 1.0
    assert s["runeclaw_scan_timed_out"] == "1.0"
    assert s["runeclaw_scan_last_timed_out"] == "0.0"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "slow", True])
def test_an_unreadable_duration_is_omitted_not_zeroed(bad):
    rec = {"last_s": bad, "peak_s": bad, "cap_s": bad, "timed_out": False,
           "last_timed_out": False}
    out = ds._render_prometheus(_engine(_phase_durations={"scan": rec}))
    assert "runeclaw_scan_latency_last_seconds" not in out
    assert "runeclaw_scan_latency_peak_seconds" not in out
    assert "runeclaw_scan_latency_cap_seconds" not in out
    assert "None" not in out
    _assert_valid_exposition(out)


def test_the_scan_heartbeat_age_is_measured_or_absent():
    assert "runeclaw_scan_heartbeat_age_seconds" not in ds._render_prometheus(_engine())
    eng = _engine(_scan_heartbeat_mono=time.monotonic() - 12.0)
    age = float(_series(ds._render_prometheus(eng))["runeclaw_scan_heartbeat_age_seconds"])
    assert 11.5 <= age < 60.0


def test_a_broken_scan_source_leaves_the_other_series_standing():
    class _Boom(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("secret detail")

    out = ds._render_prometheus(_engine(_phase_durations=_Boom(scan=1)))
    assert "runeclaw_up 1" in out
    assert "secret detail" not in out


# ── risk rejections by gate ──────────────────────────────────────────────────


class _GateRisk:
    circuit_breaker_active = False
    consecutive_losses = 0
    stats: dict = {}

    def __init__(self, stats):
        self._stats = stats

    def gate_stats(self):
        return self._stats


def test_risk_rejections_are_labelled_by_gate():
    risk = _GateRisk({
        "Confidence": {"passed": 9, "failed": 3, "skipped": 0},
        "R:R": {"passed": 12, "failed": 0, "skipped": 0},
    })
    s = _series(ds._render_prometheus(_engine(risk=risk)))
    assert s['runeclaw_risk_gate_rejections_total{gate="Confidence"}'] == "3"
    # A gate evaluated and never failed is a MEASURED zero, and is emitted as one.
    assert s['runeclaw_risk_gate_rejections_total{gate="R:R"}'] == "0"


def test_no_gate_counters_means_no_gate_series():
    for risk in (_GateRisk({}), SimpleNamespace(gate_stats=lambda: None),
                 SimpleNamespace()):  # no gate_stats at all
        out = ds._render_prometheus(_engine(risk=risk))
        assert "runeclaw_risk_gate_rejections_total" not in out
        assert "runeclaw_up 1" in out


def test_an_unreadable_failure_count_is_dropped_not_zeroed():
    risk = _GateRisk({
        "Good": {"failed": 2},
        "NoCount": {"passed": 4},                 # no 'failed' key
        "Garbled": {"failed": "many"},
        "Blank ": {"failed": 1},                  # stripped to a name, kept
        "": {"failed": 5},                        # no name, no series
    })
    out = ds._render_prometheus(_engine(risk=risk))
    s = _series(out)
    assert s['runeclaw_risk_gate_rejections_total{gate="Good"}'] == "2"
    assert 'runeclaw_risk_gate_rejections_total{gate="NoCount"}' not in s
    assert 'runeclaw_risk_gate_rejections_total{gate="Garbled"}' not in s
    assert 'runeclaw_risk_gate_rejections_total{gate=""}' not in s


def test_a_gate_name_cannot_break_out_of_its_label():
    risk = _GateRisk({'evil"} 1\nrunegate_injected 9 #': {"failed": 1}})
    out = ds._render_prometheus(_engine(risk=risk))
    assert "\nrunegate_injected" not in out
    _assert_valid_exposition(out)


def test_the_gate_label_set_is_bounded():
    risk = _GateRisk({f"gate{i:03d}": {"failed": i} for i in range(ds._MAX_GATE_LABELS + 40)})
    out = ds._render_prometheus(_engine(risk=risk))
    assert out.count("runeclaw_risk_gate_rejections_total{") == ds._MAX_GATE_LABELS


def test_the_real_risk_engine_after_the_red_team_attacked_it(tmp_path):
    """Driven, not stubbed: the counters are the ones `RiskEngine.evaluate`
    keeps, and the red team's 30 scenarios reject most of what they send."""
    from bot.core.red_team import RedTeamEngine
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine

    portfolio = PortfolioTracker()
    risk = RiskEngine(portfolio, state_file=str(tmp_path / "state.json"))
    report = RedTeamEngine(risk, portfolio).run_stress_test()
    assert report.total_scenarios > 0
    stats = risk.gate_stats()
    failing = sorted(g for g, rec in stats.items() if rec.get("failed"))
    assert failing, "the red team rejected nothing, so this fixture measures nothing"

    out = ds._render_prometheus(_engine(risk=risk))
    s = _series(out)
    for gate in failing:
        key = f'runeclaw_risk_gate_rejections_total{{gate="{ds._label_value(gate)}"}}'
        assert int(s[key]) == stats[gate]["failed"], gate
    assert "# TYPE runeclaw_risk_gate_rejections_total counter" in out


# ── swarm workers ────────────────────────────────────────────────────────────


def test_no_swarm_means_no_swarm_series():
    """SWARM_ENABLED is off by default. 'No swarm' is not 'zero workers'."""
    out = ds._render_prometheus(_engine(_swarm=None))
    assert "runeclaw_swarm_" not in out
    assert "runeclaw_swarm_" not in ds._render_prometheus(_engine())


def test_a_running_swarm_reports_the_workers_that_are_running():
    swarm = SimpleNamespace(status=lambda: {
        "halted": False,
        "workers": {"scanner": True, "analyst": True, "risk": True,
                    "executor": False, "sentinel": True, "coordinator": True},
    })
    s = _series(ds._render_prometheus(_engine(_swarm=swarm)))
    assert s["runeclaw_swarm_workers_active"] == "5.0"
    assert s["runeclaw_swarm_workers_total"] == "6.0"
    assert s["runeclaw_swarm_halted"] == "0.0"


def test_the_halt_latch_and_a_stopped_swarm_are_both_visible():
    swarm = SimpleNamespace(status=lambda: {
        "halted": True, "workers": {"scanner": False, "risk": False}})
    s = _series(ds._render_prometheus(_engine(_swarm=swarm)))
    assert s["runeclaw_swarm_workers_active"] == "0.0"   # measured: all stopped
    assert s["runeclaw_swarm_halted"] == "1.0"


def test_an_unreadable_swarm_is_omitted():
    def boom():
        raise RuntimeError("no")

    for status in (boom, lambda: {"halted": False}, lambda: {"workers": "x"}):
        out = ds._render_prometheus(_engine(_swarm=SimpleNamespace(status=status)))
        assert "runeclaw_swarm_workers_active" not in out
        assert "runeclaw_up 1" in out


def test_the_real_coordinator_is_counted():
    from bot.core.swarm import SwarmCoordinator

    async def drive():
        coord = SwarmCoordinator(heartbeat_interval_s=0.05, heartbeat_timeout_s=5.0)
        eng = _engine(_swarm=coord)
        before = _series(ds._render_prometheus(eng))
        await coord.start()
        try:
            during = _series(ds._render_prometheus(eng))
        finally:
            await coord.stop()
        after = _series(ds._render_prometheus(eng))
        return before, during, after

    before, during, after = asyncio.run(drive())
    assert before["runeclaw_swarm_workers_active"] == "0.0"
    assert float(during["runeclaw_swarm_workers_active"]) == float(during["runeclaw_swarm_workers_total"]) > 0
    assert after["runeclaw_swarm_workers_active"] == "0.0"


# ── all of it, unauthenticated ───────────────────────────────────────────────


def test_the_new_series_carry_no_financial_word():
    rec = {"last_s": 1.0, "peak_s": 2.0, "cap_s": 300.0, "timed_out": False,
           "last_timed_out": False}
    risk = _GateRisk({"Confidence": {"failed": 1}, "Drawdown": {"failed": 2}})
    swarm = SimpleNamespace(status=lambda: {"halted": False, "workers": {"risk": True}})
    out = ds._render_prometheus(_engine(
        _phase_durations={"scan": rec}, _scan_heartbeat_mono=time.monotonic(),
        risk=risk, _swarm=swarm))
    low = out.lower()
    for forbidden in ("equity", "pnl", "balance", "usd", "profit", "$"):
        assert forbidden not in low, forbidden
    assert "None" not in out
    _assert_valid_exposition(out)

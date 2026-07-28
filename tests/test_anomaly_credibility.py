"""The sentinel's credibility fix — a breakdown needs something to break.

Operator report (live pages, 2026-07-27): two CORRELATION_BREAKDOWN alerts at
max severity 1.00 advising HALT_NEW_TRADES, for pairs of unrelated small-caps
whose correlation sat near zero — normal alt behaviour — plus a third page
within 69 seconds for the same underlying move. The old check compared every
tracked symbol against every other with NO baseline requirement, and its
severity formula saturated at 1.00 the moment a pair crossed zero. An alarm
that cries max severity for Tuesday trains the operator to ignore the day a
genuinely correlated book breaks down.

The contract now:
- a pair alerts ONLY when its PRIOR-window correlation was >= 0.6 (something
  existed to break) and its current correlation fell below 0.3;
- severity scales with the DROP from baseline (a tight pair inverting is
  1.0; dipping just under the threshold is not);
- insufficient history (< 2 windows) is silence, never a guess;
- one symbol decorrelating from many peers clusters into ONE page naming
  the hub, instead of one page per counterpart;
- visual tiers: only >= 0.8 severity wears the siren/red; 0.3-0.8 orange;
  below 0.3 a yellow "NOTED".
"""

import math

import numpy as np
import pytest

from bot.core.black_swan import (
    _CORRELATION_BASELINE, _CORRELATION_THRESHOLD, _CORRELATION_WINDOW,
    AnomalyAlert, AnomalyType, BlackSwanDetector,
)


def _feed(det, symbol, prices):
    for p in prices:
        det.update(symbol, float(p), volume=1000.0, atr=0.0)


def _corr_alerts(det, symbol):
    return [a for a in det.check_all()
            if a.anomaly_type == AnomalyType.CORRELATION_BREAKDOWN and a.symbol == symbol]


def _series(n, fn):
    return [100.0 + fn(i) for i in range(n)]


def test_never_correlated_pairs_are_silent():
    """Two unrelated small-caps near zero correlation is Tuesday, not an
    anomaly — exactly the operator's PUMP/NIL page."""
    det = BlackSwanDetector()
    rng = np.random.default_rng(7)
    n = 2 * _CORRELATION_WINDOW
    _feed(det, "PUMP/USDT", _series(n, lambda i: math.sin(i * 1.3) * 3))
    _feed(det, "NIL/USDT", [100.0 + v for v in np.cumsum(rng.normal(0, 1, n))])
    assert _corr_alerts(det, "PUMP/USDT") == []
    assert _corr_alerts(det, "NIL/USDT") == []


def test_a_real_breakdown_fires_and_names_the_baseline():
    """Tightly correlated for the prior window, inverted in the current one —
    the thing the detector exists for."""
    det = BlackSwanDetector()
    w = _CORRELATION_WINDOW
    base = list(range(w))                       # rising together
    a = [100.0 + i for i in base] + [120.0 + i for i in range(w)]        # keeps rising
    b = [200.0 + 2 * i for i in base] + [240.0 - 2 * i for i in range(w)]  # inverts
    _feed(det, "AAA/USDT", a)
    _feed(det, "BBB/USDT", b)
    alerts = _corr_alerts(det, "AAA/USDT")
    assert len(alerts) == 1
    al = alerts[0]
    assert al.peer == "BBB/USDT"
    assert al.metric_value < _CORRELATION_THRESHOLD
    assert "was" in al.description, "the baseline is stated, not implied"
    assert al.severity > 0.8, "a tight pair inverting is the severe case"
    assert al.recommended_action == "HALT_NEW_TRADES"


def test_severity_scales_with_the_drop_not_the_threshold():
    """Crossing just under the threshold from a modest baseline must NOT
    saturate at 1.0 (the old formula maxed the moment corr <= 0)."""
    det = BlackSwanDetector.__new__(BlackSwanDetector)
    # severity via the same helper the check uses: drop over ceiling 1.0
    assert det._severity_from_ratio(0.35, floor=0.0, ceiling=1.0) == pytest.approx(0.35)
    assert det._severity_from_ratio(1.2, floor=0.0, ceiling=1.0) == 1.0


def test_insufficient_history_is_silence_never_a_guess():
    det = BlackSwanDetector()
    w = _CORRELATION_WINDOW
    # one full window only — no PRIOR window exists to define a baseline
    _feed(det, "AAA/USDT", _series(w, lambda i: i))
    _feed(det, "BBB/USDT", _series(w, lambda i: -i))
    assert _corr_alerts(det, "AAA/USDT") == []


def test_monitor_clusters_one_hub_into_one_page():
    """Two breakdowns naming the same peer collapse into one clustered alert
    — one market event, one page."""
    from bot.core.proactive_monitor import ProactiveMonitor

    class _Det:
        active_alerts = [
            AnomalyAlert(anomaly_type=AnomalyType.CORRELATION_BREAKDOWN, severity=0.9,
                         symbol="PUMP/USDT", description="d1", metric_value=-0.19,
                         threshold=0.3, recommended_action="HALT_NEW_TRADES", peer="NIL/USDT"),
            AnomalyAlert(anomaly_type=AnomalyType.CORRELATION_BREAKDOWN, severity=0.85,
                         symbol="DFEN/USDT:USDT", description="d2", metric_value=-0.06,
                         threshold=0.3, recommended_action="HALT_NEW_TRADES", peer="NIL/USDT"),
            AnomalyAlert(anomaly_type=AnomalyType.PRICE_ACCELERATION, severity=0.03,
                         symbol="VELVET/USDT", description="d3", metric_value=3.2,
                         threshold=3.0, recommended_action="REDUCE_POSITION_SIZE"),
        ]

    class _Eng:
        black_swan = _Det()

    mon = ProactiveMonitor.__new__(ProactiveMonitor)
    mon.engine = _Eng()
    alerts = mon._check_black_swan()
    bodies = [a.body for a in alerts]
    clustered = [a for a in alerts if "clustered" in a.body]
    assert len(clustered) == 1, "two same-hub breakdowns -> ONE page"
    assert "NIL/USDT" in clustered[0].body
    assert "DFEN/USDT:USDT" in clustered[0].body and "PUMP/USDT" in clustered[0].body
    # the tiny price-acceleration page survives, but WITHOUT the siren
    tiny = [a for a in alerts if "VELVET" in a.body]
    assert len(tiny) == 1
    assert "\U0001f6a8" not in tiny[0].body, "0.03 severity never wears the siren"
    assert "NOTED" in tiny[0].body
    assert "\U0001f7e1" in tiny[0].body, "sub-0.3 severity shows the yellow tier"
    # dedup: the cluster keys on the hub, so the storm cannot re-page per pair
    assert clustered[0].dedup_key == "bs_CORR_HUB_NIL/USDT"


def test_severe_single_alert_keeps_the_siren():
    from bot.core.proactive_monitor import ProactiveMonitor

    class _Det:
        active_alerts = [
            AnomalyAlert(anomaly_type=AnomalyType.VOLATILITY_EXPLOSION, severity=0.95,
                         symbol="BTC/USDT", description="d", metric_value=4.0,
                         threshold=3.0, recommended_action="HALT_NEW_TRADES"),
        ]

    class _Eng:
        black_swan = _Det()

    mon = ProactiveMonitor.__new__(ProactiveMonitor)
    mon.engine = _Eng()
    alerts = mon._check_black_swan()
    assert len(alerts) == 1
    assert "\U0001f6a8" in alerts[0].body and "DETECTED" in alerts[0].body
    assert alerts[0].severity == "CRITICAL"

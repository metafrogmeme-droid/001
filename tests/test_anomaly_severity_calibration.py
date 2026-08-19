"""Everything scored 1.00, so the digest built in #89 never saw anything.

Eleven ANOMALY DETECTED cards in sixteen minutes on 2026-08-19, reported with
screenshots, every one of them severity 1.00 or 0.82.

#89's routing is right and was working: at/above `_HALT_SEVERITY` an alert
takes the severe path and gets its own card immediately; below it, everything
collapses into one digest with a 30-minute floor. Nothing was reaching the
digest because nothing scored below 0.8.

CORRELATION — an arithmetic defect, not a taste call.

    severity = clip(drop / 1.0),  drop = baseline - current

A correlation is bounded [-1, 1], so `drop` is bounded [0, 2]. THE SCALE
CEILINGED AT HALF THE RANGE IT MEASURES, and any pair falling from a 0.6+
baseline into negative territory saturated:

    LINK/USDT vs RTXSTOCK   0.724 -> -0.375   drop 1.099   1.00
    HBAR/USDT vs ACE        0.635 -> -0.551   drop 1.186   1.00
    ACE/USDT  vs NATGAS     0.700 -> -0.408   drop 1.108   1.00
    ACE/USDT  vs HBAR       0.700 -> -0.598   drop 1.298   1.00

ACE decorrelating from natural gas is not a market emergency; it scored what a
total inversion of a tightly-coupled pair would. The call site's own comment
said full severity meant "a genuinely tight pair inverting" — at ceiling 1.0 it
did not, since that is 1.0 -> -1.0, a drop of two.

SPREAD — empirical, and labelled as such. A spread ratio is unbounded, so no
arithmetic argument fixes its ceiling. What fixed it is that 8x was reached
constantly: BBSTOCK 8.4x and RTXSTOCK 10.6x both saturated, and those are
tokenized equities outside their market's hours, where a spread several times
baseline is what the instrument does.

A severity whose maximum is the common case carries no information. Red that
arrives every few minutes is read as decoration, which is exactly how the next
real one becomes invisible — the failure mode this repository names by name.

Nothing here changes trading. `engine.py` states the detector observes only and
`halt_recommended` is never acted on, so severity drives the card and the
routing and nothing else. Checked before touching a risk signal, not after.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.core.black_swan import (BlackSwanDetector, _CORRELATION_BASELINE,
                                 _CORRELATION_THRESHOLD, _HALT_SEVERITY,
                                 _SPREAD_FACTOR)

_sev = BlackSwanDetector._severity_from_ratio

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _corr(baseline: float, current: float) -> float:
    return _sev(baseline - current, floor=0.0, ceiling=2.0)


def _spread(ratio: float) -> float:
    return _sev(ratio, floor=_SPREAD_FACTOR, ceiling=_SPREAD_FACTOR * 10)


# ── the reported cards are no longer maximum severity ───────────────────────

@pytest.mark.parametrize("baseline,current,label", [
    (0.724, -0.375, "LINK/RTXSTOCK"),
    (0.635, -0.551, "HBAR/ACE"),
    (0.700, -0.408, "ACE/NATGAS"),
    (0.700, -0.598, "ACE/HBAR"),
])
def test_the_reported_decorrelations_now_reach_the_digest(baseline, current, label):
    """The verbatim numbers off the screenshots. Each scored 1.00, took the
    severe path, and paged on its own."""
    sev = _corr(baseline, current)
    assert sev < _HALT_SEVERITY, (
        f"{label} still scores {sev:.2f} — at or above {_HALT_SEVERITY} it "
        "bypasses the digest and pages individually, which is the flood")
    assert sev > 0.3, (
        f"{label} scores {sev:.2f}, understating a real decorrelation — the "
        "fix must widen the scale, not mute the signal")


@pytest.mark.parametrize("ratio,label", [(8.4, "BBSTOCK"), (10.6, "RTXSTOCK")])
def test_the_reported_spread_blowouts_now_reach_the_digest(ratio, label):
    sev = _spread(ratio)
    assert sev < _HALT_SEVERITY, f"{label} at {ratio}x still pages on its own ({sev:.2f})"
    assert sev > 0.0, f"{label} at {ratio}x is no longer reported at all"


# ── the controls, which matter more than the fix ────────────────────────────

def test_a_genuine_inversion_is_still_severe():
    """Recalibrating so nothing is ever severe silences the alarm rather than
    tuning it, and would pass every assertion above."""
    assert _corr(0.95, -0.95) >= _HALT_SEVERITY, (
        "a tightly-coupled pair inverting completely is no longer severe — the "
        "scale was flattened instead of widened")
    assert _corr(1.0, -1.0) == 1.0, "the top of the correlation scale is unreachable"


def test_a_genuine_liquidity_failure_is_still_severe():
    assert _spread(_SPREAD_FACTOR * 10) == 1.0
    assert _spread(30.0) >= _HALT_SEVERITY


def test_the_quietest_reportable_event_sits_at_the_bottom():
    """A pair grazing the alert threshold, and a spread just over its factor,
    are the smallest things the detector can say."""
    assert _corr(_CORRELATION_BASELINE, _CORRELATION_THRESHOLD) < 0.3
    assert _spread(_SPREAD_FACTOR * 1.1) < 0.3


def test_the_correlation_scale_covers_the_metrics_whole_range():
    """Pinned as a property of the maths, not as a number: correlation is
    bounded [-1, 1], so the largest possible drop is 2.0. A ceiling below that
    makes the top of the scale reachable by ordinary moves."""
    assert _corr(1.0, -1.0) == 1.0
    assert _corr(0.6, -0.6) < 1.0, "a 0.6 pair merely flipping sign saturates again"


# ── the wiring, which none of the above can see ─────────────────────────────

def test_the_detector_passes_the_widened_ceilings():
    """Every test above passes the ceiling in itself, which proves nothing
    about what the detector passes. Checked at the call sites — the only place
    it is visible."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "black_swan.py").read_text(encoding="utf-8"))

    i = src.index("AnomalyType.CORRELATION_BREAKDOWN")
    assert "ceiling=2.0" in src[max(0, i - 1200):i], (
        "correlation severity is back on a ceiling below the metric's range, "
        "so ordinary decorrelations score 1.00 again")

    j = src.index("AnomalyType.SPREAD_WIDENING")
    assert "_SPREAD_FACTOR * 10" in src[max(0, j - 1200):j], (
        "the spread ceiling is back where routine tokenized-equity spreads "
        "saturate it")


def test_severity_still_drives_nothing_but_the_message():
    """THE SAFETY CHECK, made before the change rather than after. Recalibrating
    would be a risk decision if anything acted on `halt_recommended`; nothing
    does, so it is a display decision."""
    import subprocess

    out = subprocess.run(
        ["grep", "-rn", "recommended_action", "--include=*.py", "bot/"],
        cwd=ROOT, capture_output=True, text=True).stdout
    for line in out.splitlines():
        path = line.split(":", 1)[0]
        assert path.endswith(("black_swan.py", "proactive_monitor.py")), (
            f"{path} consumes recommended_action — severity now has a "
            "consequence beyond the card, and this calibration is a risk "
            "change that needs re-deciding")


# ── end to end: the reported set becomes one digest ─────────────────────────

class _Anom:
    def __init__(self, kind, symbol, severity, peer=None):
        self.anomaly_type = kind
        self.symbol = symbol
        self.severity = severity
        self.peer = peer
        self.description = f"{symbol} {kind}"
        self.recommended_action = "MONITOR"


def test_the_reported_flood_collapses_to_a_single_message():
    """The whole point, driven through the real routing rather than reasoned
    about: the eight alerts from the screenshots, at their recalibrated
    severities, produce one digest instead of eight cards."""
    from bot.core.proactive_monitor import ProactiveMonitor

    reported = [
        _Anom("CORRELATION_BREAKDOWN", "LINK/USDT", _corr(0.724, -0.375), peer="RTXSTOCK/USDT"),
        _Anom("CORRELATION_BREAKDOWN", "HBAR/USDT", _corr(0.635, -0.551), peer="ACE/USDT"),
        _Anom("CORRELATION_BREAKDOWN", "ACE/USDT", _corr(0.700, -0.408), peer="NATGAS/USDT"),
        _Anom("CORRELATION_BREAKDOWN", "ACE/USDT", _corr(0.700, -0.598), peer="HBAR/USDT"),
        _Anom("SPREAD_WIDENING", "BBSTOCK/USDT", _spread(8.4)),
        _Anom("SPREAD_WIDENING", "RTXSTOCK/USDT", _spread(10.6)),
        _Anom("SPREAD_WIDENING", "SKYAI/USDT", _spread(4.0)),
        _Anom("SPREAD_WIDENING", "GME/USDT", _spread(6.0)),
    ]
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = type("E", (), {"black_swan": type("D", (), {"active_alerts": reported})()})()
    out = m._check_black_swan()

    assert len(out) == 1, (
        f"{len(out)} messages for the set that produced eleven cards; the "
        "digest is still being bypassed")
    assert out[0].severity == "WARNING", "the digest is announcing itself as CRITICAL"
    body = out[0].body
    for sym in ("BBSTOCK/USDT", "RTXSTOCK/USDT", "SKYAI/USDT", "GME/USDT"):
        assert sym in body, f"the digest does not name {sym} — worse than the flood"


def test_a_real_emergency_still_breaks_out_of_the_digest():
    """THE CONTROL for the end-to-end case. A flash crash beside routine noise
    must still arrive as its own card."""
    from bot.core.proactive_monitor import ProactiveMonitor

    mixed = [
        _Anom("SPREAD_WIDENING", "SKYAI/USDT", _spread(4.0)),
        _Anom("SPREAD_WIDENING", "GME/USDT", _spread(6.0)),
        _Anom("FLASH_CRASH", "BTC/USDT", 0.97),
    ]
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = type("E", (), {"black_swan": type("D", (), {"active_alerts": mixed})()})()
    out = m._check_black_swan()

    severe = [a for a in out if a.severity == "CRITICAL"]
    assert len(severe) == 1, "the flash crash was digested beside the noise"
    assert "BTC/USDT" in severe[0].body
    assert "SKYAI/USDT" not in severe[0].body, (
        "an advisory alert was folded into the severe card")

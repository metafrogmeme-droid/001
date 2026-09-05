"""The drawdown an operator READS must be the drawdown the breaker ENFORCES.

Traced 2026-07-28, from a question about whether a $45 withdrawal could hurt
anything. Three findings:

1. GOOD — the live gate is correct. _evaluate_locked measures against a LIVE
   high-water mark (_live_equity_peak) when live_equity is supplied, with a
   comment recording that using the paper snapshot here was an audit CRITICAL.
   A genuine live loss does trip it.

2. BAD — drawdown_status() returned the PAPER figure while its own docstring
   promised "the LIVE drawdown the breaker actually gates on". In pure-live
   operation the paper snapshot never moves, so the reporter could say ~0%
   while the gate refused trades at 9%.

3. BAD — /status rendered that same paper figure. The number on the card and
   the number in the gate were different quantities with the same label.

(2) and (3) are the same defect at two layers: a control that cannot be
observed. The gate itself is left alone — it was right.

Note for operators, since it follows from (1): the live peak is a plain
high-water mark with no transfer awareness, so a WITHDRAWAL reads exactly
like a loss. Moving 9% of equity out of a live account will trip a 7% cap.
The existing remedy is /reset, which re-seeds the peak from the next live
evaluation. This test file pins the reporting so that situation is at least
VISIBLE rather than silent.
"""

from types import SimpleNamespace

import pytest

from bot.risk.risk_engine import RiskEngine


class _Portfolio:
    def __init__(self, paper_dd=0.0):
        self._dd = paper_dd

    def snapshot(self):
        return SimpleNamespace(current_drawdown_pct=self._dd,
                               max_drawdown_pct=self._dd)


def _engine(paper_dd=0.0, peak=0.0, last_live=None):
    eng = RiskEngine.__new__(RiskEngine)
    eng._portfolio = _Portfolio(paper_dd)
    eng._live_equity_peak = peak
    eng._last_live_equity = last_live
    eng._effective_max_drawdown_pct = lambda: 7.0
    eng._live_hardening = lambda: True
    return eng


def test_live_mode_reports_the_live_drawdown_not_the_paper_one():
    # The reported case: peak 495.69, equity 450.69 after a $45 withdrawal.
    st = _engine(paper_dd=0.0, peak=495.69, last_live=450.69).drawdown_status()
    assert st["drawdown_source"] == "live"
    assert st["drawdown_pct"] == pytest.approx(9.08, abs=0.02)
    assert st["paper_drawdown_pct"] == 0.0, "the paper figure is kept, but not headline"
    assert st["live_equity_peak"] == pytest.approx(495.69)


def test_the_headline_number_is_the_one_that_would_trip_the_cap():
    st = _engine(paper_dd=0.0, peak=495.69, last_live=450.69).drawdown_status()
    assert st["drawdown_pct"] >= st["effective_limit_pct"], (
        "9.08% against a 7% cap must be READABLE as over the limit — the whole "
        "point is that an operator can see why trading is blocked")


def test_without_a_live_evaluation_it_says_paper_and_uses_the_paper_number():
    st = _engine(paper_dd=3.5).drawdown_status()
    assert st["drawdown_source"] == "paper"
    assert st["drawdown_pct"] == pytest.approx(3.5)
    assert st["live_drawdown_pct"] is None
    assert st["live_equity_peak"] is None


def test_equity_above_the_peak_never_reports_a_negative_drawdown():
    st = _engine(peak=400.0, last_live=460.0).drawdown_status()
    assert st["drawdown_pct"] == 0.0, "a gain is not a negative drawdown"


def test_a_zero_or_missing_peak_falls_back_to_paper():
    for peak, last in ((0.0, 450.0), (495.0, None), (0.0, None)):
        st = _engine(paper_dd=2.0, peak=peak, last_live=last).drawdown_status()
        assert st["drawdown_source"] == "paper", f"peak={peak} last={last}"
        assert st["drawdown_pct"] == pytest.approx(2.0)


def test_the_reporter_never_raises():
    eng = RiskEngine.__new__(RiskEngine)
    eng._portfolio = SimpleNamespace(snapshot=lambda: (_ for _ in ()).throw(RuntimeError()))
    assert eng.drawdown_status() == {}, "best-effort: empty, never an exception"


# ── the wiring ────────────────────────────────────────────────────────────

def test_evaluate_records_the_live_equity_it_gated_on():
    from pathlib import Path
    src = Path("bot/risk/risk_engine.py").read_text(encoding="utf-8")
    assert "self._last_live_equity = live_equity" in src, \
        "the reporter needs the same equity the gate used, or it cannot agree"
    # and a breaker reset clears it alongside the peak
    block = src[src.index("self._live_equity_peak = 0.0"):]
    assert "self._last_live_equity = None" in block[:400], \
        "re-seeding the peak must also drop the stale equity behind it"


def test_status_card_renders_the_enforced_number_in_live_mode():
    """DRIVEN, not grepped.

    This asserted the literal `_dd.get("drawdown_source") == "live"` and the
    byte offset of the line above it, so it failed the moment the read moved
    into a shared helper — while every behaviour it names was untouched, and
    one it could not name (whether the reader is TOLD which number it got) was
    the actual defect. `resolve_display_drawdown` is the seam; driving it
    settles the contract and no rewrite of the call site can fake it.
    """
    from bot.formatters.drawdown_card import resolve_display_drawdown as _r

    live = {"drawdown_pct": 9.0, "drawdown_source": "live",
            "effective_limit_pct": 7.0}

    # The live figure overrides the paper one, and brings its own limit.
    assert _r(3.1, live, 5.0) == (9.0, "live", 7.0)

    # Fail-safe: an unreadable gate KEEPS the paper number rather than
    # blanking the line — but says which one it is.
    assert _r(3.1, None, 7.0) == (3.1, "paper", 7.0)
    assert _r(3.1, {}, 7.0) == (3.1, "paper", 7.0)

    # A paper reading from the gate is the claim we already hold; it must not
    # be promoted to "live".
    assert _r(3.1, dict(live, drawdown_source="paper"), 5.0)[1] == "paper"

    # `if state.max_drawdown_pct else 0.0` was falsy: an unreadable paper
    # figure became a measured 0.0%, the calmest possible reading, on the
    # control that halts real-money losses.
    assert _r(None, None, 7.0) == (None, None, 7.0)
    # ...while a MEASURED zero survives, because a flat book is a reading.
    assert _r(0.0, None, 7.0) == (0.0, "paper", 7.0)

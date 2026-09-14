"""The self-audit's NOT DISTINGUISHED said one thing about two different facts.

Live, 2026-09-14, both proposals came back:

    ⬜ NOT DISTINGUISHED — this benchmark returned the baseline's figures
    unchanged, so it did not exercise the change.

SYMBOL_LOSS_STREAK_THRESHOLD is on the benchmark path (backtest/engine.py
reads it) and the dataset simply never built a three-loss streak on one
symbol — another dataset could distinguish it. OF_MAX_SPREAD_BPS is not on
the path at all: the backtest sets `order_flow = None` unless
`use_recorded_order_flow`, `run_benchmark` never passes it, and even switched
on `RecordedOrderFlow` reads no spread. "Did not exercise the change" reads
as *try another night*; for that flag no night can come.

Three values: measured, not-distinguished-on-this-dataset, not-measurable-
here. And the evidence now says whether the brain was answering, because a
window traded by the rule engine is not evidence about the AI path.
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

from bot.core import self_audit as sa
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _render(flag, measured, baseline=None):
    baseline = baseline or {"return_pct": 3.14, "pf": 1.87, "trades": 39}
    ev = {"summary": {"n": 40, "scored": 40, "win_rate": 0.18, "pf": 0.27,
                      "net_pnl": -35.11}}
    results = [{"flag": flag, "value": 100, "rationale": "r", "measured": measured}]
    return sa.SelfAudit.render_report(ev, results, baseline=baseline,
                                      dataset="alts_1h")


# ── three verdicts, not two ────────────────────────────────────────────────

def test_a_blind_flag_says_not_measurable_and_why():
    out = _render("OF_MAX_SPREAD_BPS", {"return_pct": 3.14, "pf": 1.87, "trades": 39})
    assert "NOT MEASURABLE HERE" in out
    assert "OrderFlowConfig" in out
    assert "another night will not answer it" in out
    assert "NOT DISTINGUISHED" not in out


def test_a_blind_flag_is_not_measurable_even_when_its_run_failed():
    """The fact that sends the operator elsewhere outranks the run's outcome."""
    out = _render("OF_MIN_DEPTH_USD", {})
    assert "NOT MEASURABLE HERE" in out
    assert "NOT VERIFIED" not in out


def test_a_measurable_flag_that_matched_the_baseline_is_not_distinguished_ON_THIS_DATASET():
    out = _render("SYMBOL_LOSS_STREAK_THRESHOLD",
                  {"return_pct": 3.14, "pf": 1.87, "trades": 39})
    assert "NOT DISTINGUISHED ON THIS DATASET" in out
    assert "A different dataset or window may" in out
    assert "NOT MEASURABLE" not in out


def test_a_measurable_flag_with_a_real_difference_is_still_measured():
    out = _render("SYMBOL_LOSS_STREAK_THRESHOLD",
                  {"return_pct": 4.00, "pf": 2.10, "trades": 41})
    assert "measured +4.00%" in out
    assert "NOT " not in out.split("SYMBOL_LOSS_STREAK_THRESHOLD", 1)[1].split("Apply:")[0]


# ── the table is true of the code, in both directions ──────────────────────

def test_every_blind_flag_is_allowlisted():
    """A blind entry for a flag nobody can propose is a stale row."""
    for flag in sa.BENCHMARK_BLIND:
        assert flag in sa.ALLOWED_FLAGS, flag


def test_the_blind_flags_really_are_off_the_benchmark_path():
    """The structural claim, checked against the source rather than asserted:
    the two blind flags are read ONLY by order_flow.py; the backtest sets
    order_flow to None unless a flag the benchmark runner never passes; and
    RecordedOrderFlow, the one alternative, reads neither spread nor depth."""
    bt = code_only((ROOT / "bot/backtest/engine.py").read_text())
    assert re.search(r"order_flow\s*=\s*None", bt), "the backtest starts with no order flow"
    assert "use_recorded_order_flow" in bt
    rec = code_only((ROOT / "bot/backtest/recorded_order_flow.py").read_text())
    assert "spread" not in rec.lower() and "depth" not in rec.lower(), \
        "RecordedOrderFlow grew a spread/depth reading — OF_* may be measurable now; re-derive BENCHMARK_BLIND"
    audit = code_only((ROOT / "bot/core/self_audit.py").read_text())
    argv = audit[audit.index("def run_benchmark"):]
    argv = argv[:argv.index("def ", 20)]
    assert "use-recorded-order-flow" not in argv, \
        "run_benchmark now passes recorded order flow — re-derive BENCHMARK_BLIND"
    for flag in sa.BENCHMARK_BLIND:
        readers = [p for p in (ROOT / "bot").rglob("*.py")
                   if flag in code_only(p.read_text())
                   and p.name not in ("self_audit.py", "config.py")]
        assert readers and all(p.name == "order_flow.py" for p in readers), \
            f"{flag} is read outside order_flow.py: {readers} — re-derive BENCHMARK_BLIND"


def test_a_measurable_flag_is_not_in_the_blind_table():
    """The other direction: a flag the backtest DOES read must not be called
    blind, or the card would tell the operator to stop waiting for a verdict
    the harness can give."""
    bt = code_only((ROOT / "bot/backtest/engine.py").read_text())
    assert "symbol_loss_streak_threshold" in bt
    assert "SYMBOL_LOSS_STREAK_THRESHOLD" not in sa.BENCHMARK_BLIND


# ── the evidence says whether the brain was answering ──────────────────────

def _engine(analyzer):
    return SimpleNamespace(live_executor=SimpleNamespace(_closed_trades=[]),
                           risk=None, analyzer=analyzer)


def test_the_evidence_carries_the_brain_state():
    health = {"degraded_streak": 9, "degraded_seconds": 540.0,
              "last_ok_seconds_ago": None, "last_error": "x", "chain_walk": None}
    ev = sa.SelfAudit().gather_evidence(_engine(SimpleNamespace(llm_health=lambda: health)))
    assert ev["brain"]["state"] == "degraded"
    assert ev["brain"]["degraded_streak"] == 9
    assert ev["brain"]["degraded_seconds"] == 540.0


def test_a_healthy_brain_reads_healthy_and_an_untested_one_untested():
    ok = {"degraded_streak": 0, "degraded_seconds": 0.0, "last_ok_seconds_ago": 30.0}
    assert sa.SelfAudit().gather_evidence(
        _engine(SimpleNamespace(llm_health=lambda: ok)))["brain"]["state"] == "healthy"
    untested = {"degraded_streak": 0, "degraded_seconds": 0.0, "last_ok_seconds_ago": None}
    assert sa.SelfAudit().gather_evidence(
        _engine(SimpleNamespace(llm_health=lambda: untested)))["brain"]["state"] == "untested"


def test_an_unreadable_brain_is_null_not_healthy():
    def boom():
        raise RuntimeError("analyzer gone")
    assert sa.SelfAudit().gather_evidence(_engine(SimpleNamespace(llm_health=boom)))["brain"] is None
    assert sa.SelfAudit().gather_evidence(_engine(None))["brain"] is None
    assert sa.SelfAudit().gather_evidence(_engine(SimpleNamespace()))["brain"] is None


def test_the_prompt_tells_the_model_what_brain_means():
    assert "`brain`" in sa._SYSTEM_PROMPT
    assert "rule engine" in sa._SYSTEM_PROMPT
    assert "`null` means the brain state could not be read" in sa._SYSTEM_PROMPT

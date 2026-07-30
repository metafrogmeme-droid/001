"""
Interactive-scan timeout diagnostic hint (live incident 2026-07-11).

Right after the operator's model-id change, /latest_signal timed out twice in
a row with only "Scan is taking longer than usual" — no way to tell a failing
LLM (bad model id burning the fallback chain per symbol) from exchange
throttling. The timeout message now appends a brain-health hint from
Analyzer.llm_health().

Follow-up (live incident 2026-07-29). The healthy branch used to conclude
"the slowness is likely exchange/data latency, not the AI" — a VERDICT
inferred from a single negative. It said exactly that while individual
analyses were hanging long enough to blow the 300s tick cap thirty-seven
times running, sending the operator to look at the exchange for a problem
that was not there. A green health check rules one cause out; it never names
the cause. The hint now reports what is measured, including the engine's own
analysis-timeout record when one is recent.
"""
import time
from types import SimpleNamespace

from bot.skills.telegram_handler import (
    ANALYSIS_TIMEOUT_HINT_WINDOW_S, _recent_analysis_timeout,
    _scan_timeout_hint,
)


def _analyzer(streak):
    return SimpleNamespace(llm_health=lambda: {
        "degraded_streak": streak, "degraded_seconds": streak * 30.0,
        "last_ok_seconds_ago": None})


def test_degraded_brain_named_as_likely_cause():
    hint = _scan_timeout_hint(_analyzer(5))
    assert "LLM brain degraded" in hint
    assert "5 analyses" in hint
    assert "/llmstatus" in hint


def _engine(*, skipped=3, of=200, cap_s=90.0, age_s=10.0):
    return SimpleNamespace(_last_analysis_timeout={
        "skipped": skipped, "of": of, "cap_s": cap_s,
        "at": time.monotonic() - age_s})


def test_healthy_brain_rules_out_the_llm():
    hint = _scan_timeout_hint(_analyzer(0))
    assert "healthy" in hint
    # It may say what it RULED OUT...
    assert "rules out" in hint
    # ...and must point at the instrument that can actually name the cause.
    assert "/status" in hint


def test_healthy_brain_does_not_blame_the_exchange():
    """The regression this whole follow-up exists for. With no evidence about
    where the time went, the hint must not name a culprit."""
    hint = _scan_timeout_hint(_analyzer(0))
    for claim in ("likely exchange", "not the AI", "exchange/data latency"):
        assert claim not in hint, f"unfounded verdict survived: {claim!r}"


def test_a_recent_analysis_timeout_is_named():
    hint = _scan_timeout_hint(_analyzer(0), _engine(skipped=7, of=180))
    assert "Analyses are hanging" in hint
    assert "7 of 180" in hint
    assert "90s" in hint


def test_a_degraded_brain_still_wins_over_the_analysis_record():
    """Every provider failing explains hanging analyses; report the cause,
    not the symptom."""
    hint = _scan_timeout_hint(_analyzer(4), _engine())
    assert "LLM brain degraded" in hint
    assert "Analyses are hanging" not in hint


def test_a_stale_analysis_record_is_not_cited():
    old = _engine(age_s=ANALYSIS_TIMEOUT_HINT_WINDOW_S + 60)
    assert _recent_analysis_timeout(old) is None
    hint = _scan_timeout_hint(_analyzer(0), old)
    assert "Analyses are hanging" not in hint


def test_recent_record_helper_is_defensive():
    assert _recent_analysis_timeout(None) is None
    assert _recent_analysis_timeout(SimpleNamespace()) is None
    assert _recent_analysis_timeout(
        SimpleNamespace(_last_analysis_timeout={})) is None
    # a record with nothing skipped is not a record of anything
    assert _recent_analysis_timeout(_engine(skipped=0)) is None
    # a record with no timestamp cannot be judged recent
    assert _recent_analysis_timeout(SimpleNamespace(
        _last_analysis_timeout={"skipped": 1, "of": 2, "cap_s": 90})) is None


def test_missing_analyzer_is_silent():
    assert _scan_timeout_hint(None) == ""
    assert _scan_timeout_hint(SimpleNamespace()) == ""  # no llm_health attr
    # ...even when the engine HAS a record: without llm_health we cannot say
    # whether the fallback chain is the cause, and a half-diagnosis is worse
    # than none.
    assert _scan_timeout_hint(None, _engine()) == ""


def test_broken_health_is_silent():
    broken = SimpleNamespace(llm_health=lambda: (_ for _ in ()).throw(RuntimeError))
    assert _scan_timeout_hint(broken) == ""


def test_the_engine_records_the_analysis_timeout():
    """The hint can only report what the engine remembers."""
    from pathlib import Path
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    assert "self._last_analysis_timeout" in src
    assert '"skipped": len(_timed_out)' in src


def test_timeout_branch_uses_the_hint():
    import inspect
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._cmd_latest_signal)
    assert "_scan_timeout_hint" in src
    assert "self.engine" in src, "the hint needs the engine to read its record"


def test_the_hanging_symbols_are_named_when_recorded():
    # "1 of 70 gave up after 90s" reached the operator on 2026-07-30 without
    # the one fact that makes the diagnosis instant: WHICH symbol. The audit
    # log carried the names all along; the operator record dropped them.
    eng = SimpleNamespace(_last_analysis_timeout={
        "skipped": 1, "of": 70, "cap_s": 90.0,
        "at": time.monotonic() - 5.0,
        "symbols": ["PEPE/USDT"]})
    hint = _scan_timeout_hint(_analyzer(0), eng)
    assert "PEPE/USDT" in hint


def test_an_old_shape_record_without_symbols_still_renders():
    # Records written before the field existed (or by stubs) must not break
    # the hint — and must not invent names either.
    hint = _scan_timeout_hint(_analyzer(0), _engine(skipped=2, of=70))
    assert "Analyses are hanging" in hint
    assert "(" not in hint.split("each")[1].split(".")[0]


def test_symbol_names_are_escaped_and_capped():
    eng = SimpleNamespace(_last_analysis_timeout={
        "skipped": 9, "of": 70, "cap_s": 90.0,
        "at": time.monotonic() - 5.0,
        "symbols": ["<b>X</b>/USDT"] + [f"S{i}/USDT" for i in range(9)]})
    hint = _scan_timeout_hint(_analyzer(0), eng)
    assert "<b>X</b>" not in hint            # escaped, not injected
    assert "&lt;b&gt;X&lt;/b&gt;" in hint
    assert "S3/USDT" in hint and "S4/USDT" not in hint   # capped at 5 total


def test_the_engine_record_carries_the_symbols():
    import inspect
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._analyze_signals_batched)
    assert '"symbols": list(_timed_out[:5])' in src

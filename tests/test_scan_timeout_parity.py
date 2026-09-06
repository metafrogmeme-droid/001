"""Four commands can time out. One of them explained why.

`_scan_timeout_hint` was built after a live incident: two interactive scans
timed out right after a model-id change, and the operator could not tell LLM
failure from exchange throttling. The hint now reports MEASURED evidence —
the degraded-provider streak, and the engine's own analysis-timeout record
with the symbol and the stage it hung in.

It was wired into `/latest_signal` and nowhere else. `/deepscan` timed out
and pointed at `/status`. `/patterns` had no deadline at all: a stalled
fetch left a command that never answered and never failed.

This is the half-fixed-surface pattern that has now cost this codebase four
incidents (#990's PNG tile vs its text twin, #999's adoption detail, #1010's
/risk, and this). The instrument must reach every surface that can show the
symptom, or the surface it misses is the one the operator is looking at.
"""
from __future__ import annotations

from pathlib import Path

from tests.source_scan import code_only, handler_sources


def _src() -> str:
    # Every file the handler class is made of, plus the scan-hints leaf the
    # hint is defined in: the definition left for `scan_hints.py` and the
    # scan commands are leaving for a mixin, and a scan of one file reads
    # either move as the hint reaching one command fewer.
    files = (*handler_sources(), Path("bot/skills/scan_hints.py"))
    return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in files)


class TestEverySlowScanExplainsItself:
    def test_the_hint_reaches_more_than_one_command(self):
        assert _src().count("_scan_timeout_hint(") >= 4, (
            "one definition plus at least three call sites: /latest_signal, "
            "/deepscan and /patterns can all blow a deadline"
        )

    def test_deepscan_carries_the_measured_hint(self):
        src = _src()
        i = src.index("Deepscan hit its")
        assert "_scan_timeout_hint" in src[i:i + 700], (
            "/deepscan sent the operator to /status for a diagnosis this "
            "process already holds in memory"
        )

    def test_patterns_carries_the_measured_hint(self):
        src = _src()
        i = src.index("Pattern scan hit its")
        assert "_scan_timeout_hint" in src[i:i + 700]

    def test_the_status_pointer_is_only_the_fallback(self):
        # When the hint has nothing measured to report it returns "" — the
        # /status pointer is what fills that gap, not what replaces the hint.
        src = _src()
        i = src.index("Deepscan hit its")
        window = src[i:i + 700]
        assert window.index("_scan_timeout_hint") < window.index("/status")


class TestPatternsHasADeadline:
    def test_the_dispatch_is_bounded(self):
        src = _src()
        i = src.index('async def _cmd_patterns')
        body = src[i:i + 1400]
        assert "asyncio.wait_for" in body, (
            "an unbounded await on an exchange fetch is a command that can "
            "hang forever with no error and no timeout"
        )
        assert "asyncio.TimeoutError" in body

    def test_the_timeout_message_blames_the_budget_not_the_exchange(self):
        src = _src()
        i = src.index("Pattern scan hit its")
        window = src[i:i + 400]
        assert "not a diagnosis of the exchange" in window
        # The retired phrasing: a verdict inferred from a deadline expiring.
        assert "Exchange may be slow" not in window

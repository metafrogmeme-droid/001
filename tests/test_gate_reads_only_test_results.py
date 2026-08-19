"""The gate counted log records as failed tests, then absolved them.

On 2026-08-19 a preflight run ended:

    [gate] FAIL — 4 NEW failure(s) not in the baseline:
             ✗ bot.utils.website_sync:website_sync.py:139 Sync HTTP error 403 ...
             ✗ bot.utils.website_sync:website_sync.py:139 Sync HTTP error 503 ...
             ✗ tests/test_tier_gate_coverage.py::test_every_gated_skill_...
             ✗ tests/test_user_admission.py::test_every_bot_access_gate_...

Two of those four are not tests. They are log lines. The suite prints what the
code under test logs, a logging formatter emits its level first, and
`_parse_failures` accepted any line beginning ``ERROR `` — so

    ERROR    bot.utils.website_sync:website_sync.py:139 Sync HTTP error 403

became a "failed test" named ``bot.utils.website_sync:website_sync.py:139``,
which no test run can ever contain. Any module that logs at ERROR during the
suite could fail the gate, for a message that is often the code correctly
reporting a condition it was written to report.

TWO DEFECTS, AND THE SECOND ONE HID THE FIRST.

  PARSE.   ``startswith("ERROR ")`` is true of a log record. A pytest node id
           has a shape — a path ending in ``.py``, optionally ``::test`` — and
           the log line's second field is a dotted logger name containing a
           colon. The shape is the discriminator; the prefix is not.

  ABSOLVE. The flake filter re-runs each new failure alone and treated
           "not in the failures of the re-run" as "it passed". A node pytest
           CANNOT COLLECT prints no FAILED line at all, so every phantom
           sailed through as "passes alone (flaky/order-dependent)" and was
           ignored. Absent read as a pass — in the gate whose whole subject is
           not reporting one thing as another — and because it silently
           absolved them, nobody saw the parse bug for weeks. They only became
           visible when a tree moved mid-run and the filter switched off.

The same rule, twice, one level apart: an unreadable result is not a result.
A re-run that could not run is not a pass, and a line that is not a test result
is not a failure.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "scripts")
import ci_test_gate as gate  # noqa: E402


# ── parse: a log record is not a test result ────────────────────────────────

REAL_LOG_LINES = [
    'ERROR    bot.utils.website_sync:website_sync.py:139 Sync HTTP error 403 '
    '(not retryable): {"error":"Invalid bot secret"}',
    'ERROR    bot.utils.website_sync:website_sync.py:139 Sync HTTP error 503 '
    '(gave up after 1 attempts over 5.1s): {"error":"starting"}',
]


@pytest.mark.parametrize("line", REAL_LOG_LINES)
def test_a_captured_log_record_is_not_a_failed_test(line):
    """The two verbatim lines from the run that exposed this."""
    failed, _ = gate._parse_failures(line)
    assert failed == set(), (
        f"a log record was parsed as a failed test: {failed}")


def test_a_module_logging_at_error_cannot_fail_the_gate():
    """The general case, which is worse than the two lines above: any module
    that logs at ERROR during the suite could add a phantom failure, including
    modules logging exactly what they are supposed to."""
    out = "\n".join([
        "ERROR    bot.core.engine:engine.py:2504 exchange sync failed",
        "ERROR    root:conftest.py:12 teardown warning",
        "ERROR bot.risk.risk_engine:risk_engine.py:88 breaker tripped",
    ])
    assert gate._parse_failures(out)[0] == set()


def test_real_pytest_lines_are_all_still_collected():
    """THE CONTROL, and the one that matters most: a parser that dropped real
    failures to fix this would turn the gate into a rubber stamp — the exact
    trade this file exists to refuse."""
    out = "\n".join([
        "FAILED tests/test_x.py::test_y",
        "FAILED tests/test_c.py::test_d - AssertionError: boom",
        "FAILED tests/test_a.py::test_b[bot/skills/telegram_handler.py]",
        "ERROR tests/test_collect.py",
        "ERROR tests/test_e.py::test_f - fixture 'x' not found",
        "FAILED app/sub dir/test_g.py::test_h",   # a path this parser drops
    ])
    failed, _ = gate._parse_failures(out)
    assert "tests/test_x.py::test_y" in failed
    assert "tests/test_c.py::test_d" in failed
    assert "tests/test_a.py::test_b[bot/skills/telegram_handler.py]" in failed
    assert "tests/test_collect.py" in failed
    assert "tests/test_e.py::test_f" in failed


def test_internal_errors_are_still_seen():
    assert gate._parse_failures("INTERNALERROR> Traceback")[1] is True


# ── absolve: a re-run that could not run is not a pass ──────────────────────

class _Run:
    """One canned `subprocess.run` result."""

    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _verdict(node, run_result):
    """The gate's OWN decision, driven — not a copy of it.

    The first version of this helper reproduced the three-way branch here in
    the test file. It passed while the real branch was mutated back to the
    broken form, which is the whole reason `_rerun_verdict` was extracted.
    """
    node_failed, _ = gate._parse_failures(run_result.stdout + run_result.stderr)
    return gate._rerun_verdict(run_result.returncode, node, node_failed)


def test_a_node_pytest_cannot_collect_is_not_flaky():
    """Exit 4 is pytest's usage error — an unknown node id among them. It
    prints no FAILED line, so the old check filed it as passing."""
    verdict = _verdict(
        "bot.utils.website_sync:website_sync.py:139",
        _Run(4, "ERROR: not found: bot.utils.website_sync:website_sync.py:139"))
    assert verdict != "flaky", (
        "a node pytest could not even collect was absolved as flaky — the "
        "behaviour that hid the parse bug for weeks")


def test_no_tests_collected_is_not_a_pass():
    """Exit 5. A renamed or deleted test that still appears in a failure list
    must not be waved through."""
    assert _verdict("tests/test_gone.py::test_x", _Run(5, "no tests ran")) != "flaky"


def test_a_genuine_flake_is_still_ignored():
    """THE CONTROL. Order-dependent tests are real and the filter exists for
    them; failing closed on everything would make the gate unusable and it
    would be switched off, which is the failure mode this repo names by name."""
    assert _verdict("tests/test_flaky.py::test_x", _Run(0, "1 passed")) == "flaky"


def test_a_real_failure_is_still_confirmed():
    assert _verdict(
        "tests/test_real.py::test_x",
        _Run(1, "FAILED tests/test_real.py::test_x - AssertionError")) == "confirmed"


# ── the wiring, which none of the above can see ─────────────────────────────

def test_the_gate_actually_applies_both_rules():
    """Every test above calls `_parse_failures` directly or reproduces the
    filter's decision; none of them prove the gate uses either. Checked from
    the source, which is the only place the inline filter is visible."""
    from tests.source_scan import code_only
    import pathlib

    src = code_only(pathlib.Path("scripts/ci_test_gate.py").read_text(encoding="utf-8"))
    assert "_NODE_ID.match(node)" in src, (
        "_parse_failures no longer shape-checks the node, so log records are "
        "phantom failures again")
    assert "_rerun_verdict(r.returncode, node, node_failed)" in src, (
        "the flake filter no longer requires the re-run to have actually run")
    assert "could not be re-run alone" in src, (
        "an unjudgeable re-run is silently confirmed — say which of the three "
        "outcomes it was, or the next person debugs it blind")

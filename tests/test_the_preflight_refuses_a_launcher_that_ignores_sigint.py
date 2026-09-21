"""A preflight launched by a shell that ignores SIGINT cannot measure the test
gate, and it says so instead of measuring.

On 2026-09-21 a full preflight went red on ONE test the slice never touched --
`tests/test_deploy_smoke_guard.py::TestAnInterruptedCheckIsNotAVerdict::
test_a_signalled_check_reports_no_verdict[2]`, SIGINT -- in the full run and
when the flake filter re-ran it alone, on a file byte-identical to main. The
instrument was the launcher. The preflight had been started as
`(nohup python3 scripts/preflight.py > log 2>&1 &)`: a non-interactive shell
sets SIGINT (and SIGQUIT) to SIG_IGN for a background command, every child
inherits that, and a non-interactive bash cannot take it back ("signals
ignored upon entry to the shell cannot be trapped or reset"). So the smoke
guard's own `trap` never armed, the SIGINT the test sent was swallowed, the
guard FINISHED with 0, and the test asserted 3 != 0 -- the one verdict it
exists to prove is never reported, manufactured by the launcher, twenty-two
minutes in. Driven, `signal.getsignal(SIGINT)` read the default handler in the
foreground and SIG_IGN under that launcher, and every case passed in the
foreground.

That is `ruff_gate.check_version`'s CANNOT-CHECK distinction arriving through
the shell: the gate did not fail, its launcher had changed a fact the test
depends on. The preflight reads that fact ONCE, up front, beside the toolchain
versions, and files the test gate as CANNOT CHECK (its third state) rather than
running it into a red that reads as a regression. Every other gate still runs.

WHAT IS DELIBERATELY NARROW: only a signal the suite really sends to a child
refuses anything. `nohup` alone ignores SIGHUP and nothing in the suite depends
on SIGHUP, so a nohup'd foreground run is a launcher the suite can be measured
under. `TEST_GATE_SIGNALS` is pinned BOTH ways against what `tests/` sends, so
a test that starts sending SIGQUIT to a child tomorrow fails here rather than
producing the same false red for a signal the reading did not know.
"""
from __future__ import annotations

import ast
import re
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import preflight  # noqa: E402
import toolchain  # noqa: E402

TEST_GATE = ("Tests", "python3 scripts/ci_test_gate.py", ".")
RATCHET = ("Ratchet", "python3 scripts/ruff_gate.py", ".")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@contextmanager
def _disposition(sig, handler):
    """Set one disposition for the block and hand the previous one back.

    Written outside monkeypatch's bookkeeping on purpose -- a signal handler is
    process state, not an attribute -- and restored in a `finally` for the
    reason this repo records about the gateway secret that outlived its test.
    """
    prev = signal.signal(sig, handler)
    try:
        yield
    finally:
        signal.signal(sig, prev)


def _plan(monkeypatch, steps):
    monkeypatch.setattr(preflight, "steps", lambda fast: steps)
    monkeypatch.setattr(preflight, "purge_pycache", lambda: 0)
    monkeypatch.setattr(preflight, "uncovered", lambda: ["Some CI job"])
    monkeypatch.setattr(sys, "argv", ["preflight.py"])
    monkeypatch.setattr(toolchain, "VERSION_PINNED_TOOLS", ())


def _calls(monkeypatch):
    """Stub subprocess.call so no CI step runs, and record what would have."""
    ran: list[str] = []

    def _call(cmd, **kw):
        ran.append(cmd)
        return 0

    monkeypatch.setattr(preflight.subprocess, "call", _call)
    return ran


# ── the reading ─────────────────────────────────────────────────────────

class TestTheReadingIsAMeasurementOfTheLaunch:
    def test_a_default_sigint_is_not_reported(self):
        with _disposition(signal.SIGINT, signal.default_int_handler):
            assert "SIGINT" not in preflight.ignored_signals()

    def test_an_ignored_sigint_is_reported(self):
        with _disposition(signal.SIGINT, signal.SIG_IGN):
            assert preflight.ignored_signals() == ["SIGINT"]

    def test_an_ignored_sighup_is_reported_as_a_measurement(self):
        """nohup's own disposition is READ -- the policy of whether it refuses
        anything is `launch_refusal`'s, and the two are kept apart the way
        `toolchain` keeps the version reading apart from what to do about it."""
        with _disposition(signal.SIGINT, signal.default_int_handler), \
                _disposition(signal.SIGHUP, signal.SIG_IGN):
            assert preflight.ignored_signals() == ["SIGHUP"]

    def test_a_signal_this_platform_does_not_define_is_skipped_not_reported(self):
        """An absent signal is not an ignored one."""
        assert preflight.ignored_signals(("SIGNOSUCHTHING",)) == []

    def test_the_reading_covers_what_a_shell_launcher_rewrites(self):
        """`&` rewrites INT and QUIT, `nohup` rewrites HUP; a reading that
        stopped at SIGINT would be right about the case that bit and blind to
        the two beside it."""
        assert set(preflight.LAUNCHER_REWRITTEN_SIGNALS) == {"SIGINT", "SIGQUIT", "SIGHUP"}


# ── the refusal ─────────────────────────────────────────────────────────

class TestTheRefusalIsNarrow:
    def test_the_test_gate_under_an_ignored_sigint_is_refused_with_the_reason(self):
        why = preflight.launch_refusal(TEST_GATE[1], ["SIGINT"])
        assert why is not None
        assert "SIGINT" in why and "finish" in why

    def test_nothing_ignored_refuses_nothing(self):
        assert preflight.launch_refusal(TEST_GATE[1], []) is None

    def test_nohup_alone_refuses_nothing(self):
        """SIGHUP ignored is what `nohup` does, and no test sends SIGHUP to a
        child -- refusing there would manufacture the accusation this reading
        exists to prevent, on the launcher most people use for a long run."""
        assert preflight.launch_refusal(TEST_GATE[1], ["SIGHUP"]) is None

    def test_a_step_that_is_not_the_test_gate_is_never_refused(self):
        assert preflight.launch_refusal(RATCHET[1], ["SIGINT"]) is None

    def test_the_real_plan_holds_exactly_one_signal_sensitive_step(self):
        """The gate name is matched against ci.yml's own steps, so a renamed
        script is a stale entry here rather than a silently un-refused gate."""
        hits = [cmd for _n, cmd, _wd in preflight.steps(False)
                if any(g in cmd for g in preflight.SIGNAL_SENSITIVE_GATES)]
        assert len(hits) == 1, hits


# ── the launcher is read once, up front, and the verdict is the third state ──

class TestMainFilesTheTestGateAsCannotCheck:
    def test_under_an_ignored_sigint_the_test_gate_is_not_run_and_the_run_is_not_green(
            self, monkeypatch, capsys):
        _plan(monkeypatch, [TEST_GATE, RATCHET])
        ran = _calls(monkeypatch)
        with _disposition(signal.SIGINT, signal.SIG_IGN):
            rc = preflight.main()
        said = _ANSI.sub("", capsys.readouterr().out)
        assert rc == preflight.CANNOT_CHECK_EXIT, said
        assert TEST_GATE[1] not in ran, "the test gate ran under an ignored SIGINT"
        assert RATCHET[1] in ran, "the other gates must still run"
        assert "LAUNCHER" in said and "SIGINT is ignored in this process" in said
        assert "Fix: run the preflight in the foreground" in said
        # The third state, on the per-gate line a reader is told to read.
        assert re.search(r"^\? Tests  \(0\.0s\)  — SIGINT is ignored", said, re.M), said
        assert "1 gate(s) could not check" in said
        assert "gate(s) failed" not in said
        assert "All local gates green" not in said

    def test_under_the_default_disposition_the_test_gate_runs_and_nothing_is_said(
            self, monkeypatch, capsys):
        _plan(monkeypatch, [TEST_GATE, RATCHET])
        ran = _calls(monkeypatch)
        with _disposition(signal.SIGINT, signal.default_int_handler):
            rc = preflight.main()
        said = _ANSI.sub("", capsys.readouterr().out)
        assert rc == 0, said
        assert TEST_GATE[1] in ran and RATCHET[1] in ran
        assert "LAUNCHER" not in said and "could not check" not in said
        assert "All local gates green" in said

    def test_nohup_alone_runs_the_whole_plan(self, monkeypatch, capsys):
        _plan(monkeypatch, [TEST_GATE, RATCHET])
        ran = _calls(monkeypatch)
        with _disposition(signal.SIGINT, signal.default_int_handler), \
                _disposition(signal.SIGHUP, signal.SIG_IGN):
            rc = preflight.main()
        said = _ANSI.sub("", capsys.readouterr().out)
        assert rc == 0, said
        assert TEST_GATE[1] in ran
        assert "LAUNCHER" not in said


# ── the tuple is pinned both ways against what the suite really sends ───

def _sends_to_a_child(tree: ast.AST) -> bool:
    """True when the file CALLS `<x>.send_signal(...)` or `os.kill(<not self>, ...)`.

    An AST walk rather than a text scan, and the reason is this guard's own
    first draft: it read the file as TEXT, spelled `send_signal(` in a string
    literal beside a real `signal.SIGHUP`, and accused itself of sending
    SIGHUP to a child -- "a comment that quotes the string it forbids", one
    token kind over. A CALL node cannot be spelled by a string.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "send_signal":
            return True
        if (node.func.attr == "kill" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os" and node.args):
            first = node.args[0]
            # `os.kill(os.getpid(), ...)` signals ITSELF after installing a
            # handler (the graceful-stop suite): `signal.signal` overrides an
            # inherited SIG_IGN in-process, so no launcher can change what
            # that test measures.
            self_send = (isinstance(first, ast.Call)
                         and isinstance(first.func, ast.Attribute)
                         and first.func.attr == "getpid")
            if not self_send:
                return True
    return False


def _signals_the_suite_sends_to_a_child() -> set[str]:
    """Every launcher-rewritable signal some test file sends to a CHILD."""
    sent: set[str] = set()
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        if not _sends_to_a_child(tree):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "signal"
                    and node.attr in preflight.LAUNCHER_REWRITTEN_SIGNALS):
                sent.add(node.attr)
    return sent


class TestTheWalkReadsCallsNotText:
    """Driven on PLANTED sources, because the real tree cannot reach two of
    these branches: the only self-signalling test names a signal that is
    already in the set, so a walk that counted self-signals would change no
    verdict on the corpus and survive a round (it did). A rule no real input
    can reach is measured where it is the only thing in play."""

    @staticmethod
    def _walk(src: str) -> bool:
        return _sends_to_a_child(ast.parse(src))

    def test_a_self_signal_after_installing_a_handler_is_not_a_child_send(self):
        assert self._walk(
            "import os, signal\nsignal.signal(signal.SIGHUP, h)\n"
            "os.kill(os.getpid(), signal.SIGHUP)\n") is False

    def test_a_kill_of_another_pid_is_a_child_send(self):
        assert self._walk("import os, signal\nos.kill(child.pid, signal.SIGHUP)\n") is True

    def test_send_signal_on_a_process_is_a_child_send(self):
        assert self._walk("import signal\nproc.send_signal(signal.SIGINT)\n") is True

    def test_the_words_in_a_string_literal_are_not_a_call(self):
        """The first draft's own false accusation: a test whose SOURCE spells
        `send_signal(` inside a string beside a real `signal.SIGHUP`."""
        assert self._walk(
            'import signal\nx = "send_signal(" in src\ny = signal.SIGHUP\n') is False


def test_the_signals_the_test_gate_depends_on_are_exactly_the_ones_the_suite_sends():
    """Both directions at once. A signal listed that no test sends would refuse
    a launcher the suite can be measured under; a signal sent that is not
    listed would be the 2026-09-21 false red again, for a signal the reading
    did not know."""
    assert set(preflight.TEST_GATE_SIGNALS) == _signals_the_suite_sends_to_a_child()


def test_every_signal_sensitive_gate_names_a_script_that_exists():
    for g in preflight.SIGNAL_SENSITIVE_GATES:
        assert (ROOT / "scripts" / g).is_file(), g

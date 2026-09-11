"""A gate that could not check is neither a pass nor a failure.

`ruff_gate.check_version` separates the two deliberately and says why, naming
the reader it was separating them for:

    "Exit 2, distinct from the 1 that means 'something really did grow', so a
    launcher reading truthiness still fails closed while a human reading the
    message learns which of the two happened."

`scripts/preflight.py` IS that launcher, and it classified every step by
``rc == 0``. So a stale analyser landed in `failed` beside a real regression:
on 2026-09-11 the summary read "3 gate(s) failed" where one was a regression
and two were silence — `requirements-ci.txt` pins ruff 0.11.13 / mypy 1.15.0,
the box resolved 0.15.8 / 1.19.1, and both ratchets had been refusing for an
unknown number of runs. It was read past twice.

The bucket already existed. `preflight` had a third state for "tool is not
installed" whose summary line already said *"that is not a pass"* and whose
`main()` already returned 2 — and nothing else was ever routed into it.

WHAT IS DELIBERATELY NOT DONE HERE: mapping every ``rc == 2`` to "could not
check". Steps come out of `ci.yml` verbatim and 2 means whatever each tool says
it means; assuming otherwise is an inference about a vocabulary rather than a
reading of one, which is the defect class itself. Only the scripts that
document exit 2 that way are taken at their word, and
`test_every_named_gate_really_documents_exit_two` pins that list against the
scripts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import preflight  # noqa: E402
import toolchain  # noqa: E402

# ── the three-valued reading ────────────────────────────────────────────

def _c(pinned, running, **kw):
    return toolchain.Comparability("tool", pinned, running, **kw)


class TestComparability:
    def test_matching_versions_are_comparable(self):
        assert _c("1.2.3", "1.2.3").comparable is True

    def test_differing_versions_are_not(self):
        assert _c("1.2.3", "1.2.4").comparable is False

    @pytest.mark.parametrize("pinned,running", [
        (None, "1.2.3"), ("1.2.3", None), (None, None)])
    def test_an_unreadable_version_is_unknown_not_comparable(self, pinned, running):
        """The third value. A version nobody could read is not a match and not
        a mismatch, and answering `comparable` for it would put a fabricated
        all-clear in the one module written to prevent exactly that."""
        c = _c(pinned, running)
        assert c.unknown is True
        assert c.comparable is False

    def test_a_matching_pair_is_not_unknown(self):
        assert _c("1.2.3", "1.2.3").unknown is False


class TestDescribe:
    def test_a_mismatch_names_both_versions(self):
        said = _c("0.11.13", "0.15.8").describe()
        assert "0.11.13" in said and "0.15.8" in said

    def test_a_shadowed_binary_says_so_instead_of_install(self):
        """The round trip this branch exists for: `pip install ruff==0.11.13`
        reported success and the version did not move, because the pinned build
        was already there and another came first on PATH."""
        said = _c("0.11.13", "0.15.8",
                  resolved="/root/.local/bin/ruff",
                  shadowed_pinned="/usr/local/bin/ruff").describe()
        assert "/usr/local/bin/ruff" in said
        assert "SHADOWED" in said
        assert "/root/.local/bin/ruff" in said, "the binary that actually ran is not named"

    def test_an_unknown_version_is_not_described_as_a_mismatch(self):
        said = _c(None, "1.2.3").describe()
        assert "could not determine" in said


class TestInstallHint:
    def test_it_names_the_pinned_spec(self):
        assert "'ruff==0.11.13'" in toolchain.install_hint(
            [_c("0.11.13", "0.15.8")._replace(tool="ruff")])

    def test_it_omits_a_tool_that_is_merely_shadowed(self):
        """Telling somebody to install what they already have is the advice
        that cost the round trip. A hint with nothing to say says nothing."""
        rows = [_c("0.11.13", "0.15.8", shadowed_pinned="/usr/local/bin/ruff")
                ._replace(tool="ruff")]
        assert toolchain.install_hint(rows) == ""


# ── preflight's classification ──────────────────────────────────────────

def _plan(monkeypatch, steps):
    monkeypatch.setattr(preflight, "steps", lambda fast: steps)
    monkeypatch.setattr(preflight, "purge_pycache", lambda: 0)
    monkeypatch.setattr(preflight, "uncovered", lambda: ["Some CI job"])
    monkeypatch.setattr(sys, "argv", ["preflight.py"])


def _no_toolchain_noise(monkeypatch):
    monkeypatch.setattr(toolchain, "comparability",
                        lambda t: toolchain.Comparability(t, "1.0.0", "1.0.0"))


def _calls(monkeypatch, rc_for):
    """Stub subprocess.call so no CI step actually runs."""
    monkeypatch.setattr(preflight.subprocess, "call",
                        lambda cmd, **kw: rc_for(cmd))


def test_a_named_gate_exiting_two_is_not_a_failure(monkeypatch, capsys):
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 2)
    rc = preflight.main()
    said = capsys.readouterr().out
    assert rc == 2, "could-not-check must not return the 1 that means failure"
    assert "could not check" in said
    assert "gate(s) failed" not in said


def test_a_named_gate_exiting_one_is_a_failure(monkeypatch, capsys):
    """The other half: exit 1 means it DID check and something grew."""
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 1)
    rc = preflight.main()
    said = capsys.readouterr().out
    assert rc == 1
    assert "1 gate(s) failed" in said


def test_an_unnamed_step_exiting_two_is_still_a_failure(monkeypatch, capsys):
    """2 means "could not check" for the gates that SAY so, and nothing else.
    Steps come out of ci.yml verbatim; assuming a vocabulary they never
    declared is the inference this design refuses to make."""
    _plan(monkeypatch, [("Some CI step", "pytest -q", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 2)
    rc = preflight.main()
    assert rc == 1
    assert "1 gate(s) failed" in capsys.readouterr().out


def test_a_clean_run_is_still_green(monkeypatch, capsys):
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 0)
    rc = preflight.main()
    assert rc == 0
    assert "All local gates green" in capsys.readouterr().out


def test_a_failure_outranks_a_could_not_check(monkeypatch, capsys):
    """Both reported, and the exit code is the failure's. A run with a real
    regression must not exit 2 and read as "just a toolchain problem"."""
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", "."),
                        ("Tests", "pytest -q", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 2 if "ruff_gate" in cmd else 1)
    rc = preflight.main()
    said = capsys.readouterr().out
    assert rc == 1
    assert "could not check" in said and "gate(s) failed" in said


def _summary(said: str) -> str:
    """Everything after the rule the summary prints.

    The first draft of the test below asserted `"mypy_gate.py" in said` over
    the WHOLE output and survived a mutation that dropped the reason entirely —
    because preflight echoes each step's command (`$ (.) python3
    scripts/mypy_gate.py`) before running it, so the assertion was matching the
    echo. An assertion that can pass for a reason unrelated to its rule is not
    an assertion about the rule.
    """
    marker = "─" * 68
    assert marker in said, "preflight no longer prints a summary rule"
    return said.split(marker, 1)[1]


def test_the_summary_says_why_it_could_not_check(monkeypatch, capsys):
    _plan(monkeypatch, [("Ratchet", "python3 scripts/mypy_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 2)
    preflight.main()
    summary = _summary(capsys.readouterr().out)
    assert "could not check" in summary
    assert "mypy_gate.py" in summary, "the summary line does not name which gate went quiet"


def test_an_unchecked_gate_carries_no_boolean_verdict(monkeypatch, capsys):
    """`ok` is None, not False. There is no honest boolean for "nothing was
    measured", and a False sitting in that field is a verdict waiting to be
    read by the next caller who forgets to check `unchecked` first."""
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 2)
    preflight.main()
    summary = _summary(capsys.readouterr().out)
    # A "?" mark, not a "✗" — the gate neither passed nor failed.
    assert "?" in summary
    assert "✗" not in summary


def test_could_not_check_is_not_called_a_pass_or_a_failure(monkeypatch, capsys):
    _plan(monkeypatch, [("Ratchet", "python3 scripts/honesty_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 2)
    preflight.main()
    said = capsys.readouterr().out
    assert "not a pass" in said
    assert "not a failure" in said
    assert "All local gates green" not in said


# ── the up-front reading ────────────────────────────────────────────────

def test_a_stale_analyser_is_named_before_any_gate_runs(monkeypatch, capsys):
    """Each gate already refuses for itself, which is correct and is also how
    it goes unread — by the time two CANNOT CHECK blocks scroll past they look
    like two of the failures."""
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", ".")])
    monkeypatch.setattr(toolchain, "comparability",
                        lambda t: toolchain.Comparability(t, "1.0.0", "9.9.9"))
    _calls(monkeypatch, lambda cmd: 2)
    preflight.main()
    said = capsys.readouterr().out
    banner = said.index("TOOLCHAIN")
    assert banner < said.index("▶"), "the toolchain reading came after a gate ran"


def test_a_matching_toolchain_prints_no_banner(monkeypatch, capsys):
    """A warning that fires when nothing is wrong is how operators learn to
    skip the next one — the lesson boot_health.py records about WEB_CREDS_KEY."""
    _plan(monkeypatch, [("Ratchet", "python3 scripts/ruff_gate.py", ".")])
    _no_toolchain_noise(monkeypatch)
    _calls(monkeypatch, lambda cmd: 0)
    preflight.main()
    assert "TOOLCHAIN" not in capsys.readouterr().out


# ── the list is a reading of the scripts, not a guess ───────────────────

def test_the_named_gates_all_exist():
    for gate in preflight.CANNOT_CHECK_GATES:
        assert (ROOT / "scripts" / gate).exists(), gate


# The first draft of the three below grepped each gate for the literals
# `SystemExit(2)` and `CANNOT CHECK`, and BOTH failed the moment the shared
# `check_version` moved into `toolchain.py` — a scan matching a string that had
# simply relocated, which is the failure mode this repo already documents twice
# (`frozen_snapshot:`, `_infer_close_price`) and which I reproduced while
# fixing something else. The property is "this gate answers exit 2 when it
# cannot check", and a property is driven.

def _fake_tool(tmp_path, name: str, version_line: str) -> dict:
    fake = tmp_path / name
    fake.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    fake.chmod(0o755)
    return {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}


@pytest.mark.parametrize("gate,tool,version_line", [
    ("ruff_gate.py", "ruff", "ruff 0.0.1"),
    ("mypy_gate.py", "mypy", "mypy 0.0.1 (compiled: yes)"),
])
def test_a_version_it_cannot_compare_makes_the_gate_exit_two(
        tmp_path, gate, tool, version_line):
    """Driven end to end: run the real gate against an analyser whose version
    the baseline was not recorded with, and read the exit code it gives the
    launcher."""
    env = _fake_tool(tmp_path, tool, version_line)
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / gate)],
        capture_output=True, text=True, cwd=ROOT, env=env)
    assert proc.returncode == preflight.CANNOT_CHECK_EXIT, proc.stdout + proc.stderr
    assert "CANNOT CHECK" in proc.stderr


def test_honesty_gate_exits_two_when_it_cannot_read_its_baseline(monkeypatch,
                                                                 tmp_path):
    """The third named gate reaches exit 2 by a different door — no version to
    compare, but a baseline it cannot read is equally the absence of a
    measurement."""
    import honesty_gate
    monkeypatch.setattr(honesty_gate, "BASELINE", tmp_path / "nope.json")
    with pytest.raises(SystemExit) as exc:
        honesty_gate._load_baseline()
    assert exc.value.code == preflight.CANNOT_CHECK_EXIT


def test_a_matching_version_does_not_make_the_gate_exit_two(tmp_path):
    """The other side of the parametrised drive: a gate that CAN compare must
    not answer 2, or preflight would file a real regression as a shrug."""
    pinned = toolchain.pinned("ruff")
    assert pinned, "requirements-ci.txt no longer pins ruff"
    env = _fake_tool(tmp_path, "ruff", f"ruff {pinned}")
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / "ruff_gate.py")],
        capture_output=True, text=True, cwd=ROOT, env=env)
    assert proc.returncode != preflight.CANNOT_CHECK_EXIT


# ── the shadowed-binary search ──────────────────────────────────────────

class TestPinnedCopyElsewhere:
    """`_pinned_copy_elsewhere` had no test at all, which a mutation found:
    dropping its version comparison — so that ANY other copy on PATH was
    reported as "the pinned one" — survived the whole file."""

    def _bin(self, d, name, version_line):
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(f"#!/bin/sh\necho '{version_line}'\n")
        p.chmod(0o755)
        return p

    def test_it_finds_a_copy_whose_version_is_the_pinned_one(self, tmp_path,
                                                             monkeypatch):
        first, second = tmp_path / "a", tmp_path / "b"
        running = self._bin(first, "tool", "tool 9.9.9")
        self._bin(second, "tool", "tool 1.2.3")
        monkeypatch.setenv("PATH", f"{first}:{second}")
        found = toolchain._pinned_copy_elsewhere("tool", "1.2.3", str(running))
        assert found == str(second / "tool")

    def test_a_copy_of_the_WRONG_version_is_not_reported_as_pinned(
            self, tmp_path, monkeypatch):
        """The mutation's case. Naming a wrong-version binary as "the pinned
        one, just shadowed" sends an operator to reorder PATH for a build that
        would not fix anything."""
        first, second = tmp_path / "a", tmp_path / "b"
        running = self._bin(first, "tool", "tool 9.9.9")
        self._bin(second, "tool", "tool 5.5.5")
        monkeypatch.setenv("PATH", f"{first}:{second}")
        assert toolchain._pinned_copy_elsewhere("tool", "1.2.3", str(running)) is None

    def test_no_other_copy_answers_none(self, tmp_path, monkeypatch):
        only = tmp_path / "a"
        running = self._bin(only, "tool", "tool 9.9.9")
        monkeypatch.setenv("PATH", str(only))
        assert toolchain._pinned_copy_elsewhere("tool", "1.2.3", str(running)) is None

    def test_an_unknown_pinned_version_looks_for_nothing(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        assert toolchain._pinned_copy_elsewhere("tool", None, None) is None

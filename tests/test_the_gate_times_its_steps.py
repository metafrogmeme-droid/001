"""The gate prints how long its own steps took, and the slowest modules.

A missing duration is not zero. The parallel flags belong on the first pass
only. A shadow run that cannot import pytest-xdist did not run the suite, and
must not announce a pass.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GATE_PATH = ROOT / "scripts" / "ci_test_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_gate_timings", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _timings():
    spec = importlib.util.spec_from_file_location(
        "_module_timings_under_test", ROOT / "tests" / "module_timings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_census_line_loads_and_names_the_walk():
    """The gate loads the census by path. A dataclass dies if that load is wrong,
    and the line would say 'not recorded' over a tree the walk can read."""
    gate = _load_gate()
    line = gate._census_line()
    assert line.startswith("[source-scan] importers="), line
    assert "private_copies=" in line and "reach=" in line
    assert "not recorded" not in line


def test_the_serial_command_is_not_rewritten():
    gate = _load_gate()
    cmd = [sys.executable, "-m", "pytest", "--timeout-method=signal", "-q"]
    assert gate.apply_parallel(cmd, parallel=False) == cmd


def test_the_parallel_command_keeps_loadfile_and_drops_the_signal_timeout():
    gate = _load_gate()
    cmd = [sys.executable, "-m", "pytest", "--timeout-method=signal", "-q"]
    out = gate.apply_parallel(cmd, parallel=True)
    assert "--timeout-method=signal" not in out
    assert "--timeout-method=thread" in out
    assert out[-4:] == ["-n", "auto", "--dist", "loadfile"]


def test_a_flake_rerun_stays_serial():
    gate = _load_gate()
    cmd = gate.rerun_command("tests/test_x.py::test_y", cov_available=True)
    assert cmd[-1] == "tests/test_x.py::test_y"
    assert "--no-cov" in cmd
    assert "-n" not in cmd
    assert "--dist" not in cmd
    bare = gate.rerun_command("tests/test_x.py::test_y", cov_available=False)
    assert "--no-cov" not in bare
    assert "-n" not in bare


def test_parallel_without_xdist_does_not_pass(monkeypatch, capsys):
    gate = _load_gate()
    monkeypatch.setattr(gate, "xdist_importable", lambda: False)
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py", "--parallel"])
    called = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: called.append(1))
    assert gate.main() == 1
    assert called == [], "the suite ran after the gate had already said it could not"
    out = capsys.readouterr().out
    assert "[gate] FAIL" in out
    assert "[gate] PASS" not in out


def test_the_suite_is_asked_to_record_module_timings(monkeypatch, capsys):
    gate = _load_gate()
    seen = {}

    class _Proc:
        returncode = 0
        stdout = "1 passed in 0.1s\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate, "COV_FLAGS", [])
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    assert gate.main() == 0
    env = seen["env"]
    assert env and env.get("RUNECLAW_MODULE_TIMINGS"), "the suite was not asked to record timings"
    assert "-n" not in seen["cmd"]
    out = capsys.readouterr().out
    assert "[gate] step timings" in out
    assert "suite:" in out
    assert "flake-filter: not run" in out
    assert "coverage: not run" in out
    assert "[gate] slowest modules: not recorded" in out
    assert "CANNOT CHECK" not in out
    assert "[source-scan]" not in out, (
        "the census printed for a run that never wrote a timing file")


def test_a_recorded_file_is_what_the_report_prints(tmp_path, monkeypatch, capsys):
    gate = _load_gate()
    path = tmp_path / "timings.json"
    path.write_text(json.dumps({
        "by_file": {"tests/slow.py": 3.2, "tests/fast.py": 0.4, "tests/nope.py": None},
        "phases": {"call": 3.2, "setup": 0.4},
    }), encoding="utf-8")

    class _Proc:
        returncode = 0
        stdout = "1 passed\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        # The gate points the suite at its own temp path. Write the fixture
        # there so the report is the file the suite would have produced.
        target = pathlib.Path(kwargs["env"]["RUNECLAW_MODULE_TIMINGS"])
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate, "COV_FLAGS", [])
    monkeypatch.setattr(
        gate, "_census_line",
        lambda: "[source-scan] importers=1 private_copies=1 both=0 reach=1 of 2 test files")
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    assert gate.main() == 0
    out = capsys.readouterr().out
    assert "1. tests/slow.py  3.2s" in out
    assert "2. tests/fast.py  0.4s" in out
    assert "tests/nope.py" not in out, "a null duration was printed as a module"
    assert "call: 3.2s" in out
    assert "setup: 0.4s" in out
    assert "teardown:" not in out, "a phase nobody reported was printed as zero"
    assert "[source-scan] importers=1" in out


def test_a_missing_duration_is_not_stored_as_zero(tmp_path):
    timings = _timings()
    plugin = timings.ModuleTimings(str(tmp_path / "out.json"))

    class _Report:
        def __init__(self, when, duration, nodeid):
            self.when = when
            self.duration = duration
            self.nodeid = nodeid

    plugin.pytest_runtest_logreport(_Report("call", None, "tests/a.py::test_x"))
    plugin.pytest_runtest_logreport(_Report("call", True, "tests/a.py::test_y"))
    plugin.pytest_runtest_logreport(_Report("call", 1.5, "tests/a.py::test_z"))
    plugin.pytest_runtest_logreport(_Report("setup", 0.5, "tests/a.py::test_z"))
    assert plugin.by_file == {"tests/a.py": 2.0}
    assert plugin.phases == {"call": 1.5, "setup": 0.5}
    assert "teardown" not in plugin.phases

    class _Session:
        class config:
            workerinput = None

    plugin.pytest_sessionfinish(_Session(), 0)
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    report = "\n".join(timings.format_timing_report(payload))
    assert "tests/a.py  2.0s" in report
    assert "teardown:" not in report


def test_a_worker_does_not_write_the_controllers_file(tmp_path, monkeypatch):
    timings = _timings()
    path = tmp_path / "out.json"
    plugin = timings.ModuleTimings(str(path))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    class _Report:
        when = "call"
        duration = 9.0
        nodeid = "tests/a.py::test_x"

    class _Session:
        class config:
            workerinput = {"workerid": "gw0"}

    plugin.pytest_runtest_logreport(_Report())
    plugin.pytest_sessionfinish(_Session(), 0)
    assert plugin.by_file == {}
    assert not path.exists()


def test_step_timings_name_a_step_that_did_not_run():
    timings = _timings()
    lines = timings.format_step_timings(suite_s=1.0, flake_s=None, cov_s=None, total_s=1.5)
    assert "  suite: 1.0s" in lines
    assert "  flake-filter: not run" in lines
    assert "  coverage: not run" in lines
    ran = timings.format_step_timings(suite_s=1.0, flake_s=0.0, cov_s=2.0, total_s=3.0)
    assert "  flake-filter: 0.0s" in ran
    assert "  coverage: 2.0s" in ran

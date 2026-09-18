"""The network-reach baseline can be re-measured, and the comment says so truly.

`tests/conftest.py` refuses every non-loopback connect and fails, by name, a
test FILE that reaches the network and is not on `tests/network_reach_baseline.txt`.
That is the GROWTH direction, enforced on every run. The baseline's own comment
names `scripts/network_reach_gate.py` as where a human re-measures the STALE
direction deliberately -- and for one commit it named a script that did not
exist, which is the `/vault` hint shape this repository records at every scale:
*a card that names a command is claiming the command does something*, here
pointed at a code comment, where the next reader trusts it because the comment
is right about everything else.

So the first thing pinned here is the hint itself, two ways: the comment names
the script, and the script is there. Everything after it is the machinery that
makes the re-measure honest -- above all the refusals, because the STALE half
DELETES rows and a partial run's silence is not evidence that a file stopped
reaching anything.
"""

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFTEST = ROOT / "tests" / "conftest.py"
BASELINE = ROOT / "tests" / "network_reach_baseline.txt"
GATE = ROOT / "scripts" / "network_reach_gate.py"


def _gate_module():
    spec = importlib.util.spec_from_file_location("network_reach_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── The hint shape, both ways ────────────────────────────────────────────────

class TestTheCommentNamesSomethingThatExists:
    def test_the_conftest_names_the_re_measure_script(self):
        src = CONFTEST.read_text(encoding="utf-8")
        assert "scripts/network_reach_gate.py" in src, (
            "the baseline's comment must name where the stale half is "
            "re-measured; without it the backlog can only ever grow")

    def test_the_script_the_comment_names_is_there(self):
        assert GATE.is_file(), (
            f"{CONFTEST.relative_to(ROOT)} names {GATE.relative_to(ROOT)} as "
            f"the remedy. A comment that names a tool that does not exist is "
            f"the /vault hint shape: the next reader trusts it.")

    def test_it_runs(self):
        """DRIVEN, not scanned: a file that exists and raises on import is a
        remedy nobody can run, which is the same thing one layer down."""
        r = subprocess.run([sys.executable, str(GATE), "--help"],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "--write" in r.stdout


# ── The report path is a report, not a bypass ────────────────────────────────

def _run(args, env_extra=None, cwd=ROOT):
    env = dict(os.environ)
    env.pop("RUNECLAW_REACH_REPORT", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:randomly",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=cwd, capture_output=True, text=True, env=env)


#: One baselined file that really does reach a venue, so the report has
#: something to record without this test making a connect of its own.
_A_BASELINED_FILE = "tests/test_the_funding_card_says_which_venues_it_read.py"


class TestTheReport:
    def test_nothing_is_written_without_the_variable(self, tmp_path):
        """A file created by a run nobody asked for is a side effect.

        The FIRST draft of this asserted `not (tmp_path / "never.txt").exists()`
        for a path nothing had been told about -- an assertion that can never
        fail, passing for a reason unrelated to its own name. The same path is
        driven both ways now, so the claim is about the variable.
        """
        out = tmp_path / "same-path.txt"
        _run([_A_BASELINED_FILE], {"RUNECLAW_REACH_REPORT": str(out)})
        assert out.exists(), "the variable must be what writes it"
        out.unlink()
        r = _run([_A_BASELINED_FILE])
        assert not out.exists()
        # AND IT MUST NOT TRY. Dropping the early return SURVIVED a round:
        # with no variable the path is "", `Path("")` is the working
        # directory, the write raises IsADirectoryError, and the writer's own
        # `except OSError` swallows it -- so no file appears and the guard
        # above still passed. What the mutant really does is print a "could
        # not write" line on EVERY run of the whole suite, which is a gate
        # complaining about a report nobody asked for.
        assert "could not write" not in r.stdout, r.stdout[-2000:]

    def test_an_unwritable_report_path_does_not_take_the_suite_down(
            self, tmp_path):
        """This runs at the very end of a session whose verdicts are already
        decided. Turning a measurement into a failure over a file nobody can
        open would cost the run its own result."""
        a_directory = tmp_path / "is-a-dir"
        a_directory.mkdir()
        r = _run([_A_BASELINED_FILE],
                 {"RUNECLAW_REACH_REPORT": str(a_directory)})
        assert r.returncode == 0, r.stdout[-3000:]
        assert "could not write" in r.stdout

    def test_a_baselined_file_is_recorded(self, tmp_path):
        """THE MUTATION THIS KILLS is recording only in the FAILING branch.

        A report that saw the fresh half alone would report all 42 baselined
        files as stale, and --write would then delete every one of them. The
        file driven here is baselined, so it never reaches the fail branch.
        """
        rep = tmp_path / "rep.txt"
        r = _run([_A_BASELINED_FILE],
                 {"RUNECLAW_REACH_REPORT": str(rep)})
        assert rep.exists(), r.stdout[-3000:]
        body = rep.read_text(encoding="utf-8")
        assert _A_BASELINED_FILE in body.splitlines()

    def test_the_verdict_is_unchanged_by_asking_for_a_report(self, tmp_path):
        """A report path that refused or allowed anything extra would be a
        bypass wearing a reporting name. Same suite, same status."""
        plain = _run([_A_BASELINED_FILE])
        with_report = _run([_A_BASELINED_FILE],
                           {"RUNECLAW_REACH_REPORT": str(tmp_path / "r.txt")})
        assert plain.returncode == with_report.returncode

    def test_a_narrow_run_says_it_measured_part_of_the_tree(self, tmp_path):
        rep = tmp_path / "rep.txt"
        _run([_A_BASELINED_FILE], {"RUNECLAW_REACH_REPORT": str(rep)})
        assert "# whole=false" in rep.read_text(encoding="utf-8")

    def test_a_synthetic_nodeid_is_not_recorded_as_a_file_and_is_named(
            self, tmp_path):
        """`test_no_test_reaches_a_venue.py` drives the containment with
        `tests/planted.py::test_planted`, which is the right way to measure a
        rule the real tree cannot reach -- and from the ledger's side it is
        indistinguishable from a real file. Writing it into the baseline would
        record a file that does not exist as reaching a venue. Named rather
        than silently dropped, because a report that discarded rows quietly
        would be a partial measurement printed as a whole one."""
        rep = tmp_path / "rep.txt"
        _run(["tests/test_no_test_reaches_a_venue.py"],
             {"RUNECLAW_REACH_REPORT": str(rep)})
        body = rep.read_text(encoding="utf-8")
        listed = [ln for ln in body.splitlines()
                  if ln.strip() and not ln.startswith("#")]
        assert "tests/planted.py" not in listed
        assert "not-a-file" in body and "tests/planted.py" in body


# ── The whole-suite reading ──────────────────────────────────────────────────

class TestWholeSuiteAskedFor:
    """Driven against planted configs: the real tree only ever produces one
    of these shapes per run, so a rule measured from it alone is measured on
    one input."""

    def _ask(self, args):
        import conftest as ct
        cfg = type("C", (), {"args": args})()
        session = type("S", (), {"config": cfg})()
        return ct._whole_suite_asked_for(session, ROOT)

    @pytest.mark.parametrize("args", [["tests"], ["tests/"], [str(ROOT)]])
    def test_the_tree_is_whole(self, args):
        assert self._ask(args) is True

    @pytest.mark.parametrize("args", [
        [],                                        # nothing asked for
        ["tests/test_no_test_reaches_a_venue.py"],  # one file
        ["tests/test_x.py::TestY::test_z"],         # one nodeid
        ["tests", "bot"],                           # one arg outside the tree
    ])
    def test_anything_narrower_is_not(self, args):
        assert self._ask(args) is False

    def test_an_unreadable_config_is_not_whole(self):
        import conftest as ct

        class Boom:
            @property
            def args(self):
                raise RuntimeError("no config")

        session = type("S", (), {"config": Boom()})()
        # NOT a crash and NOT "whole": a config nobody could read is no
        # evidence that the whole tree was measured, which is the direction
        # that refuses rather than deletes.
        assert ct._whole_suite_asked_for(session, ROOT) is False


# ── The gate's three outcomes ────────────────────────────────────────────────

def _report(tmp_path, files, *, collected=900, status=0, whole="true"):
    p = tmp_path / "rep.txt"
    lines = [f"# collected={collected}", f"# exitstatus={status}"]
    if whole is not None:
        lines.append(f"# whole={whole}")
    lines += list(files)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class TestTheGateOutcomes:
    def test_a_missing_report_is_could_not_check(self, tmp_path):
        mod = _gate_module()
        assert mod.main(["--report", str(tmp_path / "nope.txt")]) == 3

    def test_a_report_from_a_run_that_died_is_could_not_check(self, tmp_path):
        """pytest's 0 and 1 are "ran through" -- 1 is EXPECTED here, since a
        file reaching the network fails its own test. 2 (interrupted), 3
        (internal error), 4 (usage) and 5 (nothing collected) all mean the
        suite did not finish, so nothing was measured."""
        mod = _gate_module()
        for status in (2, 3, 4, 5):
            rep = _report(tmp_path, [_A_BASELINED_FILE], status=status)
            assert mod.main(["--report", str(rep)]) == 3, status

    def test_a_report_with_no_collected_count_is_could_not_check(self, tmp_path):
        mod = _gate_module()
        rep = _report(tmp_path, [], collected=None)
        rep.write_text("# exitstatus=0\n# whole=true\n", encoding="utf-8")
        assert mod.main(["--report", str(rep)]) == 3

    def test_a_partial_run_cannot_check_the_stale_half(self, tmp_path, capsys):
        mod = _gate_module()
        rep = _report(tmp_path, [_A_BASELINED_FILE], whole="false")
        assert mod.main(["--report", str(rep)]) == 3
        out = capsys.readouterr().out
        assert "STALE not checked" in out
        # The forty-odd unreached rows must NOT be named as stale.
        assert "STALE tests/" not in out

    def test_a_partial_run_still_reports_growth(self, tmp_path, capsys):
        """GROWTH is a finding from any run and STALE is not: a file that
        reached is evidence wherever it was seen; a file that did not reach is
        evidence only when the run could have reached it."""
        mod = _gate_module()
        rep = _report(tmp_path, ["tests/test_a_file_nobody_listed.py"],
                      whole="false")
        assert mod.main(["--report", str(rep)]) == 1
        assert "GREW  tests/test_a_file_nobody_listed.py" in capsys.readouterr().out

    def test_write_refuses_a_partial_run_and_changes_nothing(
            self, tmp_path, capsys):
        """THE EXPENSIVE DIRECTION. A --write over a one-file run would delete
        every other row from the baseline on no evidence at all.

        THE FILE IS SAFE BY STRUCTURE, AND THE SENTENCE IS WHAT THE BRANCH
        BUYS. Deleting the explicit refusal SURVIVED the first round: control
        then falls through to the same `return 3` and `write_baseline` is never
        reached either way, so the baseline is untouched under both. The branch
        is not belt-and-braces though -- without it the operator who typed
        `--write` reads "no growth; the stale half was not measured", a status
        line that says nothing about their write, and walks away believing it
        happened. So the claim is the SENTENCE, and it is asserted: naming
        --write, and naming what would have been destroyed.
        """
        mod = _gate_module()
        before = BASELINE.read_bytes()
        rep = _report(tmp_path, [_A_BASELINED_FILE], whole="false")
        assert mod.main(["--report", str(rep), "--write"]) == 3
        assert BASELINE.read_bytes() == before
        out = capsys.readouterr().out
        assert "REFUSING --write" in out
        assert "deleted on no evidence" in out

    def test_an_older_report_with_no_whole_line_is_not_read_as_partial(
            self, tmp_path, capsys):
        """`whole` absent is a question the report did not answer, which is
        not the same as answering no -- so the stale half is UNMEASURED rather
        than reported, and --write still refuses."""
        mod = _gate_module()
        rep = _report(tmp_path, [_A_BASELINED_FILE], whole=None)
        assert mod.main(["--report", str(rep)]) == 3
        assert "an older conftest" in capsys.readouterr().out

    def test_a_whole_run_matching_the_baseline_passes(self, tmp_path):
        mod = _gate_module()
        _header, listed = mod.read_baseline()
        rep = _report(tmp_path, listed, whole="true")
        assert mod.main(["--report", str(rep)]) == 0

    def test_a_whole_run_that_moved_fails_and_names_both_directions(
            self, tmp_path, capsys):
        mod = _gate_module()
        _header, listed = mod.read_baseline()
        moved = listed[1:] + ["tests/test_a_file_nobody_listed.py"]
        rep = _report(tmp_path, moved, whole="true")
        assert mod.main(["--report", str(rep)]) == 1
        out = capsys.readouterr().out
        assert "GREW  tests/test_a_file_nobody_listed.py" in out
        assert f"STALE {listed[0]}" in out


class TestTheRewritePreservesTheArgument:
    def test_the_header_survives(self, tmp_path):
        """The header carries WHY the backlog exists and what is and is not
        enforced. A rewrite that dropped it would leave the next reader a bare
        list of 42 files with no reason -- which is how a ratchet becomes a
        permission slip."""
        mod = _gate_module()
        header, listed = mod.read_baseline()
        assert any("RATCHET" in h for h in header), (
            "the baseline must carry its own argument")
        out = tmp_path / "copy.txt"
        mod.write_baseline(header, ["tests/test_one.py"], out)
        new_header, new_listed = mod.read_baseline(out)
        assert new_header == header
        assert new_listed == ["tests/test_one.py"]

    def test_the_baseline_reads_back_as_files_that_exist(self):
        """Every listed row is a real test file. A row that is not is either a
        synthetic nodeid that leaked in (the report filters those) or a file
        that was deleted without re-measuring."""
        _header, listed = _gate_module().read_baseline()
        missing = [f for f in listed if not (ROOT / f).is_file()]
        assert not missing, (
            f"{len(missing)} baselined path(s) are not files: {missing[:5]} — "
            f"re-measure with scripts/network_reach_gate.py --write")

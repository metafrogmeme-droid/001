"""The gate that watches for `unreadable is never zero` — and what it claims.

`scripts/honesty_gate.py` is the third attempt at enforcing the rule CLAUDE.md
has stated since 2026-07-31 and that was broken in twenty-plus places the same
day it was written down. The first two were reading every diff and auditing the
previous PR; both work and neither scales. This one is the
`ruff_gate`/`mypy_gate`/`known_failures.txt` pattern pointed at the shapes.

A ratchet is only as good as the honesty of its own claim, and this repo has
watched two reachability checkers accuse live code because theirs was wrong.
So the tests below are in three groups:

  * it FINDS the shapes, on planted source where the rule is the only thing in
    play (a real-tree assertion can pass for a reason unrelated to the rule);
  * it does NOT find the things that merely look like them, because a gate
    that cries wolf gets a blanket `--update` and stops meaning anything;
  * it RATCHETS in both directions, and refuses to compare counts produced by
    a different rule set — the trap `ruff_gate.check_version` documents, where
    a baseline recorded under mypy 1.19.1 and checked under 1.15.0 named
    eleven classes as grown and not one was a code change.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import honesty_gate as hg  # noqa: E402


def _shapes(src: str) -> list:
    """Every shape the scanner finds in a snippet, by name."""
    import ast
    scanner = hg._Scan("planted.py", src)
    scanner.visit(ast.parse(src))
    return [s for s, _rel, _line, _text in scanner.hits]


class TestItFindsEachShape:
    @pytest.mark.parametrize("src,shape", [
        ("x = float(raw or 0)", "or-zero-coerce"),
        ("x = int(raw or 0)", "or-zero-coerce"),
        ("x = float(raw or 0.0)", "or-zero-coerce"),
        # `(x || 0) >= 0` — CLAUDE.md's "unreadable WON", since 0 >= 0 is true.
        ("if (raw or 0) >= 0: pass", "or-zero-compare"),
        ("if (raw or 0) > 0: pass", "or-zero-compare"),
        ("if 0 < (raw or 0): pass", "or-zero-compare"),
        ("pnl = raw or 0", "or-zero-assign"),
        ("self.max_drawdown = raw or 0.0", "or-zero-assign"),
        ('x = row.get("pnl", 0)', "get-default-zero"),
        ('x = row.get("entry_price", 0.0)', "get-default-zero"),
        ('x = getattr(o, "pnl_usd", 0)', "getattr-default-zero"),
        # The spelling that hid the most expensive instance in the tree.
        ('x = _attr(t, "pnl", 0)', "lookup-default-zero"),
        ("losses = len(rows) - wins", "complement-count"),
        ("failures = len(all_checks) - passed", "complement-count"),
    ])
    def test_shape_is_found(self, src, shape):
        assert shape in _shapes(src), f"{src!r} did not report {shape}"

    def test_an_annotated_assignment_is_not_a_blind_spot(self):
        assert "or-zero-assign" in _shapes("pnl: float = raw or 0.0")

    def test_nested_shapes_are_each_reported(self):
        found = _shapes('v = float(_attr(t, "pnl", 0) or 0)')
        assert "lookup-default-zero" in found and "or-zero-coerce" in found


class TestItDoesNotCryWolf:
    @pytest.mark.parametrize("src", [
        # A count, a retry budget, an index: zero is an honest default for
        # something that is not a measurement.
        'n = row.get("retries", 0)',
        'i = row.get("attempt", 0)',
        'x = getattr(o, "index", 0)',
        # A boolean default is a different thing, and `False == 0` in Python.
        'flag = row.get("pnl", False)',
        # A non-zero default states no measurement.
        'x = row.get("pnl", None)',
        'x = row.get("price", 1)',
        # `or` with something that is not zero.
        "name = raw or 'unknown'",
        "px = raw or None",
        # The complement shape needs BOTH a losing name and a len() subtraction.
        "remaining = len(rows) - taken",
        "losses = scored - wins",
    ])
    def test_not_a_hit(self, src):
        assert _shapes(src) == [], f"{src!r} was reported and should not be"

    def test_the_vocabulary_separates_measurements_from_counters(self):
        for word in ("pnl", "entry_price", "max_margin", "win_rate",
                     "fdv_mcap_ratio", "drawdown", "netProfit", "hold_rate"):
            assert hg.is_measurement(word), word
        for word in ("retries", "attempt", "index", "user_id", "timeout",
                     "limit", "offset", "version"):
            assert not hg.is_measurement(word), word

    def test_zero_is_zero_and_false_is_not(self):
        import ast
        assert hg._is_zero(ast.parse("0", mode="eval").body)
        assert hg._is_zero(ast.parse("0.0", mode="eval").body)
        assert not hg._is_zero(ast.parse("False", mode="eval").body)
        assert not hg._is_zero(ast.parse("None", mode="eval").body)
        assert not hg._is_zero(ast.parse("1", mode="eval").body)


class TestTheRatchetGoesBothWays:
    @staticmethod
    def _run(tmp_path, baseline: dict, *args):
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")
        orig = hg.BASELINE
        hg.BASELINE = path
        try:
            return hg.main() if not args else hg.main()
        finally:
            hg.BASELINE = orig

    @staticmethod
    def _live_baseline() -> dict:
        return json.loads((ROOT / "tests" / "honesty_baseline.json")
                          .read_text(encoding="utf-8"))

    def test_the_tree_matches_its_own_baseline(self, capsys):
        assert hg.main() == 0, capsys.readouterr().out

    def test_a_new_hit_fails(self, tmp_path, capsys):
        base = self._live_baseline()
        shape, files = next(iter(base["counts"].items()))
        rel = next(iter(files))
        base["counts"][shape][rel] -= 1          # pretend the tree grew by one
        assert self._run(tmp_path, base) == 1
        assert "NEW hits" in capsys.readouterr().out

    def test_an_improvement_ALSO_fails_until_it_is_rerecorded(
            self, tmp_path, capsys):
        # Same rule as known_failures.txt: a baseline sitting above reality
        # stops meaning anything, and the stale entry is what hides the next
        # real one.
        base = self._live_baseline()
        shape, files = next(iter(base["counts"].items()))
        rel = next(iter(files))
        base["counts"][shape][rel] += 5
        assert self._run(tmp_path, base) == 1
        out = capsys.readouterr().out
        assert "re-record the baseline in this commit" in out

    def test_a_file_that_is_gone_from_the_tree_counts_as_an_improvement(
            self, tmp_path, capsys):
        base = self._live_baseline()
        shape = next(iter(base["counts"]))
        base["counts"][shape]["bot/does_not_exist.py"] = 3
        assert self._run(tmp_path, base) == 1
        assert "bot/does_not_exist.py" in capsys.readouterr().out

    def test_a_different_rule_set_is_CANNOT_CHECK_not_a_verdict(
            self, tmp_path, capsys):
        base = self._live_baseline()
        base["rules_fingerprint"] = "0000000000000000"
        # 2, distinct from the 1 that means something really grew — a launcher
        # reading truthiness still fails closed, a human learns which happened.
        assert self._run(tmp_path, base) == 2
        assert "CANNOT CHECK" in capsys.readouterr().err

    def test_the_fingerprint_moves_when_the_vocabulary_does(self, monkeypatch):
        before = hg._rules_fingerprint()
        monkeypatch.setattr(hg, "MEASUREMENT_WORDS",
                            hg.MEASUREMENT_WORDS | {"sharpe"})
        assert hg._rules_fingerprint() != before, (
            "a widened vocabulary counts different things on an identical "
            "tree, and comparing across it manufactures growth that is not "
            "in the code")

    def test_the_recorded_fingerprint_is_the_current_one(self):
        assert (self._live_baseline().get("rules_fingerprint")
                == hg._rules_fingerprint()), (
            "the baseline was recorded under a different rule set — re-record "
            "it in the commit that changed the rules")


class TestItSaysWhatItDoesNotCover:
    """A gate whose coverage is overstated is the failure it exists to stop."""

    def test_the_docstring_names_the_exclusions(self):
        doc = hg.__doc__
        for claim in ("Python only", "tests/", "Five of the eight shapes"):
            assert claim in doc, f"the coverage statement dropped {claim!r}"

    def test_the_baseline_carries_the_coverage_statement(self):
        base = json.loads((ROOT / "tests" / "honesty_baseline.json")
                          .read_text(encoding="utf-8"))
        assert "Python only" in base.get("_coverage", "")
        assert "RATCHET" in base.get("_comment", "")

    def test_tests_are_excluded_deliberately_and_bot_is_not(self):
        assert "bot" in hg.ROOTS and "scripts" in hg.ROOTS
        assert "tests" not in hg.ROOTS, (
            "a test PLANTS these shapes to prove the code rejects them; "
            "including it buries the signal in fixtures")

    def test_an_unparseable_file_is_refused_rather_than_scored_zero(
            self, tmp_path, monkeypatch, capsys):
        bad = tmp_path / "bot"
        bad.mkdir()
        (bad / "broken.py").write_text("def (:\n", encoding="utf-8")
        monkeypatch.setattr(hg, "ROOT", tmp_path)
        with pytest.raises(SystemExit) as exc:
            hg.scan()
        assert exc.value.code == 2
        assert "CANNOT CHECK" in capsys.readouterr().err


class TestItRunsAsCIRunsIt:
    def test_the_script_is_green_from_a_clean_process(self):
        proc = subprocess.run([sys.executable, "scripts/honesty_gate.py"],
                              cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_list_prints_every_hit_with_a_location(self):
        proc = subprocess.run(
            [sys.executable, "scripts/honesty_gate.py", "--list"],
            cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0
        body = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        assert any(":" in ln and ln.split()[0] in hg.SHAPES for ln in body)
        assert "place to LOOK, not a defect" in proc.stdout

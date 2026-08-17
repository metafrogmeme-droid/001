""""Passes alone" is only evidence if both runs saw the same code.

2026-08-17. A source-scanning test failed legitimately in `scripts/ci_test_gate.py`'s
full-suite pass. The fix was written while that ~15 minute suite was still
running. The gate's flake filter then re-ran the failing node in isolation, read
the ALREADY-FIXED file from disk, watched it pass, and classified it:

    ~ passes alone (flaky/order-dependent): tests/test_leverage_overshoot_guard.py::…
    [gate] PASS — no new failures beyond the known baseline.

A run containing a genuine regression reported PASS. CI failed the same commit,
because its checkout is immutable for the duration of the job — so the LOCAL
result, the one a developer sees first and trusts most, was the wrong one.

This is the defect the whole repository is organised against, sitting inside the
gate that exists to prevent it: a conclusion drawn from a premise that changed
underneath it. The gate already carries the same lesson one layer down — it used
to ignore pytest's exit code and announce PASS having executed nothing.

The fix is not to make the flake filter smarter. It is to notice that the
question cannot be answered and say so: when the tree moved, an isolated re-run
tests DIFFERENT code, "passes alone" proves nothing about flakiness, and every
new failure is reported as real rather than dropped.

WHY THIS IS A REAL RISK AND NOT A CONTRIVED ONE. Editing during a long gate is
the NORMAL way to work — the suite takes a quarter of an hour and nobody sits
idle through it. The window is wide open by design, and the failure is silent
and in the reassuring direction.
"""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "ci_test_gate.py"


def _gate():
    spec = importlib.util.spec_from_file_location("_gate_mod", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the fingerprint actually detects a change ────────────────────────────────

def test_the_fingerprint_moves_when_a_source_file_appears(tmp_path):
    g = _gate()
    before = g._tree_fingerprint()
    probe = ROOT / "tests" / "_fingerprint_probe_delete_me.py"
    try:
        probe.write_text("# transient\n", encoding="utf-8")
        assert g._tree_fingerprint() != before, (
            "a new source file did not move the fingerprint — the guard cannot "
            "see the edits it exists to notice")
    finally:
        probe.unlink(missing_ok=True)
    assert g._tree_fingerprint() == before, "removing it must restore the value"


def test_the_fingerprint_moves_when_an_existing_file_is_edited():
    """The case that actually happened: an EXISTING test file rewritten
    mid-run, which changes its size and mtime but adds no file."""
    g = _gate()
    target = ROOT / "tests" / "_fingerprint_edit_probe_delete_me.py"
    target.write_text("# one line\n", encoding="utf-8")
    try:
        before = g._tree_fingerprint()
        target.write_text("# one line\n# and another, changing size\n", encoding="utf-8")
        assert g._tree_fingerprint() != before
    finally:
        target.unlink(missing_ok=True)


def test_the_fingerprint_is_stable_on_a_quiescent_tree():
    """It must not cry wolf. A fingerprint that changes on its own would
    disable the flake filter on every run, and a guard that always fires gets
    switched off."""
    g = _gate()
    assert g._tree_fingerprint() == g._tree_fingerprint()


def test_the_fingerprint_covers_the_directories_the_suite_reads():
    """bot/ and tests/ are obvious. scripts/ matters because several tests
    scan preflight and this gate itself."""
    g = _gate()
    src = GATE.read_text(encoding="utf-8")
    body = src[src.index("def _tree_fingerprint"):src.index("def main(")]
    for d in ("bot", "tests", "scripts"):
        assert f'"{d}"' in body, f"{d}/ is not fingerprinted"


# ── the gate refuses to call anything flaky on a moved tree ──────────────────

def test_a_changed_tree_disables_the_flake_filter():
    """The wiring, which no unit test can reach: the filter lives inside a
    900-line `main()` that shells out to a full pytest run."""
    from tests.source_scan import code_only

    src = code_only(GATE.read_text(encoding="utf-8"))
    assert "fingerprint_before = _tree_fingerprint()" in src, (
        "the fingerprint must be taken BEFORE the suite runs")
    assert "tree_changed = _tree_fingerprint() != fingerprint_before" in src
    assert "if new_failures and tree_changed:" in src, (
        "the flake filter must be skipped entirely when the tree moved — "
        "re-running against different code answers a different question")

    # Order matters: the snapshot has to precede the subprocess that runs the
    # suite, or it records the post-edit state and can never differ.
    snap = src.index("fingerprint_before = _tree_fingerprint()")
    run = src.index("proc = subprocess.run(first_cmd")
    assert snap < run, (
        "the fingerprint is taken after the suite starts, so an edit during "
        "the run is already baked in and the comparison is vacuous")


def test_the_disabled_path_says_why_rather_than_just_failing():
    """An operator who sees failures they cannot reproduce will re-run and
    trust the second answer. The message has to name the cause."""
    src = GATE.read_text(encoding="utf-8")
    block = src[src.index("if new_failures and tree_changed:"):]
    block = block[:block.index("elif new_failures:")]
    assert "SOURCE CHANGED DURING THE RUN" in block
    assert "quiescent" in block, (
        "it must tell the reader what to do next, not merely that something "
        "was wrong")
    assert "flake filter DISABLED" in block


def test_the_flake_filter_still_exists_for_a_stable_tree():
    """THE CONTROL. The filter is there because genuinely order-dependent
    tests redden every run; this change must narrow it, not delete it. If the
    `elif` ever becomes unreachable, real flakes start failing the build and
    somebody will disable the whole gate."""
    from tests.source_scan import code_only

    src = code_only(GATE.read_text(encoding="utf-8"))
    assert "elif new_failures:" in src
    tail = src[src.index("elif new_failures:"):]
    assert "passes alone (flaky/order-dependent)" in tail
    assert "still fails alone" in tail

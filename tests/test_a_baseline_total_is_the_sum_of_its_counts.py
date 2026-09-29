"""A ratchet baseline's total is the sum of its counts, because it is never stored.

Git merges these files line by line. Two branches that each lowered a
different per-file count, and each re-recorded the same total, merged with no
conflict into a file whose counts summed to one less than its total. Driven on
2026-09-26: integrating the time-stop slice onto the scan-card slice left the
honesty baseline's counts at 695 under a recorded total of 696.

Nothing else could see it. The gates compare per-file counts, and each count
was right, so all three were green. The first cure was this file requiring
the stored total to equal the sum. That caught the disagreement one run later;
it did not remove the field a merge gets wrong. Now no baseline stores a total:

  * each gate (ruff, mypy, honesty) sums its counts wherever it prints one;
  * ``--update`` writes no total;
  * a baseline that still stores one -- an older branch re-recorded and
    merged -- is refused as CANNOT CHECK (exit 2), with a sentence saying to
    re-record, before any analyser runs. Exit 2 and not 1, because it is not a
    verdict on the code: the counts may be fine.

Every claim is DRIVEN through each gate's own ``main`` on a planted baseline;
the analyser is substituted, as ``test_lint_type_ratchets`` does and for the
reason it gives (a whole-tree analyser inside a suite that writes the tree
measures a moving target).
"""
from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ratchet_baseline as rb  # noqa: E402

BASELINES = (("ruff_baseline.json", 1), ("mypy_baseline.json", 1),
             ("honesty_baseline.json", 2))

MERGED = {"or-zero-coerce": {"a.py": 3, "b.py": 1},
          "get-default-zero": {"a.py": 691}}


def _gate(name: str):
    return importlib.import_module(name)


def _run(g) -> int:
    """The gate's exit status, whether ``main`` returns it or a loader raises
    ``SystemExit`` with it (both reach the process as its exit code)."""
    try:
        return g.main()
    except SystemExit as exc:
        return exc.code


def _never(*_a, **_k):
    raise AssertionError("the analyser ran; the refusal must come first")


def _plant_ruff(monkeypatch, tmp_path, record, counts=None):
    g = _gate("ruff_gate")
    path = tmp_path / "ruff_baseline.json"
    path.write_text(record if isinstance(record, str) else json.dumps(record))
    monkeypatch.setattr(g, "BASELINE", path)
    monkeypatch.setattr(g, "check_version", lambda _t: None)
    monkeypatch.setattr(g, "current_counts",
                        _never if counts is None else (lambda: Counter(counts)))
    monkeypatch.setattr(sys, "argv", ["ruff_gate.py"])
    return g, path


def _plant_mypy(monkeypatch, tmp_path, record, counts=None):
    g = _gate("mypy_gate")
    path = tmp_path / "mypy_baseline.json"
    path.write_text(record if isinstance(record, str) else json.dumps(record))
    monkeypatch.setattr(g, "BASELINE", path)
    monkeypatch.setattr(g, "check_version", lambda _t: None)
    monkeypatch.setattr(g, "current_counts",
                        _never if counts is None else (lambda: (Counter(counts), 9)))
    monkeypatch.setattr(sys, "argv", ["mypy_gate.py"])
    return g, path


def _hits(counts: dict) -> list:
    return [(shape, rel, i, "x") for shape, files in counts.items()
            for rel, n in files.items() for i in range(n)]


def _plant_honesty(monkeypatch, tmp_path, record, counts=None):
    g = _gate("honesty_gate")
    path = tmp_path / "honesty_baseline.json"
    if isinstance(record, dict):
        record = {"rules_fingerprint": g._rules_fingerprint(), **record}
    path.write_text(record if isinstance(record, str) else json.dumps(record))
    monkeypatch.setattr(g, "BASELINE", path)
    monkeypatch.setattr(g, "scan", lambda: _hits(counts or {}))
    monkeypatch.setattr(sys, "argv", ["honesty_gate.py"])
    return g, path


PLANTERS = {"ruff": _plant_ruff, "mypy": _plant_mypy, "honesty": _plant_honesty}
FLAT = {"E501": 4, "F401": 3}


def _counts_for(gate: str) -> dict:
    return MERGED if gate == "honesty" else FLAT


# ── the real baselines ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,depth", BASELINES)
def test_no_real_baseline_stores_a_total(name, depth):
    record = json.loads((ROOT / "tests" / name).read_text(encoding="utf-8"))
    assert "total" not in record, (
        f"{name} stores a total. The gates derive it and refuse a stored one; "
        f"re-record with: python3 scripts/rerecord.py --all")
    assert rb.derived_total(record, depth) == rb.summed(record["counts"], depth) > 0


# ── a stored total is refused, before the analyser, as CANNOT CHECK ───────

@pytest.mark.parametrize("gate", PLANTERS)
def test_a_planted_baseline_with_a_total_is_refused(gate, monkeypatch, tmp_path,
                                                    capsys):
    counts = _counts_for(gate)
    # ruff and mypy must refuse before their analyser runs (it would raise);
    # honesty scans first by design, so it is handed the same counts.
    planted = PLANTERS[gate](monkeypatch, tmp_path,
                             {"total": 696, "counts": counts},
                             counts if gate == "honesty" else None)[0]
    assert _run(planted) == 2, f"{gate} compared against a stored total"
    err = capsys.readouterr().err
    assert "CANNOT CHECK" in err and "stores a total (696)" in err, err
    assert "scripts/rerecord.py --all" in err, (
        "the refusal must say how to re-record")


@pytest.mark.parametrize("gate", PLANTERS)
def test_a_total_that_even_agrees_is_refused(gate, monkeypatch, tmp_path, capsys):
    """The field is refused, not the disagreement: an agreeing total is the
    same field one merge away from disagreeing."""
    counts = _counts_for(gate)
    total = rb.summed(counts, 2 if gate == "honesty" else 1)
    g = PLANTERS[gate](monkeypatch, tmp_path, {"total": total, "counts": counts},
                       counts if gate == "honesty" else None)[0]
    assert _run(g) == 2
    assert f"stores a total ({total})" in capsys.readouterr().err


# ── a baseline without one is summed ─────────────────────────────────────────

@pytest.mark.parametrize("gate", PLANTERS)
def test_a_baseline_without_a_total_is_summed(gate, monkeypatch, tmp_path, capsys):
    counts = _counts_for(gate)
    g = PLANTERS[gate](monkeypatch, tmp_path, {"counts": counts}, counts)[0]
    assert _run(g) == 0, capsys.readouterr()
    out = capsys.readouterr().out
    want = 695 if gate == "honesty" else 7
    baseline_line = next(line for line in out.splitlines()
                         if line.startswith("baseline:"))
    assert f" {want} " in f"{baseline_line} ", (
        f"{gate} printed {baseline_line!r}; the baseline's total is the sum "
        f"of its counts, {want}")


def test_the_merge_that_found_it_sums_to_what_its_counts_say():
    assert rb.derived_total({"counts": MERGED}, 2) == 695
    with pytest.raises(rb.BaselineUnreadable, match=r"stores a total \(696\)"):
        rb.derived_total({"total": 696, "counts": MERGED}, 2)


# ── --update writes no total ─────────────────────────────────────────────────

@pytest.mark.parametrize("gate", PLANTERS)
def test_update_writes_no_total(gate, monkeypatch, tmp_path):
    counts = _counts_for(gate)
    g, path = PLANTERS[gate](monkeypatch, tmp_path,
                             {"total": 1, "counts": {}}, counts)
    monkeypatch.setattr(sys, "argv", [f"{gate}_gate.py", "--update"])
    assert _run(g) == 0
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "total" not in written, f"{gate} --update still stores a total"
    assert written["counts"] == counts


# ── what the gate cannot read is CANNOT CHECK, never a regression ───────────

@pytest.mark.parametrize("gate", PLANTERS)
@pytest.mark.parametrize("bad", [True, 1.5, "3", None])
def test_a_count_that_is_not_a_whole_number_is_refused(gate, bad, monkeypatch,
                                                       tmp_path, capsys):
    if gate == "honesty":
        record = {"counts": {"or-zero-coerce": {"a.py": bad}}}
    else:
        record = {"counts": {"E501": bad}}
    g = PLANTERS[gate](monkeypatch, tmp_path, record,
                       _counts_for(gate) if gate == "honesty" else None)[0]
    assert _run(g) == 2
    assert "not a whole number" in capsys.readouterr().err


@pytest.mark.parametrize("gate", PLANTERS)
def test_a_half_merged_baseline_is_refused_not_a_traceback(gate, monkeypatch,
                                                          tmp_path, capsys):
    """Conflict markers are the shape a bad merge of these files takes, and a
    `json.loads` traceback exits 1 -- which reads as "the code regressed"."""
    conflicted = ('{\n<<<<<<< HEAD\n  "counts": {"E501": 3}\n=======\n'
                  '  "counts": {"E501": 4}\n>>>>>>> branch\n}\n')
    g = PLANTERS[gate](monkeypatch, tmp_path, conflicted,
                       _counts_for(gate) if gate == "honesty" else None)[0]
    assert _run(g) == 2
    err = capsys.readouterr().err
    assert "CANNOT CHECK" in err and "does not read as JSON" in err, err


@pytest.mark.parametrize("gate", PLANTERS)
@pytest.mark.parametrize("planted", ["[]", "3", '"counts"'])
def test_a_baseline_that_is_not_an_object_is_refused(gate, planted, monkeypatch,
                                                     tmp_path, capsys):
    """Valid JSON that is not a baseline: the honesty gate reads a fingerprint
    off it before anything else, and a list has no `.get`."""
    g = PLANTERS[gate](monkeypatch, tmp_path, planted,
                       _counts_for(gate) if gate == "honesty" else None)[0]
    assert _run(g) == 2
    assert "CANNOT CHECK" in capsys.readouterr().err


@pytest.mark.parametrize("gate", PLANTERS)
def test_a_count_table_of_the_wrong_depth_is_refused(gate, monkeypatch, tmp_path,
                                                     capsys):
    """A flat table handed to the per-file gate (or a nested one to a per-rule
    gate) is not that gate's baseline, and comparing it would be guessing."""
    record = {"counts": FLAT} if gate == "honesty" else {"counts": MERGED}
    g = PLANTERS[gate](monkeypatch, tmp_path, record,
                       _counts_for(gate) if gate == "honesty" else None)[0]
    assert _run(g) == 2
    assert "CANNOT CHECK" in capsys.readouterr().err


def test_a_missing_count_table_is_refused():
    with pytest.raises(rb.BaselineUnreadable, match="no count table"):
        rb.derived_total({"files": 3})

"""A ratchet baseline's recorded total is the sum of the counts it records.

Git merges these files line by line. Two branches that each lower a
different per-file count, and each re-record the same total, merge with no
conflict into a file whose counts sum to one less than its total. Driven on
2026-09-26: integrating the time-stop slice onto the scan-card slice left the
honesty baseline's counts at 695 under a recorded total of 696.

Nothing else could see it. The gates compare per-file counts, and each count
was right, so all three were green. `test_claude_md_accuracy` compares the
figure CLAUDE.md quotes with the recorded total, and the two agreed, so the
prose quoted a number the tree did not have.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINES = ("ruff_baseline.json", "mypy_baseline.json", "honesty_baseline.json")


def _summed(counts: object) -> int:
    """The counts added up, however deep they nest (honesty keys by shape,
    then by file; ruff and mypy key by rule)."""
    if isinstance(counts, dict):
        return sum(_summed(v) for v in counts.values())
    if isinstance(counts, bool) or not isinstance(counts, int):
        raise TypeError(f"a count that is not a whole number: {counts!r}")
    return counts


def _disagreement(record: dict) -> str | None:
    total, summed = record["total"], _summed(record["counts"])
    if total == summed:
        return None
    return f"recorded total {total}, counts sum to {summed}"


@pytest.mark.parametrize("name", BASELINES)
def test_the_recorded_total_is_the_sum(name):
    record = json.loads((ROOT / "tests" / name).read_text(encoding="utf-8"))
    problem = _disagreement(record)
    assert problem is None, (
        f"{name}: {problem}. A merge that took both sides' counts keeps a "
        f"stale total; re-record the baseline with its gate's --update.")


def test_the_merge_that_found_it_is_refused():
    # The shape the merge left: two per-file decrements, one total.
    merged = {"total": 696, "counts": {"or-zero-coerce": {"a.py": 3, "b.py": 1},
                                       "get-default-zero": {"a.py": 691}}}
    assert _disagreement(merged) == "recorded total 696, counts sum to 695"
    merged["total"] = 695
    assert _disagreement(merged) is None


def test_a_flat_count_table_is_summed_too():
    assert _disagreement({"total": 7, "counts": {"E501": 4, "F401": 3}}) is None
    assert _disagreement({"total": 8, "counts": {"E501": 4, "F401": 3}})


@pytest.mark.parametrize("bad", [True, 1.5, "3", None])
def test_a_count_that_is_not_a_whole_number_is_refused(bad):
    with pytest.raises(TypeError):
        _summed({"x": bad})

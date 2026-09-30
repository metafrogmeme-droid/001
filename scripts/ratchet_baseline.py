"""The one reading of a whole-tree ratchet baseline: its counts, its total
(DERIVED, never stored) and how today's counts compare with it.

WHY NO BASELINE STORES A TOTAL ANY MORE
---------------------------------------
``tests/ruff_baseline.json``, ``tests/mypy_baseline.json`` and
``tests/honesty_baseline.json`` used to carry a ``"total"`` beside their
per-rule or per-file counts. Git merges these files line by line, so two
branches that each lowered a DIFFERENT count, and each re-recorded the total,
merged with no conflict into a file whose counts summed to one less than its
total: both decrements, one side's total. Driven on 2026-09-26, the honesty
baseline read 695 in its counts under a recorded 696.

Nothing could see it. Every gate compares counts, and each count was right, so
all three were green; the one reader of the total (the accuracy test over the
engineering log) agreed with it, so the prose quoted a number the tree did not
have. The first cure was a test that the two figures agree. A stored figure
that a clean merge can make wrong is a second answer, though, and the cure for
a second answer is having one: the total is summed from the counts wherever it
is printed or compared, and nothing writes it down.

WHAT A GATE DOES WITH A FILE THAT STILL STORES ONE
--------------------------------------------------
Refuses it, as CANNOT CHECK (exit 2). A ``"total"`` key means the file was
written by a gate from before this change -- most likely an older branch that
re-recorded and then merged -- and the key is exactly the field a merge gets
wrong. It is not a verdict about the code (the counts may be fine), which is
why it is the ``2`` of ``ruff_gate.check_version`` and not the ``1`` that means
something grew. ``scripts/rerecord.py --all`` (or the gate's own ``--update``)
re-records the file without it.

A baseline that does not parse, has no count table, nests to a different depth
than the gate's own counts, or holds a count that is not a whole number is
refused the same way: a baseline the gate cannot read is one it cannot compare
against, and that says nothing about whether the code grew.

ONE COMPARISON
--------------
``compare`` is the growth/improvement reading every gate prints and the one
``scripts/rerecord.py`` asks before it re-records anything. A second copy of
that judgement in the re-record command would be a second answer about whether
a count grew -- on the one decision where agreeing with the gate is the point.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

Key = tuple[str, ...]


class BaselineUnreadable(ValueError):
    """The baseline cannot be compared against. Its message says why."""


def read_record(path: Path) -> dict:
    """The baseline file as a JSON object, or ``BaselineUnreadable``.

    A file left half-merged (``<<<<<<<`` markers) or truncated is the shape a
    bad merge of these very files takes, and it used to escape as a traceback
    -- exit 1, which a launcher reads as "the code regressed".
    """
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineUnreadable(
            f"it does not read as JSON ({type(exc).__name__}). A half-merged "
            "file has to be resolved (either side's counts) before it can be "
            "compared or re-recorded") from None
    if not isinstance(record, dict):
        raise BaselineUnreadable("the baseline is not a JSON object")
    return record


def _flatten(counts: Any, depth: int, prefix: Key = ()) -> dict[Key, int]:
    """``{key path: count}`` for a count table exactly ``depth`` levels deep.

    ruff and mypy key by rule (depth 1); the honesty baselines key by shape and
    then by file (depth 2). A leaf that is not a whole number -- a bool
    included, since ``True`` is an ``int`` to Python and a count to nobody --
    or a table that nests to a different depth raises ``BaselineUnreadable``.
    """
    if depth == 0:
        if isinstance(counts, bool) or not isinstance(counts, int):
            raise BaselineUnreadable(
                f"a count that is not a whole number: {counts!r}")
        return {prefix: counts}
    if not isinstance(counts, Mapping):
        where = "/".join(prefix) or "the count table"
        raise BaselineUnreadable(
            f"{where} is not a table of counts ({counts!r})")
    out: dict[Key, int] = {}
    for key, value in counts.items():
        out.update(_flatten(value, depth - 1, prefix + (str(key),)))
    return out


def counts_table(record: Any, depth: int = 1) -> dict[Key, int]:
    """The recorded counts, flattened to ``{key path: count}``.

    Reads the counts only: a stored ``"total"`` is neither checked nor refused
    here, because re-recording such a file has to be able to compare its
    counts first. ``derived_total`` is the reading that refuses it.
    """
    if not isinstance(record, dict):
        raise BaselineUnreadable("the baseline is not a JSON object")
    counts = record.get("counts")
    if not isinstance(counts, Mapping):
        raise BaselineUnreadable("the baseline records no count table")
    return _flatten(counts, depth)


def summed(counts: Any, depth: int = 1) -> int:
    """A count table ``depth`` levels deep, added up (validated as a baseline's
    would be). The one sum both a gate and ``scripts/rerecord.py`` print."""
    return sum(_flatten(counts, depth).values())


def derived_total(record: Any, depth: int = 1) -> int:
    """The baseline's total, summed from its counts; refuses what it cannot read.

    Raises ``BaselineUnreadable`` for a stored ``"total"`` key (see the module
    docstring for why it is refused rather than checked) and for anything
    ``counts_table`` cannot read.
    """
    if isinstance(record, dict) and "total" in record:
        raise BaselineUnreadable(
            f"the baseline stores a total ({record['total']!r}). Totals are "
            "derived from the counts now, because a stored total is the field "
            "a clean git merge gets wrong. Re-record it: "
            "python3 scripts/rerecord.py --all")
    return sum(counts_table(record, depth).values())


def compare(now: Any, base: Any, depth: int = 1
            ) -> tuple[list[tuple[Key, int, int]], list[tuple[Key, int, int]]]:
    """``(grew, shrank)``: every key whose count went up, and every one that
    went down, as ``(key path, was, now)`` sorted by key path.

    ``now`` and ``base`` are nested count tables of the same ``depth``; both
    are read as ``counts_table`` reads one, so a malformed side raises
    ``BaselineUnreadable`` rather than comparing. A key absent on one side
    counts zero there: a rule that appears is growth and a file that clears is
    an improvement.
    """
    n, b = _flatten(now, depth), _flatten(base, depth)
    grew, shrank = [], []
    for key in sorted(set(n) | set(b)):
        is_, was = n.get(key, 0), b.get(key, 0)
        if is_ > was:
            grew.append((key, was, is_))
        elif is_ < was:
            shrank.append((key, was, is_))
    return grew, shrank

"""A field that is read but never written is a feature that never runs.

WHERE THIS CAME FROM.

`RuneClawEngine._last_scan_time` was initialised to 0.0 and assigned NOWHERE.
It is the sole input to the interactive freshness gate — the thing that lets a
"Latest Signal" tap answer instantly from the background sweep instead of
running a live 45s re-scan — and `_background_scan_is_fresh` refuses
`last_scan_time <= 0`. So the gate took its never-scanned branch on every tap,
on every deploy, for the entire life of the feature. Its own test file names
that case `test_never_scanned_is_not_fresh`; it was the only branch production
ever reached.

Nothing could see it. The tests all passed `last_scan_time` in as a PARAMETER,
so the pure function was thoroughly covered while the wiring was invisible —
and a test that supplies the input itself can never discover that nothing
supplies it in real life.

This is the same shape as `tests/test_no_new_unreachable_modules.py`, one level
down: there, a module nothing imports; here, a field nothing writes. Both are
properties of the CALLERS, so neither can be checked from inside the file that
declares them, and a green suite is no evidence either way.

WHY SCALARS ONLY.

A dict or list initialised once and then mutated (`self._pending[k] = v`) is
perfectly alive, and there are dozens of those. A scalar cannot be mutated in
place, so `written == 1` really does mean "the initialiser, and nothing else".
Restricting to scalars is what makes this checker quiet enough to be trusted:
it reports 0 across the whole tree today, and reported exactly 1 — the real
bug, nothing else — against the commit before the fix.

WHY ASSIGNMENTS ARE COUNTED TREE-WIDE.

A field may legitimately be written by a collaborator. `TelegramHandler` sets
three of the engine's (`_user_store`, `_proactive_monitor`,
`_monitor_stale_callback`) at startup, and the first draft of this sweep looked
only inside `engine.py` and accused all three. CLAUDE.md already records that
trap in the module checker: "a reachability checker with a blind spot
manufactures exactly the accusation it exists to prevent."
"""

from __future__ import annotations

import ast
import collections
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Only values a scalar sentinel can take. `[]`/`{}`/`set()` are excluded on
#: purpose — see the module docstring.
SENTINELS = frozenset({"0.0", "0", "None", "''", '""', "False"})

#: Fields allowed to be read-only despite the rule.
#:
#: EMPTY, and it should stay that way. If something genuinely belongs here it
#: needs a comment saying why, and — like `tests/known_failures.txt` and
#: `tests/unreachable_baseline.txt` — an entry that stops being necessary must
#: be deleted in the same commit that makes it unnecessary. A stale allowance
#: hides the next real one.
ALLOWED: frozenset[str] = frozenset()


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p) and "/.git/" not in str(p)
    ]


def _written_anywhere(files: list[pathlib.Path]) -> collections.Counter:
    """Every `x.ATTR = ...`, `x.ATTR[k] = ...`, and `setattr(x, "ATTR", ...)`.

    Tests are INCLUDED here. A field only ever written by a test is still a
    field production never writes, but a test that assigns it is evidence the
    author intended it to be settable from outside — and this checker exists to
    find the ones nobody writes at all, not to litigate that design.
    """
    out: collections.Counter = collections.Counter()
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "setattr" and len(n.args) >= 2
                    and isinstance(n.args[1], ast.Constant)):
                out[str(n.args[1].value)] += 1
                continue
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.For)):
                targets = [n.target]
            else:
                continue
            for t in targets:
                for node in ([t] + (list(t.elts) if isinstance(t, ast.Tuple) else [])):
                    if isinstance(node, ast.Attribute):
                        out[node.attr] += 1
                    elif (isinstance(node, ast.Subscript)
                          and isinstance(node.value, ast.Attribute)):
                        out[node.value.attr] += 1
    return out


def _read_in_production(files: list[pathlib.Path]) -> collections.Counter:
    """Every attribute LOAD, plus `getattr(x, "ATTR")`, outside the tests.

    Tests are excluded here on purpose: a field read only by its own test is
    not a feature anybody uses, and counting those would let a dead field hide
    behind the test written for it.
    """
    out: collections.Counter = collections.Counter()
    for p in files:
        if "/tests/" in str(p) or p.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                out[n.attr] += 1
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "getattr" and len(n.args) >= 2
                  and isinstance(n.args[1], ast.Constant)):
                out[str(n.args[1].value)] += 1
    return out


def read_only_fields(root: pathlib.Path) -> list[tuple[str, str, str, int]]:
    """(file:line, Class.attr, sentinel, read_count) for each offender."""
    files = _py_files(root)
    written = _written_anywhere(files)
    read = _read_in_production(files)

    found = []
    for p in files:
        if "/tests/" in str(p) or p.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            init = next((n for n in cls.body
                         if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
            if init is None:
                continue
            for n in ast.walk(init):
                if isinstance(n, ast.Assign):
                    targets, value = n.targets, n.value
                elif isinstance(n, ast.AnnAssign):
                    targets, value = [n.target], n.value
                else:
                    continue
                if value is None:
                    continue
                try:
                    literal = ast.unparse(value)
                except Exception:
                    continue
                if literal not in SENTINELS:
                    continue
                for t in targets:
                    if not (isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name) and t.value.id == "self"):
                        continue
                    attr = t.attr
                    if attr in ALLOWED:
                        continue
                    if written[attr] <= 1 and read[attr] >= 1:
                        rel = p.relative_to(root)
                        found.append((f"{rel}:{n.lineno}", f"{cls.name}.{attr}",
                                      literal, read[attr]))
    return found


# ── the ratchet ────────────────────────────────────────────────────────────

def test_no_field_is_read_but_never_written() -> None:
    offenders = read_only_fields(ROOT)
    assert offenders == [], (
        "these fields are initialised to a sentinel, read by production code, "
        "and assigned NOWHERE — so every reader takes the no-data branch "
        "forever, and no test can see it:\n  "
        + "\n  ".join(f"{loc}  {name} = {lit}  ({n} read(s))"
                      for loc, name, lit, n in offenders)
    )


# ── a check that cannot fail has not been tested ───────────────────────────

def test_the_checker_catches_a_planted_instance(tmp_path: pathlib.Path) -> None:
    """Plant the exact bug and require the sweep to report it.

    Validated against history too: run over the commit before the fix, this
    reports exactly one offender — `RuneClawEngine._last_scan_time` — and
    nothing else. A ratchet that has never been observed to fail is a green
    tick of unknown meaning, which is the failure mode the whole file is about.
    """
    (tmp_path / "thing.py").write_text(
        "class Thing:\n"
        "    def __init__(self):\n"
        "        self._never_written = 0.0\n"
        "\n"
        "    def read_it(self):\n"
        "        return self._never_written > 0\n",
        encoding="utf-8",
    )
    found = read_only_fields(tmp_path)
    assert [f[1] for f in found] == ["Thing._never_written"], found


def test_the_checker_does_not_flag_a_field_a_collaborator_writes(
        tmp_path: pathlib.Path) -> None:
    """The blind spot that would manufacture false accusations.

    `TelegramHandler` sets three of the engine's fields at startup. A sweep
    that looked only inside the declaring file called all three dead.
    """
    (tmp_path / "owner.py").write_text(
        "class Owner:\n"
        "    def __init__(self):\n"
        "        self._set_by_someone_else = None\n"
        "\n"
        "    def use(self):\n"
        "        return self._set_by_someone_else\n",
        encoding="utf-8",
    )
    (tmp_path / "wirer.py").write_text(
        "def wire(owner, value):\n"
        "    owner._set_by_someone_else = value\n",
        encoding="utf-8",
    )
    assert read_only_fields(tmp_path) == []


def test_the_checker_does_not_flag_a_mutated_container(tmp_path: pathlib.Path) -> None:
    """A dict written by subscript is alive; scalars are the whole scope."""
    (tmp_path / "bag.py").write_text(
        "class Bag:\n"
        "    def __init__(self):\n"
        "        self._items = {}\n"
        "        self._count = 0\n"
        "\n"
        "    def add(self, k, v):\n"
        "        self._items[k] = v\n"
        "\n"
        "    def read(self):\n"
        "        return self._items, self._count\n",
        encoding="utf-8",
    )
    # _items is mutated, _count is a scalar nothing writes — only the second
    # is a finding, and it proves the container exclusion is not just blanket
    # silence.
    assert [f[1] for f in read_only_fields(tmp_path)] == ["Bag._count"]


def test_a_field_only_its_own_test_reads_is_not_credited(
        tmp_path: pathlib.Path) -> None:
    """Tests do not count as readers.

    Otherwise a dead field hides behind the test written for it — the same
    reason the module reachability checker excludes them.
    """
    (tmp_path / "thing.py").write_text(
        "class Thing:\n"
        "    def __init__(self):\n"
        "        self._orphan = 0.0\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_orphan.py").write_text(
        "from thing import Thing\n"
        "def test_it():\n"
        "    assert Thing()._orphan == 0.0\n",
        encoding="utf-8",
    )
    # Read ONLY by a test → not reported (nothing in production depends on it,
    # so there is no surface silently taking a no-data branch).
    assert read_only_fields(tmp_path) == []

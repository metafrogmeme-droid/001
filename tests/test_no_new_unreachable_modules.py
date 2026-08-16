"""A module nothing calls is indistinguishable from one that does not work.

THE LESSON THIS ENCODES

`token_dossier`, `presale_claims` and `deployer_history` were pure, correct,
heavily tested, and imported by zero non-test modules. Four scorers and a
composer, seventy-seven tests between them, and no human could reach any of
them — every dossier they were built to produce did not exist. The tests all
passed the whole time, because tests were the only caller.

That gap was found by hand, once. This finds it every run.

WHY A RATCHET AND NOT A BAN

Twenty-five modules are in that state today, and they are not equally wrong.
`bot/learning/*` may be a research area nobody has wired on purpose;
`bot/guardian/integrity_veto.py` is a veto-only safety control with an
`off/shadow/enforce` mode switch, which is a different and more uncomfortable
thing to find uncalled. Deciding each one is a judgement call per module, and
guessing would be worse than recording — so they are baselined, exactly as
`tests/known_failures.txt` and the npm/cargo advisory gates do it, and only
DRIFT fails.

BOTH DIRECTIONS FAIL, WHICH IS THE POINT

* a NEW test-only module means somebody just built another scorer nobody
  calls, and the moment to notice is now rather than a year later;
* a module that LEAVES the list — someone wired it up — must be removed from
  the baseline in the same commit. A stale entry is how a list stops meaning
  anything, and `known_failures.txt` fails the same way for the same reason.

A NOTE ON THE DETECTOR, WHICH WAS WRONG FIRST

Its first version scanned only `bot/` and `scripts/` for importers and declared
`bot/api/auth_routes.py` dead — it is mounted by `api_bridge.py`, at the repo
root, which was not being read at all. A reachability checker with a blind spot
manufactures exactly the accusation it exists to prevent, so it now reads every
Python file in the tree.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).parent / "unreachable_baseline.txt"

SKIP_DIRS = {"__pycache__", "node_modules", ".git", "venv", ".venv", "target",
             "build", "dist", ".pytest_cache"}

#: Only these trees are CANDIDATES. Root-level scripts and app/ are importers
#: but never subjects — they are launched, not imported.
CANDIDATE_ROOTS = ("bot", "scripts")


def _py_files():
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO)).replace(os.sep, "/")


def _imported_names(src: str) -> set:
    """Every dotted module path this source could be importing.

    Covers the three shapes that actually appear here — `import a.b`,
    `from a.b import c` (which reaches BOTH `a.b` and `a.b.c`, since `c` may be
    a module), and `importlib.import_module("a.b")` — plus any dotted `bot.…`
    string, because a registry that imports by name is still a caller.
    """
    names = set()
    for m in re.finditer(r"^\s*import\s+([\w.,\s]+)", src, re.M):
        for part in m.group(1).split(","):
            part = part.strip().split(" as ")[0].strip()
            if part:
                names.add(part)
    for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+(.+)$", src, re.M):
        pkg = m.group(1)
        names.add(pkg)
        tail = m.group(2).split("#")[0]
        for part in tail.replace("(", " ").replace(")", " ").split(","):
            part = part.strip().split(" as ")[0].strip()
            if part and part != "*":
                names.add(f"{pkg}.{part}")
    for m in re.finditer(r"[\"']((?:bot|scripts)\.[\w.]+)[\"']", src):
        names.add(m.group(1))
    return names


def unreachable_modules() -> set:
    """Candidate modules with no importer outside `tests/`.

    Entry points are excluded: a module with a `__main__` guard is run, not
    imported, and calling it unreachable would be the same false accusation the
    detector's first version made.
    """
    candidates = {}
    sources = {}
    for path in _py_files():
        rel = _rel(path)
        try:
            sources[rel] = path.read_text(encoding="utf-8")
        except Exception:                                          # noqa: BLE001
            continue
        if (rel.startswith(CANDIDATE_ROOTS) and not rel.endswith("__init__.py")
                and '__main__' not in sources[rel]):
            candidates[rel] = rel[:-3].replace("/", ".")

    imported = set()
    for rel, src in sources.items():
        if rel.startswith("tests/"):
            continue                       # a test caller is not reachability
        imported |= _imported_names(src)

    return {rel for rel, mod in candidates.items()
            if mod not in imported and rel not in imported}


def _baseline() -> set:
    if not BASELINE.exists():
        return set()
    return {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def test_no_new_module_becomes_reachable_only_from_tests():
    """A scorer nobody calls is the defect. New ones fail here."""
    new = sorted(unreachable_modules() - _baseline())
    assert not new, (
        "these modules are imported by tests and by nothing else — they cannot "
        "run in production, and their passing tests say nothing about that:\n  "
        + "\n  ".join(new)
        + "\n\nWire it to a caller, or add it to tests/unreachable_baseline.txt "
          "with a reason if it is deliberately not wired yet.")


def test_the_baseline_has_no_stale_entries():
    """Wiring one up must remove it from the list, in the same commit.

    Same rule as `known_failures.txt`: an entry that starts passing is a hard
    failure, because a list carrying names that are no longer true is a list
    nobody reads.
    """
    fixed = sorted(_baseline() - unreachable_modules())
    assert not fixed, (
        "these are no longer test-only — something imports them now. Remove "
        "them from tests/unreachable_baseline.txt:\n  " + "\n  ".join(fixed))


def test_the_detector_reads_the_whole_tree():
    """Its first version scanned only bot/ and scripts/ for importers.

    `bot/api/auth_routes.py` is mounted by `api_bridge.py` at the repo root,
    which was not being read — so a live router was reported as dead code. A
    reachability checker with a blind spot manufactures the accusation it
    exists to prevent.
    """
    unreachable = unreachable_modules()
    assert "bot/api/auth_routes.py" not in unreachable, (
        "the router mounted by api_bridge.py is being called unreachable — "
        "the importer sweep has lost the repo root again")
    assert "bot/core/token_research.py" not in unreachable, (
        "token_research is reached from the Telegram handler's /token command")


def test_claude_md_states_the_real_count():
    """CLAUDE.md cites the size of this list, so the citation is pinned.

    The repo already does this to itself — the preflight gate count in
    CLAUDE.md is pinned by `test_claude_md_accuracy.py`, and it "failed the
    moment they did". A number in prose is the part that rots first, and a doc
    that confidently states a stale one is the same defect as a panel printing
    a stale figure.
    """
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"`tests/unreachable_baseline\.txt`[^)]*?\*\*(\d+)\*\* modules", text)
    assert m, "CLAUDE.md no longer states the baseline size — restore it or drop the claim"
    assert int(m.group(1)) == len(_baseline()), (
        f"CLAUDE.md says {m.group(1)} modules; the baseline holds {len(_baseline())}")


def test_entry_points_are_not_called_dead():
    """`python -m bot.main` is a caller, even though nothing imports it."""
    unreachable = unreachable_modules()
    for entry in ("bot/main.py", "scripts/preflight.py", "scripts/ci_test_gate.py"):
        assert entry not in unreachable, f"{entry} is launched, not imported"

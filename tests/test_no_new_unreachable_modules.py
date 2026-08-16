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


def _resolve_relative(pkg: str, dots: int, tail: str) -> str:
    """`from ..x import y` inside a package -> the absolute dotted path.

    `dots` is the leading-dot count: one dot means "this package", two means
    the parent, and so on.
    """
    parts = pkg.split(".") if pkg else []
    up = dots - 1
    base = parts[:len(parts) - up] if up else parts
    return ".".join([p for p in (base + ([tail] if tail else [])) if p])


def _imported_names(src: str, rel_path: str = "") -> set:
    """Every dotted module path this source could be importing.

    Covers `import a.b`, `from a.b import c` (which reaches BOTH `a.b` and
    `a.b.c`, since `c` may be a module), `importlib.import_module("a.b")`, any
    dotted `bot.…` string (a registry importing by name is still a caller) —
    and RELATIVE imports, which need `rel_path` to resolve.

    THE RELATIVE CASE IS NOT A DETAIL; ITS ABSENCE FALSELY ACCUSED TEN MODULES.

    `bot/learning/orchestrator.py` — which `bot/core/engine.py` instantiates on
    every run — reaches ten siblings as `from .experience import ...`. The
    first version of this function recorded that as a module literally named
    `.experience`, which matches no candidate, so all ten were reported as
    "imported by tests and nothing else" and written into the baseline under
    the heading "an entire subsystem with no caller". The subsystem was running
    the whole time.

    That is precisely the failure this file's own docstring warns about one
    paragraph up — a reachability checker with a blind spot manufactures the
    accusation it exists to prevent — committed a second time, by the person
    who wrote the warning. Hence `test_relative_imports_count_as_reachability`
    below: the rule needed a test, not another paragraph.
    """
    names = set()
    for m in re.finditer(r"^\s*import\s+([\w.,\s]+)", src, re.M):
        for part in m.group(1).split(","):
            part = part.strip().split(" as ")[0].strip()
            if part:
                names.add(part)

    # The importing module's own package, for resolving leading dots.
    pkg = ""
    if rel_path.endswith(".py"):
        parts = rel_path[:-3].split("/")
        pkg = ".".join(parts[:-1])

    for m in re.finditer(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)$", src, re.M):
        dots, mod, tail = m.group(1), m.group(2), m.group(3)
        if dots:
            if not pkg:
                continue                    # a relative import with no package
            base = _resolve_relative(pkg, len(dots), mod)
        else:
            base = mod
        if not base:
            continue
        names.add(base)
        for part in tail.split("#")[0].replace("(", " ").replace(")", " ").split(","):
            part = part.strip().split(" as ")[0].strip()
            if part and part != "*":
                names.add(f"{base}.{part}")

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
        imported |= _imported_names(src, rel)

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


def test_relative_imports_count_as_reachability():
    """`from .sibling import X` is an import. The detector once thought not.

    THE SECOND BLIND SPOT, FOUND THE SAME WAY AS THE FIRST.

    `_imported_names` matched `from ([\\w.]+) import`, which captures
    `.experience` for `from .experience import ExperienceMemory` and records a
    module by that literal name — matching no candidate. So every module
    reached only from a sibling looked unreachable.

    It put all ten leaves of `bot/learning/` into the baseline under the
    heading "an entire subsystem with no caller", when
    `bot/core/engine.py:354` constructs `LearningOrchestrator()` on every run
    and the orchestrator imports all ten. The subsystem was live the whole
    time, and the list said the opposite in prose.

    The lesson had already been written into this file's docstring after the
    api_bridge miss, and writing it down did not prevent the repeat. So it is
    a test now.
    """
    src = "from .experience import ExperienceMemory\nfrom ..core import engine\n"
    names = _imported_names(src, "bot/learning/orchestrator.py")
    assert "bot.learning.experience" in names, "one dot means this package"
    assert "bot.core" in names, "two dots means the parent package"
    assert "bot.core.engine" in names

    # Deeper nesting, and a bare `from . import x`.
    names = _imported_names("from . import store\n", "bot/learning/orchestrator.py")
    assert "bot.learning.store" in names

    # And the real thing, end to end.
    unreachable = unreachable_modules()
    for leaf in ("experience", "reflection", "safety_policy", "strategy_eval",
                 "patterns", "models"):
        assert f"bot/learning/{leaf}.py" not in unreachable, (
            f"bot/learning/{leaf}.py is imported by orchestrator.py, which "
            "bot/core/engine.py instantiates — calling it unreachable is the "
            "detector accusing live code again")


def test_absolute_imports_still_work():
    """The relative fix must not have cost the ordinary case."""
    names = _imported_names("from bot.core import engine\nimport bot.risk.portfolio\n",
                            "scripts/whatever.py")
    assert "bot.core" in names and "bot.core.engine" in names
    assert "bot.risk.portfolio" in names


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

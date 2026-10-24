"""The source-scan counts, derived from the tree.

Two walks. Neither result is written back into the engineering log: a number
in that prose was the part that rotted, and every new test file had to edit
it. ``scripts/ci_test_gate.py`` prints what these functions return.

The rules are the ones ``tests/test_claude_md_accuracy.py`` used to compare
against the quoted integers. Changing a rule changes the count, so the tests
drive planted trees through the same functions rather than through a second
copy of the walk.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_COPY_NAMES = frozenset({"code_only", "_code_only", "strip_comments", "_strip_comments"})
_TOKENIZE_SPELLINGS = ("tokenize.generate_tokens", "tokenize.tokenize")


@dataclass(frozen=True)
class SourceScanAdoption:
    """Importers of ``source_scan``, private tokenize copies, and the overlap.

    ``copies`` is one entry per function (``relative/path.py:line``), which is
    what the old prose called a file: each file held one. ``both`` is the
    number of files that import the module and still define such a function.
    """

    importers: frozenset[str]
    copies: tuple[str, ...]
    both: int


@dataclass(frozen=True)
class SourceScanningFiles:
    """``test_*.py`` files whose AST imports or calls a reader, and all of them.

    A mention in a docstring is not a reach. A hand-rolled ``read_text()`` is
    not a reach either, so ``reach`` is a floor.
    """

    reach: frozenset[str]
    files: frozenset[str]


def _py_under(root: Path, directory: str) -> list[Path]:
    base = root / directory
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.py") if p.is_file())


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def source_scan_adoption(root: Path | None = None) -> SourceScanAdoption:
    """(importers, private tokenize copies, files that are both).

    DERIVED, never restated. A copy is a function named like the shared one
    that TOKENIZES for itself. BOTH spellings count: ``tokenize.generate_tokens``
    and ``tokenize.tokenize``. A walk that knew only the first undercounted.
    ``source_scan.py`` itself is the shared reading and is not a copy of it.

    ImportFrom only, and the module name must end with ``source_scan``. A bare
    ``import source_scan`` is a different statement and is not an importer
    here; widening the walk would be a different count.
    """
    root = ROOT if root is None else root
    importers: set[str] = set()
    copies: list[str] = []
    both = 0
    for path in _py_under(root, "tests") + _py_under(root, "scripts"):
        if path.name == "source_scan.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = _relative(root, path)
        imports_it = any(
            isinstance(n, ast.ImportFrom) and (n.module or "").endswith("source_scan")
            for n in ast.walk(tree))
        if imports_it:
            importers.add(rel)
        owns = [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name in _COPY_NAMES
            and any(spelling in ast.unparse(n) for spelling in _TOKENIZE_SPELLINGS)
        ]
        copies.extend(f"{rel}:{n.lineno}" for n in owns)
        if owns and imports_it:
            both += 1
    return SourceScanAdoption(frozenset(importers), tuple(copies), both)


def _reaches_for_source(path: Path) -> bool:
    """Imports or CALLS one of the readers — not merely names one.

    A docstring mentioning ``code_only`` is a string token, not a use. An AST
    walk does not count it. ``inspect.getsource`` counts because it is a call
    that reads source text.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and "source_scan" in (n.module or ""):
            return True
        if isinstance(n, ast.Import):
            if any("source_scan" in a.name for a in n.names):
                return True
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in ("code_only", "_code_only"):
                return True
            if isinstance(f, ast.Attribute) and f.attr in (
                    "code_only", "_code_only", "getsource"):
                return True
    return False


def source_scanning_files(root: Path | None = None) -> SourceScanningFiles:
    """Files under ``tests/`` named ``test_*.py`` that reach for source text."""
    root = ROOT if root is None else root
    files = [p for p in _py_under(root, "tests") if p.name.startswith("test_")]
    reach = frozenset(_relative(root, p) for p in files if _reaches_for_source(p))
    return SourceScanningFiles(reach, frozenset(_relative(root, p) for p in files))


def format_census(root: Path | None = None) -> str:
    """One line. The integers live here, in the run, not in the log."""
    adoption = source_scan_adoption(root)
    scanning = source_scanning_files(root)
    return (
        "[source-scan] "
        f"importers={len(adoption.importers)} "
        f"private_copies={len(adoption.copies)} "
        f"both={adoption.both} "
        f"reach={len(scanning.reach)} "
        f"of {len(scanning.files)} test files"
    )

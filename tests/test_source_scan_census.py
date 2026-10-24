"""The source-scan walk, driven on a planted tree and on the files the log names.

The engineering log used to quote the live integers. Every new test file
edited the quote, and the quote was only a proxy for this walk. The walk is
the count now. A rule that is not reached by the real tree is planted, because
a rule no input can reach is a claim that there is a check.
"""

from __future__ import annotations

from pathlib import Path

from tests.source_scan_census import (
    format_census,
    source_scan_adoption,
    source_scanning_files,
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _plant(root: Path) -> None:
    _write(root, "tests/source_scan.py", (
        "from tests.source_scan import code_only\n"
        "def code_only(src):\n"
        "    import tokenize\n"
        "    tokenize.generate_tokens(None)\n"
    ))
    _write(root, "tests/test_imports_it.py", "from tests.source_scan import code_only\n")
    _write(root, "tests/test_generate.py", (
        "def code_only(src):\n"
        "    import tokenize\n"
        "    tokenize.generate_tokens(None)\n"
    ))
    _write(root, "tests/test_tokenize_spelling.py", (
        "def _strip_comments(src):\n"
        "    import tokenize\n"
        "    tokenize.tokenize(None)\n"
    ))
    _write(root, "tests/test_both.py", (
        "from tests.source_scan import handler_sources\n"
        "def strip_comments(src):\n"
        "    import tokenize\n"
        "    return tokenize.generate_tokens(None)\n"
    ))
    _write(root, "tests/test_doc_only.py", (
        '"""mentions code_only and tokenize.generate_tokens but calls neither."""\n'
        "def test_ok():\n"
        "    return 1\n"
    ))
    _write(root, "tests/test_named_but_hand_rolled.py", (
        "def _code_only(src):\n"
        "    return src.replace('#', '')\n"
    ))
    _write(root, "tests/test_other_function.py", (
        "def helper(src):\n"
        "    import tokenize\n"
        "    tokenize.generate_tokens(None)\n"
    ))
    _write(root, "tests/test_bare_import.py", "import source_scan\n")
    _write(root, "tests/test_calls_it.py", (
        "def test_x(m):\n"
        "    m.code_only('x')\n"
        "    m._code_only('y')\n"
    ))
    _write(root, "tests/test_getsource.py", (
        "def test_x():\n"
        "    import inspect\n"
        "    inspect.getsource(test_x)\n"
    ))
    _write(root, "scripts/uses_it.py", "from tests.source_scan import code_only\n")
    _write(root, "scripts/source_scan.py", "from tests.source_scan import code_only\n")
    _write(root, "bot/ignored.py", "from tests.source_scan import code_only\n")
    _write(root, "tests/not_a_test_module.py", "from tests.source_scan import code_only\n")


def test_a_planted_tree_is_counted_by_the_rules_the_log_names(tmp_path):
    """Each arm the walk claims, including the ones the real tree cannot separate."""
    _plant(tmp_path)
    adoption = source_scan_adoption(tmp_path)
    assert adoption.importers == frozenset({
        "tests/test_imports_it.py",
        "tests/test_both.py",
        "tests/not_a_test_module.py",
        "scripts/uses_it.py",
    }), sorted(adoption.importers)
    # The shared module is skipped even though it imports and tokenizes.
    # A bare `import source_scan` is not an ImportFrom. bot/ is not walked.
    # scripts/source_scan.py is skipped by file name.
    assert "tests/source_scan.py" not in adoption.importers
    assert "tests/test_bare_import.py" not in adoption.importers
    assert "bot/ignored.py" not in adoption.importers
    assert not any(item.startswith("scripts/source_scan.py:") for item in adoption.copies)
    assert not any(item.startswith("tests/source_scan.py:") for item in adoption.copies)

    copies = set(adoption.copies)
    assert "tests/test_generate.py:1" in copies
    assert "tests/test_tokenize_spelling.py:1" in copies
    assert "tests/test_both.py:2" in copies
    assert not any("test_named_but_hand_rolled.py" in item for item in copies), (
        "a function with the right name that does not tokenize is not a copy")
    assert not any("test_other_function.py" in item for item in copies), (
        "tokenize inside a differently named function is not a copy")
    assert not any("test_doc_only.py" in item for item in copies)
    assert adoption.both == 1

    scanning = source_scanning_files(tmp_path)
    assert scanning.reach == frozenset({
        "tests/test_imports_it.py",
        "tests/test_both.py",
        "tests/test_bare_import.py",
        "tests/test_calls_it.py",
        "tests/test_getsource.py",
    }), sorted(scanning.reach)
    assert "tests/test_doc_only.py" in scanning.files
    assert "tests/test_doc_only.py" not in scanning.reach, (
        "a docstring that names the reader is not a reach")
    assert "tests/not_a_test_module.py" not in scanning.files, (
        "the wholesale count is test_*.py, not every module under tests/")
    assert "tests/test_named_but_hand_rolled.py" in scanning.files
    assert "tests/test_named_but_hand_rolled.py" not in scanning.reach


def test_the_printed_line_is_that_walk(tmp_path):
    """One reading. The line the gate prints is the lengths of this walk."""
    _plant(tmp_path)
    adoption = source_scan_adoption(tmp_path)
    scanning = source_scanning_files(tmp_path)
    line = format_census(tmp_path)
    assert line == (
        "[source-scan] "
        f"importers={len(adoption.importers)} "
        f"private_copies={len(adoption.copies)} "
        f"both={adoption.both} "
        f"reach={len(scanning.reach)} "
        f"of {len(scanning.files)} test files"
    )


def test_the_real_tree_still_matches_the_paragraphs_examples():
    """The files the log names, on the tree that ships, not only on a fixture."""
    adoption = source_scan_adoption()
    assert any(
        item.startswith("tests/test_preflight_matches_ci.py:") for item in adoption.copies)
    assert "tests/test_preflight_matches_ci.py" not in adoption.importers
    assert not any("test_black_swan_is_reached.py" in item for item in adoption.copies)
    assert "tests/test_drawdown_limit_label.py" in adoption.importers

    scanning = source_scanning_files()
    assert "tests/test_preflight_matches_ci.py" in scanning.reach
    assert "tests/test_drawdown_limit_label.py" in scanning.reach
    assert scanning.reach < scanning.files

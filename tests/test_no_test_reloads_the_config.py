"""No test reloads `bot.config` in-process.

`importlib.reload(bot.config)` builds a new CONFIG, a new RUNTIME and a new
Config class, while every module that ran `from bot.config import CONFIG` at
import keeps the old objects. Later tests in the same run then patch or read
objects nothing else uses, and fail only in a full run. The flake filter
forgives them because they pass alone, so the leak hides behind a green gate.

It happened twice. tests/test_bg_scan_valve.py moved to a subprocess for this
reason, and then tests/test_voter_correlation.py reloaded the config anyway:
test_web_reports' stance test and two yield-card tests failed in every full CI
run until it was found. Read an import-time default in a subprocess instead.

An AST walk over raw source, so a comment or docstring that names the pattern
is not a hit.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _config_reloads(source: str) -> list[int]:
    """Line numbers of reload() calls whose argument is bot.config."""
    tree = ast.parse(source)
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "bot.config" and a.asname:
                    aliases.add(a.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "bot":
            for a in node.names:
                if a.name == "config":
                    aliases.add(a.asname or "config")

    def is_config(arg: ast.expr) -> bool:
        if isinstance(arg, ast.Name):
            return arg.id in aliases
        return (isinstance(arg, ast.Attribute) and arg.attr == "config"
                and isinstance(arg.value, ast.Name) and arg.value.id == "bot")

    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name == "reload" and is_config(node.args[0]):
            hits.append(node.lineno)
    return hits


def test_the_scan_finds_each_spelling_and_ignores_other_modules():
    planted = (
        "import importlib\n"
        "import bot.config as config\n"
        "import bot.config\n"
        "from bot import config as cfg\n"
        "import bot.analyzer as analyzer\n"
        "importlib.reload(config)\n"
        "importlib.reload(bot.config)\n"
        "importlib.reload(cfg)\n"
        "importlib.reload(analyzer)\n"
        "# importlib.reload(config) in a comment\n"
    )
    assert _config_reloads(planted) == [6, 7, 8]


def test_no_test_reloads_bot_config():
    offenders = []
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for line in _config_reloads(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "these tests reload bot.config in-process, which leaves later tests "
        "reading stale CONFIG/RUNTIME objects; use a subprocess instead: "
        + ", ".join(offenders))

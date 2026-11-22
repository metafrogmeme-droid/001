"""The AudaceFi publisher cannot reach the order path, a live flag, or a key.

Three independent holds, each shown to catch what it looks for:

1. IMPORT CLOSURE. No module the package can import, at any depth, is one of
   the modules that hold the bot's order path. The walk is the position
   watchdog's (`tests/test_the_position_watchdog_only_reads.py::closure`), so
   the two guards cannot disagree about what "reachable" means.
2. RUNTIME. Importing the package in a fresh interpreter leaves the executor
   and the engine out of ``sys.modules`` -- the closure is a static reading,
   and this is the same question asked of Python itself.
3. VOCABULARY. The package's code names no confirm door, no live switch, no
   key, secret or wallet, reads no such environment variable, imports no
   venue or HTTP client library, and names no host but the loopback address.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.source_scan import code_only
from tests.test_the_position_watchdog_only_reads import ORDER_PATH, closure

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "bot" / "integrations" / "audacefi"
START = "bot.integrations.audacefi.publisher"


def _package_files() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


# ── 1. the import closure ────────────────────────────────────────────────────

def test_the_package_is_in_its_own_closure():
    # A walk that found nothing would pass every assertion below.
    reached = closure(START)
    for f in _package_files():
        mod = "bot.integrations.audacefi" + ("" if f.stem == "__init__" else f".{f.stem}")
        assert mod in reached, mod


@pytest.mark.parametrize("module", ORDER_PATH)
def test_no_order_path_module_is_reachable(module):
    assert module not in closure(START)


def test_the_walk_sees_a_planted_import_of_the_executor(tmp_path):
    pkg = tmp_path / "bot" / "integrations" / "audacefi"
    pkg.mkdir(parents=True)
    for name in ("bot", "bot/integrations"):
        (tmp_path / name / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "publisher.py").write_text("from bot.core import live_executor\n")
    (tmp_path / "bot" / "core").mkdir()
    (tmp_path / "bot" / "core" / "__init__.py").write_text("")
    (tmp_path / "bot" / "core" / "live_executor.py").write_text("")
    assert "bot.core.live_executor" in closure(START, root=tmp_path)


# ── 2. the interpreter agrees ────────────────────────────────────────────────

def test_importing_the_package_loads_neither_the_executor_nor_the_engine():
    code = ("import sys, bot.integrations.audacefi.publisher as p; "
            "print(sorted(m for m in ('bot.core.live_executor', 'bot.core.engine') "
            "if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, timeout=120, check=True)
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


# ── 3. the vocabulary ────────────────────────────────────────────────────────

#: Names the package must never mention in code (comments and docstrings are
#: stripped first, so the module docstring may say what it does not do).
FORBIDDEN_NAMES = re.compile(
    r"confirm_trade|live_executor|golive|live_mode|is_live|live_suspended|"
    r"AUTO_CONFIRM_LIVE|private_key|api_key|apikey|secret|wallet|keypair|mnemonic|"
    r"solana|solders", re.IGNORECASE)
#: `bot.config.RUNTIME` (live mode, the auto-confirm threshold) by its own
#: spelling: case-sensitive and whole-word, so `RuntimeError` is not it.
FORBIDDEN_RUNTIME = re.compile(r"\bRUNTIME\b")
#: An environment variable whose name says it is a live switch or a credential.
FORBIDDEN_ENV = re.compile(r"LIVE|KEY|SECRET|TOKEN|WALLET|PRIVATE|PASS", re.IGNORECASE)
FORBIDDEN_IMPORTS = ("requests", "httpx", "aiohttp", "urllib", "ccxt", "websockets",
                     "solana", "solders", "web3", "socket")
_HOST = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|localhost|https?://", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    """ids of docstring nodes: prose about the package, not its code."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _string_constants(tree: ast.AST) -> list[str]:
    skip = _docstrings(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


def _violations(src: str) -> list[str]:
    """Every rule the source breaks, by name. Shared by the real scan and the plants."""
    out: list[str] = []
    code = code_only(src)
    for m in FORBIDDEN_NAMES.finditer(code):
        out.append(f"name:{m.group(0)}")
    for m in FORBIDDEN_RUNTIME.finditer(code):
        out.append(f"name:{m.group(0)}")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            mods = []
        for mod in mods:
            if mod.split(".")[0] in FORBIDDEN_IMPORTS:
                out.append(f"import:{mod}")
        if isinstance(node, ast.Call):
            fn = ast.unparse(node.func)
            if fn in ("os.getenv", "os.environ.get", "getenv", "_env_bool",
                      "_env_float_bounded", "_env_float", "_env", "_env_switch"):
                if node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str) \
                        and FORBIDDEN_ENV.search(node.args[0].value):
                    out.append(f"env:{node.args[0].value}")
        if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
            out.append("env:os.environ[...]")
    for text in _string_constants(tree):
        for h in _HOST.findall(text):
            if h != "127.0.0.1":
                out.append(f"host:{h}")
    return out


@pytest.mark.parametrize("path", _package_files(), ids=lambda p: p.name)
def test_the_package_breaks_none_of_the_rules(path):
    assert _violations(path.read_text(encoding="utf-8")) == []


def test_the_only_host_it_names_is_loopback_and_it_is_named_once():
    hits = [(f.name, h) for f in _package_files()
            for text in _string_constants(ast.parse(f.read_text(encoding="utf-8")))
            for h in _HOST.findall(text)]
    assert hits == [("settings.py", "127.0.0.1")]


@pytest.mark.parametrize("plant,rule", [
    ("from bot.core.engine import RuneClawEngine\nRuneClawEngine.confirm_trade\n", "name:confirm_trade"),
    ("import os\nos.getenv('AUTO_CONFIRM_LIVE_ENABLED')\n", "env:AUTO_CONFIRM_LIVE_ENABLED"),
    ("import os\nx = os.environ['ANY']\n", "env:os.environ[...]"),
    ("import requests\n", "import:requests"),
    ("import urllib.request\n", "import:urllib.request"),
    ("HOST = '10.0.0.5'\n", "host:10.0.0.5"),
    ("URL = 'https://gateway.example'\n", "host:https://"),
    ("\"\"\"doc 10.0.0.5\"\"\"\nHOST = 'localhost'\n", "host:localhost"),
    ("from bot.config import _env_bool\n_env_bool('SOL_WALLET_PATH')\n", "env:SOL_WALLET_PATH"),
    ("def f(golive=False):\n    return golive\n", "name:golive"),
    ("from bot.config import RUNTIME\nRUNTIME.live_mode\n", "name:RUNTIME"),
])
def test_each_rule_catches_a_planted_violation(plant, rule):
    assert rule in _violations(plant), _violations(plant)


def test_an_exception_class_is_not_the_runtime_switch():
    assert _violations("raise RuntimeError('x')\n") == []


def test_a_comment_that_names_a_forbidden_thing_is_not_a_violation():
    assert _violations("# never calls confirm_trade or reads a wallet\nX = 1\n") == []

"""Every mixin the handler class is made of, held to the same rules.

The 15,000-line handler is being split one command group at a time, and each
group leaves as a MIXIN — a class the handler inherits, whose methods read
`self.engine`, gate on `self._is_admin` and answer through `self._send`, the
F-15 redaction chokepoint. The first group (`guardian_commands.py`) got a
hand-written split test; this file is that test made generic, derived from
`TelegramHandler.__mro__`, so the next group is held to the rules the moment
it is added and nobody has to remember to write the pin:

  1. the handler reaches the mixin's own objects — the same function under
     the same name — and defines none of them itself, so no method is
     silently shadowed by a stale copy left behind;
  2. every `_cmd_*` a mixin defines is still REGISTERED in `build_app`. A
     method that moved out of the file and out of the registration at the
     same time would be present, tested, and dispatched by nothing (#999);
  3. the host contract is declarations only, under `TYPE_CHECKING`, and
     every name in it is one the handler really provides, with the
     parameters it really takes — a stub for a method the handler lacks
     type-checks fine and fails at the first tap;
  4. a mixin never defines `_send`, `_is_admin`, `_lang` or `_guard`: a
     second redaction chokepoint or a second auth gate is the shape the
     redaction and guard tests exist to forbid;
  5. every `self.<name>(` a mixin calls resolves on the handler — the
     `test_handler_methods_exist` rule, applied per mixin;
  6. a mixin imports nothing from the handler, at module level or lazily:
     the split exists to remove that cycle, and the leaf helpers a mixin
     needs (`exc_text`, `command_guard`) exist so it never has to.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from bot.skills.telegram_handler import TelegramHandler
from tests.source_scan import code_only

HOST_ONLY = ("_send", "_is_admin", "_lang", "_guard")

MIXINS = [cls for cls in TelegramHandler.__mro__[1:] if cls is not object]
HANDLER_SRC = code_only(Path(inspect.getsourcefile(TelegramHandler)).read_text(encoding="utf-8"))


def _class_node(cls) -> ast.ClassDef:
    tree = ast.parse(Path(inspect.getsourcefile(cls)).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
            return node
    raise AssertionError(f"{cls.__name__} is not a top-level class of its module")


def _split_body(cls):
    """(methods defined for real, {stub name: parameter names or None})."""
    real, stubs = set(), {}
    for stmt in _class_node(cls).body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            real.add(stmt.name)
        elif isinstance(stmt, ast.If) and getattr(stmt.test, "id", "") == "TYPE_CHECKING":
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stubs[sub.name] = [a.arg for a in sub.args.args]
                    assert all(isinstance(b, ast.Expr) for b in sub.body), (
                        f"{cls.__name__}.{sub.name}: a stub under TYPE_CHECKING carries a body")
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    stubs[sub.target.id] = None
    return real, stubs


def test_there_is_at_least_one_mixin():
    """Vacuous-pass check: a parametrised file over an empty list is green."""
    assert MIXINS, "the handler inherits nothing — the split's slices are gone"


@pytest.mark.parametrize("cls", MIXINS, ids=lambda c: c.__name__)
def test_the_handler_reaches_the_mixins_own_objects_and_shadows_none(cls):
    real, _ = _split_body(cls)
    assert real, f"{cls.__name__} defines no methods"
    for name in real:
        assert getattr(TelegramHandler, name) is getattr(cls, name), name
        assert name not in vars(TelegramHandler), f"{name} is defined twice"
        assert f"def {name}(" not in HANDLER_SRC, f"a stale copy of {name} is left in the handler"


@pytest.mark.parametrize("cls", MIXINS, ids=lambda c: c.__name__)
def test_every_moved_command_is_still_registered(cls):
    build = inspect.getsource(TelegramHandler.build_app)
    real, _ = _split_body(cls)
    for name in sorted(n for n in real if n.startswith("_cmd_")):
        assert f"self.{name})" in build, (
            f"{name} left the file and build_app at the same time — present, unreached")


@pytest.mark.parametrize("cls", MIXINS, ids=lambda c: c.__name__)
def test_the_host_contract_is_true(cls):
    _, stubs = _split_body(cls)
    assert stubs, f"{cls.__name__} declares no host contract"
    init = inspect.getsource(TelegramHandler.__init__)
    for name, params in stubs.items():
        if params is None:
            # An attribute: set on the instance by the handler's __init__, or
            # a class attribute the handler itself defines (a constant such as
            # _WEB_LINK_HINT that more than one group reads).
            assert re.search(rf"self\.{re.escape(name)}\s*=", init) or name in vars(TelegramHandler), (
                f"{cls.__name__} declares {name} as provided by the host; "
                "__init__ never sets it and the handler does not define it")
            continue
        assert name in vars(TelegramHandler), (
            f"{cls.__name__} declares {name} as provided by the host; the handler does not define it")
        assert params == list(inspect.signature(getattr(TelegramHandler, name)).parameters), name


@pytest.mark.parametrize("cls", MIXINS, ids=lambda c: c.__name__)
def test_a_mixin_never_defines_the_host_only_methods(cls):
    for name in HOST_ONLY:
        assert name not in vars(cls), f"{cls.__name__} defines {name}"


@pytest.mark.parametrize("cls", MIXINS, ids=lambda c: c.__name__)
def test_every_self_call_resolves_on_the_handler(cls):
    src = inspect.getsource(cls)
    called = set(re.findall(r"self\.(\w+)\(", src))
    assert called, f"{cls.__name__}: the scan found no self-calls — extractor broken"
    assigned = set(re.findall(r"self\.(\w+)\s*=", src))
    missing = sorted(c for c in called if c not in set(dir(TelegramHandler)) | assigned)
    assert missing == [], f"{cls.__name__} calls these on self and nothing defines them: {missing}"


@pytest.mark.parametrize("cls", MIXINS, ids=lambda c: c.__name__)
def test_a_mixin_imports_nothing_from_the_handler(cls):
    tree = ast.parse(Path(inspect.getsourcefile(cls)).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "telegram_handler" not in (node.module or ""), ast.unparse(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "telegram_handler" not in alias.name, ast.unparse(node)

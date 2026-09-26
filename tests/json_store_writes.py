"""The shape `test_no_json_store_writes_over_a_failed_read.py` refuses.

A LOADER reads a JSON file inside a ``try`` whose handler SWALLOWS the failure:
it raises nothing, returns nothing but an empty value, sets nothing but an
empty value, and calls nothing but a logger. A WRITER saves a whole JSON file
(``atomic_write_json``, ``json.dump`` or ``json.dumps`` handed to a write,
never to a file opened for append). When one scope holds both, the next write
saves the map the loader fell back to over the file it could not read -- which
erases every row that file held. `bot/utils/json_store.py` is the cure, and
its one write (`update_json_store`) re-reads and refuses; it is not a writer
here.

A loader and a writer are PAIRED when:

  * they are methods of one class (a class keeps its map on ``self``, so
    ``__init__`` calling the loader and ``put`` calling the saver never meet
    in one call chain and still share the state), or
  * some module-level function reaches both through the module's own calls
    (the free-chat quota's ``consume`` read with ``_load`` and wrote with
    ``_save``).

What it does NOT see, stated because a guard whose coverage is overstated is
the failure this repo keeps recording, and each is pinned on planted source:

  * a class nested inside another class, and a class or def under a
    module-level ``if`` or ``try`` block -- only the module's top-level defs
    and classes are read. Driven over ``bot/`` with every ClassDef and every
    such block read as well, none holds a site today;
  * a read or a write through a helper in ANOTHER module.

A def or class nested inside a module-level FUNCTION is seen, and charged to
that function: `is_loader` and the write test walk the whole body. The first
draft of this docstring said a nested def was a blind spot, and the test that
pinned it found the rule reading it.

A handler whose only refusal is on one branch (``if not p.exists(): return
{}; raise``) is acquitted -- the missing-file case is a fresh start and that is
the right read of that code.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent

#: A handler may call these and still be swallowing: they report, they do not
#: stop the write.
LOGGING_RECEIVERS = frozenset({"logger", "log", "_log", "logging", "_logger",
                               "LOG", "warnings", "system_log"})
LOGGING_CALLS = frozenset({"audit", "print"})
EMPTY_CALLS = frozenset({"dict", "list", "set", "tuple"})
WRITE_ATTRS = frozenset({"write_text", "write", "atomic_write_text",
                         "write_bytes"})
#: A read that answers a file's contents. `load_json_store` is here because a
#: ``try`` around it whose handler hands back ``{}`` is the defect again in
#: the cure's clothes.
READ_CALLS = frozenset({("json", "load"), ("json", "loads"),
                        (None, "load_json_store")})


def _parts(call: ast.Call) -> tuple[str | None, str | None]:
    f = call.func
    if isinstance(f, ast.Attribute):
        b = f.value
        if isinstance(b, ast.Name):
            return b.id, f.attr
        if isinstance(b, ast.Attribute):
            return b.attr, f.attr
        return "?", f.attr
    if isinstance(f, ast.Name):
        return None, f.id
    return None, None


def _calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _reads_json(node: ast.AST) -> bool:
    return any(_parts(c) in READ_CALLS for c in _calls(node))


def _open_mode(call: ast.Call) -> str | None:
    """The mode a file-opening call opens with, or None for any other call.

    ``open(path, mode)``, ``io.open``, ``codecs.open`` and ``os.fdopen`` take
    the mode second; ``Path.open(mode)`` takes it FIRST -- the probe this rule
    grew from read it second and called an append-only JSONL writer a whole
    write."""
    recv, name = _parts(call)
    if name == "fdopen" or (name == "open" and recv in (None, "io", "codecs")):
        pos = 1
    elif name == "open":
        pos = 0
    else:
        return None
    for k in call.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            return str(k.value.value)
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        return str(call.args[pos].value)
    return "r"


def _writes_json(fn: ast.AST) -> bool:
    cs = _calls(fn)
    names = [_parts(c) for c in cs]
    if any(n[1] == "atomic_write_json" for n in names):
        return True
    dumps = any(n == ("json", "dump") for n in names) or (
        any(n == ("json", "dumps") for n in names)
        and any(n[1] in WRITE_ATTRS for n in names))
    if not dumps:
        return False
    modes = [m for m in (_open_mode(c) for c in cs) if m is not None]
    # Every file this function opens is opened for APPEND: a line added to a
    # log, not a map saved over one.
    return not (modes and all("a" in m for m in modes))


def _is_empty(v: ast.AST | None) -> bool:
    if v is None:
        return True
    if isinstance(v, ast.Constant):
        return v.value is None
    if isinstance(v, ast.Dict):
        return not v.keys
    if isinstance(v, (ast.List, ast.Set)):
        return not v.elts
    if isinstance(v, ast.Tuple):
        return all(_is_empty(e) for e in v.elts)
    if isinstance(v, ast.Call):
        recv, name = _parts(v)
        if v.args or v.keywords:
            return False
        # `dict()` is an empty value, and `self._data.clear()` makes one.
        return (recv is None and name in EMPTY_CALLS) or (
            recv is not None and name == "clear")
    return False


def _caught(h: ast.ExceptHandler) -> set[str]:
    t = h.type
    if t is None:
        return {"<bare>"}
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    return {getattr(e, "id", getattr(e, "attr", "?")) for e in elts}


def _is_logging(call: ast.Call) -> bool:
    recv, name = _parts(call)
    if recv is None:
        return name in LOGGING_CALLS
    return recv in LOGGING_RECEIVERS


def _acting_calls(node: ast.AST) -> list[ast.Call]:
    """Every call in ``node`` that is not a logger, and not inside one: a
    ``str(e)`` handed to ``logger.error`` is part of the report."""
    out: list[ast.Call] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Call):
            if _is_logging(n):
                continue
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def swallows(h: ast.ExceptHandler) -> bool:
    """True when this handler turns a failed read into an empty result and
    does nothing that could stop the next write."""
    if _caught(h) <= {"FileNotFoundError"}:
        return False
    body = ast.Module(body=h.body, type_ignores=[])
    for n in ast.walk(body):
        if isinstance(n, ast.Raise):
            return False
        if isinstance(n, ast.Return) and not _is_empty(n.value):
            return False
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and not _is_empty(n.value):
            return False
    return all(_is_empty(c) for c in _acting_calls(body))


def is_loader(fn: ast.AST) -> bool:
    for t in ast.walk(fn):
        if isinstance(t, ast.Try) and any(_reads_json(s) for s in t.body):
            if any(swallows(h) for h in t.handlers):
                return True
    return False


_FN = (ast.FunctionDef, ast.AsyncFunctionDef)


def scan_source(src: str) -> list[str]:
    """Every loader in ``src`` that is paired with a writer, as qualnames."""
    tree = ast.parse(src)
    mod_fns = {n.name: n for n in tree.body if isinstance(n, _FN)}
    graph = {name: {_parts(c)[1] for c in _calls(fn)
                    if _parts(c)[0] is None and _parts(c)[1] in mod_fns}
             for name, fn in mod_fns.items()}

    def reach(name: str) -> set[str]:
        seen: set[str] = set()
        todo = [name]
        while todo:
            cur = todo.pop()
            if cur in seen:
                continue
            seen.add(cur)
            todo.extend(graph.get(cur, ()))
        return seen

    mod_writers = {n for n, f in mod_fns.items() if _writes_json(f)}
    out: list[str] = []
    for name, fn in mod_fns.items():
        if not is_loader(fn):
            continue
        if any(name in reach(f) and (reach(f) & mod_writers) for f in mod_fns):
            out.append(name)
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        meths = [m for m in cls.body if isinstance(m, _FN)]
        writes = any(_writes_json(m) for m in meths) or any(
            _parts(c)[0] is None and _parts(c)[1] in mod_writers
            for m in meths for c in _calls(m))
        if not writes:
            continue
        out.extend(f"{cls.name}.{m.name}" for m in meths if is_loader(m))
    return out


def scan_tree(root: pathlib.Path = REPO) -> dict[str, list[str]]:
    """``{relative path: [qualnames]}`` over every module under ``bot/``."""
    found: dict[str, list[str]] = {}
    for p in sorted((root / "bot").rglob("*.py")):
        hits = scan_source(p.read_text(encoding="utf-8"))
        if hits:
            found[p.relative_to(root).as_posix()] = hits
    return found


def keys(found: dict[str, list[str]]) -> set[str]:
    return {f"{path}::{q}" for path, qs in found.items() for q in qs}

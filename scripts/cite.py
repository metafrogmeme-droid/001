#!/usr/bin/env python3
"""Symbol-anchored code citations: resolve them, check them, and convert to them.

`docs/INCOME_MAP.md` cited code as ``path:line``. A line number is a claim
about a file at one commit: every edit ABOVE the cited line moves it, and it
rots by pointing at the WRONG line rather than an impossible one, so nothing
short of a reader who knows what the citation meant can tell that it rotted.
Most of the map's churn was re-pointing those numbers, and a mechanical remap
carries a wrong citation forward faithfully. An anchor names what the sentence
names, and this script resolves it:

    bot/risk/risk_engine.py::RiskEngine                       a class
    bot/skills/yield_commands.py::YieldCommands._cmd_stake    a method
    bot/config.py::StrategyTypeConfig.swing_sl_atr_mult       a class-body field
    bot/core/arb_tracker.py::ROUND_TRIP_FEE_PCT               a module binding
    bot/core/basis.py::__doc__                                a docstring
    bot/core/engine.py::RuneClawEngine.__init__#"self.basis = BasisAnalyzer("
                                                              one line inside it
    app/routes/arena.js::sweepFollows                         a JS function/const
    app/routes/arena.js::post('/follow')                      a JS route
    app/routes/mcp.js::TOOLS.get_gas                          a JS object member
    app/routes/arena.js::#"INSERT INTO arena_positions"       one line of a file

Grammar::

    anchor  := [path] "::" [symbol] ["#" marker]
    symbol  := name ("." name)*  |  method "('" route "')"       (JS routes)
    marker  := '"' text-without-'"' '"'  |  "'" text-without-"'" "'"
             |  word ("." word)*

An anchor is written on ONE line. A reflow that breaks one (inside its code
span, or before a marker) is refused rather than read up to the break: the
part after the break would never be checked.

``path`` is the repo-relative path, never a bare basename: two files share
``arena.js``, ``engine.py`` and ``chat.js``, and an anchor that had to guess
would carry a citation into the other one. A path with no extension is read
as one when it holds a ``/`` or names a file (``Dockerfile::#"..."``), so a
dropped extension is refused by name; a bare word before ``::`` that names no
file (a Rust ``module::item``) is prose. An anchor with no path before its
``::`` CONTINUES the last anchor in the same paragraph, the convention the
map already used for a bare ``:1159`` after ``trading_commands.py:1150``.

A marker is a literal substring that must occur on exactly ONE line inside
the symbol it follows (or inside the whole file, for ``path::#marker``). It
is how a citation points at a statement without naming its line.

Resolution is exact or it is an error, never a guess:

* Python: parsed with ``ast``. A symbol is a def or class (a def nested in a
  def is ``outer.inner``), or a name bound by an assignment at module or class
  level (including inside a module- or class-level ``if``/``try``/``with``).
  ``__doc__`` is the docstring of the module or of the symbol before it. A
  name bound more than once is AMBIGUOUS, except a property setter/deleter
  and an ``@overload`` stub, which are the same symbol. A symbol's range
  includes its decorators.
* JavaScript: read by a conservative line scanner, not a parser. It skips
  strings, comments, template text and regex literals, and reads a
  template's ``${...}`` as code. A name is one declared by
  ``function``/``class``/``const``/``let``/``var`` on exactly one line of the
  file and not inside a function body. A ``function`` or ``class`` declared
  inside a function (or a function-valued binding) is ``outer.inner``, as a
  Python nested def is. Anything else declared in a body -- a
  ``const``/``let``/``var``, or any declaration inside a route handler or a
  method -- is a local, not something a document can name. A route is
  ``<object>.<method>('<path>'`` on exactly one line;
  ``Outer.member`` is a property or method declared at the first nesting
  level of ``Outer``'s braces. A range is the declaration line to the line
  that closes its brackets; a symbol whose brackets do not close cleanly has
  no range and takes no marker. Anything the scanner cannot place is refused,
  never approximated.

Usage::

    python3 scripts/cite.py bot/config.py::RiskLimits           # resolve one
    python3 scripts/cite.py --check                             # the map
    python3 scripts/cite.py --check docs/X.md                   # named docs
    python3 scripts/cite.py --codemod docs/INCOME_MAP.md        # dry run: report
    python3 scripts/cite.py --codemod docs/INCOME_MAP.md --write

``--check`` exits 1 listing every anchor that does not resolve (a missing
file, a missing symbol, an ambiguous symbol, a marker that is not inside the
symbol or names several lines, an anchor split across a line, an anchor
written with one colon), every ``path:line`` citation into a ``.py`` or
``.js`` file (code is cited by anchor, never by line), and every other line
citation that no longer lands on a real, non-blank line. It exits 2 when a
document could not be read or decoded: could not check is neither a pass nor
a failure (the vocabulary ``scripts/ruff_gate.py`` documents).

``--codemod`` rewrites ``path:line`` citations into ``.py`` and ``.js`` files
as anchors: the innermost symbol enclosing the cited line, plus a short
unique marker from that line when it is a statement inside the symbol rather
than its declaration. A citation it cannot convert confidently (a path that
names several files, a blank line, a line with no unique marker) is left
as written and reported. One is rewritten: a bare ``:N`` it cannot convert
whose head it DID convert is given its path (``pkg/mod.py:7``), because it
would otherwise sit after an anchor rather than a line citation; the report
says so. A paragraph that mentions one of several same-named files in full
does not choose between them: the mention may be about something else. It
preserves what a citation POINTED AT,
right or wrong, so every conversion still needs a reader who knows what the
sentence names.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent

#: The documents `--check` reads when none is named. The engineering log and
#: the dated plans are HISTORY: a line number there records what a file said
#: on the day it was written, and rewriting it would falsify the record.
DEFAULT_DOCS = ("docs/INCOME_MAP.md",)

PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".mjs", ".cjs")
#: The files an anchor can name a symbol in.
SYMBOL_SUFFIXES = PY_SUFFIXES + JS_SUFFIXES
#: File types a ``path:line`` citation can name. Anything else before a colon
#: and a number (a time ``09:30``, a ratio) is not a citation.
CITABLE_SUFFIXES = SYMBOL_SUFFIXES + (
    ".html", ".md", ".sh", ".json", ".yml", ".yaml", ".toml", ".css", ".sql",
    ".txt", ".rs", ".ts", ".sol", ".example",
)

_PATH = r"(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]+"
#: An anchor's path may lack an extension (``Dockerfile``); `_is_anchor`
#: decides whether a bare word before ``::`` is a path or prose.
_APATH = r"(?:[\w.-]+/)*[\w.-]+"
_NAME = r"[A-Za-z_$][\w$]*"
_QUAL = rf"{_NAME}(?:\.{_NAME})*"
_ROUTE = r"(?:get|post|put|patch|delete|all|use)\('[^'\s]*'\)"
_MARK = (r"#(?:\"(?P<qmark>[^\"\n]+)\"|'(?P<smark>[^'\n]+)'"
         r"|(?P<mark>\w+(?:\.\w+)*))")
ANCHOR_RE = re.compile(
    rf"(?:(?P<path>{_APATH})|(?<![\w/.:-]))::"
    rf"(?:(?P<route>{_ROUTE})|(?P<qual>{_QUAL}))?(?:{_MARK})?")
#: An anchor written with one colon: ``path.py:Symbol`` or ``path:#"marker"``.
#: No other reader sees it as a citation, so without this it passes silently.
ONE_COLON_RE = re.compile(
    rf"(?<![\w/.:-])(?P<path>{_APATH}):(?:(?P<name>[A-Za-z_$][\w$]*)|#[\"'])")
#: A ``path:N`` (or ``path:N-M``) head, and a bare ``:N`` continuation.
LINE_CITE_RE = re.compile(
    rf"(?:(?P<path>{_PATH})|(?<![\w.:#-])):(?P<a>\d+)(?:-(?P<b>\d+))?(?!\w|:\d)")
#: A path mentioned with no line: a bare ``:N`` after it continues it.
MENTION_RE = re.compile(rf"(?<![\w/.-])(?P<path>{_PATH})(?![\w/-]|::|:\d)")

_JS_KEYWORDS = frozenset(
    "if for while switch catch function return else do try with typeof new "
    "await case default throw delete void yield in of instanceof let const var "
    "class super this import export".split())


class CiteError(Exception):
    """An anchor that does not resolve to exactly one place."""


@dataclass(frozen=True)
class Resolution:
    path: str      # repo-relative
    start: int     # 1-based, inclusive: the line the anchor names
    end: int       # 1-based, inclusive

    def __str__(self) -> str:
        return (f"{self.path}:{self.start}" if self.start == self.end
                else f"{self.path}:{self.start}-{self.end}")


@dataclass(frozen=True)
class Anchor:
    path: str
    symbol: Optional[str]
    marker: Optional[str]
    offset: int = 0          # where it starts in the text it was found in
    end: int = 0             # where it ends
    explicit: bool = True    # False: a continuation, path taken from context
    problem: Optional[str] = None   # the text around it says it was cut short

    def __str__(self) -> str:
        return render_anchor(self.path, self.symbol, self.marker)


def render_anchor(path: str, symbol: Optional[str], marker: Optional[str]) -> str:
    """The text form of an anchor; ``path`` empty renders a continuation."""
    m = ""
    if marker is not None:
        q = "'" if '"' in marker else '"'
        m = f"#{q}{marker}{q}"
    return f"{path}::{symbol or ''}{m}"


# ── reading files ────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _lines(path: str) -> tuple[str, ...]:
    try:
        return tuple((ROOT / path).read_text(encoding="utf-8").split("\n"))
    except (OSError, UnicodeDecodeError) as exc:
        raise CiteError(f"{path}: cannot be read ({type(exc).__name__})") from None


@dataclass
class Symbol:
    qual: str
    start: int                 # first line of the span (decorators included)
    head: int                  # the declaration line an anchor names
    end: Optional[int]         # None: the range could not be determined
    kind: str = "def"


# ── Python ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _py_symbols(path: str) -> dict[str, list[Symbol]]:
    """qualname -> every binding of it in one Python file."""
    tree = ast.parse("\n".join(_lines(path)))
    index: dict[str, list[Symbol]] = {}

    def add(q: str, node: ast.AST, kind: str) -> None:
        head = getattr(node, "lineno")
        start = min([head] + [d.lineno for d in getattr(node, "decorator_list", [])])
        end = getattr(node, "end_lineno", None) or head
        index.setdefault(q, []).append(Symbol(q, start, head, end, kind))

    def docstring(q: str, body: list) -> None:
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            add(f"{q}.__doc__" if q else "__doc__", body[0], "doc")

    def is_alias(node: ast.AST) -> bool:
        # A property setter/deleter and an @overload stub are the same symbol
        # as the def they sit beside, not a second binding of the name.
        for d in getattr(node, "decorator_list", []):
            text = ast.unparse(d)
            if text.endswith((".setter", ".deleter")) or text in (
                    "overload", "typing.overload"):
                return True
        return False

    def targets(node: ast.AST) -> Iterator[str]:
        tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in tgts:
            elts = t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]
            for e in elts:
                if isinstance(e, ast.Name):
                    yield e.id

    def walk(body: list, prefix: str, in_function: bool) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                q = f"{prefix}{node.name}"
                if not is_alias(node):
                    add(q, node, "class" if isinstance(node, ast.ClassDef) else "def")
                docstring(q, node.body)
                walk(node.body, q + ".",
                     in_function=not isinstance(node, ast.ClassDef))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not in_function:
                for name in targets(node):
                    add(f"{prefix}{name}", node, "bind")
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For,
                                   ast.While, ast.AsyncWith, ast.AsyncFor)):
                # A module- or class-level `if`/`try` binds names at that level
                # (optional imports, platform branches). In a function body only
                # the defs nested in it are symbols; its locals are not.
                if not in_function and not isinstance(node, (ast.If, ast.Try, ast.With)):
                    continue
                for part in ("body", "orelse", "finalbody"):
                    walk(getattr(node, part, []) or [], prefix, in_function)
                for h in getattr(node, "handlers", []) or []:
                    walk(h.body, prefix, in_function)

    docstring("", tree.body)
    walk(tree.body, "", in_function=False)
    return index


# ── JavaScript ───────────────────────────────────────────────────────────────

_JS_DECL = re.compile(
    r"^(?P<ind>\s*)(?:export\s+(?:default\s+)?)?(?:"
    r"(?:async\s+)?function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)\s*\("
    r"|class\s+(?P<cls>[A-Za-z_$][\w$]*)\b"
    r"|(?:const|let|var)\s+(?P<bind>[A-Za-z_$][\w$]*)\s*=)")
_JS_ROUTE = re.compile(
    r"""^(?P<ind>\s*)[A-Za-z_$][\w$]*\.(?P<method>get|post|put|patch|delete|all|use)"""
    r"""\(\s*(?P<q>['"`])(?P<route>[^'"`\s]*)(?P=q)""")
_JS_MEMBER = re.compile(
    r"^\s*(?:(?:static|async|get|set)\s+)*(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?:(?P<prop>:)|\([^)]*\)\s*\{)")


_JS_REGEX_WORDS = frozenset(
    "return typeof case in of delete void throw new else do yield await".split())


def _js_regex_end(ln: str, i: int) -> Optional[int]:
    """Index just past the regex literal opening at `ln[i] == "/"`, or None.

    A regex literal cannot span lines, so one with no closing slash on its
    own line is read as a division instead.
    """
    j, n, in_class = i + 1, len(ln), False
    while j < n:
        ch = ln[j]
        if ch == "\\":
            j += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "/":
            j += 1
            while j < n and (ln[j].isalnum() or ln[j] == "_"):
                j += 1
            return j
        j += 1
    return None


def _js_scan(lines: tuple[str, ...]) -> list[tuple[bool, int]]:
    """Per line: (does it start in code, net bracket depth change).

    A crude lexer, and deliberately so: strings, line and block comments,
    template text and regex literals are skipped, and a template's `${...}`
    is read as code, nested templates included.
    A `/` opens a regex only where an operand cannot end (after an operator,
    an opening bracket, a comma, the start of the file, or a keyword such as
    `return`); anywhere else it is a division. It only has to find where a
    declaration's brackets close.
    """
    out: list[tuple[bool, int]] = []
    state: Optional[str] = None      # None | "block" | "tmpl"
    interp: list[int] = []           # brace depth inside each open `${`
    prev, prev_word = "", ""         # last significant code token
    for ln in lines:
        in_code = state is None
        delta = 0
        i, n = 0, len(ln)
        while i < n:
            c = ln[i]
            if state == "block":
                if ln.startswith("*/", i):
                    state, i = None, i + 2
                else:
                    i += 1
                continue
            if state == "tmpl":
                if c == "\\":
                    i += 2
                elif c == "`":
                    state, i, prev, prev_word = None, i + 1, "`", ""
                elif ln.startswith("${", i):
                    interp.append(0)
                    state, i, prev, prev_word = None, i + 2, "{", ""
                else:
                    i += 1
                continue
            if c == "}" and interp and interp[-1] == 0:
                interp.pop()
                state, i = "tmpl", i + 1
                continue
            if c.isspace():
                i += 1
                continue
            if ln.startswith("//", i):
                break
            if ln.startswith("/*", i):
                state, i = "block", i + 2
                continue
            if c in "\"'":
                j = i + 1
                while j < n and ln[j] != c:
                    j += 2 if ln[j] == "\\" else 1
                i, prev = j + 1, c
                continue
            if c == "`":
                state, i = "tmpl", i + 1
                continue
            if c == "/":
                operand_ended = (prev.isalnum() or prev in "_$)]}\"'`"
                                 ) and prev_word not in _JS_REGEX_WORDS
                end = None if operand_ended else _js_regex_end(ln, i)
                if end is not None:
                    i, prev, prev_word = end, "/", ""
                    continue
            if c.isalnum() or c in "_$":
                j = i
                while j < n and (ln[j].isalnum() or ln[j] in "_$"):
                    j += 1
                prev, prev_word, i = ln[j - 1], ln[i:j], j
                continue
            if c in "{([":
                delta += 1
                if interp and c == "{":
                    interp[-1] += 1
            elif c in "})]":
                delta -= 1
                if interp and c == "}":
                    interp[-1] -= 1
            prev, prev_word = c, ""
            i += 1
        out.append((in_code, delta))
    return out


def _js_end(scan: list[tuple[bool, int]], head: int) -> Optional[int]:
    """The 1-based line that closes the brackets opened on line `head`."""
    depth = scan[head - 1][1]
    if depth <= 0:
        return head
    for j in range(head, len(scan)):
        depth += scan[j][1]
        if depth <= 0:
            return j + 1
    return None


def _js_is_container(lines: tuple[str, ...], s: Symbol) -> bool:
    """A class, or a binding to an object literal, whose members can be named."""
    return (s.kind in ("class", "bind") and s.end is not None and s.end > s.head
            and (s.kind == "class" or lines[s.head - 1].rstrip().endswith("{")))


def _js_members(lines: tuple[str, ...], scan: list[tuple[bool, int]],
                container: Symbol) -> Iterator[tuple[int, "re.Match[str]"]]:
    """(line, match) for each member declared at the first nesting level of
    `container`'s braces."""
    assert container.end is not None
    depth = scan[container.head - 1][1]
    for j in range(container.head + 1, container.end):
        if depth == 1 and scan[j - 1][0]:
            mm = _JS_MEMBER.match(lines[j - 1])
            if mm and mm.group("name") not in _JS_KEYWORDS:
                yield j, mm
        depth += scan[j - 1][1]


_JS_FN_VALUE = re.compile(r"=>|\bfunction\b")


@lru_cache(maxsize=None)
def _js_symbols(path: str) -> dict[str, list[Symbol]]:
    lines = _lines(path)
    scan = _js_scan(lines)
    decls: list[Symbol] = []
    for i, ln in enumerate(lines, 1):
        if not scan[i - 1][0]:
            continue                     # inside a comment or a template
        m = _JS_DECL.match(ln)
        if m:
            name = m.group("fn") or m.group("cls") or m.group("bind")
            kind = "class" if m.group("cls") else ("bind" if m.group("bind") else "def")
            decls.append(Symbol(name, i, i, _js_end(scan, i), kind))
            continue
        r = _JS_ROUTE.match(ln)
        if r:
            q = f"{r.group('method')}('{r.group('route')}')"
            decls.append(Symbol(q, i, i, _js_end(scan, i), "route"))
    # Function bodies: declared functions, routes, bindings whose value is a
    # function expression, and the methods of classes and object literals.
    bodies = [s for s in decls if s.end is not None and s.end > s.head and (
        s.kind in ("def", "route")
        or (s.kind == "bind" and _JS_FN_VALUE.search(lines[s.head - 1])))]
    for c in decls:
        if _js_is_container(lines, c):
            for j, mm in _js_members(lines, scan, c):
                end = _js_end(scan, j)
                if end is not None and end > j and (
                        mm.group("prop") is None or _JS_FN_VALUE.search(lines[j - 1])):
                    bodies.append(Symbol(mm.group("name"), j, j, end, "method"))

    def enclosing(s: Symbol) -> Optional[Symbol]:
        """The innermost function body `s` is declared in."""
        inside = [(b.end - b.head, b) for b in bodies
                  if b.end is not None and b.head < s.head <= b.end]
        return min(inside, key=lambda t: t[0])[1] if inside else None

    quals: dict[int, Optional[str]] = {}

    def qual_of(s: Symbol) -> Optional[str]:
        """What a document calls `s`, or None for a local it cannot name.

        A `function` or `class` inside a function (or a function-valued
        binding) is `outer.inner`, as a Python nested def is: naming it by its
        bare name would hide the nesting, and the next same-named helper in
        another function would make the anchor ambiguous. A binding in any
        body, and anything declared inside a route handler or a method, is a
        local: `const rows` in one handler is not something a document can
        name. A route is named by its path wherever it is registered.
        """
        if s.head in quals:
            return quals[s.head]
        quals[s.head] = None             # ranges nest strictly; no cycle, but be safe
        outer = enclosing(s)
        q: Optional[str]
        if s.kind == "route" or outer is None:
            q = s.qual
        elif s.kind in ("def", "class") and outer.kind in ("def", "bind"):
            parent = qual_of(outer)
            q = None if parent is None else f"{parent}.{s.qual}"
        else:
            q = None
        quals[s.head] = q
        return q

    index: dict[str, list[Symbol]] = {}
    for s in decls:
        named = qual_of(s)
        if named is not None:
            index.setdefault(named, []).append(Symbol(named, s.start, s.head, s.end, s.kind))
    # Members: direct children of a class or of an object-literal binding
    # that a document can name unambiguously.
    for name, found in list(index.items()):
        if len(found) != 1 or not _js_is_container(lines, found[0]):
            continue
        for j, mm in _js_members(lines, scan, found[0]):
            q = f"{name}.{mm.group('name')}"
            index.setdefault(q, []).append(Symbol(q, j, j, _js_end(scan, j), "member"))
    return index


# ── resolution ───────────────────────────────────────────────────────────────

def _symbols(path: str) -> dict[str, list[Symbol]]:
    if path.endswith(PY_SUFFIXES):
        try:
            return _py_symbols(path)
        except SyntaxError as exc:
            raise CiteError(f"{path}: does not parse ({exc.msg})") from None
    if path.endswith(JS_SUFFIXES):
        return _js_symbols(path)
    raise CiteError(f"{path}: only .py and .js files have symbols; "
                    f"use {path}::#\"marker\" for one line of it")


def symbol(path: str, qual: str) -> Symbol:
    """The one binding of `qual` in `path`, or `CiteError`."""
    found = _symbols(path).get(qual, [])
    if not found:
        raise CiteError(f"{path}::{qual}: no such symbol")
    if len(found) > 1:
        raise CiteError(f"{path}::{qual}: ambiguous, bound on lines "
                        f"{', '.join(str(s.head) for s in found)}")
    return found[0]


def marker_lines(path: str, marker: str, lo: int, hi: int) -> list[int]:
    lines = _lines(path)
    return [i for i in range(lo, min(hi, len(lines)) + 1) if marker in lines[i - 1]]


def resolve(anchor: "Anchor | str") -> Resolution:
    """The one place an anchor names, or `CiteError`."""
    if isinstance(anchor, str):
        found = list(find_anchors(anchor))
        if len(found) != 1 or found[0].offset != 0 or found[0].end != len(anchor):
            raise CiteError(f"{anchor!r} is not one anchor")
        anchor = found[0]
    if anchor.problem:
        raise CiteError(f"{anchor}: {anchor.problem}")
    path = anchor.path
    if not path:
        raise CiteError(f"{anchor}: continues no anchor in its paragraph")
    if not (ROOT / path).is_file():
        cands = resolve_cited_path(path) or [
            f for f in _tracked() if f.startswith(path + ".") and "/" not in f[len(path):]]
        hint = (f"; did you mean {cands[0]}?" if len(cands) == 1 else "")
        raise CiteError(f"{path}: no such file (an anchor takes the "
                        f"repo-relative path){hint}")
    where = str(anchor)
    if anchor.symbol is None:
        if anchor.marker is None:
            raise CiteError(f"{where}: names neither a symbol nor a marker")
        lo, hi = 1, len(_lines(path))
    else:
        sym = symbol(path, anchor.symbol)
        if anchor.marker is None:
            return Resolution(path, sym.head, sym.end or sym.head)
        if sym.end is None:
            raise CiteError(f"{where}: the scanner could not find where "
                            f"{anchor.symbol} ends, so a marker cannot be placed in it")
        lo, hi = sym.start, sym.end
    hits = marker_lines(path, anchor.marker, lo, hi)
    if not hits:
        raise CiteError(f'{where}: marker "{anchor.marker}" is not in it')
    if len(hits) > 1:
        raise CiteError(f'{where}: marker "{anchor.marker}" is on {len(hits)} lines '
                        f"({', '.join(map(str, hits[:6]))}); it must name one")
    return Resolution(path, hits[0], hits[0])


# ── finding citations in prose ───────────────────────────────────────────────

def paragraphs(text: str) -> Iterator[tuple[int, str]]:
    """(offset, text) of each blank-line-separated block."""
    for m in re.finditer(r"(?:[^\n]*\S[^\n]*(?:\n|$))+", text):
        yield m.start(), m.group(0)


def _is_anchor(m: "re.Match[str]") -> bool:
    """Whether an `ANCHOR_RE` match is an anchor rather than prose.

    A path with a ``/`` or an extension is one (a missing file is then refused
    by name), and so is a bare word that names a file (``Dockerfile``). A bare
    word that names nothing is a language path such as Rust's
    ``layout_tests::borsh_offsets``, which a document may quote.
    """
    p = m.group("path")
    if p is None or "/" in p or re.search(r"\.[A-Za-z0-9]+$", p):
        return True
    return (ROOT / p).is_file()


def _anchor_matches(para: str) -> Iterator["re.Match[str]"]:
    return (m for m in ANCHOR_RE.finditer(para) if _is_anchor(m))


def _cut_short(para: str, m: "re.Match[str]") -> Optional[str]:
    """Why the text right after an anchor says the anchor was cut short.

    The reader stops where the grammar stops, so an anchor broken by a line
    break (a reflowed code span) or by a marker that does not close on its
    line would otherwise resolve as whatever came before the break, and the
    rest of it -- the method after a wrapped ``.``, the marker text -- would
    never be checked.
    """
    rest = para[m.end():]
    if rest.startswith("#"):
        return ("its marker does not close before a line break; a marker is a "
                "quoted string or a dotted word, on the anchor's own line")
    closed = bool(m.group("qmark") or m.group("smark") or m.group("route"))
    after = rest.lstrip(" \t")
    broken = "it is split across a line break; write an anchor on one line"
    if _in_code_span(para, m.start()):
        if rest.startswith(".") or (after.startswith("\n") and not closed):
            return broken
    elif after.startswith("\n"):
        nxt = after[1:].lstrip(" \t")
        if nxt.startswith(('#"', "#'")) or re.match(r"\.[A-Za-z_$]", nxt):
            return broken
    return None


def find_anchors(text: str) -> Iterator[Anchor]:
    """Every anchor in `text`, continuations resolved to their paragraph's file.

    A continuation with no anchor before it in its paragraph is yielded with
    an empty path, which `check_text` reports rather than guessing at.
    """
    for base, para in paragraphs(text):
        last = ""
        for m in _anchor_matches(para):
            explicit = bool(m.group("path"))
            if explicit:
                last = m.group("path")
            marker = m.group("qmark") or m.group("smark") or m.group("mark")
            yield Anchor(m.group("path") or last, m.group("route") or m.group("qual"),
                         marker, base + m.start(), base + m.end(), explicit,
                         _cut_short(para, m))


@dataclass(frozen=True)
class LineCite:
    path: str          # as written, or the head it continues ("" for none)
    first: int
    last: int
    offset: int
    end: int
    bare: bool         # a ``:N`` continuation rather than a ``path:N`` head


def line_citations(text: str) -> Iterator[LineCite]:
    """Every ``path:line`` citation left in `text`, heads and continuations.

    A bare ``:N`` continues the last path in its paragraph that was cited
    (by a line or by an anchor) or merely mentioned: "routes/command.js,
    authMiddleware at :24" cites line 24 of the file named before it. A bare
    ``:N`` with no path before it is yielded with an empty path, so a reader
    can refuse it rather than skip it.
    """
    for base, para in paragraphs(text):
        events: list[tuple[int, str, object]] = []
        anchor_spans = []
        for a in _anchor_matches(para):
            anchor_spans.append((a.start(), a.end()))
            if a.group("path"):
                events.append((a.start(), "head", a.group("path")))
        for m in MENTION_RE.finditer(para):
            if any(s <= m.start() < e for s, e in anchor_spans):
                continue
            if m.group("path").endswith(CITABLE_SUFFIXES):
                events.append((m.start(), "head", m.group("path")))
        for m in LINE_CITE_RE.finditer(para):
            if any(s <= m.start() < e for s, e in anchor_spans):
                continue
            events.append((m.start(), "cite", m))
        events.sort(key=lambda e: e[0])
        last = ""
        for off, kind, obj in events:
            if kind == "head":
                last = str(obj)
                continue
            m = obj  # type: ignore[assignment]
            path = m.group("path")
            if path is not None:
                if not path.endswith(CITABLE_SUFFIXES):
                    last = ""
                    continue
                last = path
            a = int(m.group("a"))
            b = int(m.group("b")) if m.group("b") else a
            yield LineCite(path or last, a, b, base + m.start(), base + m.end(),
                           bare=path is None)


@lru_cache(maxsize=None)
def _tracked() -> tuple[str, ...]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split("\n")
        files = [f for f in out if f]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv"}
        files = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
                 if p.is_file() and not (skip & set(p.relative_to(ROOT).parts))]
    return tuple(sorted(files))


def resolve_cited_path(path: str) -> list[str]:
    """Every file a cited path could mean: the path itself, else its suffix.

    One answer is a resolution; none or several is not, and the caller says
    which. No convention picks one of several: that is the guess an anchor
    exists to remove.
    """
    if (ROOT / path).is_file():
        return [path]
    return [f for f in _tracked() if f.endswith("/" + path)]


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def code_line_citations(text: str) -> list[LineCite]:
    """The line citations in `text` that point into ``.py`` or ``.js`` code."""
    return [c for c in line_citations(text) if c.path.endswith(SYMBOL_SUFFIXES)]


def check_text(text: str, name: str = "<text>") -> list[str]:
    """Every problem with the citations in `text`, as sentences."""
    errors: list[str] = []
    spans = []
    for a in find_anchors(text):
        spans.append((a.offset, a.end))
        where = f"{name}:{_line_of(text, a.offset)}"
        try:
            resolve(a)
        except CiteError as exc:
            errors.append(f"{where}: {exc}")
    for m in ONE_COLON_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue            # inside an anchor's own marker text
        path = m.group("path")
        named = m.group("name")
        if named is not None and not path.endswith(SYMBOL_SUFFIXES):
            continue            # `Note:Text`, `config.json:key`: not a symbol file
        if named is None and not ("/" in path or "." in path or (ROOT / path).is_file()):
            continue
        errors.append(f"{name}:{_line_of(text, m.start())}: {m.group(0)} has one colon; "
                      f"an anchor is {path}::{named or ''}")
    flat = " ".join(text.split())
    for c in line_citations(text):
        where = f"{name}:{_line_of(text, c.offset)}"
        if not c.path:
            errors.append(f"{where}: `:{c.first}` continues no path in its paragraph")
            continue
        if c.path.endswith(SYMBOL_SUFFIXES):
            # Code is cited by what it IS, never by where it sits: a line
            # number into a .py or .js file is refused however true it is
            # today, because the next edit above it moves it silently.
            errors.append(
                f"{where}: {c.path + ':' if not c.bare else ':'}{c.first} cites code "
                f"by line number; write an anchor (path::Qualname[#marker])")
            continue
        cands = resolve_cited_path(c.path)
        if len(cands) != 1:
            errors.append(
                f"{where}: {c.path} names "
                f"{'no file' if not cands else 'several files: ' + ', '.join(cands)}"
                "; write its repo-relative path")
            continue
        try:
            lines = _lines(cands[0])
        except CiteError as exc:
            errors.append(f"{where}: {exc}")
            continue
        if not 1 <= c.first <= c.last <= len(lines):
            errors.append(f"{where}: {c.path}:{c.first}-{c.last} is past the end "
                          f"({len(lines)} lines)")
        elif not lines[c.first - 1].strip() and (
                f"{c.path}:{c.first} is a blank line" not in flat):
            # A retraction may cite a blank line on purpose; its own sentence
            # says so, and that sentence -- not the number -- is the exclusion.
            errors.append(f"{where}: {c.path}:{c.first} is a blank line")
    return errors


# ── the codemod ──────────────────────────────────────────────────────────────

#: A head ``path:N`` or ``path:N-M``, with optional ``/N`` or ``,N`` extras
#: that carry no colon (``duel.js:43/57/73``, ``duel_squads.js:5,92``).
_CODEMOD_HEAD = re.compile(
    rf"(?<![\w/.-])(?P<path>{_PATH}):(?P<a>\d+)(?:-(?P<b>\d+))?"
    r"(?P<extra>(?:[/,]\d+(?![\d.]))*)(?!\w|:\d)")
_CODEMOD_BARE = re.compile(r"(?<![\w.:#-]):(?P<a>\d+)(?:-(?P<b>\d+))?(?!\w|:\d)")

_MARKER_BAD = set("`|\n")
_MARKER_MIN = 14
_MARKER_MAX = 64


@dataclass
class Conversion:
    doc_line: int
    original: str
    replacement: Optional[str]
    note: str
    targets: list[str] = field(default_factory=list)


def _innermost(path: str, a: int, b: int) -> Optional[Symbol]:
    """The smallest unambiguous symbol whose span holds lines a..b."""
    best: Optional[Symbol] = None
    for qual, found in _symbols(path).items():
        if len(found) != 1:
            continue
        s = found[0]
        if s.end is None or not (s.start <= a and b <= s.end):
            continue
        if best is None or (s.end - s.start) < (best.end - best.start) or (
                (s.end - s.start) == (best.end - best.start) and len(qual) > len(best.qual)):
            best = s
    return best


def _code_part(line: str, js: bool) -> str:
    """The line without a trailing comment, when code remains."""
    text = line.strip()
    token = "//" if js else "#"
    cut = text.find(" " + token)
    if cut > 0:
        text = text[:cut].rstrip()
    return text


def pick_marker(path: str, n: int, lo: int, hi: int) -> Optional[str]:
    """A short literal from line `n` that no other line in lo..hi holds."""
    raw = _lines(path)[n - 1]
    text = _code_part(raw, path.endswith(JS_SUFFIXES))
    if not text:
        return None
    cands: list[str] = []
    # Prefixes ending at a token boundary, shortest first.
    for m in re.finditer(r"\w\b|[)\]}'\"(,:=]", text):
        end = m.end()
        if _MARKER_MIN <= end <= _MARKER_MAX:
            cands.append(text[:end])
    if len(text) <= _MARKER_MAX:
        cands.append(text)
    # Then a window starting at each later token, for a line whose opening
    # (`return`, `if`, `await self.`) is shared with its neighbours.
    for s in re.finditer(r"(?<![\w.])[A-Za-z_\"'(]", text):
        if s.start() == 0:
            continue
        rest = text[s.start():]
        for m in re.finditer(r"\w\b|[)\]}'\"(,:=]", rest):
            if _MARKER_MIN <= m.end() <= _MARKER_MAX:
                cands.append(rest[:m.end()])
    seen = set()
    for c in cands:
        c = c.strip()
        if c in seen or len(c) < 4 or _MARKER_BAD & set(c):
            continue
        seen.add(c)
        if '"' in c and "'" in c:
            continue
        if c.endswith("\\"):
            continue
        if marker_lines(path, c, lo, hi) == [n]:
            return c
    return None


def convert_one(path_written: str, a: int, b: int,
                context_paths: list[str]) -> tuple[Optional[tuple[str, str, Optional[str]]], str]:
    """((path, symbol, marker) or None, note) for one cited line range."""
    cands = resolve_cited_path(path_written)
    if len(cands) != 1:
        # A paragraph that names one of them in full elsewhere is NOT the
        # document saying which one: the mention may be about something else
        # ("the lib (app/lib/arena.js) ...; the route is arena.js:2"). The
        # reader who knows what the sentence names picks; this only says so.
        named = [c for c in cands if c in context_paths]
        hint = (f" (the paragraph also names {', '.join(named)}, which does not "
                f"say which one this citation meant)" if named else "")
        return None, (f"{path_written} names "
                      f"{'no file' if not cands else 'several files: ' + ', '.join(cands)}"
                      f"{hint}")
    path = cands[0]
    if not path.endswith(SYMBOL_SUFFIXES):
        return None, "not a .py/.js file"
    try:
        lines = _lines(path)
    except CiteError as exc:
        return None, str(exc)
    if not 1 <= a <= b <= len(lines):
        return None, f"{path}:{a}-{b} is past the end ({len(lines)} lines)"
    if not lines[a - 1].strip() and a == b:
        return None, f"{path}:{a} is a blank line"
    # A range that opens or closes on blank lines holds what is between them.
    while a < b and not lines[a - 1].strip():
        a += 1
    while b > a and not lines[b - 1].strip():
        b -= 1
    try:
        syms = _symbols(path)
    except CiteError as exc:
        return None, str(exc)
    # The cited line is itself a declaration (or its decorator).
    heads = [s[0] for s in syms.values()
             if len(s) == 1 and s[0].start <= a <= s[0].head
             and (b == a or (s[0].end is not None and b <= s[0].end))]
    if heads:
        best = min(heads, key=lambda s: (s.head - s.start, -len(s.qual)))
        return (path, best.qual, None), "declaration"
    # A range whose two ends are each a one-line declaration (two fields).
    if b != a:
        ends = [[s[0] for s in syms.values() if len(s) == 1 and s[0].head == n
                 and s[0].kind in ("bind", "member")] for n in (a, b)]
        if all(len(e) == 1 for e in ends):
            return (path, f"{ends[0][0].qual}|{ends[1][0].qual}", None), "two declarations"
    inner = _innermost(path, a, b)
    lo, hi = (inner.start, inner.end) if inner else (1, len(lines))
    # The cited line first; for a range, the first line in it that carries a
    # marker of its own (a range that opens on a blank line or a bare `/**`).
    marker, at = None, a
    for n in range(a, b + 1):
        if lines[n - 1].strip():
            marker, at = pick_marker(path, n, lo, hi), n
            if marker is not None:
                break
    if marker is None:
        return None, f"no unique marker on {path}:{a} ({lines[a - 1].strip()[:60]!r})"
    note = "statement"
    if b != a:
        note = f"range {a}-{b} anchored at line {at}"
    if inner is None:
        note += ", file-level"
    elif inner.kind == "doc":
        note += ", in a docstring"
    if _code_part(lines[at - 1], path.endswith(JS_SUFFIXES)).startswith(("#", "//", "*", "/*")):
        note += ", marker from a comment"
    return (path, inner.qual if inner else None, marker), note


def _in_code_span(line: str, col: int) -> bool:
    return line[:col].count("`") % 2 == 1


def codemod_text(text: str) -> tuple[str, list[Conversion]]:
    """Rewrite code line citations in `text` as anchors; report every one."""
    out: list[str] = []
    report: list[Conversion] = []
    pos = 0
    for base, para in paragraphs(text):
        out.append(text[pos:base])
        new_para, conv = _codemod_paragraph(text, base, para)
        out.append(new_para)
        report.extend(conv)
        pos = base + len(para)
    out.append(text[pos:])
    return "".join(out), report


def _codemod_paragraph(text: str, base: int, para: str) -> tuple[str, list[Conversion]]:
    report: list[Conversion] = []
    context_paths = sorted({m.group("path") for m in MENTION_RE.finditer(para)}
                           | {m.group("path") for m in _CODEMOD_HEAD.finditer(para)})
    context_paths = [p for p in context_paths if (ROOT / p).is_file()]
    # Every head and bare continuation, in order, with the path it cites.
    events = []
    for m in _CODEMOD_HEAD.finditer(para):
        events.append((m.start(), m.end(), m.group("path"), m))
    for m in _CODEMOD_BARE.finditer(para):
        if any(s <= m.start() < e for s, e, _, _ in events):
            continue
        events.append((m.start(), m.end(), None, m))
    for m in MENTION_RE.finditer(para):
        if m.group("path").endswith(CITABLE_SUFFIXES):
            events.append((m.start(), m.start(), "@" + m.group("path"), None))
    events.sort(key=lambda e: (e[0], e[1]))

    pieces: list[str] = []
    cursor = 0
    last_written = ""      # the path a bare `:N` in the ORIGINAL continues
    last_anchor = ""       # the path a `::x` continuation in the OUTPUT continues
    last_line_head = ""    # the path a bare `:N` in the OUTPUT continues
    for start, end, path, m in events:
        if path is not None and path.startswith("@"):
            last_written = path[1:]
            continue
        if m is None:
            continue
        if path is not None:
            last_written = path
            nums = [(int(m.group("a")), int(m.group("b") or m.group("a")))]
            nums += [(int(x), int(x)) for x in re.findall(r"\d+", m.group("extra") or "")]
        else:
            nums = [(int(m.group("a")), int(m.group("b") or m.group("a")))]
        written = path or last_written
        original = para[start:end]
        doc_line = _line_of(text, base + start)
        if not written or not written.endswith(SYMBOL_SUFFIXES):
            if written:
                last_line_head = written
            continue
        results = [convert_one(written, a, b, context_paths) for a, b in nums]
        if any(r is None for r, _ in results):
            note = "; ".join(n for r, n in results if r is None)
            report.append(Conversion(doc_line, original, None, note))
            # Left as written. If it is a bare continuation whose head was
            # converted, it must now name its own path to stay a citation.
            if path is None and last_line_head != written:
                pieces.append(para[cursor:start])
                full = resolve_cited_path(written)
                p = full[0] if len(full) == 1 else written
                pieces.append(f"{p}{original}")
                cursor = end
                report[-1].note += f" (now written with its path, {p})"
            last_line_head = written
            continue
        rendered: list[str] = []
        targets = []
        for k, ((res, note), (a, b)) in enumerate(zip(results, nums)):
            fpath, sym, marker = res  # type: ignore[misc]
            for j, s in enumerate(sym.split("|") if sym and "|" in sym else [sym]):
                # A head the document wrote with its path keeps it; only what
                # was a bare continuation becomes a `::` continuation.
                continues = (path is None or k or j) and fpath == last_anchor
                rendered.append(render_anchor("" if continues else fpath, s, marker))
                last_anchor = fpath
                n = b if j else a
                targets.append(f"{fpath}:{n} [{note}]")
        # A Markdown code span may wrap a line, so the count runs from the
        # paragraph's start, not the line's.
        inside = _in_code_span(para, start)
        joined = ", ".join(rendered) if inside else ", ".join(f"`{r}`" for r in rendered)
        pieces.append(para[cursor:start])
        pieces.append(joined)
        cursor = end
        report.append(Conversion(doc_line, original, joined, "; ".join(n for _, n in results),
                                 targets))
    pieces.append(para[cursor:])
    return "".join(pieces), report


# ── command line ─────────────────────────────────────────────────────────────

def _docs(args: list[str]) -> list[Path]:
    return [ROOT / a for a in args] if args else [ROOT / d for d in DEFAULT_DOCS]


def _doc_name(p: Path) -> str:
    """A document's name in a report: repo-relative, or as given when outside."""
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("items", nargs="*",
                    help="anchors to resolve; with --check or --codemod, documents")
    ap.add_argument("--check", action="store_true",
                    help="check every citation in the named documents (default: the map)")
    ap.add_argument("--codemod", action="store_true",
                    help="convert code line citations to anchors (dry run unless --write)")
    ap.add_argument("--write", action="store_true", help="with --codemod: rewrite the files")
    args = ap.parse_args(argv)

    if args.check or args.codemod:
        paths = _docs(args.items)
        texts: dict[Path, str] = {}
        for p in paths:
            try:
                texts[p] = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                # A document that cannot be read or decoded was not checked;
                # a traceback would exit 1, which says a citation is wrong.
                print(f"CANNOT CHECK {_doc_name(p)}: {type(exc).__name__}")
                return 2
        if args.check:
            errors: list[str] = []
            for p, text in texts.items():
                errors.extend(check_text(text, _doc_name(p)))
            for e in errors:
                print(e)
            if not errors:
                n = sum(len(list(find_anchors(t))) for t in texts.values())
                print(f"ok: {n} anchors in {len(paths)} document(s) resolve")
            return 1 if errors else 0
        left = 0
        for p, text in texts.items():
            new, report = codemod_text(text)
            rel = _doc_name(p)
            for c in report:
                if c.replacement is None:
                    left += 1
                    print(f"LEFT  {rel}:{c.doc_line}: {c.original!r} -- {c.note}")
                else:
                    print(f"ANCHOR {rel}:{c.doc_line}: {c.original!r} -> {c.replacement}"
                          f"   [{'; '.join(c.targets)}]")
            if args.write and new != text:
                p.write_text(new, encoding="utf-8")
        print(f"{left} citation(s) left as written")
        return 0

    if not args.items:
        ap.print_usage()
        return 2
    rc = 0
    for text in args.items:
        try:
            print(resolve(text))
        except CiteError as exc:
            print(f"ERROR {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

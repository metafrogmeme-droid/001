"""Which ccxt calls hand a venue the bot's own spelling of a symbol.

The bot records ``BTC/USDT``; a venue lists its perp as ``BTC/USDT:USDT``
(Bybit, which ALSO lists the spot market under the recorded spelling) or
``BTC/USDC:USDC`` (Hyperliquid, which lists nothing under it). Every order and
cancel in the executor was mapped through ``self._venue.order_symbol`` and the
reads that decide what those orders did were not — so a list of the sites
somebody fixed is the ten-of-eleven shape, and this is the rule instead.

A SITE is a call ``<exchange>.<method>(...)`` whose method takes a symbol,
on any receiver but ``self``/``self._venue``. Its symbol argument is:

  * MAPPED — a call to ``order_symbol``/``swap_symbol``, a list of mapped
    expressions (``fetch_positions([x])``), or a local name every assignment
    of which is mapped;
  * CARRIED — a parameter of the enclosing function; it is then resolved
    through every in-file call of that function, recursively, and the site is
    mapped only when every caller hands a mapped expression;
  * BARE — anything else: ``pos.symbol``, a loop variable, a name with an
    unmapped assignment, a ``**kwargs`` hand-off the walk cannot see into.

A bare site, or a carried site with a bare caller, fails unless
``tests/venue_symbol_read_baseline.txt`` carries a row for it WITH a reason,
and the row's count matches. Two-way, the `known_failures.txt` rule: a row
whose site is gone, or whose count moved, fails too.

``fetch_order`` has a second rule, and it is the B2 half: every order read
goes through ``LiveExecutor._fetch_order``, the one seam that adds the venue's
read params (Bybit refuses a unified-account read without them). A raw
``fetch_order`` anywhere else is reported under its own key.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FILES = ("bot/core/live_executor.py", "bot/core/open_orders.py")
BASELINE = ROOT / "tests" / "venue_symbol_read_baseline.txt"

#: method -> (positional index of the symbol argument, its keyword name)
SYMBOL_ARG = {
    "fetch_order": (1, "symbol"), "cancel_order": (1, "symbol"),
    "set_leverage": (1, "symbol"), "set_margin_mode": (1, "symbol"),
    "fetch_positions": (0, "symbols"), "fetch_open_orders": (0, "symbol"),
    "fetch_ticker": (0, "symbol"), "fetch_my_trades": (0, "symbol"),
    "fetch_closed_orders": (0, "symbol"), "amount_to_precision": (0, "symbol"),
    "price_to_precision": (0, "symbol"), "create_order": (0, "symbol"),
    "fetch_leverage": (0, "symbol"), "market": (0, "symbol"),
}
MAPPERS = {"order_symbol", "swap_symbol"}
#: The one function allowed to call ``fetch_order`` on an exchange directly.
READ_SEAM = "_fetch_order"


class Finding(NamedTuple):
    key: str
    line: int


def _parents(tree: ast.AST) -> dict:
    out = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            out[c] = n
    return out


def _enclosing(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _params(fn) -> list:
    a = fn.args
    return [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]


def _own_nodes(fn):
    """The function's own statements, not those of a def nested in it."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _assignments(fn, name) -> list:
    """Every value bound to ``name`` in ``fn``; None for a binding with no
    single value (a loop target, a tuple unpack)."""
    out: list = []
    for n in _own_nodes(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    out.append(n.value)
                elif any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(t)):
                    out.append(None)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(n.target, ast.Name) and n.target.id == name:
                out.append(n.value if isinstance(n, ast.AnnAssign) else None)
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension, ast.With, ast.AsyncWith)):
            targets = ([n.target] if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension))
                       else [i.optional_vars for i in n.items if i.optional_vars is not None])
            for t in targets:
                if any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(t)):
                    out.append(None)
    return out


def classify(expr, fn) -> tuple:
    """("mapped", None) | ("carried", (function, param)) | ("bare", None)."""
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
            and expr.func.attr in MAPPERS):
        return ("mapped", None)
    if isinstance(expr, ast.IfExp):
        for branch in (expr.body, expr.orelse):
            c = classify(branch, fn)
            if c[0] != "mapped":
                return c
        return ("mapped", None)
    if isinstance(expr, (ast.List, ast.Tuple)):
        for e in expr.elts:
            c = classify(e, fn)
            if c[0] != "mapped":
                return c
        return ("mapped", None)
    if isinstance(expr, ast.Name) and fn is not None:
        if expr.id in _params(fn):
            return ("carried", (fn.name, expr.id))
        vals = _assignments(fn, expr.id)
        if vals and all(v is not None and classify(v, fn)[0] == "mapped" for v in vals):
            return ("mapped", None)
    return ("bare", None)


def _symbol_arg(call):
    idx, kw = SYMBOL_ARG[call.func.attr]
    for k in call.keywords:
        if k.arg == kw:
            return k.value
    if len(call.args) > idx:
        return call.args[idx]
    return None


def _is_site(node) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in SYMBOL_ARG):
        return False
    recv = ast.unparse(node.func.value)
    return not (recv == "self" or recv.startswith("self._venue"))


def _arg_for(call, fdef, pname):
    """The expression ``call`` binds to ``fdef``'s parameter ``pname``:
    an AST node, None (the default applies), or "**" when a mapping splat
    hides it from the walk."""
    ps = [a.arg for a in fdef.args.posonlyargs + fdef.args.args]
    if ps and ps[0] == "self" and isinstance(call.func, ast.Attribute):
        ps = ps[1:]
    for k in call.keywords:
        if k.arg == pname:
            return k.value
    if pname in ps:
        i = ps.index(pname)
        if i < len(call.args) and not any(isinstance(a, ast.Starred) for a in call.args[:i + 1]):
            return call.args[i]
    if any(k.arg is None for k in call.keywords) or any(isinstance(a, ast.Starred) for a in call.args):
        return "**"
    return None


def _resolve(tree, parents, funcs, fname, pname, seen) -> list:
    """(caller, arg) pairs that hand ``fname``'s ``pname`` something unmapped."""
    if (fname, pname) in seen:
        return []
    seen = seen | {(fname, pname)}
    defs = funcs.get(fname, [])
    if len(defs) != 1:
        return [(fname, f"{pname} (defined {len(defs)} times)")]
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ((isinstance(n.func, ast.Attribute) and n.func.attr == fname)
                  or (isinstance(n.func, ast.Name) and n.func.id == fname))]
    if not calls:
        return [(fname, f"{pname} (no caller in this file)")]
    bad = []
    for c in calls:
        a = _arg_for(c, defs[0], pname)
        caller = _enclosing(c, parents)
        cname = caller.name if caller is not None else "<module>"
        if a is None:
            continue
        if isinstance(a, str):
            bad.append((cname, a))
            continue
        k = classify(a, caller)
        if k[0] == "mapped":
            continue
        if k[0] == "carried":
            bad += _resolve(tree, parents, funcs, k[1][0], k[1][1], seen)
        else:
            bad.append((cname, ast.unparse(a)))
    return bad


def findings_for(source: str) -> list:
    """Every site that hands a venue the recorded spelling, keyed for the baseline."""
    tree = ast.parse(source)
    parents = _parents(tree)
    funcs: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(n.name, []).append(n)
    out: list = []
    for n in ast.walk(tree):
        if not _is_site(n):
            continue
        fn = _enclosing(n, parents)
        fname = fn.name if fn is not None else "<module>"
        method = n.func.attr
        if method == "fetch_order" and fname != READ_SEAM:
            arg = _symbol_arg(n)
            expr = ast.unparse(arg) if arg is not None else ""
            out.append(Finding(f"{fname}.fetch_order({expr}) outside {READ_SEAM}", n.lineno))
            continue
        arg = _symbol_arg(n)
        if arg is None:
            continue
        expr = ast.unparse(arg)
        kind, where = classify(arg, fn)
        if kind == "mapped":
            continue
        if kind == "bare":
            out.append(Finding(f"{fname}.{method}({expr})", n.lineno))
            continue
        for caller, carg in _resolve(tree, parents, funcs, where[0], where[1], frozenset()):
            out.append(Finding(f"{fname}.{method}({expr}) <- {caller}({carg})", n.lineno))
    return out


def tree_findings() -> Counter:
    counts: Counter = Counter()
    for rel in FILES:
        for f in findings_for((ROOT / rel).read_text()):
            counts[f"{rel}: {f.key}"] += 1
    return counts


def parse_baseline(text: str) -> tuple:
    """(counts, reasonless rows). A row is ``key | count | reason``."""
    counts: Counter = Counter()
    reasonless: list = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) != 3 or not parts[2]:
            reasonless.append(line)
            continue
        key, count, _reason = parts
        try:
            counts[key] += int(count)
        except ValueError:
            reasonless.append(line)
    return counts, reasonless


def baseline() -> tuple:
    return parse_baseline(BASELINE.read_text())


def compare(found: Counter, recorded: Counter) -> tuple:
    """(unrecorded, stale): sites the baseline does not carry, rows no site holds."""
    unrecorded = {k: v - recorded.get(k, 0) for k, v in found.items() if v > recorded.get(k, 0)}
    stale = {k: v - found.get(k, 0) for k, v in recorded.items() if v > found.get(k, 0)}
    return unrecorded, stale

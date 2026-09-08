#!/usr/bin/env python3
"""Ratchet for the SHAPES of the rule this repo keeps breaking.

    Unreadable is never zero, and absent is never a measurement.

CLAUDE.md has said that since 2026-07-31. That day the same rule was broken in
twenty-plus places across ten PRs, and the note it grew afterwards is the
reason this file exists:

    A principle is not searchable. The **shapes** it takes are, so here they
    are:

        parseFloat(x) || 0 · float(x or 0)   unreadable is break-even
        (x || 0) >= 0                        unreadable WON  (0 >= 0 is true)
        (x or 0) > 0                         unreadable LOST
        losses = len(all) - wins             unscorable rows are losses
        .get("pnl", 0) · getattr(o,"pnl",0)  absent field is zero
        sum(...) over unreadable rows        a partial total, printed as whole
        if total != 0: guarding a display    all-missing and flat hidden alike
        isConfigured() as "encrypted at rest" a flag as the state of the rows

Two practices found those, and neither scales: reading every diff, and auditing
the previous PR. This is the third — the `ruff_gate` / `mypy_gate` /
`known_failures.txt` pattern pointed at the rule that actually generates this
repo's defects. Record what is there; fail on what is NEW.

WHAT IT CLAIMS, AND WHAT IT DOES NOT
------------------------------------
It claims one thing: **these shapes did not increase.** It does NOT claim the
tree is honest, and it does not claim a hit is a defect. Most are not:

  * ``patterns.py`` computes ``len(wins) / len(completed) if completed else 0``
    two lines under ``if not completed: continue`` -- dead, not dishonest.
  * ``sum(t.pnl_result or 0 for t in completed)`` where ``completed`` is
    already filtered ``pnl_result is not None`` changes ``0.0`` into ``0`` and
    nothing else.
  * ``arena_trades.pnl`` is ``NOT NULL``; ``track.js`` filters on ``isFinite``
    upstream.

CLAUDE.md names those last two itself, under *"Then check reachability before
fixing"*, and calls a refactor bought with no safety a real cost. So the
baseline is a list of **places to look**, recorded wholesale and unreviewed,
exactly as ``ruff_baseline.json`` was. Lowering a number means somebody read
one and decided.

COVERAGE, STATED RATHER THAN IMPLIED
------------------------------------
A gate whose coverage is overstated is the failure this repo spends most of its
guard tests preventing, so:

  * **Python only.** The JS half of every shape above is NOT checked here.
    ``app/test/panel_failure_honesty.test.js`` covers ``renderPanel`` loaders
    structurally and nothing covers the rest.
  * **``bot/`` and ``scripts/`` only.** ``tests/`` is excluded on purpose: a
    test PLANTS these shapes to prove the code rejects them, so including it
    would bury the signal in fixtures. That exclusion is a hole, and it is
    named here rather than left to be discovered.
  * **Five of the eight shapes.** The other three -- a partial ``sum``, an
    ``if total != 0:`` guarding a display, and a config flag rendered as the
    state of stored rows -- are semantic, not syntactic. No AST distinguishes
    them from the correct code they look like. They stay a reading job.
  * **A vocabulary decides what counts as a measurement**, so ``.get("pnl", 0)``
    is a hit and ``.get("retries", 0)`` is not. That is a heuristic and it
    both over- and under-flags. `--list` prints every hit for reading.

THE ANALYSER IS THIS FILE, so its rules are fingerprinted into the baseline.
Change the vocabulary or add a shape and the counts stop being comparable --
the same trap ``ruff_gate.check_version`` documents, where a baseline recorded
under mypy 1.19.1 and checked under 1.15.0 reported eleven classes as having
grown and not one was a code change. A mismatch here is CANNOT CHECK (exit 2),
never a verdict.

USAGE
-----
    python3 scripts/honesty_gate.py             # gate (CI)
    python3 scripts/honesty_gate.py --list      # every hit, with source
    python3 scripts/honesty_gate.py --update    # re-record, deliberately
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "honesty_baseline.json"

#: Directories scanned. `tests/` is deliberately absent -- see the docstring.
ROOTS = ("bot", "scripts")

#: Words whose ZERO IS A CLAIM. A count, an index, a retry budget or a
#: timeout may default to zero honestly; a price, a P&L or a win rate may not,
#: because zero is a real, measured value those fields can legitimately hold
#: and nothing downstream can tell the two apart.
MEASUREMENT_WORDS = frozenset("""
    pnl pl profit loss losses gain roi return returns win wins winrate edge
    expectancy rate ratio pct percent score confidence weight
    price mark last bid ask close open high low vwap twap
    equity balance free used margin leverage notional size qty quantity amount
    exposure value usd cost basis fee fees funding slippage spread
    drawdown dd liq liquidation sl tp stop takeprofit
    volume oi apy apr yield tvl fdv mcap marketcap supply cap
    """.split())

#: Substring probes for names the token split cannot reach (`maxMargin`,
#: `pnlUsd`, `fdv_mcap_ratio`). Kept short: every entry here is a word that
#: means a measurement wherever it appears.
MEASUREMENT_SUBSTRINGS = ("pnl", "margin", "price", "equity", "balance",
                          "drawdown", "leverage", "notional", "roi", "fdv",
                          "mcap", "tvl", "apy", "apr", "expectancy",
                          "slippage", "liquidat")

#: Names that mean "the ones that did not succeed", for the complement shape.
COMPLEMENT_WORDS = frozenset(
    "loss losses lost losers fail failed failures miss missed misses "
    "rejected denied bad red negative".split())

#: Coercions whose argument is a measurement being forced to a number.
COERCIONS = frozenset("float int abs round Decimal".split())

SHAPES = (
    "or-zero-coerce",     # float(x or 0)
    "or-zero-compare",    # (x or 0) > 0
    "or-zero-assign",     # pnl = x or 0
    "get-default-zero",   # d.get("pnl", 0)
    "getattr-default-zero",   # getattr(o, "pnl", 0)
    # ANY call ending (..., "<measurement>", 0). The first draft knew only
    # `.get` and `getattr`, and the single most expensive hit in the tree was
    # neither: bot/utils/website_sync.py wrote `float(_attr(t, "pnl", 0))` --
    # a project-local accessor -- which is the WIRE to the dashboard, and it
    # turned every unpriced live close into a $0.00 break-even before the web
    # reader (which counts unpriced closes properly) ever saw it. A gate that
    # knows two spellings of a shape is a gate that finds it in two places.
    "lookup-default-zero",
    "complement-count",   # losses = len(all) - wins
)


def _rules_fingerprint() -> str:
    """A hash of the analyser, so counts are never compared across rule sets."""
    blob = "|".join((
        ",".join(sorted(MEASUREMENT_WORDS)),
        ",".join(MEASUREMENT_SUBSTRINGS),
        ",".join(sorted(COMPLEMENT_WORDS)),
        ",".join(sorted(COERCIONS)),
        ",".join(SHAPES),
        ",".join(ROOTS),
    ))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _words(name: str) -> list:
    out, cur = [], ""
    for ch in name:
        if ch == "_" or ch.isdigit():
            if cur:
                out.append(cur.lower())
            cur = ""
        elif ch.isupper() and cur and not cur[-1].isupper():
            out.append(cur.lower())
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur.lower())
    return out


def is_measurement(name: str) -> bool:
    """Does this identifier name something whose zero is a claim?"""
    if not name:
        return False
    low = name.lower().replace("_", "")
    if any(s in low for s in MEASUREMENT_SUBSTRINGS):
        return True
    return bool(set(_words(name)) & MEASUREMENT_WORDS)


def is_complement(name: str) -> bool:
    return bool(set(_words(name or "")) & COMPLEMENT_WORDS)


def _is_zero(node) -> bool:
    """A literal numeric zero. `False` is excluded: `False == 0` is true in
    Python and a boolean default is a different (and usually honest) thing."""
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
            and node.value == 0)


def _or_zero(node) -> bool:
    """`<anything> or 0` -- the coercion at the heart of five of the shapes."""
    return (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
            and len(node.values) >= 2 and _is_zero(node.values[-1]))


def _target_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, (ast.Subscript,)):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return ""


class _Scan(ast.NodeVisitor):
    def __init__(self, rel: str, src: str):
        self.rel = rel
        self.lines = src.splitlines()
        self.hits: list = []

    def _hit(self, shape: str, node) -> None:
        line = getattr(node, "lineno", 0)
        text = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        self.hits.append((shape, self.rel, line, text[:120]))

    # -- float(x or 0) / int(x or 0) -------------------------------------
    def visit_Call(self, node):
        fn = node.func
        fname = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else "")
        if fname in COERCIONS and node.args and _or_zero(node.args[0]):
            self._hit("or-zero-coerce", node)
        # d.get("pnl", 0)
        if (isinstance(fn, ast.Attribute) and fn.attr == "get"
                and len(node.args) == 2 and _is_zero(node.args[1])
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and is_measurement(node.args[0].value)):
            self._hit("get-default-zero", node)
        # getattr(o, "pnl", 0) -- and any other accessor spelled the same way
        if (len(node.args) >= 2 and _is_zero(node.args[-1])
                and isinstance(node.args[-2], ast.Constant)
                and isinstance(node.args[-2].value, str)
                and is_measurement(node.args[-2].value)):
            if isinstance(fn, ast.Name) and fn.id == "getattr":
                self._hit("getattr-default-zero", node)
            elif not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
                # `.get` is already reported under its own name above; this is
                # every OTHER accessor with a key and a default, `_attr`
                # included.
                self._hit("lookup-default-zero", node)
        self.generic_visit(node)

    # -- (x or 0) > 0 ----------------------------------------------------
    def visit_Compare(self, node):
        if _or_zero(node.left) or any(_or_zero(c) for c in node.comparators):
            self._hit("or-zero-compare", node)
        self.generic_visit(node)

    # -- pnl = x or 0  /  losses = len(all) - wins ------------------------
    def visit_Assign(self, node):
        for t in node.targets:
            name = _target_name(t)
            if _or_zero(node.value) and is_measurement(name):
                self._hit("or-zero-assign", node)
            if (is_complement(name) and isinstance(node.value, ast.BinOp)
                    and isinstance(node.value.op, ast.Sub)
                    and isinstance(node.value.left, ast.Call)
                    and isinstance(node.value.left.func, ast.Name)
                    and node.value.left.func.id == "len"):
                self._hit("complement-count", node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        name = _target_name(node.target)
        if node.value is not None and _or_zero(node.value) and is_measurement(name):
            self._hit("or-zero-assign", node)
        self.generic_visit(node)


def scan() -> list:
    """Every hit in the scanned roots, as (shape, relpath, line, source)."""
    hits: list = []
    for root in ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            try:
                src = path.read_text(encoding="utf-8")
                tree = ast.parse(src, filename=rel)
            except (OSError, SyntaxError) as exc:
                # A file that will not parse is NOT a file with no findings.
                # Reporting it as zero is the exact defect this gate is about.
                print(f"CANNOT CHECK: {rel}: {exc}", file=sys.stderr)
                raise SystemExit(2) from exc
            scanner = _Scan(rel, src)
            scanner.visit(tree)
            hits.extend(scanner.hits)
    return hits


def counts_from(hits: list) -> dict:
    out: dict = {}
    for shape, rel, _line, _text in hits:
        out.setdefault(shape, {})
        out[shape][rel] = out[shape].get(rel, 0) + 1
    return {s: dict(sorted(f.items())) for s, f in sorted(out.items())}


def _load_baseline() -> dict:
    if not BASELINE.exists():
        print(f"No {BASELINE}. Create it with: "
              f"python3 scripts/honesty_gate.py --update", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def main() -> int:
    hits = scan()
    counts = counts_from(hits)
    total = len(hits)
    fingerprint = _rules_fingerprint()

    if "--list" in sys.argv:
        for shape, rel, line, text in sorted(hits):
            print(f"{shape:22} {rel}:{line}  {text}")
        print(f"\n{total} hit(s). A hit is a place to LOOK, not a defect.")
        return 0

    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps({
            "_comment": "Per-file counts of the shapes CLAUDE.md tabulates for "
                        "'unreadable is never zero'. A RATCHET: a file may only "
                        "go DOWN, and a file that improves must be re-recorded "
                        "in the same commit, same rule as known_failures.txt. "
                        "A hit is a place to LOOK -- most are not defects. "
                        "Regenerate with scripts/honesty_gate.py --update.",
            "_coverage": "Python only, bot/ and scripts/ only, five of the "
                         "eight shapes. See the module docstring for what is "
                         "NOT covered and why.",
            "rules_fingerprint": fingerprint,
            "total": total,
            "counts": counts,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline updated: {total} hit(s) across {len(counts)} shape(s)")
        return 0

    baseline = _load_baseline()
    recorded = baseline.get("rules_fingerprint")
    if recorded != fingerprint:
        print("CANNOT CHECK: the baseline was recorded by a different rule set "
              f"({recorded}) and this analyser is {fingerprint}.", file=sys.stderr)
        print("  Different rules count different things on an identical tree, so "
              "any growth reported here would be an artefact of the vocabulary "
              "rather than a fact about the code.", file=sys.stderr)
        print("  Re-record deliberately: python3 scripts/honesty_gate.py --update",
              file=sys.stderr)
        return 2

    base = baseline.get("counts", {})
    grew, shrank = [], []
    for shape in sorted(set(counts) | set(base)):
        now_files, was_files = counts.get(shape, {}), base.get(shape, {})
        for rel in sorted(set(now_files) | set(was_files)):
            now, was = now_files.get(rel, 0), was_files.get(rel, 0)
            if now > was:
                grew.append((shape, rel, was, now))
            elif now < was:
                shrank.append((shape, rel, was, now))

    print(f"honesty shapes: {total} hit(s) across {len(counts)} shape(s)")
    print(f"baseline:       {baseline.get('total')} hit(s)")

    if grew:
        print("\nNEW hits -- this gate fails on growth, not on the backlog:")
        for shape, rel, was, now in grew:
            print(f"  {shape:22} {rel}: {was} -> {now}  (+{now - was})")
        print("\nRead each one. If the zero really is a measurement (the field "
              "cannot be null,\nthe rows are pre-filtered, the branch is "
              "unreachable), re-record deliberately:")
        print("  python3 scripts/honesty_gate.py --update")
        return 1

    if shrank:
        # Not a failure of the CODE -- but a baseline sitting above reality
        # stops meaning anything, exactly as a stale known_failures.txt entry
        # does, so it fails until it is re-recorded in the same commit.
        print("\nThese improved; re-record the baseline in this commit:")
        for shape, rel, was, now in shrank:
            print(f"  {shape:22} {rel}: {was} -> {now}  (-{was - now})")
        print("\n  python3 scripts/honesty_gate.py --update")
        return 1

    print("\nNo new honesty shapes. (The baselined backlog above is still "
          "unread; `--list` prints it.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

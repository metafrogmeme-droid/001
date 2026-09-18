"""Every venue candle read either drops the forming bar, or says why not.

Twenty-five fetching functions did neither. `bot/utils/candles.
drop_forming_candle` has existed since the repaint audit and its policy note
says it "must apply to EVERY consumer of fetch_ohlcv"; driven by AST over the
whole tree, eleven of thirty-six applied it. The rest computed RSI, ATR,
squeezes, sweeps, volume ratios, MTF structure and the risk gate's own
denominator over a bar that had not closed, and published the answer.

A LIST OF THE SITES I FIXED WOULD BE THE WRONG GUARD. That is the
`/setllm` ten-of-eleven shape -- the twenty-sixth site, added tomorrow, is the
one missing from the list. So the rule is structural: a venue
`<x>.fetch_ohlcv(...)` must have hygiene somewhere in its enclosing function
chain, or be named in `tests/candle_hygiene_baseline.txt` WITH its reason.
Two-way, the `known_failures.txt` rule: an exemption that stops applying is a
hard failure, so a stale reason cannot hide the next unhygiened read.

A BLANKET SWEEP WOULD HAVE BEEN WRONG, which is why exemptions exist at all.
A forming candle's close IS the current price, so dropping it from a MARK read
answers with a close up to one whole timeframe old. Six sites needed both --
the mark read before the drop, the window after it -- and two do not want the
drop at all.

THE PROBE THAT FOUND THIS HAD THREE BLIND SPOTS, each of which manufactured
exactly the accusation it exists to prevent, so they are stated here and the
rule below is written against them:

  * a local WRAPPER that applies hygiene (`api_bridge._fetch_ohlcv`) -- its
    four callers were accused of a function that is clean. Keying on the
    VENUE read rather than on the fetching function answers this: a caller
    that only calls a local wrapper makes no venue read and is not a site.
  * a NESTED def whose caller hygienes the result (`skill_registry.
    _fetch_one`, dropped at the gather) -- hence the ENCLOSING CHAIN.
  * a name defined many times in one file (the mark probe resolved
    `skill_registry.execute` to the wrong class, the methods ratchet's own
    ambiguity) -- hence the dotted path plus an occurrence index.

A FOURTH was found by the mutation round rather than by reading: "hygiene is
somewhere in the chain" cannot see ONE OF N. `scan_skill.callback_confirm_reject`
holds three separate 4h reads, two of which feed `engine.risk.evaluate(idea,
atr=...)`, and removing the drop from any one of them left the other two to
acquit it -- three mutations survived a green suite for exactly that. So the
rule COUNTS: a scope needs at least as many hygiene calls as it has BARE reads
(a read written inline inside `drop_forming_candle(...)` carries its own). A
shortfall marks the LAST bare sites in line order, deterministically, so the
row that must be explained is a specific read rather than a whole function.

It objected to two two-branch reads on its first run, and it was right about
both: `skill_registry`'s movers fetch chose its venue in two branches behind
one drop (consolidated to one read -- a branch added later would inherit
nothing), and the backtest loader's paging read is genuinely two branches
assembling ONE series, which is a baselined row with that reason.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINE = REPO / "tests" / "candle_hygiene_baseline.txt"

HYGIENE = {"drop_forming_candle", "_drop_forming_candle"}
SKIP_DIRS = ("tests/", "node_modules/", ".git/", "app/", "site/", "contracts/")


def _iter_py() -> list[pathlib.Path]:
    out = []
    for p in sorted(REPO.rglob("*.py")):
        rel = p.relative_to(REPO).as_posix()
        if any(rel.startswith(d) or f"/{d}" in rel for d in SKIP_DIRS):
            continue
        out.append(p)
    return out


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _is_venue_fetch(node: ast.Call) -> bool:
    """`<something>.fetch_ohlcv(...)` -- a read from an exchange object.

    A bare `_fetch_ohlcv(...)` is a LOCAL wrapper call, not a venue read; the
    wrapper itself is the site. That distinction is what stops this rule
    accusing `api_bridge`'s four callers of a wrapper that is already clean.
    """
    return isinstance(node.func, ast.Attribute) and node.func.attr == "fetch_ohlcv"


def _hygiene_operands(call: ast.Call) -> list[ast.AST]:
    """Every expression a hygiene call is applied TO, awaits unwrapped.

    `drop_forming_candle(await ex.fetch_ohlcv(...), tf)` wraps its read; the
    read is then not a bare one and needs no separate drop.
    """
    out: list[ast.AST] = []
    for a in list(call.args) + [k.value for k in call.keywords]:
        out.append(a)
        while isinstance(a, ast.Await):
            a = a.value
            out.append(a)
    return out


def _own_calls(node: ast.AST) -> list[ast.Call]:
    """Calls written directly in this scope, not inside a nested def."""
    out: list[ast.Call] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Call):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def candle_read_sites() -> dict[str, dict]:
    """Every venue candle read in the tree, keyed `path::dotted.name#n`.

    Each value carries `covered` (hygiene somewhere in the enclosing function
    chain) and `line`, which is reported but never part of the key -- a
    `path:line` key is invalidated by any insertion above it, which is the
    staleness this repo records about `docs/INCOME_MAP.md`'s citations.
    """
    sites: dict[str, dict] = {}
    for path in _iter_py():
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                       # a file this tree cannot parse
            continue                              # is the parse gate's business
        seen: dict[str, int] = {}

        def walk(node: ast.AST, chain: tuple[str, ...],
                 budget: int, bare_above: int) -> None:
            own = _own_calls(node)
            drops = sum(1 for c in own if _call_name(c) in HYGIENE)
            # A read written INSIDE a hygiene call carries its own, so it is
            # neither a bare read nor a claim on the budget.
            wrapped = {id(a) for c in own if _call_name(c) in HYGIENE
                       for a in _hygiene_operands(c)}
            bare = [c for c in own if _is_venue_fetch(c) and id(c) not in wrapped]
            inline = [c for c in own if _is_venue_fetch(c) and id(c) in wrapped]
            # The budget is what the chain can cover: every hygiene call, less
            # the ones already spent on an inline read.
            budget = budget + drops - len(inline)
            for i, call in enumerate(sorted(bare, key=lambda c: c.lineno)):
                dotted = ".".join(chain) or "<module>"
                n = seen.get(dotted, 0)
                seen[dotted] = n + 1
                sites[f"{rel}::{dotted}#{n}"] = {
                    "covered": (bare_above + i) < budget, "line": call.lineno}
            for call in inline:
                dotted = ".".join(chain) or "<module>"
                n = seen.get(dotted, 0)
                seen[dotted] = n + 1
                sites[f"{rel}::{dotted}#{n}"] = {
                    "covered": True, "line": call.lineno}
            spent = bare_above + len(bare)
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, chain + (child.name,), budget, spent)
                elif not isinstance(child, ast.Lambda):
                    # a nested class holds methods; keep descending
                    for g in ast.walk(child):
                        if isinstance(g, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            break
                    else:
                        continue
                    if isinstance(child, ast.ClassDef):
                        for m in child.body:
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                walk(m, chain + (child.name, m.name), budget, spent)

        walk(tree, (), 0, 0)
    return sites


def _baseline() -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, reason = line.partition("  ")
        assert reason.strip(), (
            f"{key}: an exemption with no reason is the next reader's false "
            f"acquittal. Write why this read wants the forming bar.")
        rows[key.strip()] = reason.strip()
    return rows


class TestEveryVenueCandleReadIsAccountedFor:

    def test_no_unhygiened_read_is_unexplained(self):
        sites = candle_read_sites()
        exempt = _baseline()
        bad = sorted(k for k, v in sites.items()
                     if not v["covered"] and k not in exempt)
        assert not bad, (
            "These venue candle reads compute on a bar that has not closed.\n"
            + "\n".join(f"  {k}  (line {sites[k]['line']})" for k in bad)
            + "\n\nEither apply bot.utils.candles.drop_forming_candle to the "
              "rows (reading any MARK off the last bar BEFORE the drop -- a "
              "forming candle's close IS the current price), or add the key "
              "to tests/candle_hygiene_baseline.txt with the reason this read "
              "wants the forming bar.")

    def test_no_exemption_has_stopped_applying(self):
        """The other direction, and the one that matters more.

        A site that grew hygiene must leave the file in the same commit. An
        exemption kept past its reason is a hole with a good reputation -- the
        `known_failures.txt` rule, whose whole point is that a stale entry
        cannot sit in front of the next real one.
        """
        sites = candle_read_sites()
        exempt = _baseline()
        now_clean = sorted(k for k in exempt
                           if k in sites and sites[k]["covered"])
        gone = sorted(k for k in exempt if k not in sites)
        assert not now_clean, (
            "These are hygiened now; delete their rows from "
            "tests/candle_hygiene_baseline.txt in this commit:\n"
            + "\n".join(f"  {k}" for k in now_clean))
        assert not gone, (
            "These exemptions name a read that no longer exists; delete "
            "their rows:\n" + "\n".join(f"  {k}" for k in gone))

    def test_the_rule_can_see_both_answers(self):
        """A rule no input can reach is a claim that there is a check.

        Driven on the real tree rather than asserted: the walk must find reads
        on BOTH sides, or a bug that marks everything covered (or everything
        bare) would pass both tests above in silence.
        """
        sites = candle_read_sites()
        assert len(sites) >= 20, f"only {len(sites)} venue candle read(s) found"
        assert any(v["covered"] for v in sites.values()), "nothing reads as covered"
        assert any(not v["covered"] for v in sites.values()), "nothing reads as bare"

    def test_a_local_wrapper_is_not_a_site_and_its_venue_read_is(self):
        """The first blind spot, pinned against the real tree.

        `api_bridge._fetch_ohlcv` applies hygiene and four handlers call it.
        The handlers must not appear (they make no venue read) and the wrapper
        must appear and read as covered -- which is the difference between
        this rule and the probe that accused all four.
        """
        sites = candle_read_sites()
        assert sites.get("api_bridge.py::_fetch_ohlcv#0", {}).get("covered") is True
        for caller in ("_scan_single", "analyze", "confirm_trade", "close_position"):
            assert not [k for k in sites if k.startswith(f"api_bridge.py::{caller}#")], (
                f"api_bridge.{caller} calls a local wrapper, not a venue -- it "
                f"is not a candle-read site")

    def test_hygiene_in_an_enclosing_scope_covers_a_nested_read(self):
        """The second blind spot. `skill_registry`'s deep scan reads in a
        nested `_fetch_one` and drops at the gather in the enclosing method,
        so the chain is what decides."""
        sites = candle_read_sites()
        nested = [k for k in sites if k.endswith("._fetch_one#0")
                  and k.startswith("bot/skills/skill_registry.py::")]
        assert nested, "the deep scan's nested fetch is no longer found"
        assert all(sites[k]["covered"] for k in nested), (
            "a nested read whose caller hygienes the rows reads as bare")

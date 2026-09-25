"""The $RCLAW gate is asked about a FEATURE, and what a caller holds is a SKILL.

`tier_gate.check_user(users, uid, feature)` answers `(True, "ok")` for any name
it does not hold — its own docstring calls that "ungated feature" — so handing
it the wrong noun is not an error, it is a SILENT PASS. Eight of the nine paid
skills are gated by COINCIDENCE, their two names happening to match, and the
ninth is `pro_scan`, sold as `premium_scan`. `tier_gate.feature_for` is the one
reading of that difference.

WHAT IT COST, driven with the gate enabled and no wallet linked. `chat_tools.
_tier_verdict` passed the SKILL, so `pro_scan` answered `(True, 'ok')` while
its eight siblings answered `no_wallet`. `skill_reach` is the ONE walk both
`tools_for` (what the model may call) and `capability_answer` (what the caller
is told they can ask for) take, on both surfaces, so the rendered card read:

    • a scan tuned to one timeframe — scalp, intraday or swing
    • 8 more need a linked, verified wallet.

Nine paid skills, eight withheld, the ninth OFFERED, and the count under it
the eight. It is not an execution bypass — the dispatch DOES read
`feature_for` — so the caller was invited and then refused at the door, which
is the `/vault` hint shape with the sign flipped. And it reached every
unqualified caller, not only the unlinked one: `required is None` returns
before wallet, verification, stake and RPC are consulted.

A LIST OF THE SITES I FIXED WOULD BE THE WRONG GUARD — the `/setllm`
ten-of-eleven shape, where the site added tomorrow is the one missing from the
list. `tier_gate`'s own comment had already named this site without knowing
it: "anything else that gates by what it is about to DISPATCH". So the rule is
structural. A `check_user` call's feature argument must be one of:

  * a string LITERAL that is a key of `FEATURE_MIN_TIER`;
  * an expression that goes through `feature_for`;
  * a local NAME bound in the enclosing function from such an expression;
  * a PARAMETER of the enclosing function — which makes that function a GATE
    HOP, and every production call to it is checked by this same rule at the
    matching argument, or against the parameter's own default;

or a row in `tests/tier_gate_noun_baseline.txt` WITH its reason, two-way as
`known_failures.txt` is.

STATED BLIND SPOTS, because a guard whose coverage is overstated is the
failure this repo is organised around:

  * NAME AMBIGUITY. A hop's callers are found by the called NAME across the
    production tree, the methods ratchet's own limitation. Two functions
    sharing a hop's name would pool their callers, which over-accuses (the
    loud direction). Driven, no name collides today, and `hop_names` is
    asserted so the day one does, that is visible rather than silent.
  * DYNAMIC DISPATCH. `getattr(tier_gate, "check_user")` or a bound method
    passed as a value is invisible here. Nothing in the tree does it.
  * ONE HOP DEEP. A hop whose caller is itself a hop is reported UNRESOLVED
    rather than followed, which is again the loud direction: it demands a
    baseline row rather than quietly passing. Nothing in the tree is two deep.
  * PYTHON ONLY. The gate is Python; no other runtime reads it.

EVERY RULE BELOW PASSES ON THE REAL TREE, so a mutation of the rule changes no
verdict against it — the trap `tests/command_gates.py` and the candle ratchet
both record. Each is therefore driven on a PLANTED tree where it is the only
thing in play.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

from bot.token.tier_gate import FEATURE_MIN_TIER, feature_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = pathlib.Path(__file__).with_name("tier_gate_noun_baseline.txt")

#: The gate function whose third argument is a FEATURE.
GATE = "check_user"
#: The reading that turns a skill into the feature it is sold as.
READING = "feature_for"
#: How far a forwarded feature is followed. Two is what the tree has
#: (`_tier_verdict` -> `skill_reach`); the bound exists so a cycle ends,
#: and a chain deeper than this stops GROWING rather than passing.
_MAX_HOP_DEPTH = 8


# ── the walk ──────────────────────────────────────────────────────────────

def _production_files(root: pathlib.Path):
    """Every production `.py`. `tests/` is excluded: a test PLANTS these
    shapes to prove the rule rejects them, which is exactly what the planted
    drives at the bottom of this file do."""
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if rel.startswith(("tests/", ".git/", "node_modules/")):
            continue
        yield f


def _chain(tree):
    """`(node, enclosing-FunctionDef chain)` for every node in `tree`."""
    def rec(n, chain):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain = chain + [n]
        yield n, chain
        for c in ast.iter_child_nodes(n):
            yield from rec(c, chain)
    return rec(tree, [])


def _called_name(call: ast.Call):
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _arg_at(call: ast.Call, index: int, name: str):
    """The argument at `index`, or the `name=` keyword, or None if omitted."""
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _local_reading_names(fn: ast.AST) -> set[str]:
    """Names bound in `fn`'s own body from an expression through `feature_for`.

    `user_gateway` writes `_feature = tier_gate.feature_for(skill_name)` and
    then hands `_feature` over, which reads as a bare name at the call and is
    the reading one line up.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and READING in ast.unparse(node.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif (isinstance(node, ast.AnnAssign) and node.value is not None
                and READING in ast.unparse(node.value)
                and isinstance(node.target, ast.Name)):
            out.add(node.target.id)
    return out


def classify(arg, enclosing) -> str:
    """One of `literal`, `literal-unknown`, `reading`, `param`, `unresolved`.

    `enclosing` is the innermost FunctionDef the argument sits in, or None.
    """
    if arg is None:
        return "unresolved"
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return "literal" if arg.value in FEATURE_MIN_TIER else "literal-unknown"
    if READING in ast.unparse(arg):
        return "reading"
    if isinstance(arg, ast.Name) and enclosing is not None:
        if arg.id in _local_reading_names(enclosing):
            return "reading"
        params = [a.arg for a in enclosing.args.args + enclosing.args.kwonlyargs]
        if arg.id in params:
            return "param"
    return "unresolved"


def _params_and_defaults(fn):
    """`([names], {name: default-node})` for a FunctionDef's plain args."""
    names = [a.arg for a in fn.args.args]
    defaults = {}
    if fn.args.defaults:
        for a, d in zip(fn.args.args[-len(fn.args.defaults):], fn.args.defaults):
            defaults[a.arg] = d
    for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        names.append(a.arg)
        if d is not None:
            defaults[a.arg] = d
    return names, defaults


def survey(root: pathlib.Path):
    """`(findings, hops)` over the production tree.

    A finding is `(key, kind)`; `kind` is `literal-unknown` or `unresolved`.
    `hops` maps a gate-hop function name to the parameter it forwards.
    """
    trees = {}
    for f in _production_files(root):
        try:
            trees[f] = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
    return survey_trees(trees, root)


def survey_trees(trees, root: pathlib.Path):
    """Findings and hops, following hops to a FIXED POINT.

    ONE HOP IS NOT ENOUGH, and the defect this file is about proves it. The
    pre-fix `_tier_verdict(users, user_id, skill_name)` forwarded its own
    PARAMETER, so a walk that stopped at the gate call saw a hop and nothing
    wrong; the noun was decided one frame further out, in `skill_reach`, where
    `name` is a loop variable over SKILL names. A walk that stops at the first
    hop acquits the very site it was written for.

    So a call that forwards its own parameter into a hop makes its function a
    hop too, and the loop repeats until the set stops growing. `_seen_hops`
    bounds it: a cycle adds nothing new on its second pass and the loop ends.
    """
    findings: list[tuple[str, str]] = []
    hops: dict[str, str] = {}
    defs = _def_index(trees)
    # `check_user`'s feature is positional index 2, keyword `feature`.
    # A target is `(param-name, the def's parameter list)`; the positional
    # index is computed AT THE CALL, because whether `self` is implicit is a
    # property of the call (`obj.m(x)`) and not of the definition. The first
    # draft subtracted it whenever a def began with `self`, and so read a
    # plain `f(self, x)` call one argument to the left.
    targets = {GATE: ("feature", ["users", "uid", "feature"])}

    for _round in range(_MAX_HOP_DEPTH):
        new_findings: list[tuple[str, str]] = []
        grew = False
        seen: dict[str, int] = {}

        def key_for(path, chain):
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            dotted = ".".join(c.name for c in chain) or "<module>"
            k = f"{rel}::{dotted}"
            seen[k] = seen.get(k, 0) + 1
            return f"{k}#{seen[k]}"

        for path, tree in trees.items():
            for node, chain in _chain(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _called_name(node)
                if name not in targets:
                    continue
                kw, pnames = targets[name]
                idx = pnames.index(kw)
                if pnames and pnames[0] == "self" and isinstance(node.func, ast.Attribute):
                    idx -= 1
                arg = _arg_at(node, idx, kw)
                if arg is None and name in hops:
                    fn = _hop_def(defs, name)
                    if fn is not None:
                        _names, defaults = _params_and_defaults(fn)
                        arg = defaults.get(hops[name])
                encl = chain[-1] if chain else None
                kind = classify(arg, encl)
                if kind == "param" and encl is not None:
                    if encl.name not in hops:
                        grew = True
                    hops[encl.name] = arg.id
                    fn = _hop_def(defs, encl.name)
                    if fn is None:
                        # An ambiguous hop NAME cannot be followed, so every
                        # call to it is unresolved rather than resolved to a
                        # guess. Recorded here at the hop itself so the row
                        # names the function that cannot be followed.
                        continue
                    pnames, _d = _params_and_defaults(fn)
                    if arg.id not in pnames:
                        continue
                    targets[encl.name] = (arg.id, pnames)
                elif kind in ("literal-unknown", "unresolved"):
                    new_findings.append((key_for(path, chain), kind))
        findings = new_findings
        if not grew:
            break

    # A hop whose name is ambiguous is followed by nobody, so every call to it
    # is reported rather than silently accepted.
    seen2: dict[str, int] = {}
    for hop in sorted(hops):
        if _hop_def(defs, hop) is not None:
            continue
        for path, tree in trees.items():
            for node, chain in _chain(tree):
                if isinstance(node, ast.Call) and _called_name(node) == hop:
                    try:
                        rel = path.relative_to(root).as_posix()
                    except ValueError:
                        rel = path.as_posix()
                    dotted = ".".join(c.name for c in chain) or "<module>"
                    k = f"{rel}::{dotted}"
                    seen2[k] = seen2.get(k, 0) + 1
                    findings.append((f"{k}#{seen2[k]}", "unresolved"))
    return findings, hops


def _is_typing_stub(fn) -> bool:
    """A Protocol body of `...`, with or without a docstring, is a TYPE, not a
    definition.

    `command_gates.py` records this blind spot costing six false accusations;
    the first draft of THIS walk reproduced it exactly, reporting all twelve
    `_token_gate_blocks` callers as unresolved because the mixin declares the
    method twice more as a Protocol stub so `_hop_def` saw three and refused.
    A checker with a blind spot manufactures exactly the accusation it exists
    to prevent — for the second time in this repo, on the same shape.
    """
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return len(body) == 1 and isinstance(body[0], ast.Expr) \
        and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis


def _def_index(trees):
    """`{name: [every real FunctionDef of that name]}`, built ONCE per survey.

    `_hop_def` used to walk EVERY tree in full on each call -- twelve calls a
    survey over the production set, 11.7 million `ast.walk` steps -- so the
    two whole-tree tests in this file took 24s and 20s ALONE against pytest's
    60s timeout, and the first full run under load pushed both past it, where
    the flake filter forgave them as order-dependent. That is the
    `get_source_segment` chapter one helper over: a test that passes alone at
    24s is a test the filter will forgive under load. One walk, then lookups.
    """
    index: dict[str, list] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not _is_typing_stub(node)):
                index.setdefault(node.name, []).append(node)
    return index


def _hop_def(defs, name):
    """The single real FunctionDef called `name`, or None if it is ambiguous.

    `defs` is `_def_index(trees)`. Ambiguity is NOT resolved to a guess: the
    methods ratchet records what one costs, and answering None here makes
    every caller UNRESOLVED, which demands a baseline row rather than quietly
    passing.
    """
    found = defs.get(name, ())
    return found[0] if len(found) == 1 else None


# ── the baseline ──────────────────────────────────────────────────────────

def baseline_rows(text: str):
    """`{key: reason}` from the baseline text. A row with no reason is a row
    with no argument, and is reported rather than accepted."""
    rows, reasonless = {}, []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, reason = line.partition("  ")
        if not sep or not reason.strip():
            reasonless.append(line.strip())
            continue
        rows[key.strip()] = reason.strip()
    return rows, reasonless


# ── the rule ──────────────────────────────────────────────────────────────

def unlisted_findings(findings, rows):
    """Findings the baseline does not excuse."""
    return [f"{k}  ({kind})" for k, kind in findings if k not in rows]


def stale_rows(findings, rows):
    """Baseline rows naming a site that now resolves."""
    live = {k for k, _ in findings}
    return sorted(k for k in rows if k not in live)


def test_every_gate_call_is_asked_about_a_feature():
    findings, _hops = survey(ROOT)
    rows, _reasonless = baseline_rows(BASELINE.read_text())
    unlisted = unlisted_findings(findings, rows)
    assert not unlisted, (
        "A $RCLAW gate call whose feature argument this walk cannot resolve to "
        "a FEATURE. `check_user` answers (True, 'ok') for a name it does not "
        "hold, so the wrong noun is a silent pass, not an error. Route it "
        "through `tier_gate.feature_for` — or add it to "
        "tests/tier_gate_noun_baseline.txt WITH the reason it is safe:\n  "
        + "\n  ".join(sorted(unlisted))
    )


def test_no_baseline_row_has_gone_stale():
    findings, _hops = survey(ROOT)
    rows, _reasonless = baseline_rows(BASELINE.read_text())
    stale = stale_rows(findings, rows)
    assert not stale, (
        "These baseline rows name a gate call that now resolves to a feature. "
        "Delete them in the same commit: an exemption kept past its reason is "
        "a hole with a good reputation, and a stale row sits in front of the "
        "next real one.\n  " + "\n  ".join(stale)
    )


def test_every_baseline_row_carries_a_reason():
    _rows, reasonless = baseline_rows(BASELINE.read_text())
    assert not reasonless, (
        "A baseline row with no reason is a row with no argument:\n  "
        + "\n  ".join(reasonless)
    )


def test_the_hop_names_are_what_the_tree_has():
    """The hops, pinned, because the walk finds their callers BY NAME.

    Two functions sharing one of these names would pool their callers — the
    methods ratchet's own ambiguity. That over-accuses rather than acquits, so
    it fails loudly; pinning the set means a NEW hop is a deliberate edit here
    rather than a silent widening of what this walk claims to cover.
    """
    _findings, hops = survey(ROOT)
    assert hops == {
        "_pane_gate_blocks": "feature",
        "_token_gate_blocks": "feature",
        "allows_user": "feature",
    }, hops


def test_the_one_skill_that_is_not_its_own_feature_is_still_the_one():
    """`feature_for` earns its keep on exactly one row, and that is WHY the
    wrong noun survives: a convention that holds eight times in nine is why
    nobody checks the ninth."""
    paid = {s for s in FEATURE_MIN_TIER}
    assert feature_for("pro_scan") == "premium_scan"
    assert "pro_scan" not in paid and "premium_scan" in paid
    assert feature_for("deepscan") == "deepscan"
    assert feature_for("nothing_by_that_name") == "nothing_by_that_name"


# ── the rule, driven where it is the only thing in play ───────────────────
#
# Every rule above passes against the real tree, so mutating one changes no
# verdict there. These plant the shape instead.

def _planted(src: str, root=pathlib.Path("/planted")):
    return {root / "planted.py": ast.parse(src)}, root


@pytest.mark.parametrize("body,where,expect", [
    # A literal that is not a feature: `check_user` answers ok, silently.
    ('def f(u, i):\n    return check_user(u, i, "pro_scan")\n', "f#1", "literal-unknown"),
    # An expression the walk cannot follow.
    ('def f(u, i, d):\n    return check_user(u, i, d["feature"])\n', "f#1", "unresolved"),
    # A bare name that is neither a parameter nor bound from the reading.
    ('def f(u, i):\n    n = pick()\n    return check_user(u, i, n)\n', "f#1", "unresolved"),
])
def test_the_rule_reports_a_feature_it_cannot_resolve(body, where, expect):
    trees, root = _planted(body)
    findings, _hops = survey_trees(trees, root)
    assert [k.split("::")[1] for k, _ in findings] == [where], findings
    assert findings[0][1] == expect


def test_the_rule_reports_the_defect_itself_two_frames_out():
    """THE SHAPE THIS FILE IS ABOUT, planted verbatim.

    `_tier_verdict` forwards its own parameter, so the gate call reads clean
    and the site is a HOP; the wrong noun is chosen one frame further out,
    where `skill_reach` loops over SKILL names. A walk that stopped at the
    first hop would acquit both frames — which is why the fixed point is
    load-bearing rather than tidy.
    """
    src = (
        'def _tier_verdict(users, user_id, skill_name):\n'
        '    return tier_gate.check_user(users, user_id, skill_name)\n'
        'def skill_reach(users, user_id, names):\n'
        '    for name in names:\n'
        '        _ok, _why = _tier_verdict(users, user_id, name)\n'
        '    return []\n'
    )
    trees, root = _planted(src)
    findings, hops = survey_trees(trees, root)
    assert hops == {"_tier_verdict": "skill_name"}
    assert [(k.split("::")[1], kind) for k, kind in findings] == [
        ("skill_reach#1", "unresolved")], findings

    # And the one-line cure closes it, driven the same way.
    # Anchored on the CALL. The first draft anchored on `user_id, skill_name)`,
    # which is also inside the `def` line one row up — an anchor matching twice,
    # inside the fixture written to find that shape.
    cured = src.replace("check_user(users, user_id, skill_name)",
                        "check_user(users, user_id, tier_gate.feature_for(skill_name))")
    assert cured != src
    trees, root = _planted(cured)
    findings, hops = survey_trees(trees, root)
    assert (findings, hops) == ([], {}), (findings, hops)


def test_the_caller_is_found_when_it_is_VISITED_BEFORE_the_hop():
    """THE ORDERING IS WHAT MAKES THE FIXED POINT LOAD-BEARING, and the
    mutation round is what said so.

    With the hop defined ABOVE its caller, one pass already reaches both
    frames — the hop joins the target set and the caller is visited after it
    — so bounding the loop to a single round changed no verdict and
    `_MAX_HOP_DEPTH = 1` SURVIVED. That is the round reporting a corpus gap
    rather than a code one: the input that separates one round from a fixed
    point is a caller visited BEFORE the hop is discovered, which is decided
    by definition order and by the file walk's sort.

    The pre-fix tree really had it this way round: `skill_reach` is defined
    at chat_tools.py:206 and `_tier_verdict` at :305.
    """
    src = (
        'def skill_reach(users, user_id, names):\n'
        '    for name in names:\n'
        '        _ok, _why = _tier_verdict(users, user_id, name)\n'
        '    return []\n'
        'def _tier_verdict(users, user_id, skill_name):\n'
        '    return tier_gate.check_user(users, user_id, skill_name)\n'
    )
    trees, root = _planted(src)
    findings, hops = survey_trees(trees, root)
    assert hops == {"_tier_verdict": "skill_name"}
    assert [(k.split("::")[1], kind) for k, kind in findings] == [
        ("skill_reach#1", "unresolved")], findings


@pytest.mark.parametrize("body", [
    'def f(u, i):\n    return check_user(u, i, "deepscan")\n',
    'def f(u, i, s):\n    return check_user(u, i, feature_for(s))\n',
    'def f(u, i, s):\n    x = tier_gate.feature_for(s)\n    return check_user(u, i, x)\n',
])
def test_the_rule_accepts_a_feature(body):
    trees, root = _planted(body)
    findings, _hops = survey_trees(trees, root)
    assert findings == [], findings


def test_a_hop_is_followed_to_its_callers():
    """The hop is the shape `_token_gate_blocks` has: the gate call is clean,
    and the noun is decided one frame up. A walk that stopped at the gate
    would acquit every one of its twelve callers."""
    src = (
        'def gate(update, mode, feature="deepscan"):\n'
        '    return check_user(u, i, feature)\n'
        'def clean(self):\n'
        '    return gate(up, "deep", "patterns")\n'
        'def defaulted(self):\n'
        '    return gate(up, "deep")\n'
        'def dirty(self, skill):\n'
        '    return gate(up, "deep", skill)\n'
        'def literal_skill(self):\n'
        '    return gate(up, "deep", "pro_scan")\n'
    )
    src += (
        'def calls_dirty(self):\n'
        '    return dirty(self, "pro_scan")\n'
    )
    trees, root = _planted(src)
    findings, hops = survey_trees(trees, root)
    # `dirty` forwards its OWN parameter, so it becomes a hop in turn and the
    # question moves to ITS caller — the fixed point, on a planted tree.
    assert hops == {"gate": "feature", "dirty": "skill"}
    got = {k.split("::")[1]: kind for k, kind in findings}
    assert got == {"calls_dirty#1": "literal-unknown",
                   "literal_skill#1": "literal-unknown"}, got


def test_an_ambiguous_hop_name_is_not_resolved_to_a_guess():
    """Two definitions of one hop name make every caller UNRESOLVED rather
    than resolved to whichever came first. That over-accuses — the loud
    direction — and it is what the methods ratchet's own note argues for."""
    src = (
        'def gate(a, b, feature="deepscan"):\n'
        '    return check_user(u, i, feature)\n'
        'class Other:\n'
        '    def gate(self, x):\n'
        '        return x\n'
        'def caller(self):\n'
        '    return gate(up, "deep", "patterns")\n'
    )
    trees, root = _planted(src)
    findings, _hops = survey_trees(trees, root)
    assert [k.split("::")[1] for k, _ in findings] == ["caller#1"], findings


def test_a_reasonless_baseline_row_is_reported():
    rows, reasonless = baseline_rows("a/b.py::f#1\nc/d.py::g#1  a real reason\n")
    assert rows == {"c/d.py::g#1": "a real reason"}
    assert reasonless == ["a/b.py::f#1"]


def test_the_baseline_excuses_only_what_it_names():
    """THE BASELINE IS EMPTY TODAY, so both rules above are vacuous against
    the real tree and a mutation of either changes no verdict there. Drive
    them where they are the only thing in play — the same argument
    `candle_hygiene_baseline` makes for its own two-way rule.
    """
    findings = [("a/b.py::f#1", "unresolved"), ("c/d.py::g#1", "literal-unknown")]
    assert unlisted_findings(findings, {}) == [
        "a/b.py::f#1  (unresolved)", "c/d.py::g#1  (literal-unknown)"]
    # A named row is excused, and ONLY that one.
    assert unlisted_findings(findings, {"a/b.py::f#1": "why"}) == [
        "c/d.py::g#1  (literal-unknown)"]
    # A row naming a site that no longer appears is STALE — the other
    # direction, which is what stops an exemption outliving its reason.
    assert stale_rows(findings, {"a/b.py::f#1": "why"}) == []
    assert stale_rows(findings, {"gone/away.py::h#1": "why"}) == ["gone/away.py::h#1"]
    assert stale_rows([], {"a/b.py::f#1": "why"}) == ["a/b.py::f#1"]


# ── the THIRD noun: the word the refusal SHOWS ────────────────────────────
#
# `_token_gate_blocks`'s own docstring separates two of the three — "`mode` is
# only ever shown to the user; `feature` is what is actually checked" — and
# that separation was made because a paywalled web caller read "Pro_scan scan
# is a staked-tier feature": an internal identifier, capitalised, in the
# sentence asking them to buy something. The display half was only half-built.
# `upgrade_message` appended " scan" to whatever it was handed, so the CALLER
# chose a word and the sentence chose the noun. Driven over the eleven display
# words in the tree, six read false: "Backtest scan", "Walk-forward scan",
# "Learning scan", "Optimize scan", "Analysis scan", "Patterns scan".

UPGRADE = "upgrade_message"


def _walk_with_chain(trees):
    """`(path, [(node, chain), ...])` — the enclosing-function chain, so a
    forwarded PARAMETER can be told from a chosen word."""
    for path, tree in trees.items():
        yield path, list(_chain(tree))


def _is_plumbing(arg, enclosing) -> bool:
    """True when `arg` is this function's own parameter, or an `or` over them."""
    if enclosing is None:
        return False
    params = {a.arg for a in enclosing.args.args + enclosing.args.kwonlyargs}
    names = [n.id for n in ast.walk(arg) if isinstance(n, ast.Name)]
    return bool(names) and all(n in params for n in names)
#: Category nouns `upgrade_message` must never add on the caller's behalf.
#: Not a list of the six I fixed — that is the shape this file exists to
#: refuse — but of the words that would make the sentence name a capability.
_CATEGORY_NOUNS = ("scan", "analysis", "backtest", "report", "detection",
                   "optimisation", "optimization", "validation")


def test_the_refusal_names_only_the_capability_it_was_given():
    """DRIVEN on a word that is no capability at all.

    Every real display phrase already contains its own noun, so a mutation
    that re-appends a category agrees with each of them on the part a reader
    checks — which is why this plants a word the product does not have.
    """
    from bot.token.tier_gate import upgrade_message

    line = upgrade_message("zzz").split("\n")[0]
    assert "Zzz is a staked-tier feature" in line, line
    for noun in _CATEGORY_NOUNS:
        assert noun not in line.lower(), (
            f"`upgrade_message` adds the noun {noun!r} the caller did not "
            f"supply, so the sentence names a capability the caller never "
            f"chose: {line!r}")


def test_no_display_word_is_a_feature_identifier():
    """The three nouns, kept apart at every call site.

    A display argument that is a `FEATURE_MIN_TIER` key is the `Pro_scan scan`
    shape — the internal name reaching the person. `_pane_gate_blocks` was
    doing exactly that, handing its FEATURE to `upgrade_message` because it
    had two names for three things.
    """
    trees = {}
    for f in _production_files(ROOT):
        try:
            trees[f] = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

    bad, seen = [], 0
    for path, tree in _walk_with_chain(trees):
        for node, chain in tree:
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if name == "_token_gate_blocks":
                arg = _arg_at(node, 1, "mode")
            elif name == "_pane_gate_blocks":
                arg = _arg_at(node, 2, "display")
            elif name == UPGRADE:
                arg = _arg_at(node, 0, "mode")
            else:
                continue
            if arg is None:
                # An OMITTED display falls back to the feature/skill name.
                # That fallback is a RECORDED decision — "it defaults to the
                # skill name, which is what every caller without one has
                # always had" — not an oversight, so this rule does not
                # re-litigate it. It checks the displays that ARE chosen.
                continue
            if _is_plumbing(arg, chain[-1] if chain else None):
                # A FORWARDED parameter. `_token_gate_blocks`,
                # `_pane_gate_blocks` and `_web_skill_denied` each end in
                # `upgrade_message(<their own parameter>)`; they do not choose
                # the word, their callers do, and those are checked above.
                # Reporting them would be the accusation a walk with no hop
                # notion manufactures — the same blind spot, one noun over.
                continue
            seen += 1
            if isinstance(arg, ast.JoinedStr):
                # A COMPUTED phrase. The mode word comes from the dispatch
                # table, so only the caller can supply the noun — and the
                # first draft of this walk skipped these entirely under a
                # comment promising "the f-string case below", which did not
                # exist. The mutation that dropped the noun from the one
                # routed call site survived a green suite because of it.
                literal = "".join(v.value for v in arg.values
                                  if isinstance(v, ast.Constant)
                                  and isinstance(v.value, str))
                if not any(n in literal.lower() for n in _CATEGORY_NOUNS):
                    bad.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                               f"  {name}(… f-string with no capability noun)")
                continue
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                # UNREADABLE IS NOT A PASS, in this walk as everywhere else.
                # The first draft skipped anything it could not classify, so
                # the mutation that swapped the routed f-string for a bare
                # `str(...)` call survived: the noun vanished from the
                # sentence and the guard said nothing, because the expression
                # was no longer one it recognised.
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                           f"  {name}(… {ast.unparse(arg)}) — a display this "
                           "walk cannot read for its capability noun")
                continue
            if arg.value in FEATURE_MIN_TIER or "_" in arg.value:
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                           f"  {name}(… {arg.value!r})")
    assert seen >= 12, seen       # the walk found the call sites at all
    assert not bad, (
        "A display argument that is a FEATURE identifier puts an internal "
        "name in the sentence asking somebody to buy something:\n  "
        + "\n  ".join(sorted(bad)))


def test_every_display_phrase_carries_its_own_noun():
    """The sentence each real call site renders, driven end to end.

    Not asserted against a list of expected strings — that is a second copy of
    the display words. The claim is that every rendered sentence NAMES the
    phrase its call site chose, and adds nothing.
    """
    from bot.token.tier_gate import upgrade_message

    trees = {}
    for f in _production_files(ROOT):
        try:
            trees[f] = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

    phrases = []
    for _path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _called_name(node) != "_token_gate_blocks":
                continue
            arg = _arg_at(node, 1, "mode")
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                phrases.append(arg.value)
    assert len(phrases) >= 11, phrases

    for phrase in phrases:
        line = upgrade_message(phrase).split("\n")[0]
        assert phrase.capitalize() in line, (phrase, line)
        # and nothing else: the only capability noun in the sentence is the
        # caller's own.
        rest = line.lower().replace(phrase.lower(), "")
        for noun in _CATEGORY_NOUNS:
            assert noun not in rest, (phrase, noun, line)


def test_the_pane_renders_the_display_word_it_was_given():
    """DRIVEN, because a call-site scan cannot see what the BODY does with it.

    `test_no_display_word_is_a_feature_identifier` reads the arguments; the
    mutation that made `_pane_gate_blocks` ignore `display` and hand its
    FEATURE to `upgrade_message` anyway survived it, since every call site
    still passed a good phrase. The claim is about the SENTENCE, so the
    sentence is what is read — through a stand-in `self` carrying the one
    attribute the method reaches for.
    """
    import types

    from bot.skills.telegram_handler import TelegramHandler
    from bot.token import tier_gate

    prev = dict(os.environ)
    os.environ["TOKEN_TIER_GATE_ENABLED"] = "true"
    os.environ["RCLAW_MINT"] = "So11111111111111111111111111111111111111112"
    resolve = tier_gate._resolve_wallet
    tier_gate._resolve_wallet = lambda users, uid: None
    try:
        host = types.SimpleNamespace(users=object())
        out = TelegramHandler._pane_gate_blocks(
            host, "learning", "u", "the learning report")
        assert out and "The learning report is a staked-tier feature" in out, out
        assert "Learning is a staked-tier" not in out, out

        # And with no display, the feature is the fallback every caller
        # without one has always had — stated, so the day it changes this
        # says so rather than the pane quietly starting to print nothing.
        bare = TelegramHandler._pane_gate_blocks(host, "learning", "u")
        assert bare and "Learning is a staked-tier feature" in bare, bare
    finally:
        tier_gate._resolve_wallet = resolve
        os.environ.clear()
        os.environ.update(prev)

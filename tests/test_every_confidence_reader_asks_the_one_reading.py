"""One reading of an idea's confidence, at every gate and every display.

`bot/core/signal_confidence.displayed_confidence` exists because one
`send_photo` went out with two different confidences on it. #105 repaired the
five surfaces it had measured and added a rule — and that rule forbade reading
`blended_confidence_raw`, which is only ONE HALF of the pair. Nothing forbade
reading `idea.confidence`, the other half, which is what the calibration curve
leaves on the field.

Driven, the class was wider than five displays and two of its members were
GATES: the critique's overconfidence concern, the pyramid gate, the
drawdown-recovery floor and a strategy preset's own threshold each compared the
CALIBRATED field against a bar on the RAW scale, and each of them cleared on the
1.0 that `build_manual_idea` STAMPS on a hand-typed ticket.

THE RULE COVERS THE TWO SHAPES THE DEFECT TOOK, and neither is "any read":

* a PERCENT-formatted display — what a caller is TOLD the confidence is;
* a COMPARISON against a bar — what a gate DOES about it.

An internal read that neither prints nor gates is outside the class on purpose.
`risk_engine`'s own arithmetic, the four decision-row writers and the flight
recorder each hand the field onward under its own name, and *a recorder is not a
reader* — which is the distinction the sibling rule already draws for
`blended_confidence_raw`, and what lets both rules cover the tree with a short
baseline rather than a long one.

The receiver is NOT judged, and it used to be. The first version kept a
hand-written list of names that hold an idea (`idea`, `new_idea`, `best`, `p`,
...), and a TradeIdea under any other name was acquitted in silence: the
engine's public thesis event read `_fi.confidence` -- the figure the
setup-expectancy nudge had already moved -- and published it on the agent
feed beside a signal row publishing the one reading for the same idea. A
vocabulary of names is the `/setllm` ten-of-eleven shape, and it failed in the
quiet direction. So every receiver is in scope, and one this rule cannot place
is LOUD: it needs a baseline row saying what quantity it is, the rule
`tests/command_gates.py` already follows for an unrecognised gate spelling.
That over-reports by construction -- the intent router's classification
confidence, a pattern detector's own, the quality reading itself -- and the
over-report is answered by a row with a reason rather than by a cleverer regex,
the ruling `confidence_provenance_baseline` already takes for the same
ambiguity.

And the SPELLING is not judged either. The rule first walked for the attribute
alone, and four surfaces read the field as `getattr(idea, "confidence")` or
through a local bound to it -- the chart subtitle, the `/analyze` card, the
model's pending-ideas row, the web chat's "Trade this" hint -- each printing
the calibrated field beside cards printing the blend. A local of the same
function bound to the field (coerced or not) carries it now; a local bound to
a function OF the field is a different quantity and is not followed; and an
`is None` check asks whether a figure is there, which is not a gate.
"""

import ast
import pathlib

import pytest

#: The one reading every gate and display must ask.
READING = "displayed_confidence"

#: The module that DEFINES it, and the two that define what the field means.
#: Each must still hold a read, or its entry is stale.
READING_MODULES = (
    "bot/core/signal_confidence.py",
    "bot/learning/confidence_calibration.py",
)

BASELINE = pathlib.Path("tests/confidence_reader_baseline.txt")


def _rows():
    """(path, kind) -> reason, from the baseline. A reasonless row fails."""
    out, bad = {}, []
    for raw in BASELINE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[2].strip():
            bad.append(raw)
            continue
        path, kind, reason = parts[0], parts[1], parts[2].strip()
        out.setdefault((path, kind), []).append(reason)
    assert not bad, f"baseline rows with no reason: {bad}"
    return out


#: What a local may be wrapped in and still BE the field it was read from.
#: `float(getattr(idea, "confidence", 0) or 0)` is the field, coerced; a call
#: that computes something else from it (`displayed_confidence(idea)`) is not.
_PASS_THROUGH = frozenset({"float", "round", "int", "abs", "_f", "_num"})


def _is_getattr_conf(node) -> bool:
    """`getattr(x, "confidence"[, default])` -- the field under another
    spelling, which the attribute walk alone could not see (#158)."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "confidence")


def _is_the_field(value) -> bool:
    """Is this expression the field itself, perhaps coerced? A local bound to
    it carries the field under a new name."""
    while True:
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in _PASS_THROUGH and value.args):
            value = value.args[0]
            continue
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            value = value.values[0]
            continue
        break
    return ((isinstance(value, ast.Attribute) and value.attr == "confidence")
            or _is_getattr_conf(value))


def _own_nodes(scope):
    """Every node in `scope` that belongs to it and not to a function nested
    inside it: a local is bound per function."""
    out, stack = [], list(ast.iter_child_nodes(scope))
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def _reads(src: str):
    """Every (kind, line, text) where an idea-like `.confidence` is PRINTED as a
    percent or COMPARED against something, and the reading is not asked."""
    # RAW source, parsed. `code_only` blanks DOCSTRINGS, and 11 files in
    # `bot/` stop parsing once theirs are gone -- the cost #106's sibling rule
    # already measured -- and it buys an AST rule nothing: a comment is not a
    # node, and a docstring is a bare string constant carrying neither a
    # `FormattedValue` nor a `Compare`.
    tree = ast.parse(src)
    out = []

    def _reads_the_field(node, carriers):
        # Any receiver. A name list acquitted `_fi` (see the module docstring).
        # And any SPELLING: the attribute, `getattr(x, "confidence")`, or a
        # local of this function bound to either -- the three the Telegram
        # chart subtitle and the model's pending-ideas row used (#158).
        for a in ast.walk(node):
            if (isinstance(a, ast.Attribute) and a.attr == "confidence"
                    and isinstance(a.ctx, ast.Load)):
                return True
            if _is_getattr_conf(a):
                return True
            if (isinstance(a, ast.Name) and isinstance(a.ctx, ast.Load)
                    and a.id in carriers):
                return True
        return False

    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        nodes = _own_nodes(scope)
        carriers = {n.targets[0].id for n in nodes
                    if isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)
                    and _is_the_field(n.value)}
        for node in nodes:
            if isinstance(node, ast.FormattedValue):
                spec = ast.unparse(node.format_spec) if node.format_spec else ""
                src_v = ast.unparse(node.value)
                if ("%" in spec or "* 100" in src_v) and _reads_the_field(
                        node.value, carriers):
                    out.append(("pct", node.lineno, src_v[:60]))
            elif isinstance(node, ast.Compare):
                # `x.confidence is None` asks whether a figure is THERE, which
                # is the question every honest reader asks first; it prints
                # nothing and gates on no bar.
                if all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                    continue
                if any(_reads_the_field(s, carriers)
                       for s in [node.left, *node.comparators]):
                    out.append(("cmp", node.lineno, ast.unparse(node)[:60]))
    return out


def _survey():
    """(path, kind) -> [(line, text)] over `bot/`, excluding the reading's own
    modules."""
    found = {}
    for p in sorted(pathlib.Path("bot").rglob("*.py")):
        if str(p) in READING_MODULES:
            continue
        for kind, line, text in _reads(p.read_text()):
            found.setdefault((str(p), kind), []).append((line, text))
    return found


class TestTheRuleOverTheClass:

    def test_no_unlisted_reader_prints_or_gates_on_the_field(self):
        rows = _rows()
        offenders = {k: v for k, v in _survey().items() if k not in rows}
        assert not offenders, (
            "these print or gate on an idea's `confidence` without asking "
            f"{READING}(): {offenders}")

    def test_no_baseline_row_is_stale(self):
        """A row the rule no longer finds hides the next copy."""
        found = _survey()
        stale = [k for k in _rows() if k not in found]
        assert not stale, (
            f"delete these rows in the same commit: {stale}")

    def test_the_counts_match(self):
        """A file may hold several sites of one kind, and a NEW one inside a
        listed file must not be acquitted by the rows already there."""
        rows, found = _rows(), _survey()
        for key, reasons in rows.items():
            assert len(found.get(key, [])) == len(reasons), (
                f"{key}: {len(found.get(key, []))} site(s) in the tree against "
                f"{len(reasons)} reason(s) in the baseline")

    def test_each_reading_module_still_reads_the_field(self):
        for path in READING_MODULES:
            src = pathlib.Path(path).read_text()
            assert "confidence" in src, (
                f"{path} is excused and no longer reads a confidence at all: "
                "delete the entry in the same commit")


class TestTheRuleItself:
    """Driven on PLANTED source, because the real tree passes and a mutation of
    the RULE changes no verdict there."""

    @pytest.mark.parametrize("shape,src", [
        ("a percent display of the field",
         'def f(idea):\n    return f"Conf {idea.confidence:.0%}"\n'),
        ("the *100 spelling",
         'def f(idea):\n    return f"Conf {idea.confidence * 100:.0f}%"\n'),
        ("a gate against a constant",
         'def f(idea):\n    return idea.confidence < 0.7\n'),
        ("a gate against a config field",
         'def f(idea):\n    return idea.confidence >= CONFIG.risk.min_confidence\n'),
        ("a pill wrapping the format",
         'def f(idea):\n    return _pill(f"{idea.confidence:.0%}")\n'),
        ("a TradeIdea under a name no list anticipated (the engine's `_fi`)",
         'def f(ideas):\n    for _fi in ideas:\n        emit(f"{_fi.confidence:.0%}")\n'),
        ("a receiver the rule cannot place, which must carry a reason",
         'def f(row):\n    return f"{row.confidence:.0%}"\n'),
        # #158: the three spellings the Telegram chart subtitle, the analyze
        # card and the model's pending-ideas row used, none of which is the
        # attribute the first rule walked for.
        ("getattr in the format itself",
         'def f(idea):\n    return f"{getattr(idea, \'confidence\', 0):.0%}"\n'),
        ("a local bound to getattr, formatted later (the chart subtitle)",
         'def f(idea):\n    conf = getattr(idea, "confidence", None)\n'
         '    bits.append(f"conf {conf:.0%}")\n'),
        ("a local bound to the coerced field (the analyze card)",
         'def f(idea):\n    conf = float(getattr(idea, "confidence", 0) or 0)\n'
         '    return f"{conf * 100:.0f}%"\n'),
        ("a local bound to the attribute, gated later",
         'def f(idea):\n    c = idea.confidence\n    return c >= 0.7\n'),
    ])
    def test_the_rule_flags_each_shape_the_slice_removed(self, shape, src):
        assert _reads(src), shape

    @pytest.mark.parametrize("shape,src", [
        ("the honest reader",
         'def f(idea):\n    return displayed_confidence(idea).pct()\n'),
        ("the honest gate",
         'def f(idea):\n    return displayed_confidence(idea).clears(0.7)\n'),
        ("a recorder handing the field onward under its own name",
         'def f(idea):\n    return Row(confidence=idea.confidence)\n'),
        ("a dict entry of the same name",
         'def f(idea):\n    return {"confidence": idea.confidence}\n'),
        ("a threshold that is not an idea's figure",
         'def f(cfg):\n    return f"{cfg.min_confidence:.0%}"\n'),
        ("a non-percent format",
         'def f(idea):\n    return f"{idea.confidence:.2f}"\n'),
        ("asking whether the figure is there",
         'def f(r):\n    return r.confidence is None\n'),
        ("asking whether a carried figure is there",
         'def f(r):\n    c = r.confidence\n    return c is not None\n'),
        ("a local bound to the ONE READING's value",
         'def f(idea):\n    conf = displayed_confidence(idea).value\n'
         '    return f"{conf:.0%}"\n'),
        ("a local of ANOTHER function, which is not this one's carrier",
         'def g(idea):\n    conf = idea.confidence\n    return conf\n'
         'def f(x):\n    conf = x.size\n    return f"{conf:.0%}"\n'),
        ("a local bound to a function OF the field, not a coercion of it",
         'def f(idea):\n    band = bucket(idea.confidence)\n'
         '    return f"{band:.0%}"\n'),
        ("a local computed FROM the field, which is a different quantity",
         'def f(idea):\n    gap = 1 - idea.confidence\n    return gap\n'
         'def h(idea):\n    edge = max(0.0, 0.5)\n    return f"{edge:.0%}"\n'),
    ])
    def test_the_rule_leaves_these_alone(self, shape, src):
        assert not _reads(src), shape

    def test_prose_naming_the_shape_is_not_the_shape(self):
        """This file's own docstring names both shapes it forbids, and the
        chapter in CLAUDE.md quotes them too. An AST walk cannot see a comment
        at all and reads a docstring as a bare string constant, which is why
        this rule parses RAW source rather than paying `code_only`'s cost."""
        assert not _reads(
            "def f(idea):\n"
            "    # f\"{idea.confidence:.0%}\" was the defect\n"
            '    """And `idea.confidence < 0.7` was the gate."""\n'
            "    return displayed_confidence(idea).pct()\n")

    def test_a_class_whose_body_is_only_a_docstring_still_parses(self):
        """The reason the rule does not strip: `code_only` leaves such a class
        with an empty body, and 11 files in `bot/` stop parsing."""
        assert not _reads('class TokenStore:\n    """Only a docstring."""\n')

    def test_a_stale_row_fails(self, tmp_path, monkeypatch):
        """Driven on a PLANTED baseline, because against an honest one the
        stale check passes whatever the rule does — the mutation that replaced
        it with `stale = []` survived a green suite."""
        import tests.test_every_confidence_reader_asks_the_one_reading as mod

        f = tmp_path / "b.txt"
        f.write_text("bot/nowhere_at_all.py  pct  a row the rule cannot find\n")
        monkeypatch.setattr(mod, "BASELINE", f)
        with pytest.raises(AssertionError, match="delete these rows"):
            mod.TestTheRuleOverTheClass().test_no_baseline_row_is_stale()

    def test_a_new_site_inside_a_listed_file_is_not_acquitted(self, tmp_path,
                                                              monkeypatch):
        """The COUNT is the check, and it needs a planted survey: a file may
        hold several sites of one kind, and against the real tree a `>=`
        comparison agrees with `==` on every row."""
        import tests.test_every_confidence_reader_asks_the_one_reading as mod

        f = tmp_path / "b.txt"
        f.write_text("bot/x.py  pct  one site, one reason\n")
        monkeypatch.setattr(mod, "BASELINE", f)
        monkeypatch.setattr(mod, "_survey",
                            lambda: {("bot/x.py", "pct"): [(1, "a"), (2, "b")]})
        with pytest.raises(AssertionError, match="against"):
            mod.TestTheRuleOverTheClass().test_the_counts_match()

    def test_a_reasonless_row_fails(self, tmp_path, monkeypatch):
        import tests.test_every_confidence_reader_asks_the_one_reading as mod

        f = tmp_path / "b.txt"
        f.write_text("bot/x.py  pct\n")
        monkeypatch.setattr(mod, "BASELINE", f)
        with pytest.raises(AssertionError, match="no reason"):
            mod._rows()

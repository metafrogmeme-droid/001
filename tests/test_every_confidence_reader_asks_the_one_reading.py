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

    def _is_idea_conf(node):
        # Any receiver. A name list acquitted `_fi` (see the module docstring).
        for a in ast.walk(node):
            if (isinstance(a, ast.Attribute) and a.attr == "confidence"
                    and isinstance(a.ctx, ast.Load)):
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue):
            spec = ast.unparse(node.format_spec) if node.format_spec else ""
            src_v = ast.unparse(node.value)
            if ("%" in spec or "* 100" in src_v) and _is_idea_conf(node.value):
                out.append(("pct", node.lineno, src_v[:60]))
        elif isinstance(node, ast.Compare):
            if any(_is_idea_conf(s) for s in [node.left, *node.comparators]):
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

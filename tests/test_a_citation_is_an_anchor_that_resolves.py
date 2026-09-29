"""`scripts/cite.py`: an anchor resolves to exactly one place, or it is refused.

The income map cited code as `path:line`, and a line number rots by pointing
at the WRONG line rather than an impossible one: every edit above it moves it
and nothing short of a reader who knows what the citation meant can tell. An
anchor (`path::Qualname[#marker]`) names what the sentence names, and this
suite drives the resolver, the checker and the codemod on PLANTED trees, so
each rule is measured where it is the only thing in play rather than on a
real tree that happens to satisfy it.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import cite  # noqa: E402

PY = '''\
"""Module docstring."""
import functools

LIMIT = 5
if True:
    OPTIONAL = 1


class Engine:
    """Engine docstring."""

    field: int = 3

    @functools.lru_cache
    def tick(self):
        total = 0
        total += LIMIT
        return total

    @property
    def mode(self):
        return "x"

    @mode.setter
    def mode(self, value):
        pass

    def outer(self):
        def inner():
            return "inner body"
        return inner


TWICE = 1
TWICE = 2
'''

JS = r'''// header
const TOOLS = {
  get_gas: {
    description: 'gas',
  },
  get_rwa: { description: 'rwa' },
};

function load(rows) {
  const local = rows.filter(r => /["']/.test(r.name));
  const text = `${rows.map(r => `<b>${r}</b>`).join('')}`;
  return local.length + text.length;
}

router.post('/follow', async (req, res) => {
  const rows = await db.all();
  res.json({ ok: true, n: rows.length });
});

async function after() {
  return 'reached after the regex and the nested template';
}
'''


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A planted repo: cite.ROOT points at it and every cache starts empty."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(PY, encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "api.js").write_text(JS, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "NOTES.md").write_text("one\n\nNo token exists here.\n",
                                                encoding="utf-8")
    monkeypatch.setattr(cite, "ROOT", tmp_path)
    for fn in (cite._lines, cite._py_symbols, cite._js_symbols, cite._tracked):
        fn.cache_clear()
    yield tmp_path
    for fn in (cite._lines, cite._py_symbols, cite._js_symbols, cite._tracked):
        fn.cache_clear()


def _at(anchor: str) -> tuple[int, int]:
    r = cite.resolve(anchor)
    return r.start, r.end


# ── Python ──────────────────────────────────────────────────────────────────

def test_a_python_anchor_names_the_symbol_its_sentence_names(tree):
    assert _at("pkg/mod.py::Engine") == (9, 31)
    assert _at("pkg/mod.py::Engine.tick") == (15, 18)
    assert _at("pkg/mod.py::Engine.field") == (12, 12)
    assert _at("pkg/mod.py::LIMIT") == (4, 4)
    assert _at("pkg/mod.py::OPTIONAL") == (6, 6)      # bound inside a module-level `if`
    assert _at("pkg/mod.py::__doc__") == (1, 1)
    assert _at("pkg/mod.py::Engine.__doc__") == (10, 10)
    assert _at("pkg/mod.py::Engine.outer.inner") == (29, 30)
    # A marker names one line inside the symbol it follows.
    assert _at('pkg/mod.py::Engine.tick#"total += LIMIT"') == (17, 17)
    # A symbol's range includes its decorators, so a marker on one is inside it.
    assert _at("pkg/mod.py::Engine.tick#functools.lru_cache") == (14, 14)
    # A property setter is the same symbol as its getter, not a second binding.
    assert _at("pkg/mod.py::Engine.mode") == (21, 22)
    # A function's local is not a symbol: a document cannot name `total`.
    with pytest.raises(cite.CiteError, match="no such symbol"):
        cite.resolve("pkg/mod.py::Engine.tick.total")


@pytest.mark.parametrize("anchor, refusal", [
    ("pkg/missing.py::Engine", "no such file"),
    ("mod.py::Engine", "no such file.*did you mean pkg/mod.py"),
    ("pkg/mod.py::Motor", "no such symbol"),
    ("pkg/mod.py::TWICE", "ambiguous, bound on lines 34, 35"),
    # The marker exists in the file, but not inside the symbol it follows.
    ('pkg/mod.py::Engine.mode#"total += LIMIT"', "is not in it"),
    ('pkg/mod.py::Engine#"return"', "is on 4 lines"),
    ('pkg/mod.py::#"nowhere at all"', "is not in it"),
    ("pkg/mod.py::", "names neither a symbol nor a marker"),
    ("docs/NOTES.md::Heading", "only .py and .js files have symbols"),
])
def test_an_anchor_that_does_not_resolve_is_refused_by_name(tree, anchor, refusal):
    with pytest.raises(cite.CiteError, match=refusal):
        cite.resolve(anchor)


def test_a_file_level_marker_reads_any_file(tree):
    assert _at('docs/NOTES.md::#"No token exists"') == (3, 3)


# ── JavaScript ──────────────────────────────────────────────────────────────

def test_a_js_anchor_resolves_declarations_routes_and_members(tree):
    assert _at("web/api.js::TOOLS") == (2, 7)
    assert _at("web/api.js::TOOLS.get_gas") == (3, 5)
    assert _at("web/api.js::TOOLS.get_rwa") == (6, 6)
    assert _at("web/api.js::load") == (9, 13)
    assert _at("web/api.js::post('/follow')") == (15, 18)
    assert _at("web/api.js::post('/follow')#\"n: rows.length\"") == (17, 17)
    # A binding inside a function or a route handler is a local.
    for local in ("local", "text", "rows"):
        with pytest.raises(cite.CiteError, match="no such symbol"):
            cite.resolve(f"web/api.js::{local}")


def test_a_regex_literal_and_a_nested_template_do_not_derail_the_scanner(tree):
    """`/["']/` holds both quote characters and `${... `<b>` ...}` nests a
    template: a lexer that read either as a string would never find where
    `load` closes, and every range after it would be wrong."""
    assert _at("web/api.js::load")[1] == 13
    assert _at("web/api.js::after") == (20, 22)
    assert _at('web/api.js::after#"nested template"') == (21, 21)


# ── the check ───────────────────────────────────────────────────────────────

def test_the_check_refuses_a_planted_broken_anchor_and_a_planted_line_citation(tree):
    good = ('The engine (`pkg/mod.py::Engine`) ticks in `::Engine.tick`, and '
            '`web/api.js::post(\'/follow\')` follows.\n')
    assert cite.check_text(good, "MAP.md") == []

    broken = "The engine ticks in `pkg/mod.py::Engine.tock`.\n"
    assert cite.check_text(broken, "MAP.md") == [
        "MAP.md:1: pkg/mod.py::Engine.tock: no such symbol"]

    line = "The engine ticks at pkg/mod.py:17 and again at :18.\n"
    errors = cite.check_text(line, "MAP.md")
    assert len(errors) == 2 and all("cites code by line number" in e for e in errors), errors

    # A continuation with nothing before it in its paragraph is refused, not
    # read as the last paragraph's file.
    orphan = "`pkg/mod.py::Engine`\n\nthen `::Engine.tick`.\n"
    assert cite.check_text(orphan, "MAP.md") == [
        "MAP.md:3: ::Engine.tick: continues no anchor in its paragraph"]

    # A non-code line citation is still checked, and a blank target refused.
    assert cite.check_text("see docs/NOTES.md:3\n", "MAP.md") == []
    assert cite.check_text("see docs/NOTES.md:2\n", "MAP.md") == [
        "MAP.md:1: docs/NOTES.md:2 is a blank line"]


def test_the_command_line_says_which_of_three_outcomes(tree):
    """Exit 0 resolved, 1 a citation is wrong, 2 the document could not be
    read: could not check is neither a pass nor a failure."""
    (tree / "docs" / "GOOD.md").write_text("`pkg/mod.py::LIMIT`\n", encoding="utf-8")
    (tree / "docs" / "BAD.md").write_text("pkg/mod.py:4\n", encoding="utf-8")
    assert cite.main(["--check", "docs/GOOD.md"]) == 0
    assert cite.main(["--check", "docs/BAD.md"]) == 1
    assert cite.main(["--check", "docs/ABSENT.md"]) == 2


# ── the codemod ─────────────────────────────────────────────────────────────

def test_the_codemod_converts_what_it_can_place_and_leaves_the_rest(tree):
    (tree / "other").mkdir()
    (tree / "other" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    text = textwrap.dedent("""\
        The class (pkg/mod.py:9), a statement in it (pkg/mod.py:17), and :15.

        A route (web/api.js:17), and a blank line (pkg/mod.py:7).

        Two files share this name: mod.py:1.
        """)
    new, report = cite.codemod_text(text)
    assert new.splitlines()[0] == (
        "The class (`pkg/mod.py::Engine`), a statement in it "
        "(`pkg/mod.py::Engine.tick#\"total += LIMIT\"`), and `::Engine.tick`.")
    assert new.splitlines()[2] == (
        "A route (`web/api.js::post('/follow')#\"res.json({ ok:\"`), "
        "and a blank line (pkg/mod.py:7).")
    # Never guessed: a blank line and a name two files share are left as
    # written and reported.
    assert new.splitlines()[4] == "Two files share this name: mod.py:1."
    left = {c.original: c.note for c in report if c.replacement is None}
    assert left["pkg/mod.py:7"] == "pkg/mod.py:7 is a blank line"
    assert "several files" in left["mod.py:1"]
    # What it wrote resolves, and what it left is still refused by the check,
    # so a citation the codemod could not place waits for a reader.
    fix = "cites code by line number; write an anchor (path::Qualname[#marker])"
    assert cite.check_text(new, "MAP.md") == [
        f"MAP.md:3: pkg/mod.py:7 {fix}", f"MAP.md:5: mod.py:1 {fix}"]


def test_a_converted_citation_inside_a_code_span_is_not_wrapped_again(tree):
    text = "Read `LIMIT (pkg/mod.py:4)` first.\n"
    new, _ = cite.codemod_text(text)
    assert new == "Read `LIMIT (pkg/mod.py::LIMIT)` first.\n"


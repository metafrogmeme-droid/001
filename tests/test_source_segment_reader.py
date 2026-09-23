"""Two security-coverage tests died at the timeout, and the gate called it flake.

`ast.get_source_segment(src, node)` re-splits the ENTIRE source on every call.
Scanning a file node-by-node is therefore quadratic, and on
`bot/skills/telegram_handler.py` — 13,575 lines, 260 function nodes — that is:

    ast.get_source_segment over every node : 29.60s
    split once, slice per node             :  0.004s   (7,184x)

30s standalone, and under full-suite load past the 60s pytest-timeout. The CI
gate re-ran them alone, watched them pass, and filed
`~ passes alone (flaky/order-dependent)`. Accurate — and a green build printed
over two real timeouts, which is the same shape as #118 even though the verdict
itself was right that time.

`segment_reader` has to be byte-identical to the stdlib or the coverage tests
built on it start asserting about the wrong text, so that is what is tested
here: not "it looks right", but "it agrees with the reference implementation on
every node of several real files".
"""
from __future__ import annotations

import ast
import pathlib
import time

import pytest

from tests.source_scan import _splitlines_no_ff, segment_reader

REPO = pathlib.Path(__file__).resolve().parent.parent

# Real files, deliberately: the handler is the pathological one, and the others
# bring emoji, decorators, async defs, nested classes and long string literals.
SCANNED = [
    "bot/skills/telegram_handler.py",
    "bot/core/live_executor.py",
    "bot/warroom/warroom_bot.py",
    "scripts/cargo_audit_gate.py",
    "tests/source_scan.py",
]


def _existing(rel):
    p = REPO / rel
    if not p.exists():
        pytest.skip(f"{rel} not present")
    return p


class TestItAgreesWithTheStdlib:
    """The only property that matters. Everything else is a speed detail."""

    # The reference implementation is the thing being called quadratic, so a
    # comparison against it is quadratic too, and this test walked into it
    # twice. Comparing EVERY node of the 13,575-line handler ran past 120s; a
    # flat cap of 400 samples still cost 45s of it, because 400 x 13,575 lines
    # is the same arithmetic. A test for a performance bug that reproduces the
    # performance bug is still the bug.
    #
    # So the budget is on WORK, not on node count: comparisons x lines is held
    # roughly constant, which spends the samples where they are cheap and
    # keeps the pathological file to a few dozen. Still spread evenly across
    # the file, so every region is sampled rather than just the top.
    COMPARISON_BUDGET = 500_000        # ~ (lines split) x (nodes compared)
    MIN_COMPARISONS = 40

    def _sample_size(self, n_lines: int) -> int:
        return max(self.MIN_COMPARISONS,
                   min(400, self.COMPARISON_BUDGET // max(1, n_lines)))

    @pytest.mark.parametrize("rel", SCANNED)
    def test_nodes_across_a_real_file_match(self, rel):
        src = _existing(rel).read_text()
        tree = ast.parse(src)
        seg = segment_reader(src)
        nodes = [n for n in ast.walk(tree) if hasattr(n, "lineno")]
        cap = self._sample_size(len(src.splitlines()))
        step = max(1, len(nodes) // cap)
        sampled = nodes[::step]

        for node in sampled:
            assert seg(node) == ast.get_source_segment(src, node), (
                f"{rel}: diverged at line {node.lineno} "
                f"({type(node).__name__})"
            )
        # A comparison loop that compared nothing would pass silently — the
        # same "verified zero things, reported success" shape this repo keeps
        # finding. Assert it actually did work, and that the sample reached the
        # end of the file rather than stopping at the top.
        assert len(sampled) >= self.MIN_COMPARISONS, (
            f"only compared {len(sampled)} nodes in {rel}")
        assert sampled[-1].lineno > nodes[-1].lineno * 0.8, (
            "the sample never reached the bottom of the file"
        )

    @pytest.mark.parametrize("rel", SCANNED[2:])
    def test_every_function_and_class_matches(self, rel):
        """Unsampled, on the defs the coverage tests actually ask about. The
        handler and the executor are excluded here only because a stdlib call
        per def on either is the ~30 seconds this whole change exists to
        remove; both are still compared, sampled, above."""
        src = _existing(rel).read_text()
        seg = segment_reader(src)
        defs = [n for n in ast.walk(ast.parse(src))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        assert defs, f"no defs found in {rel}"
        for node in defs:
            assert seg(node) == ast.get_source_segment(src, node), (
                f"{rel}: {node.name} at line {node.lineno}")

    def test_multibyte_columns_are_handled_as_bytes(self):
        """`col_offset` is a UTF-8 BYTE offset, and this codebase is full of
        emoji. Naive character slicing cuts the wrong place on any line holding
        one — and only on those lines, so it would survive most fixtures."""
        src = 'x = "🟢🔴 long"\ndef f():\n    return "🪂"\n'
        seg = segment_reader(src)
        for node in ast.walk(ast.parse(src)):
            if hasattr(node, "lineno"):
                assert seg(node) == ast.get_source_segment(src, node)

    def test_a_single_line_node_matches(self):
        src = "a = [1, 2, 3]\n"
        seg = segment_reader(src)
        node = ast.parse(src).body[0].value
        assert seg(node) == ast.get_source_segment(src, node) == "[1, 2, 3]"

    def test_a_node_without_end_position_returns_none(self):
        seg = segment_reader("x = 1\n")

        class Bare:
            lineno = 1
            col_offset = 0
            end_lineno = None
            end_col_offset = None

        assert seg(Bare()) is None


class TestTheSplitMatchesTheParserNotStrSplitlines:
    """`str.splitlines()` breaks on form feed, vertical tab and \\x1c-\\x1e;
    the Python parser does not. Using it would shift every line index after
    such a character — silently, and only in files that contain one."""

    @pytest.mark.parametrize("ch", ["\f", "\v", "\x1c", "\x1d", "\x1e"])
    def test_a_parser_transparent_character_does_not_shift_lines(self, ch):
        src = f'a = "x{ch}y"\ndef f():\n    return 1\n'
        # State the hazard as a measurement rather than a claim: the naive
        # split sees one more line than the parser does, and every index after
        # it would be off by that one.
        assert len(src.splitlines()) == 4, "str.splitlines() should over-split here"
        assert len(_splitlines_no_ff(src)) == 3, "the parser sees three lines"

        seg = segment_reader(src)
        fn = [n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef)][0]
        assert seg(fn) == ast.get_source_segment(src, fn)
        assert "def f():" in seg(fn), "off-by-one would return the wrong body"


class TestItIsActuallyFasterOnThePathologicalFile:
    """The reason this exists. Pinned as a RATIO against the stdlib measured in
    the same run, never as a wall-clock budget — an absolute threshold would
    fail on a slow CI box for reasons that have nothing to do with the code."""

    def test_it_beats_the_quadratic_form_by_orders_of_magnitude(self):
        # The handler was the pathological file (13,575 lines, 260 nodes)
        # until it was split into mixins. Whichever file under bot/ carries
        # the most function nodes now stands in for it, so the ratio is
        # still measured on the worst case rather than on a file that shrank.
        def _nodes(text: str) -> int:
            try:
                return sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                           for n in ast.walk(ast.parse(text)))
            except SyntaxError:
                return 0
        src = max((p.read_text(encoding="utf-8") for p in REPO.joinpath("bot").rglob("*.py")
                   if "__pycache__" not in p.parts), key=_nodes)
        funcs = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert len(funcs) > 100, f"only {len(funcs)} functions — no large file left to measure on?"

        # Sample the stdlib rather than running all 260 — this test must not
        # itself take 30 seconds to prove that something took 30 seconds.
        sample = funcs[:20]
        t = time.perf_counter()
        for n in sample:
            ast.get_source_segment(src, n)
        stdlib_per_node = (time.perf_counter() - t) / len(sample)

        t = time.perf_counter()
        seg = segment_reader(src)
        for n in funcs:
            seg(n)
        ours_per_node = (time.perf_counter() - t) / len(funcs)

        assert ours_per_node * 50 < stdlib_per_node, (
            f"expected a large margin; stdlib {stdlib_per_node*1e6:.0f}us/node "
            f"vs ours {ours_per_node*1e6:.0f}us/node"
        )



def looped_stdlib_segments(src: str) -> list[int]:
    """Lines where `ast.get_source_segment` is called inside a loop or a
    comprehension, within its own function. One call splits the file once and
    is fine; a call per node is the quadratic shape this module removes.

    Bounded at a def or lambda: a helper holding ONE call, invoked from a loop
    in its caller, is invisible here. Stated rather than chased, because
    following calls across functions is a guess about which helper a name
    means, and a guess that accuses correct code is the failure this repo keeps
    recording about its own instruments.
    """
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    loops = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp,
             ast.DictComp, ast.GeneratorExp)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name != "get_source_segment":
            continue
        up = parents.get(node)
        while up is not None and not isinstance(
                up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(up, loops):
                out.append(node.lineno)
                break
            up = parents.get(up)
    return out


class TestNoTestScansAFileNodeByNodeWithTheStdlib:
    """The fix above reached two tests and not the next three.

    `segment_reader` was written on 2026-08-21 because two coverage tests on
    `telegram_handler.py` were dying at the 60s timeout and being filed as
    "passes alone (flaky/order-dependent)". Five weeks later three more guards
    were filed the same way on four consecutive full runs --
    `test_halt_holds_at_order_submission` twice and
    `test_kill_switch_reaches_executor` once, the two invariants that stop an
    order being placed during a halt -- and all three were the identical shape:
    a dict of every function in `bot/core/live_executor.py` (12,828 lines, 166
    defs) built with one stdlib call per def. About 19s each on an idle box and
    past 60s under the full suite, so in every full run the guard TIMED OUT
    before it asserted anything, and the only run that measured it was the
    flake filter's re-run. That was read as a state leak until the traceback
    was read. A helper that fixes the shape does not stop the shape; a rule
    does.
    """

    # The reference suite calls the stdlib per node ON PURPOSE: it is the
    # implementation `segment_reader` is proved against. Budgeted above.
    EXEMPT = {"test_source_segment_reader.py"}

    def test_no_test_calls_the_stdlib_per_node(self):
        offenders = []
        for path in sorted(REPO.joinpath("tests").glob("*.py")):
            if path.name in self.EXEMPT:
                continue
            text = path.read_text(encoding="utf-8")
            if "get_source_segment" not in text:
                continue
            offenders += [f"{path.name}:{line}"
                          for line in looped_stdlib_segments(text)]
        assert not offenders, (
            "ast.get_source_segment re-splits the whole file on every call; "
            "called per node it is quadratic and dies at the timeout under the "
            "full suite. Use tests.source_scan.segment_reader: "
            + ", ".join(offenders))

    def test_the_exemption_is_still_needed(self):
        """An exemption that stops applying is a stale row, the
        `known_failures.txt` rule."""
        for name in self.EXEMPT:
            text = REPO.joinpath("tests", name).read_text(encoding="utf-8")
            assert looped_stdlib_segments(text), (
                f"{name} no longer calls the stdlib per node; drop it from EXEMPT")

    # The rule, driven on planted source -- on the real tree it answers
    # nothing, and a mutation of the RULE changes no verdict there.
    @pytest.mark.parametrize("planted", [
        "import ast\nfor n in ns:\n    ast.get_source_segment(src, n)\n",
        "import ast\nb = {n.name: ast.get_source_segment(src, n) for n in ns}\n",
        "import ast\nb = [ast.get_source_segment(src, n) or '' for n in ns]\n",
        "import ast\nb = '\\n'.join(ast.get_source_segment(src, n) for n in ns)\n",
        "import ast\nwhile ns:\n    ast.get_source_segment(src, ns.pop())\n",
        "from ast import get_source_segment\nfor n in ns:\n    get_source_segment(src, n)\n",
        "import ast\ndef f():\n    for n in ns:\n        if n:\n"
        "            x = ast.get_source_segment(src, n)\n",
    ])
    def test_a_per_node_call_is_found(self, planted):
        assert looped_stdlib_segments(planted), planted

    @pytest.mark.parametrize("planted", [
        "import ast\nbody = ast.get_source_segment(src, node)\n",
        "import ast\ndef f(n):\n    return ast.get_source_segment(src, n)\n",
        "import ast\nfor n in ns:\n    pass\nb = ast.get_source_segment(src, n)\n",
        # A def inside a loop body is a boundary: its call runs when the def
        # is CALLED, which this rule states it cannot see.
        "import ast\nfor n in ns:\n    def g(m):\n"
        "        return ast.get_source_segment(src, m)\n",
        "import ast\nseg = segment_reader(src)\nb = [seg(n) for n in ns]\n",
    ])
    def test_a_single_call_is_not_accused(self, planted):
        assert looped_stdlib_segments(planted) == [], planted

"""A name in `__all__` that nothing references is a promise nobody kept.

THE GAP THIS CLOSES

`tests/test_no_new_unreachable_modules.py` ratchets at MODULE granularity, and
it is right to. But `bot/warroom/warroom_bot.py` is reachable — eight of its
renderers are imported by the Telegram handler — so that check is satisfied,
and it cannot see that FOUR of the eleven names in the same module's `__all__`
are referenced by nothing at all: not the handler, not another module, not the
module itself, not even a test.

    render_status  ·  render_signal  ·  render_positions  ·  handle_callback

A third of that module's declared public API. Found 2026-08-21 while chasing
something else, exactly the way the three dead subsystems were found — by hand,
once. Same failure as #999 one level finer: the Telegram adoption card was
built, source-scanned, shipped, and rendered ZERO times in production. The code
was present. It was never reached, and no green suite distinguishes those.

WHY `__all__` AND NOT EVERY FUNCTION

Scanning every def would drown this in private helpers and be worthless.
`__all__` is an explicit, deliberate, machine-readable declaration of what a
module offers the rest of the program. An entry nothing references is therefore
not an unused private — it is a stated promise with no taker, which is a much
narrower and much more meaningful claim. 58 names are promised across 8
modules; 4 fail. That ratio is what makes this checkable rather than noisy.

"USED INTERNALLY" IS NOT DEAD, AND THE FIRST DRAFT GOT THAT WRONG

Its first version counted only references OUTSIDE the declaring module and
accused three more names:

    analyzer._sanitize_symbol      used at analyzer.py:4532
    mtf_availability.is_suppressed used at mtf_availability.py:176
    stage_profile.CEILING_SHARE    used at stage_profile.py:221

All three are live code that happens to be over-exported — a tidiness question,
not a reachability one, and lumping them in would have made the list mean two
different things at once. A reachability checker with a blind spot manufactures
exactly the accusation it exists to prevent; this one nearly manufactured three.

The same draft also produced the opposite error. A plain substring grep for
`render_status` "found" it in two modules — they define `render_status_card`
and `render_signal_card`, different functions entirely. That reading absolved
two genuinely dead names. Word boundaries in both directions, and the import
list checked by hand, is what settled it.

BOTH DIRECTIONS FAIL, WHICH IS THE POINT

* a NEW entry means somebody just added public API nothing calls;
* an entry that LEAVES the list — someone wired it up, or deleted it — must be
  removed from the baseline in the same commit. A stale entry is how a list
  stops meaning anything, and `known_failures.txt` fails the same way for the
  same reason.

WHAT IS *NOT* CLAIMED HERE

That these four should be deleted. `render_positions` is superseded by
`_cmd_open_positions`, and `handle_callback` by the main handler's own callback
router, so deletion looks right — but that is a product call about a public
surface, not a test's call. This records the fact and stops the list growing.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.source_scan import code_only as _code_only

REPO = Path(__file__).resolve().parent.parent


def code_only(text: str) -> str:
    """`source_scan.code_only`, but never fatal.

    It tokenizes, so a file that does not tokenize (a template, a fixture of
    deliberately broken syntax) would raise and take the whole detector with
    it. Falling back to the raw text is the conservative direction here: it can
    only make a name look MORE referenced, i.e. under-report dead API, never
    accuse something that is alive.
    """
    try:
        return _code_only(text)
    except Exception:
        return text


BASELINE = Path(__file__).parent / "dead_public_api_baseline.txt"

_SKIP = ("__pycache__", ".venv", "node_modules", "/build/", "/dist/")


def _python_files() -> list:
    """Every .py in the tree, tests included.

    Tests are read too — a name referenced only by tests is a DIFFERENT finding
    (that is the module-level ratchet's job) and must not be reported here as
    referenced-by-nothing. The module ratchet's own docstring records what
    happens when a detector reads too little of the tree.
    """
    return [p for p in REPO.rglob("*.py")
            if not any(s in str(p) for s in _SKIP)]


def _declared_exports(path: Path, text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in node.targets):
            try:
                names = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return []
            if isinstance(names, (list, tuple)):
                return [str(n) for n in names]
    return []


def _is_definition_line(name: str, line: str) -> bool:
    s = line.strip()
    return (s.startswith(f"def {name}(") or s.startswith(f"async def {name}(")
            or s.startswith(f"class {name}(") or s.startswith(f"class {name}:")
            or re.match(rf"^{re.escape(name)}\s*(:|=)", s) is not None)


def _references(name: str, text: str, is_own_module: bool) -> int:
    """Mentions of `name` that are neither its definition nor its export entry.

    Comments AND DOCSTRINGS are blanked first via `code_only`. Stripping only
    `#` lines was not enough and this detector proved it on itself: the module
    docstring above names all four dead exports in order to explain them, so
    the first run reported them as referenced — by the very file that exists to
    record that nothing references them. Seventh occurrence of the trap
    CLAUDE.md opens with; `code_only` is the repo's answer to it.
    """
    return _references_stripped(name, code_only(text), is_own_module)


def _references_stripped(name: str, stripped: str, is_own_module: bool) -> int:
    """The same, over text `code_only` has ALREADY been applied to.

    Split out so the tokenize pass can be hoisted out of the per-name loop —
    see `dead_public_api`. `_references` stays as the single-shot form the
    tests below exercise, so both paths are the same logic.
    """
    hits = 0
    for line in stripped.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if re.fullmatch(rf'["\']{re.escape(name)}["\'],?', s):
            continue                      # a bare entry inside __all__
        if "__all__" in s:
            continue
        if is_own_module and _is_definition_line(name, s):
            continue
        if re.search(rf"\b{re.escape(name)}\b", s):
            hits += 1
    return hits


def dead_public_api() -> set:
    """`module::name` for every `__all__` entry referenced nowhere at all.

    STRIP ONCE, NOT ONCE PER NAME. `code_only` tokenizes, and the first draft
    called it inside the per-name loop: 58 names x ~1000 files x a full
    tokenize pass, which ran in 163 SECONDS. Under full-suite load that is well
    past the 60s pytest-timeout — the exact defect #121 had just finished
    removing from three security-coverage tests, reintroduced in the test
    written the same afternoon. Hoisting the strip out of the loop takes it to
    a couple of seconds.
    """
    files = _python_files()
    stripped = {}
    exports = {}
    for p in files:
        raw = p.read_text(encoding="utf-8", errors="ignore")
        stripped[p] = code_only(raw)
        if not (p.name.startswith("test_") or "/tests/" in str(p)):
            got = _declared_exports(p, raw)
            if got:
                exports[p] = got

    dead = set()
    for path, names in exports.items():
        for name in names:
            found = False
            for other, otext in stripped.items():
                # Cheap reject first: the substring must at least appear before
                # the line-by-line pass is worth doing.
                if name not in otext:
                    continue
                if _references_stripped(name, otext, is_own_module=(other == path)):
                    found = True
                    break
            if not found:
                dead.add(f"{path.relative_to(REPO)}::{name}")
    return dead


def _baseline() -> set:
    if not BASELINE.exists():
        return set()
    return {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def test_no_new_dead_public_api():
    new = sorted(dead_public_api() - _baseline())
    assert not new, (
        "these names are declared in __all__ and referenced by nothing — not "
        "another module, not their own module, not a test:\n  "
        + "\n  ".join(new)
        + "\n\nWire it to a caller, delete it, or take it out of __all__. If it "
          "is deliberately unwired for now, add it to "
          "tests/dead_public_api_baseline.txt with a reason."
    )


def test_the_baseline_has_no_stale_entries():
    """An entry that got wired up (or deleted) must leave in the same commit.

    Same rule as known_failures.txt: a baseline nobody prunes stops being a
    record and becomes a place where real findings go to hide.
    """
    fixed = sorted(_baseline() - dead_public_api())
    assert not fixed, (
        "these are no longer dead — they are referenced now, or gone. Remove "
        "them from tests/dead_public_api_baseline.txt:\n  " + "\n  ".join(fixed))


def test_the_detector_does_not_accuse_internally_used_names():
    """The first draft's error, pinned so it cannot come back.

    `analyzer._sanitize_symbol` is exported AND used at analyzer.py:4532. It is
    over-exported, which is a tidiness question; calling it unreachable would
    make this list mean two things at once.
    """
    dead = dead_public_api()
    for name in ("_sanitize_symbol", "is_suppressed", "CEILING_SHARE"):
        offenders = [d for d in dead if d.endswith(f"::{name}")]
        assert not offenders, (
            f"{name} is used inside its own module; being over-exported is not "
            f"the same finding as being unreachable: {offenders}")


def test_the_detector_is_not_fooled_by_a_longer_name():
    """The opposite error, and the one that absolved two real findings.

    A substring grep for the short name matches the longer one — a different
    function in a different module — and reports a dead name as live. Word
    boundaries are what separate them.

    INVENTED NAMES, not the real ones, and that is not fussiness: a fixture
    naming a genuinely dead export would itself count as a reference to it, and
    this file would quietly resurrect the four names it exists to record. The
    alternative — excluding this file from the scan — buys the same result with
    a blind spot, and a detector with a blind spot is what the module-level
    ratchet's docstring warns about.
    """
    text = "msg = zz_widget_card(data)\nx = zz_other_card(d)\n"
    assert _references("zz_widget", text, is_own_module=False) == 0, (
        "zz_widget_card must not count as a reference to zz_widget")
    assert _references("zz_widget_card", text, is_own_module=False) == 1


def test_the_detector_ignores_comments_and_docstrings():
    """Both, because only one of them was handled at first and this detector
    reported its own explanatory docstring as usage."""
    assert _references("zz_widget", "# zz_widget is dead\nx = 1\n",
                       is_own_module=False) == 0
    assert _references("zz_widget", '"""zz_widget is dead."""\nx = 1\n',
                       is_own_module=False) == 0


def test_the_detector_finds_a_real_reference():
    """The control. A detector that never finds anything would pass every
    assertion above while reporting the whole codebase dead."""
    text = "from somewhere import zz_widget\nzz_widget()\n"
    assert _references("zz_widget", text, is_own_module=False) == 2


def test_the_baseline_is_small_enough_to_be_read():
    """A guard on the guard. If this list ever grows to dozens, the ratchet has
    stopped being a record of known exceptions and become a second codebase
    nobody reads — the failure mode of every allowlist in this repo."""
    entries = _baseline()
    assert len(entries) <= 12, (
        f"{len(entries)} baselined dead exports. Past a dozen this is not a "
        "list of exceptions any more; delete some or wire them up.")

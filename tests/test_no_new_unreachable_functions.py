"""The module ratchet cannot see a dead FUNCTION inside a live module.

`tests/test_no_new_unreachable_modules.py` catches a whole module nobody
imports. It is module-granular by design, and that leaves a gap one level down:

    bot/formatters/rich_cards.py is imported by THREE production modules —
    scan_skill, skill_registry, telegram_handler — so the module ratchet passes
    it. Inside it, render_pnl_report, render_pending_orders and
    render_multi_analysis have no caller anywhere in non-test source.

That is #999 one level in: present, correct, tested, unreachable. And it is
where a real defect was found during the 2026-08-27 audit — render_pnl_report
does `pnl = trade.get("pnl", 0)` and then `session_pnl += net_pnl`, which is
both "absent field is zero" and "a partial total printed as whole" from
CLAUDE.md's table. A defect that cannot hurt anyone BECAUSE nothing calls it is
exactly the ambiguity the module ratchet exists to remove.

Five of the findings sit in safety controls, which is the uncomfortable half:

    bot/learning/safety_policy.py:170  audit_proposal()          0 callers, 0 tests
    bot/learning/safety_policy.py:154  validate_learning_action() 0 callers
    bot/guardian/firewall.py:167       defang()                   0 callers
    bot/guardian/flight_recorder.py:509 verify_entries()          0 callers
    bot/token/tier_gate.py:866         allows_user()              0 callers

`audit_proposal`'s own docstring SAID "Always called before apply." It is called
by nothing and tested by nothing — a guarantee asserted in prose and nowhere
else. That sentence has been corrected, so this paragraph is now the historical
record rather than a description of the file: the reachability finding stands,
the false claim about it does not. `validate_learning_action` fails closed
correctly on unknown actions, and protects nothing, because no caller reaches
it.

Correcting the prose is NOT the fix and must not be mistaken for one. Both
functions are still unreachable and `bot/learning` still has no safety control
anything can reach; all that changed is that the module no longer tells a
reader otherwise.

RATCHET, NOT BAN — for the same reasons the module one is

Some of these are unbuilt features rather than rot, and deciding each is a
judgement call. So the known set is recorded in
`tests/unreachable_functions_baseline.txt` and only DRIFT fails, both
directions: a new entry means somebody just wrote another function nobody
calls; an entry that leaves must be deleted in the same commit, exactly as
`known_failures.txt` works.

THE BLIND SPOT THIS DETECTOR HAD TO AVOID

The first pass reported 27 dead functions and five of them were FastAPI route
handlers — `lab_run`, `api_unlink` and friends, reachable through
`@router.post(...)` with nothing referencing the name. A checker that reports
those manufactures exactly the accusation it exists to prevent, which is the
warning the module checker's docstring already carries. Decorated functions are
therefore excluded outright: a decorator is a registration, and this detector
cannot know what a given registry does with it.

It is also a LOWER BOUND, deliberately: a name defined in more than one module
is skipped rather than guessed at.
"""
from __future__ import annotations

import ast
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).parent / "unreachable_functions_baseline.txt"

SKIP_DIRS = {"__pycache__", "node_modules", ".git", "venv", ".venv", "target",
             "build", "dist", ".pytest_cache", "site-packages"}

#: Only these trees are CANDIDATES — the same split the module ratchet uses.
CANDIDATE_ROOTS = ("bot",)

#: Every tree that can CALL one. Root-level modules are included because
#: api_bridge.py mounts bot/api/auth_routes.py from there, and the module
#: checker's first version declared it dead for not reading the repo root.
IMPORTER_ROOTS = ("bot", "scripts")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _py_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO)).replace(os.sep, "/")


def _candidate_defs() -> dict:
    """name -> (relpath, lineno) for undecorated public module-level defs."""
    seen = defaultdict(list)
    for root in CANDIDATE_ROOTS:
        for path in _py_files(REPO / root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in tree.body:            # module level only, never methods
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                if node.decorator_list:
                    # A decorator is a registration. @router.post, @app.get,
                    # @lru_cache, @property on a factory — this detector cannot
                    # know what a registry does with the name, and guessing is
                    # how five FastAPI handlers got falsely accused.
                    continue
                seen[node.name].append((_rel(path), node.lineno))
    # A name defined twice is ambiguous; skipping is the lower-bound choice.
    return {n: sites[0] for n, sites in seen.items() if len(sites) == 1}


def _code_only(text: str) -> str:
    """Source with comments and docstrings stripped.

    WITHOUT THIS, PROSE SILENCES THE RATCHET.

    This function counted identifiers in RAW file text, so any mention of a
    name in a comment or docstring read as a call. Writing

        ``validate_learning_action`` — together the entire safety surface

    into a docstring took that function from 1 occurrence (its own ``def``) to
    2, and the gate promptly reported it as no longer unreachable. A dead
    safety control declared alive by a sentence describing it as dead.

    That direction is the dangerous one. The module ratchet's own note records
    a checker whose blind spot "manufactures exactly the accusation it exists
    to prevent"; this was the inverse — manufactured innocence — and it is
    worse, because a false accusation gets argued with and a false all-clear
    gets filed. Anyone documenting one of these functions would silently
    retire the gate that watches it.

    Copied from `tests/test_preflight_matches_ci.py`, which CLAUDE.md names as
    the version worth copying, after this became the fifth time the trap has
    been hit here.
    """
    import io
    import tokenize
    out, prev_type = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev_type in (
                    None, tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                    tokenize.DEDENT):
                continue                      # a docstring, not a value
            out.append(tok.string)
            prev_type = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable source: fall back to raw text. That direction over-counts
        # (prose may read as a call), which leaves a dead function looking
        # alive — but the alternative, returning nothing, would accuse every
        # function in the file at once.
        return text
    return " ".join(out)


def _production_identifier_counts() -> Counter:
    counts: Counter = Counter()
    files = set()
    for root in IMPORTER_ROOTS:
        files.update(_py_files(REPO / root))
    files.update(p for p in REPO.iterdir() if p.suffix == ".py")
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        counts.update(_IDENT.findall(_code_only(raw)))
    return counts


def unreachable_functions() -> set:
    """Public module-level functions whose only mention is their own `def`."""
    defs = _candidate_defs()
    counts = _production_identifier_counts()
    return {f"{rel}:{name}" for name, (rel, _ln) in defs.items()
            if counts[name] <= 1}


def _baseline() -> set:
    if not BASELINE.exists():
        return set()
    return {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def test_no_new_unreachable_functions():
    """A NEW entry means somebody just wrote a function nobody calls."""
    new = sorted(unreachable_functions() - _baseline())
    assert not new, (
        "these public functions have no caller anywhere outside tests:\n  "
        + "\n  ".join(new)
        + "\n\nThe module ratchet cannot see them — their modules ARE imported. "
          "Wire each one up, delete it, or (if it is a deliberate unbuilt "
          "feature) add it to tests/unreachable_functions_baseline.txt with a "
          "line saying why.")


def test_the_baseline_has_no_stale_entries():
    """An entry that LEAVES must be deleted in the same commit.

    A stale entry is how a list stops meaning anything; known_failures.txt
    fails the same way for the same reason.
    """
    gone = sorted(_baseline() - unreachable_functions())
    assert not gone, (
        "these are no longer unreachable — they were wired up, renamed or "
        "deleted — but are still baselined:\n  " + "\n  ".join(gone)
        + "\n\nRemove them from tests/unreachable_functions_baseline.txt.")


def test_decorated_route_handlers_are_not_accused():
    """The blind spot that would make this checker worse than nothing.

    bot/api/lab.py's handlers are reachable through `@lab_router.get(...)` and
    nothing references their names. An earlier pass reported five of them as
    dead code. A reachability checker with a blind spot manufactures exactly
    the accusation it exists to prevent.
    """
    found = unreachable_functions()
    for handler in ("bot/api/lab.py:lab_run", "bot/api/lab.py:lab_status",
                    "bot/api/lab.py:lab_meta",
                    "bot/api/auth_routes.py:api_unlink",
                    "bot/api/auth_routes.py:get_link_token"):
        assert handler not in found, (
            f"{handler} is a decorated route handler, reachable through its "
            f"router. Reporting it as dead is the false accusation this "
            f"detector must not make.")


def test_the_detector_finds_a_planted_dead_function(tmp_path, monkeypatch):
    """Drive it, rather than trusting that it works.

    A detector that silently found nothing would make both ratchets above pass
    forever, which is the same green-and-blind failure this whole file is
    about.
    """
    pkg = tmp_path / "bot"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "planted.py").write_text(
        "def a_function_nobody_calls():\n    return 1\n\n"
        "def a_function_that_is_called():\n    return 2\n", encoding="utf-8")
    (pkg / "caller.py").write_text(
        "from bot.planted import a_function_that_is_called\n"
        "a_function_that_is_called()\n", encoding="utf-8")

    import sys
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "REPO", tmp_path)
    found = mod.unreachable_functions()
    assert "bot/planted.py:a_function_nobody_calls" in found, (
        f"the detector missed a planted dead function; it found {found}")
    assert "bot/planted.py:a_function_that_is_called" not in found, (
        "the detector reported a function that IS called")


# ══════════════════════════════════════════════════════════════════════════
# METHODS — the same gap, one level further in
# ══════════════════════════════════════════════════════════════════════════
#
# The function ratchet above walks `tree.body` and so sees module-level defs
# only. `LearningOrchestrator.process_proposals` fell straight through both
# checks: bot/learning/orchestrator.py IS imported (module ratchet passes) and
# the method is not a module-level def (function ratchet never looks). It was a
# public method on a class the engine constructs on every run, with no caller,
# whose four surfaces claimed proposals had been "applied" — found by hand in
# the 2026-08-27 audit, not by any gate.
#
# WHY A CLASS WITH A BASE IS SKIPPED ENTIRELY
#
# `AuthIn.model_post_init` in bot/api/auth_routes.py has zero Python callers
# and runs on every unauthenticated POST to /auth/register and /auth/login: it
# is pydantic v2's documented BaseModel lifecycle hook, invoked by
# pydantic-core (compiled Rust) after each successful validation. There is no
# identifier anywhere in the tree for an identifier-counting detector to find,
# so it is not merely missed — it is structurally invisible, and the detector
# would confidently report a live auth guard as dead.
#
# That is the failure this repo already has on record: the module checker's own
# note says "a reachability checker with a blind spot manufactures exactly the
# accusation it exists to prevent" — the `.experience` relative-import bug that
# condemned ten bot/learning modules at once. Believing this one would mean
# deleting the only length cap on `password` before it reaches the hash path.
#
# So an override cannot be told from dead code by counting names, and the
# honest move is to decline the whole class. It costs almost nothing: of the 76
# methods this sweep first surfaced, exactly ONE sat on a subclass. Declining a
# whole category to avoid one false accusation is the same trade the function
# ratchet already makes for decorated defs and duplicated names.

METHOD_BASELINE = Path(__file__).parent / "unreachable_methods_baseline.txt"


def _candidate_methods() -> dict:
    """name -> (relpath, class, lineno) for unambiguous public methods.

    Conservative on every axis, and each exclusion is a LOWER BOUND rather
    than a guess:

    * a leading underscore is already private;
    * a decorator is a registration this detector cannot follow;
    * a class with ANY base may be overriding a framework contract (see above);
    * a name defined more than once — as a method OR as a module-level
      function — is ambiguous, and the identifier count cannot tell which
      definition a call site meant.
    """
    methods = defaultdict(list)
    module_level = set()
    for root in CANDIDATE_ROOTS:
        for path in _py_files(REPO / root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module_level.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    if node.bases or node.keywords:
                        continue          # possible override — decline the class
                    for sub in node.body:
                        if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        if sub.name.startswith("_") or sub.decorator_list:
                            continue
                        methods[sub.name].append(
                            (_rel(path), node.name, sub.lineno))
    return {n: sites[0] for n, sites in methods.items()
            if len(sites) == 1 and n not in module_level}


def unreachable_methods() -> set:
    """Public methods whose only mention in production code is their own def."""
    counts = _production_identifier_counts()
    return {f"{rel}:{cls}.{name}"
            for name, (rel, cls, _ln) in _candidate_methods().items()
            if counts[name] <= 1}


def _method_baseline() -> set:
    if not METHOD_BASELINE.exists():
        return set()
    return {ln.strip() for ln in
            METHOD_BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def test_no_new_unreachable_methods():
    """A NEW entry means somebody just wrote a method nobody calls."""
    new = sorted(unreachable_methods() - _method_baseline())
    assert not new, (
        "these public methods have no caller anywhere outside tests:\n  "
        + "\n  ".join(new)
        + "\n\nNeither the module ratchet nor the function ratchet can see "
          "them — the module IS imported and the method is not a module-level "
          "def. Wire each one up, delete it, or add it to "
          "tests/unreachable_methods_baseline.txt with a line saying why.")


def test_the_method_baseline_has_no_stale_entries():
    """An entry that LEAVES must be deleted in the same commit."""
    gone = sorted(_method_baseline() - unreachable_methods())
    assert not gone, (
        "these are no longer unreachable — wired up, renamed or deleted — but "
        "are still baselined:\n  " + "\n  ".join(gone)
        + "\n\nRemove them from tests/unreachable_methods_baseline.txt.")


def test_a_framework_override_is_never_reported_dead(tmp_path, monkeypatch):
    """The blind spot must not come back.

    `AuthIn.model_post_init` is the live case: a pydantic hook with zero Python
    callers, running on every unauthenticated login. Planted here rather than
    asserted against the real file so the guard survives that file being
    renamed — and so it fails for the RIGHT reason if the base-class exclusion
    is ever removed.

    In the spirit of test_relative_imports_count_as_reachability, which pins
    the module checker's equivalent repaired blind spot.
    """
    pkg = tmp_path / "bot"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "planted.py").write_text(
        "class Hooked(BaseModel):\n"
        "    def model_post_init(self, _ctx):\n"
        "        return None\n\n"
        "class Plain:\n"
        "    def a_method_nobody_calls(self):\n"
        "        return 1\n\n"
        "    def a_method_that_is_called(self):\n"
        "        return 2\n", encoding="utf-8")
    (pkg / "caller.py").write_text(
        "from bot.planted import Plain\n"
        "Plain().a_method_that_is_called()\n", encoding="utf-8")

    import sys
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "REPO", tmp_path)
    found = mod.unreachable_methods()
    assert "bot/planted.py:Hooked.model_post_init" not in found, (
        "a framework override on a subclass was reported dead — this is the "
        "blind spot that would condemn a running auth guard")
    assert "bot/planted.py:Plain.a_method_nobody_calls" in found, (
        f"the detector missed a planted dead method; it found {found}")
    assert "bot/planted.py:Plain.a_method_that_is_called" not in found, (
        "the detector reported a method that IS called")

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

`audit_proposal`'s own docstring says "Always called before apply." It is called
by nothing and tested by nothing — a guarantee asserted in prose and nowhere
else. `validate_learning_action` fails closed correctly on unknown actions, and
protects nothing, because no caller reaches it.

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


def _production_identifier_counts() -> Counter:
    counts: Counter = Counter()
    files = set()
    for root in IMPORTER_ROOTS:
        files.update(_py_files(REPO / root))
    files.update(p for p in REPO.iterdir() if p.suffix == ".py")
    for path in files:
        counts.update(_IDENT.findall(path.read_text(encoding="utf-8", errors="replace")))
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

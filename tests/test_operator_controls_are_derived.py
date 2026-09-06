"""`OPERATOR_CONTROL_PERMISSIONS` is a hand-written list. Lists go stale.

The list names `halt`, `reset` and `mode` as the permissions whose commands
mutate state shared by every account, and `SELF_ADMISSION_ROLE` is defined as
holding none of them. Both halves are only worth anything if the list is
CORRECT — and nothing about a hand-written constant stays correct across a
release. The command that reopens this hole will not be `/reset`. It will be a
command that does not exist yet, whose permission somebody adds to the paper
role because it looked user-facing.

So the list is re-derived here, from what the handlers actually call, and the
constant is checked against the derivation rather than trusted.

WHAT COUNTS AS SHARED STATE, and why these three markers:

    engine.risk.emergency_halt(...)         trips the breaker for everyone
    engine.risk.reset_circuit_breaker()     clears it for everyone
    engine.reset_circuit_breaker_all()      "the shared engine AND every
                                            per-user RiskEngine" — its docstring
    RUNTIME.<x> = ...                       process-wide config every account
                                            scans against

Two hops are needed, because the interesting handler bodies contain no marker
at all. `_cmd_halt` is three lines and dispatches to `HaltSkill`, which is where
`emergency_halt` lives. So the scan runs over `skill_registry.py` first to find
which SKILLS are global, then treats `registry.dispatch("<that skill>")` inside
a guarded handler as a marker in its own right.

WHAT IS DELIBERATELY NOT A MARKER: writes to `engine._pending_ideas`. They look
exactly like the others and they are not the same thing — `analyze_asset` and
`pro_scan` write there too, and `scan` is in the VIEWER set, so the idea book is
already shared by everyone who can read the bot. Adding it would have pulled
`run`, `analyze` and `scan` into the operator set and bought no safety. Checking
reachability before fixing is the difference between a security boundary and a
refactor.

WHY A SOURCE SCAN HERE. This file does not test behaviour; the behaviour is
covered by `test_self_admission_is_not_vouched.py`, which drives the real
commands against a planted tripped breaker. What it locks is a property no unit
test can reach: that a permission held by the self-admission role has no path to
shared state ANYWHERE in the handler module, including through commands that do
not exist yet. That is the narrow case the repo's own guidance keeps for source
scans — a shape, not a substitute for a behavioural test.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from bot.utils.user_store import (OPERATOR_CONTROL_PERMISSIONS, ROLE_PERMISSIONS,
                                  SELF_ADMISSION_ROLE)
from tests.source_scan import handler_sources

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = REPO / "bot" / "skills" / "skill_registry.py"


def _handler_trees() -> list[ast.Module]:
    """One parsed tree per file that contributes methods to the handler class.

    The handler is being split into mixins, and this derivation reads
    decorators. Read from one file it would stop seeing a guarded command
    the moment the command moved — and a permission that reaches shared
    state from inside a mixin is exactly the one this file exists to find.
    """
    return [ast.parse(p.read_text(encoding="utf-8")) for p in handler_sources()]

# Method names that mutate state shared by every account.
_MUTATORS = {"emergency_halt", "reset_circuit_breaker", "reset_circuit_breaker_all"}


def _touches_shared_state(node: ast.AST) -> bool:
    """Any call to a _MUTATORS method, or any assignment to a RUNTIME attribute."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in _MUTATORS:
                return True
        if isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for tgt in targets:
                if (isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "RUNTIME"):
                    return True
    return False


def _dispatched_skills(node: ast.AST) -> set[str]:
    """Skill names passed as the first literal argument to `.dispatch(...)`."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "dispatch" and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)):
            out.add(sub.args[0].value)
    return out


def _global_skills() -> set[str]:
    """Registry skills whose execute() reaches shared state."""
    tree = ast.parse(SKILLS.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        name = None
        for stmt in node.body:
            if (isinstance(stmt, ast.Assign)
                    and any(getattr(t, "id", "") == "name" for t in stmt.targets)
                    and isinstance(stmt.value, ast.Constant)):
                name = stmt.value.value
        if name and _touches_shared_state(node):
            out.add(name)
    return out


def _guard_permission(node: ast.AST) -> str | None:
    for dec in getattr(node, "decorator_list", []):
        if (isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "guard"
                and dec.args and isinstance(dec.args[0], ast.Constant)):
            return dec.args[0].value
    return None


def _gates_on_admin(node: ast.AST) -> bool:
    """An inline `_is_admin` check makes the handler operator-only regardless of
    which role holds its permission — the pattern /settier and /llmreset use."""
    return any(isinstance(sub, ast.Attribute) and sub.attr == "_is_admin"
               for sub in ast.walk(node))


def _derived_operator_permissions() -> dict[str, set[str]]:
    """{permission: {handler names that give it away}}."""
    globals_ = _global_skills()
    found: dict[str, set[str]] = {}
    trees = _handler_trees()
    for node in (n for tree in trees for n in ast.walk(tree)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        perm = _guard_permission(node)
        if not perm or _gates_on_admin(node):
            continue
        if _touches_shared_state(node) or (_dispatched_skills(node) & globals_):
            found.setdefault(perm, set()).add(node.name)

    # The destructive-callback map is a DECLARATION of the same fact, written by
    # the F-11 fix: these permissions gate inline buttons that pause,
    # emergency-stop or close everything. Reading it keeps the two in step
    # instead of asking a maintainer to remember both.
    for node in (n for tree in trees for n in ast.walk(tree)):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "_DESTRUCTIVE_CB_PERM"
                        for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    label = k.value if isinstance(k, ast.Constant) else "callback"
                    found.setdefault(v.value, set()).add(f"callback:{label}")
    return found


# ── the derivation must work before it can guard anything ────────────

def test_the_scanner_finds_the_global_skills():
    """Vacuous-pass check. A scanner that found nothing would make every
    assertion below trivially true."""
    skills = _global_skills()
    assert "halt" in skills, (
        f"HaltSkill calls engine.risk.emergency_halt() and the scan missed it; "
        f"found {sorted(skills)}")


def test_the_scanner_finds_guarded_handlers():
    n = sum(1 for tree in _handler_trees() for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _guard_permission(node))
    assert n > 50, f"only {n} @guard-decorated handlers found — extractor broken"


def test_the_scanner_reads_every_file_the_handler_is_made_of():
    """Vacuous-pass check for the split: the Guardian group moved into a
    mixin, and a derivation that read one file would still report the three
    known controls and pass — while seeing nothing of the moved commands."""
    trees = _handler_trees()
    assert len(trees) >= 2, "no mixin file found — the split's slices are not being read"
    defs = {node.name for tree in trees for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"_cmd_policy", "_cmd_guardian", "_cmd_halt"} <= defs


def test_the_derivation_finds_the_three_known_controls():
    """The floor, not the ceiling. If the derivation ever stops seeing these,
    it has been broken and the guard below is passing on nothing."""
    derived = _derived_operator_permissions()
    for perm in ("halt", "reset", "mode"):
        assert perm in derived, (
            f"{perm!r} no longer derives as an operator control — the scanner "
            f"broke, or the command moved. Found: {sorted(derived)}")


# ── the guard ────────────────────────────────────────────────────────

def test_the_self_admission_role_holds_no_derived_operator_control():
    """THE assertion. A stranger who let themselves in must have no path to
    state shared by every account — including through a command written after
    this test was."""
    derived = _derived_operator_permissions()
    held = ROLE_PERMISSIONS[SELF_ADMISSION_ROLE] & set(derived)
    assert not held, (
        f"role {SELF_ADMISSION_ROLE!r} — which anyone who messages the bot gets "
        f"for free — holds permissions that reach shared state:\n  "
        + "\n  ".join(f"{p!r} via {sorted(derived[p])}" for p in sorted(held))
        + "\n\nEither the command should be caller-scoped, or its permission "
          "belongs in OPERATOR_CONTROL_PERMISSIONS and out of the paper role.")


def test_the_declared_constant_covers_what_is_derived():
    """The constant is documentation that other code reads. Documentation that
    disagrees with the code is worse than none — `_WEB_SKILL_PERMISSION` omits
    `halt` on the strength of a comment about which roles hold it.

    Scoped to permissions a NON-ADMIN role actually holds. The derivation also
    surfaces `admin` (/golive and /autoconfirm assign to RUNTIME), which is
    true and uninteresting: only the admin role holds `admin`, via `"*"`, so it
    is operator-only by construction and declaring it would say nothing. What
    the constant exists to describe is the delta between a vouched-for role and
    a self-admitted one.
    """
    derived = set(_derived_operator_permissions())
    non_admin_held = {p for p, perms in ROLE_PERMISSIONS.items() if "*" not in perms
                      for p in perms}
    missing = (derived & non_admin_held) - set(OPERATOR_CONTROL_PERMISSIONS)
    assert not missing, (
        f"these permissions reach shared state, are held by a non-admin role, "
        f"and are not declared in OPERATOR_CONTROL_PERMISSIONS: {sorted(missing)}")


def test_every_declared_control_is_one_the_derivation_agrees_with():
    """The other direction: a constant that lists a permission the code does not
    back is a claim nobody checked. Both directions, because an over-broad list
    is how a real feature gets withheld from users for no reason and nobody can
    say why."""
    derived = set(_derived_operator_permissions())
    invented = set(OPERATOR_CONTROL_PERMISSIONS) - derived
    assert not invented, (
        f"OPERATOR_CONTROL_PERMISSIONS names {sorted(invented)}, which no "
        f"@guard-ed handler backs with a path to shared state")


@pytest.mark.parametrize("role", ["pending", "viewer"])
def test_the_weaker_roles_hold_none_either(role):
    derived = set(_derived_operator_permissions())
    assert not (ROLE_PERMISSIONS[role] & derived), (
        f"{role!r} holds {sorted(ROLE_PERMISSIONS[role] & derived)}")

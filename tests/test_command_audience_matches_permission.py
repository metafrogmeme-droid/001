"""What the catalog SAYS a command is, and what the code ENFORCES, must agree.

THE FOURTH INSTANCE

Three times this audit found a correct guard that some call site never received
(assertDevnet, assertKeyfilePermissions, _token_gate_blocks). This is a fourth
variant of the same family, and it is the one a call-site linter cannot see: the
guard IS called at every site. It just does not enforce what the documentation
claims.

`command_catalog.py` labels each group "user" or "admin", and its own docstring
says an admin command is one normal users "cannot run anyway". Enforcement lives
somewhere else entirely — in `@guard('<permission>')` plus the role→permission
sets in `user_store.ROLE_PERMISSIONS`. Nothing connected the two, so a command
could be documented as operator-only while its permission string sat in the
viewer role's set.

Three did:

    /livebalance   @guard('portfolio')  reachable by trader, viewer
    /llmstatus     @guard('status')     reachable by trader, viewer
    /llmtiers      @guard('status')     reachable by trader, viewer

WHY BOTH READINGS WERE WRONG IN DIFFERENT DIRECTIONS

That is the interesting part, and why this needs a test rather than a judgement
call each time. The mismatch does not have one fix:

* `/livebalance` is DELIBERATELY user-facing — its own comment says it routes to
  the caller's own account and is read-only. The CATALOG was wrong, and the cost
  was the one the catalog's docstring itself names: the command was hidden from
  /help for the very users who could run it, which "is what makes commands feel
  broken".

* `/llmstatus` and `/llmtiers` print operator infrastructure — provider, model,
  base_url, per-tier key fingerprints. The PERMISSION was wrong. (The
  fingerprints are non-reversible, six characters plus a sha256 prefix, so this
  was configuration disclosure to a semi-trusted role, not a key leak.)

Severity is bounded by `_guard`'s outer allowlist, which admits only
TELEGRAM_CHAT_ID / ADMIN_TELEGRAM_IDS, so "any user" here means "any allowlisted
non-admin", not the public. Worth fixing, not worth alarm.
"""

import ast
import json
import pathlib
import re

import pytest

from bot.skills.command_catalog import GROUPS
from bot.utils.user_store import ROLE_PERMISSIONS

HANDLER = pathlib.Path(__file__).resolve().parent.parent / "bot" / "skills" / "telegram_handler.py"


def _handler_index():
    src = HANDLER.read_text()
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    registry = dict(re.findall(r'\(\s*["\'](\w+)["\']\s*,\s*self\.(_\w+)\s*\)', src))
    return src, funcs, registry


# An early-return admin gate, not a mere mention. `/analyze` does
# `admin = self._is_admin(update)` to vary its OUTPUT, and treating that as a
# gate marks three commands "guarded" that are not — a false negative in the
# security-relevant direction, which is the one that matters.
_ADMIN_GATE = re.compile(r"if\s+not\s+self\._is_admin\(update\)\s*:")


def _gates_on_admin(body: str) -> bool:
    return bool(_ADMIN_GATE.search(body))


def _permission_string(src, node):
    """The permission `_guard` is called with, from the decorator or inline."""
    for d in node.decorator_list:
        m = re.match(r"guard\('([^']*)'\)", ast.unparse(d))
        if m:
            return m.group(1)
    m = re.search(r'_guard\(update,\s*["\'](\w+)["\']', ast.get_source_segment(src, node) or "")
    return m.group(1) if m else None


def test_operator_only_commands_are_actually_operator_only():
    """A command the catalog calls "operator" must be unreachable by any
    non-admin role — either by checking _is_admin, or by gating on a permission
    no non-admin role holds."""
    src, funcs, registry = _handler_index()
    admin_cmds = sorted({c for _t, aud, entries in GROUPS if aud == "admin" for c, _d in entries})
    assert len(admin_cmds) > 20, f"only {len(admin_cmds)} admin commands found — did the catalog move?"

    non_admin = {r: p for r, p in ROLE_PERMISSIONS.items() if "*" not in p}
    assert non_admin, "every role has '*' — this test would pass vacuously"

    leaks = []
    for cmd in admin_cmds:
        handler = registry.get(cmd)
        assert handler, f"/{cmd} is in the catalog but bound to no handler"
        node = funcs.get(handler)
        assert node, f"{handler} not found in the handler module"
        body = ast.get_source_segment(src, node) or ""
        if _gates_on_admin(body):
            continue  # explicitly operator-gated in the body
        perm = _permission_string(src, node)
        reachable = sorted(r for r, p in non_admin.items() if perm in p)
        if reachable:
            leaks.append(f"/{cmd} (@guard({perm!r})) is documented operator-only "
                         f"but reachable by: {', '.join(reachable)}")

    assert not leaks, (
        "catalog says operator-only, enforcement disagrees:\n  " + "\n  ".join(leaks) +
        "\n\nFix ONE of the two — either add an _is_admin check (the docs were right) "
        "or move it to a user group (the code was right). They cannot both stand."
    )


def test_no_NEW_user_facing_command_becomes_unreachable():
    """The mirror direction, as a ratchet.

    A command in a "user" group that no non-admin role can reach is hidden from
    the people it was written for. The catalog's own docstring names this: a
    command users cannot run "is what makes commands feel broken".

    Sixteen already exist. Each is either a catalog error (it really is
    operator-only) or a permission error (users should be able to run it), the
    fix differs per command, and guessing would be worse than recording — so
    they are baselined and only NEW drift fails. Same ratchet the Rust and npm
    advisory gates use.

    /lang was the one unambiguous case and is fixed rather than recorded: it is
    documented under "Start here", it sets a display preference and nothing
    else, and a person who cannot read the interface cannot ask for permission
    to change its language.
    """
    baseline = json.loads((pathlib.Path(__file__).resolve().parent / "fixtures"
                           / "command_audience_backlog.json").read_text())
    known = set(baseline["known"])

    src, funcs, registry = _handler_index()
    non_admin = {r: p for r, p in ROLE_PERMISSIONS.items() if "*" not in p}
    unreachable = set()
    for cmd in sorted({c for _t, aud, e in GROUPS if aud == "user" for c, _d in e}):
        handler = registry.get(cmd)
        if not handler or handler not in funcs:
            continue
        body = ast.get_source_segment(src, funcs[handler]) or ""
        perm = _permission_string(src, funcs[handler])
        if _gates_on_admin(body) or (perm and not any(perm in p for p in non_admin.values())):
            unreachable.add(cmd)

    new = sorted(unreachable - known)
    assert not new, (
        "these are documented for users but no non-admin role can run them:\n  "
        + "\n  ".join("/" + c for c in new)
        + "\n\nEither move it to an admin group or grant the permission."
    )
    fixed = sorted(known - unreachable)
    assert not fixed, (
        f"{fixed} no longer mismatch — remove them from "
        "tests/fixtures/command_audience_backlog.json so the ratchet keeps tightening"
    )


def test_llm_config_commands_require_admin():
    """Pinned specifically: these print provider, model, base_url and per-tier
    key fingerprints. `@guard('status')` alone put that in the viewer role's set."""
    src, funcs, _ = _handler_index()
    for handler in ("_cmd_llmstatus", "_cmd_llmtiers"):
        body = ast.get_source_segment(src, funcs[handler]) or ""
        assert "_is_admin" in body, f"{handler} no longer checks _is_admin"


def test_the_permission_extractor_actually_finds_permissions():
    """Guards the guard. If `_permission_string` silently returned None for
    everything, both tests above would pass while checking nothing — the same
    vacuous-pass shape this audit keeps finding.
    """
    src, funcs, registry = _handler_index()
    found = sum(1 for h in registry.values()
                if h in funcs and _permission_string(src, funcs[h]))
    assert found > 50, f"only {found} handlers yielded a permission string — the extractor is broken"


def test_lang_is_reachable_by_every_role():
    """`/lang` specifically, for every non-admin role — not just "some" role.

    Added because a mutation revealed the gap: revoking `lang` from `viewer`
    alone passed the ratchet above, since `trader` still held it and that test
    only asks whether SOME non-admin can reach a command. That is the right
    question for the general case and the wrong one here, where the claim made
    was that a person who cannot read the interface can always change its
    language. A claim about every role needs a test about every role.
    """
    for role, perms in ROLE_PERMISSIONS.items():
        assert "*" in perms or "lang" in perms, (
            f"role {role!r} cannot run /lang — it sets a display preference and "
            "nothing else, and is documented under \"Start here\" for all users"
        )

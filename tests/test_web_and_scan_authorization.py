"""Two paths that reached privileged capability with no authorisation at all.

Both came out of a 67-agent adversarial sweep and were re-verified by hand.

1. `scan_confirm:` — tap-to-trade on a scan card called `engine.confirm_trade`
   with NO live-trading check. Grepping `bot/skills/scan_skill.py` for
   `_can_trade_live|_is_admin|has_permission|_guard` returned nothing, while its
   three sibling confirm paths all carry the H-18 block. A user in
   LIVE_TRADER_TELEGRAM_IDS who had never been granted per-user live could tap ✅
   and place a real order on the SHARED OPERATOR account — while the same person
   tapping `confirm:` on an /analyze card was refused.

2. Web chat executed ANY registry skill by name. `_guard_user` is called without
   a `command`, so its role branch (`if command and not ...has_permission`) was
   dead code, and web ids deliberately skip the Telegram allowlist. A stranger
   who signed up on the website was auto-provisioned with DEFAULT_AUTO_ROLE
   ("trader") and could POST "halt the bot" to /api/chat, tripping the GLOBAL
   circuit breaker. Separately, the $RCLAW tier gate was absent from that path
   entirely, so every paid feature was free through chat.

WHY `halt` IS NOT SIMPLY PERMISSION-CHECKED

Adding the role check alone does not close it: DEFAULT_AUTO_ROLE holds the
`halt` permission, so a self-provisioned web account would still stop trading
for everybody. `HaltSkill` is global — shared circuit breaker, every per-user
risk engine, all pending ideas. The codebase had already drawn this line
elsewhere: `POST /api/controls/stop` refuses a merely SELF-SCOPED stop from a
web identity with 409 `telegram_required`. A global halt over chat cannot be
laxer than that, so `halt` is absent from the web-reachable set entirely.
"""

import ast
import pathlib
import re

import pytest

from bot.utils.user_store import DEFAULT_AUTO_ROLE, ROLE_PERMISSIONS

REPO = pathlib.Path(__file__).resolve().parent.parent
HANDLER = REPO / "bot" / "skills" / "telegram_handler.py"


class _Users:
    def __init__(self, role):
        self.role = role

    def get(self, uid):
        return {"role": self.role, "authorized": True}

    def has_permission(self, uid, cmd):
        perms = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in perms or cmd in perms


class _Handler:
    def __init__(self, role):
        self.users = _Users(role)


@pytest.fixture
def gateway():
    return pytest.importorskip("bot.web.user_gateway",
                               reason="web gateway needs aiohttp")


def test_no_web_role_can_halt_the_shared_engine(gateway):
    """The reported scenario, for every role including admin.

    A role check alone is insufficient — DEFAULT_AUTO_ROLE holds `halt`.
    """
    assert "halt" in ROLE_PERMISSIONS.get(DEFAULT_AUTO_ROLE, set()), (
        "the premise changed: the auto-provisioned role no longer holds 'halt', so "
        "this test is no longer testing what it was written for")
    for role in ("trader", "viewer", "admin", "pending"):
        denied = gateway._web_skill_denied(_Handler(role), "web:stranger", "halt")
        assert denied is not None, f"role {role} can halt the shared engine from web chat"
        assert denied.status == 403


def test_an_unmapped_skill_is_denied_not_allowed(gateway):
    """Fail closed. A skill added later must be unreachable from web chat until
    somebody decides what it needs — the alternative is the original defect
    arriving again, silently."""
    denied = gateway._web_skill_denied(_Handler("admin"), "web:x", "some_new_skill")
    assert denied is not None and denied.status == 403


def test_web_role_permissions_match_telegram(gateway):
    """A viewer cannot run what a viewer cannot run on Telegram."""
    assert gateway._web_skill_denied(_Handler("viewer"), "web:x", "run_strategy") is not None
    assert gateway._web_skill_denied(_Handler("trader"), "web:x", "run_strategy") is None
    # ...and a permission both roles hold stays open.
    assert gateway._web_skill_denied(_Handler("viewer"), "web:x", "scan_market") is None


def test_the_web_dispatch_actually_calls_the_check(gateway):
    """The helper existing is worthless if the dispatch site does not call it.

    This is the exact failure mode the sweep was hunting, so it would be absurd
    not to pin it for the fix.
    """
    src = (REPO / "bot" / "web" / "user_gateway.py").read_text()
    i = src.index("skill = tg_handler.registry.get(skill_name)")
    j = src.index("await skill.execute(", i)
    assert "_web_skill_denied(" in src[i:j], (
        "the web chat dispatch reaches skill.execute without calling _web_skill_denied")


def test_the_web_path_consults_the_tier_gate(gateway):
    """$RCLAW paid features were free through web chat."""
    src = (REPO / "bot" / "web" / "user_gateway.py").read_text()
    fn = src[src.index("def _web_skill_denied"):src.index("def _guard_user")]
    assert "tier_gate" in fn and "check_user" in fn


def test_scan_confirm_checks_live_permission():
    """The tap-to-trade path now carries the H-18 block its siblings have."""
    src = (REPO / "bot" / "skills" / "scan_skill.py").read_text()
    fn = None
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node) or ""
            if "engine.confirm_trade(" in seg:
                fn = seg
                break
    assert fn, "no function in scan_skill dispatches confirm_trade — did it move?"
    assert "_can_trade_live" in fn, "scan_confirm reaches confirm_trade with no live check"
    # ...and refuses when the check itself is unreachable, rather than proceeding.
    assert fn.index("is_live()") < fn.index("engine.confirm_trade("), (
        "the live check must run BEFORE confirm_trade, not after")
    assert "_h is None" in fn, (
        "scan_confirm no longer fails closed when the telegram handler — and so the "
        "permission check — is unavailable")


# ── The web map must not be laxer than Telegram ───────────────────────────
#
# `_WEB_SKILL_PERMISSION` is a hand-written copy of what the Telegram side
# enforces via `@guard(...)`. Copies drift, and this one drifts SILENTLY in the
# dangerous direction: tighten a Telegram command's `@guard` and nothing tells
# you the web map still carries the old, looser permission, so the same skill
# becomes reachable by a role that was just locked out — by opening a different
# client.
#
# `test_command_audience_matches_permission.py` compares the catalog against
# `@guard` for exactly this reason, but reads only telegram_handler.py, so it
# never saw the web map. That is the same blind spot `guard_lint.py` had, and
# these two files were the entire structural coverage of authorisation.


def _telegram_skill_permissions() -> dict[str, set[str]]:
    """{skill: permissions that reach it on Telegram}, transitively.

    One hop of indirection is not optional here. `check_risk` and
    `get_portfolio` are dispatched by `_render_pane`, which carries no
    decorator of its own — it is called by `_cmd_dashboard`, which is
    `@guard("dashboard")`. Attributing dispatches only to the directly
    enclosing function reports those two as "not reachable on Telegram" and
    quietly drops them from the comparison. Two skills silently excluded from
    an authorisation check is precisely the failure this file exists for, so
    the walk follows `self.x(...)` calls.
    """
    src = HANDLER.read_text()
    lines = src.splitlines(keepends=True)
    fns = {}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        perms = {ast.literal_eval(d.args[0]) for d in node.decorator_list
                 if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "guard" and d.args}
        body = "".join(lines[node.lineno - 1:node.end_lineno])
        callees = {c.func.attr for c in ast.walk(node)
                   if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                   and isinstance(c.func.value, ast.Name) and c.func.value.id == "self"}
        fns[node.name] = (perms, set(re.findall(r'dispatch\(\s*"(\w+)"', body)), callees)

    def reachable(name, depth=3, seen=None):
        seen = set() if seen is None else seen
        if depth < 0 or name in seen or name not in fns:
            return set()
        seen.add(name)
        _, skills, callees = fns[name]
        return set(skills).union(*(reachable(c, depth - 1, seen) for c in callees)) \
            if callees else set(skills)

    out: dict[str, set[str]] = {}
    for name, (perms, _, _) in fns.items():
        for skill in (reachable(name) if perms else ()):
            out.setdefault(skill, set()).update(perms)
    return out


def _roles_holding(permission: str) -> set[str]:
    return {r for r, ps in ROLE_PERMISSIONS.items() if "*" in ps or permission in ps}


def test_no_web_skill_is_reachable_by_a_role_telegram_would_refuse(gateway):
    """The real invariant: web ⊆ Telegram, per skill, measured in ROLES.

    Comparing permission STRINGS would pass on `"scan"` vs `"scan"` and miss the
    case that matters — two different strings whose role sets differ. The
    question is who can reach the skill, so that is what is compared.
    """
    telegram = _telegram_skill_permissions()
    assert len(telegram) >= 15, (
        f"only {len(telegram)} skills resolved from telegram_handler.py — the "
        "parse broke and this test is comparing almost nothing")

    laxer = []
    for skill, web_perm in gateway._WEB_SKILL_PERMISSION.items():
        tg_perms = telegram.get(skill)
        # Not a skip: a web-reachable skill with no Telegram equivalent has no
        # reference point for "not laxer than Telegram", so it must be decided
        # deliberately rather than default to allowed.
        assert tg_perms, (
            f"{skill!r} is reachable from web chat but never dispatched behind a "
            "@guard on Telegram — there is nothing to compare it against")
        extra = _roles_holding(web_perm) - set().union(*map(_roles_holding, tg_perms))
        if extra:
            laxer.append(f"{skill}: web '{web_perm}' admits {sorted(extra)}, "
                         f"Telegram {sorted(tg_perms)} does not")
    assert not laxer, "web chat is laxer than Telegram:\n  " + "\n  ".join(laxer)


def test_halt_is_the_only_telegram_skill_deliberately_off_the_web(gateway):
    """Absence is load-bearing, so it is asserted rather than assumed.

    A future edit that adds `halt` back to the map reopens the shared circuit
    breaker to anyone who signs up on the website; a future edit that drops a
    different skill should have to say why here.
    """
    missing = set(_telegram_skill_permissions()) - set(gateway._WEB_SKILL_PERMISSION)
    assert missing == {"halt"}, (
        f"expected only 'halt' to be web-unreachable, got {sorted(missing)} — "
        "if that is intended, record the reason here; if not, web chat just "
        "gained or lost a capability silently")

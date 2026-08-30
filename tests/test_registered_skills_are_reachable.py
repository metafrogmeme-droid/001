"""A skill registered on no transport is a command nobody can run.

`permission_for()` is deliberately fail-closed, and says so:

    None means REFUSE. A skill added later is unreachable from chat until
    somebody decides what it needs.

That is the right default and it has one gap: nothing ever reports the pending
decision. A fail-closed default with no report is a silent backlog, and this
one grew to **nine of thirty** registered skills before anything asked.

Five of the nine live in `bot/skills/macro_skills.py` and advertise slash
commands — `/macro`, `/eventrisk`, `/compliance`, `/approve`, `/kill` — that no
transport dispatches. That `command` attribute is a promise of a user surface
that does not exist. And being unrunnable is exactly why nobody noticed that
every one of that module's seven attribute probes named a field that was never
there: `/macro` printed "No upcoming events loaded" over a calendar holding 40
events, and `/kill` — the kill switch — answered "v2 circuit breaker not
wired" every time, with a complete tested halt one attribute away. See
tests/test_macro_skills_read_real_state.py.

This is the module-level lesson from CLAUDE.md one rung lower: *a module
nothing calls is indistinguishable from one that does not work*. The existing
ratchets could not see it. `unreachable_baseline.txt` works on MODULES, and
this module is imported and its `build_v2_skills()` genuinely called.
`unreachable_methods_baseline.txt` declines any class with a base — correctly,
since `execute` is an override — so every skill's body is invisible to it by
construction. Registration is the seam neither covers.

RATCHET, BOTH DIRECTIONS, the `known_failures.txt` rule: a new dark skill fails
(somebody registered another command nobody can reach) and an entry that
becomes reachable must be deleted in the same commit (so the file cannot rot
into a list of things that were once true).
"""
from __future__ import annotations

import ast
from pathlib import Path

from bot.skills.skill_permissions import SKILL_PERMISSION
from bot.skills.skill_registry import build_default_registry

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("unreachable_skills_baseline.txt")

# Attribute calls whose first string argument names a skill to run.
DISPATCH_ATTRS = {"get", "dispatch"}


def _registered() -> dict[str, str]:
    """skill name -> the file that defines it."""
    reg = build_default_registry()
    return {
        name: type(skill).__module__.replace(".", "/") + ".py"
        for name, skill in reg._skills.items()
    }


def _receiver(func: ast.Attribute) -> str:
    v = func.value
    if isinstance(v, ast.Name):
        return v.id
    if isinstance(v, ast.Attribute):
        return v.attr
    return ""


def _dispatch_sites() -> dict[str, set[str]]:
    """Skill names appearing in a DISPATCH position, and where.

    Deliberately narrow. A first draft counted every string literal in the tree
    and acquitted `feedback` on three sites that were learning-store dict keys
    — a reachability checker with a loose signal manufactures a false
    ACQUITTAL just as readily as a blind one manufactures a false accusation,
    and an acquittal is the more dangerous direction here because it is silent.

    Only two shapes count: a `skill_name=` keyword (how `bot/mcp/server.py`
    declares its tools) and `<something registry/skill-ish>.get("name")` /
    `.dispatch("name")`.

    Scripts are NOT scanned. `scripts/test_all_skills.py` names skills only to
    exercise them, so counting it would acquit exactly the dark commands this
    test exists to report — a dev harness is not a user surface.
    """
    sites: dict[str, set[str]] = {}

    def note(value: object, path: str) -> None:
        if isinstance(value, str):
            sites.setdefault(value, set()).add(path)

    files = [*ROOT.glob("*.py"), *sorted(ROOT.glob("bot/**/*.py"))]
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = str(p.relative_to(ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "skill_name" and isinstance(kw.value, ast.Constant):
                    note(kw.value.value, rel)
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in DISPATCH_ATTRS:
                recv = _receiver(f).lower()
                if ("registry" in recv or "skill" in recv) and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        note(node.args[0].value, rel)
    return sites


def unreachable_skills() -> dict[str, str]:
    """Registered skills reachable from no product transport.

    Reachable means at least one of:
      * a key in SKILL_PERMISSION — the single table both chat transports read.
        Telegram free text goes through `permission_for()`, which REFUSES on
        None; web chat's set is derived from the same dict. So a skill absent
        here cannot be reached by chat however an intent is classified.
      * dispatched by name from `bot/` or a repo-root module (this is how MCP
        reaches `explain_trade`).
    """
    sites = _dispatch_sites()
    dark: dict[str, str] = {}
    for name, defined_in in sorted(_registered().items()):
        if name in SKILL_PERMISSION:
            continue
        elsewhere = {f for f in sites.get(name, set())
                     if not f.replace("\\", "/").endswith(defined_in)}
        if not elsewhere:
            dark[name] = defined_in
    return dark


def _baseline() -> set[str]:
    out = set()
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def test_no_new_unreachable_skills():
    """Registering a command no transport can dispatch is the defect."""
    new = sorted(set(unreachable_skills()) - _baseline())
    assert not new, (
        "These skills are registered but reachable from no transport:\n  "
        + "\n  ".join(f"{n}  ({unreachable_skills()[n]})" for n in new)
        + "\n\nEither give each one a permission in "
          "bot/skills/skill_permissions.py (which is what makes it reachable "
          "from chat), dispatch it by name, or record it in "
        + str(BASELINE.relative_to(ROOT))
        + " with a line saying why it is parked. Do not leave it registered "
          "and silent — that is how five slash commands came to advertise "
          "surfaces nobody could reach, with seven broken data probes inside "
          "them that no test could ever have run."
    )


def test_the_skill_baseline_has_no_stale_entries():
    """An entry that became reachable must go in the same commit."""
    stale = sorted(_baseline() - set(unreachable_skills()))
    assert not stale, (
        "These are recorded as unreachable but are now reachable: "
        + ", ".join(stale)
        + f"\n\nDelete them from {BASELINE.relative_to(ROOT)} in the commit "
          "that wired them up. A baseline that keeps entries after they are "
          "fixed stops describing the tree and starts describing the past."
    )


def test_a_permissioned_skill_is_never_reported_unreachable():
    """The blind-spot guard, pointed at this detector.

    A checker that reports a live command as dead manufactures exactly the
    accusation it exists to prevent, so the invariant is asserted rather than
    assumed: everything the permission table names is reachable BY DEFINITION,
    because that table is what both chat transports consult.
    """
    dark = set(unreachable_skills())
    registered = set(_registered())
    overlap = sorted(dark & set(SKILL_PERMISSION))
    assert not overlap, (
        "detector reported permissioned skills as unreachable: " + ", ".join(overlap)
    )
    # And it must not invent names that are not registered at all.
    assert dark <= registered, sorted(dark - registered)


def test_claude_md_quotes_the_real_count():
    """A number in prose is the part that rots first.

    Same pin as `unreachable_baseline.txt`'s module count, for the same
    reason: CLAUDE.md is read as the map, and a stale figure there is a
    confident claim from stale evidence.
    """
    import re

    doc = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+)\*\* of (\d+) registered skills", doc)
    assert m, "the registered-skills count sentence is gone from CLAUDE.md"
    claimed_dark, claimed_total = int(m.group(1)), int(m.group(2))
    assert claimed_dark == len(_baseline()), (
        f"CLAUDE.md says {claimed_dark} unreachable skills, "
        f"{BASELINE.name} lists {len(_baseline())}"
    )
    assert claimed_total == len(_registered()), (
        f"CLAUDE.md says {claimed_total} registered skills, "
        f"the registry builds {len(_registered())}"
    )


def test_the_baseline_names_only_registered_skills():
    """A baseline entry for a skill that no longer exists is a dangling claim."""
    registered = set(_registered())
    ghosts = sorted(_baseline() - registered)
    assert not ghosts, (
        "baseline names skills that are not registered at all: "
        + ", ".join(ghosts)
        + " — delete the lines; the skills are gone."
    )

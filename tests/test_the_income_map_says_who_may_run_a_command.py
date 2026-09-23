"""The north star must not describe shipped work as unbuilt.

`docs/INCOME_MAP.md` is the document this repo is worked from — read at the
start of a session to decide what to build next. On 2026-09-16 it sent a
reader to re-scope finished work twice:

  * the funding-arb *Gap* paragraph ended *"the proposal card that would size
    both legs (and place nothing) IS THE NEXT SLICE"* and claimed *"No
    delta-neutral pair construction"* — while the paragraph DIRECTLY ABOVE IT
    described `/arbpair` in full shipped detail, down to the six-valued
    per-leg margin read. Two adjacent paragraphs, one capability, opposite
    claims.
  * the staking section called `_cmd_stake` **admin-only** and cited
    `yield_commands.py:199`. Driven: the guard is `@guard("stake")` — trader
    and admin, on the CALLER's own linked account — and line 199 is BLANK,
    one line above `_earn_button_account`, which is not `/stake` either. The
    same document states the permission correctly (*"`stake`, held by trader
    and admin"*) a thousand lines earlier, so it contradicted itself about
    one command.

That is `CLAUDE.md`'s own *"filed, not done records are stale, and a reader
would re-scope finished work"* — in the file a reader consults FIRST.

WHAT THIS GUARD CHECKS, AND WHY IT IS NARROW. Exactly one property, driven:
**a command the map calls admin-only is admin-gated in the code.** The guard
reads the real decorators and the real in-body `_is_admin` calls; it counts
`@guard("admin")` as admin-gated, because it is.

THREE WIDER GUARDS WERE MEASURED AND REFUSED, and the refusals are the point:

  1. *Are the `file:line` citations fresh?* The obvious check is
     RESOLVABILITY — file exists, line in range — and driven over every
     citation in the map it is **0 unresolvable and 0 past end of file**,
     including over `:199`. A citation rots by pointing at the WRONG line,
     not an impossible one. Asking instead whether the cited line is BLANK
     found a second stale one (`/mystrategy` at `trading_commands.py:178`,
     six lines short of its own `@guard`), and both are FIXED here. It is
     still not a ratchet, because of the key a baseline would need:
     `path:line` pairs are invalidated by any line added ABOVE a cited line
     in any cited file, so most of its firings would be edits with no
     relation to the document — and it cannot see a citation that lands on a
     wrong NON-blank line at all.
  2. *Do citations that name a symbol point at it?* The doc's grammar is
     PROSE — `arb_tracker.py:18 states it outright`, `venue_router.js:26
     folds that DEX basis` — so a probe that reads the token after a citation
     as a symbol accused `states`, `says`, `folds`, `applies`, `creates` and
     `selects`: **36 accusations, 9 real citations, a 4:1 false rate.** The
     one genuine miss (`:199` for `_cmd_stake`) was found by READING.
  3. *Does a `*Gap.*` paragraph name a shipped command as "the next slice"?*
     One instance, and generalising it needs a reader to decide which noun
     phrase names which command — which is the same guess as (2).

A checker with a blind spot manufactures exactly the accusation it exists to
prevent. Two of these three would have; they are recorded here rather than
built, so the next reader does not rebuild them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.source_scan import segment_reader

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "docs" / "INCOME_MAP.md"

def _admin_only_permissions() -> frozenset[str]:
    """Permissions NO non-admin role holds — derived, never assumed.

    The first draft hard-coded `{"admin"}` and accused `/calibration`, which
    is `@guard("admin")` and correctly documented as admin-only. Printing the
    real guard beside the verdict is what caught it.

    The derivation is not the obvious one either: `ROLE_PERMISSIONS["admin"]`
    is the literal `{"*"}`, a wildcard, so "what admin holds" answers `*` and
    nothing else. The honest reading is the complement — a permission is
    admin-only when no OTHER role carries it. Driven, `admin` qualifies and
    `stake` does not, because `trader` holds `stake`.
    """
    from bot.utils.user_store import ROLE_PERMISSIONS
    non_admin = [p for r, p in ROLE_PERMISSIONS.items() if r != "admin"]
    held_elsewhere: set[str] = set().union(*non_admin) if non_admin else set()
    everything = set().union(*ROLE_PERMISSIONS.values()) | {"admin"}
    return frozenset(everything - held_elsewhere - {"*"})


def _command_guards() -> dict[str, tuple[bool, list[str]]]:
    """Every `_cmd_*` handler: is it admin-gated, and by what.

    Admin-gating has two spellings in this tree and both count: a
    `@guard("admin")` decorator, and an `_is_admin(...)` / `_is_admin_id(...)`
    call in the body (which is how `/idleyield` does it).
    """
    out: dict[str, tuple[bool, list[str]]] = {}
    for path in sorted((ROOT / "bot" / "skills").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:                      # pragma: no cover - parse gate covers it
            continue
        seg = segment_reader(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_cmd_"):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            perms = set(re.findall(r"guard\(['\"]([a-z_]+)['\"]",
                                   " ".join(decorators)))
            admin_perms = _admin_only_permissions()
            body = seg(node) or ""
            admin = bool(perms & admin_perms) or (
                "_is_admin(" in body or "_is_admin_id(" in body)
            out[node.name[len("_cmd_"):]] = (admin, decorators or ["<undecorated>"])
    return out


def _admin_claims(doc: str, known: set[str]):
    """(line number, command) for every sentence calling a command admin-only.

    BOTH spellings are read and both are load-bearing. The map names a command
    as `/stake` in prose and as `_cmd_stake` beside a `file:line` citation, and
    the defect this guard exists for was written in the SECOND form — on a line
    whose only slash tokens are `bot/skills/` and `/yield_commands.py`, neither
    of which is a command. Driven, the slash spelling alone finds nothing
    there; `test_the_defect_as_it_was_written_is_caught` is that drive.
    """
    for i, line in enumerate(doc.split("\n"), 1):
        low = line.lower()
        if "admin-only" not in low and "admin only" not in low:
            continue
        named = set(re.findall(r"/([a-z_]{3,})", line))
        named |= set(re.findall(r"_cmd_([a-z_]{3,})", line))
        for cmd in sorted(named & known):
            yield i, cmd


def test_the_handler_sweep_finds_the_commands():
    """A sweep that found nothing would pass every assertion below."""
    guards = _command_guards()
    assert len(guards) > 100, f"only {len(guards)} command handlers found"
    assert sum(1 for admin, _ in guards.values() if admin) > 20
    # Both spellings are represented, so neither branch is dead.
    assert guards["idleyield"][0], "/idleyield gates on _is_admin in its body"
    assert guards["stake"][0] is False, "/stake is @guard('stake'), not admin"


def test_admin_only_is_derived_from_the_role_table():
    """`stake` is not admin-only BECAUSE trader holds it — read, not assumed.

    `ROLE_PERMISSIONS["admin"]` is `{"*"}`, so the naive derivation ("what
    admin holds") answers `*`. The complement is the reading.
    """
    from bot.utils.user_store import ROLE_PERMISSIONS
    admin_only = _admin_only_permissions()
    assert "admin" in admin_only
    assert "stake" not in admin_only
    assert "stake" in ROLE_PERMISSIONS["trader"], (
        "the whole reason /stake is not admin-only")
    assert "*" not in admin_only, "the wildcard is not a permission a guard names"


def test_the_map_is_read_and_makes_these_claims():
    """The corpus is non-empty — otherwise the check below is vacuous."""
    doc = MAP.read_text(encoding="utf-8")
    claims = list(_admin_claims(doc, set(_command_guards())))
    assert claims, "INCOME_MAP names no command as admin-only; guard is vacuous"
    assert any(c == "idleyield" for _, c in claims), (
        "the /idleyield claim is the one this guard must NOT flag")


def _wrong_claims(doc: str,
                  guards: dict[str, tuple[bool, list[str]]]) -> list[str]:
    """Every admin-only claim in `doc` that the code does not enforce.

    One reading, two callers — the real map and the planted defect below. A
    second copy would agree with every fixture and diverge on the first edit
    to either, which is what a second copy looks like from outside.
    """
    wrong = []
    for line_no, cmd in _admin_claims(doc, set(guards)):
        admin, decorators = guards[cmd]
        if not admin:
            wrong.append(f"line {line_no} calls /{cmd} admin-only; "
                         f"its guard is {decorators}")
    return wrong


def test_no_command_is_called_admin_only_when_its_guard_says_otherwise():
    wrong = _wrong_claims(MAP.read_text(encoding="utf-8"), _command_guards())
    assert not wrong, (
        "INCOME_MAP.md describes access the code does not enforce:\n  "
        + "\n  ".join(wrong))


# The staking sentence exactly as it stood in INCOME_MAP.md on 2026-09-16,
# before this slice corrected it.
ORIGINAL_DEFECT = (
    "flexible/fixed Earn (bot/skills/yield_commands.py:199 _cmd_stake, "
    "admin-only;\n"
)


def test_the_defect_as_it_was_written_is_caught():
    """The guard is driven against the sentence it was written for.

    The mutation round is why this test exists. With the map corrected,
    dropping the `_cmd_` spelling from `_admin_claims` changed no verdict —
    no remaining line pairs that spelling with "admin-only", so the guard's
    own subject had left the corpus and the branch that caught the defect
    went unmeasured. An equivalent mutant is the round reporting coverage it
    does not have, so the corpus gets the input that measures it.
    """
    assert "/stake" not in ORIGINAL_DEFECT, (
        "this fixture measures the `_cmd_` spelling; a `/stake` in it would "
        "let the slash spelling answer and leave that branch unmeasured again")
    wrong = _wrong_claims(ORIGINAL_DEFECT, _command_guards())
    assert len(wrong) == 1 and "/stake" in wrong[0], wrong


def test_the_funding_arb_gap_does_not_call_the_shipped_card_unbuilt():
    """The one instance of the wider shape, pinned where it happened.

    Generalising it needs a reader to decide which noun phrase names which
    command; this asserts the two sentences that were false, and that the
    command they were false about is registered.
    """
    from bot.skills.command_catalog import all_entries
    assert "arbpair" in all_entries(), "/arbpair left the catalogue"

    doc = MAP.read_text(encoding="utf-8")
    assert "is the next slice" not in doc, (
        "a *Gap.* paragraph calls something 'the next slice'; if it names a "
        "command already in the catalogue, it will send a reader to rebuild it")
    assert "No delta-neutral pair" not in doc, (
        "/arbpair constructs the pair — it sizes both legs and places nothing")

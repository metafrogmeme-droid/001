"""A command with NO gate is absent from a baseline of what IS gated.

`tests/guarded_commands_baseline.txt` records the 109 commands that carry a
permission gate, and `test_the_income_map_says_who_may_run_a_command.py`
derives who may run the `_is_admin` ones. Neither can see a command that
carries neither — so the pair of ratchets whose own header says "a guard that
silently disappears ... is an auth regression nothing else notices" acquits,
by omission, the case where there was never a guard to disappear. A false
accusation is loud and gets fixed; a false acquittal just sits there.

Driven on 2026-09-18: `/duel`, `/alpha`, `/session` and `/funding`, each the
lone ungated row in its own catalogue group, all documented `audience='user'`
while `pending` holds only {start, help, lang} -- so a caller the bot had
never admitted reached them with no allowlist gate, no rate limit and no
registration. `/alpha` and `/funding` each spend a live venue fetch per
invocation.

`bot/formatters/market_cards.py` had already written this exact finding down,
for a DIFFERENT batch: "the only market commands with no `@guard` at all, so
they bypassed the F-2 allowlist entirely while three of them spend an exchange
call per invocation". Those four were fixed; these four were not looked at.
Ask which OTHER surface makes the same claim, applied to a fix's own street.
"""
from __future__ import annotations

import ast

import pytest

from tests.command_gates import BASELINE, baseline, command_gates, reasonless_rows, registered_commands


def test_every_registered_command_is_recorded():
    live, rec = command_gates(), baseline()
    missing = sorted(set(live) - set(rec))
    assert not missing, (
        "registered commands with no row in command_gate_baseline.txt: "
        f"{missing}. A new command is recorded in the same commit, the "
        "known_failures.txt rule.")


def test_no_stale_rows():
    live, rec = command_gates(), baseline()
    stale = sorted(set(rec) - set(live))
    assert not stale, (
        f"{stale} are recorded here and no longer registered. A stale row "
        "cannot hide a real change -- delete it in the commit that removes "
        "the command.")


def test_no_gate_changed_without_the_record_moving():
    live, rec = command_gates(), baseline()
    moved = {c: (rec[c], live[c]) for c in sorted(set(live) & set(rec))
             if rec[c] != live[c]}
    assert not moved, (
        f"gate spellings changed without the baseline moving: {moved}. "
        "Both directions matter: a gate ADDED is recorded so the next reader "
        "knows it is deliberate, and a gate REMOVED is the regression.")


def test_an_ungated_command_carries_a_reason():
    # The whole point. `none` is allowed -- /link is the linking door and its
    # token IS the credential -- but never silently.
    # Read the FILE through the rule, not through `baseline()`. A parser that
    # manufactures a reason defeats an assertion made through it, and the
    # mutation round proved exactly that: `reason = "x"` inside the reader
    # survived a green suite.
    reasonless = reasonless_rows(BASELINE.read_text(encoding="utf-8"))
    assert not reasonless, (
        f"{reasonless} carry no gate and no reason. An exemption nobody can "
        "find the argument for is the next reader's false acquittal.")


def test_nothing_is_unresolved():
    # NOT-FOUND IS NOT NO-GATE. While this was measured by hand, a probe that
    # printed the same thing for "absent" and "found, gateless" accused four
    # module-level commands it had never read -- this repository's opening
    # rule, inside the instrument built to find it.
    unresolved = sorted(c for c, g in command_gates().items()
                        if g == "unresolved")
    assert not unresolved, (
        f"{unresolved}: the walk could not find these handlers, so their "
        "gates were not measured. Add the file to `_EXTRA_SOURCES` rather "
        "than letting an unmeasured command read as an ungated one.")


class TestTheWalkItself:
    """Each of these ACCUSED correct code when it was done by hand."""

    def test_a_typing_stub_is_not_a_definition(self):
        # `callback_handler.py` declares `_cmd_*` Protocol stubs. Keying on the
        # name alone reported six guarded commands as ungated.
        gates = command_gates()
        for cmd in ("positions", "orders", "performance", "risk"):
            assert gates.get(cmd, "").startswith("guard"), (
                f"/{cmd} resolved to a `...` typing stub rather than its real "
                f"definition: {gates.get(cmd)!r}")

    def test_a_stub_is_refused_on_a_planted_tree(self):
        # The real tree cannot measure this today: `_definitions()` takes the
        # FIRST definition in MRO order and every stub sits in a file that
        # comes after the real one, so deleting the rule changes no verdict
        # and the mutation SURVIVED. The rule is still true and still worth
        # having -- a declaration is not a definition, and the ordering that
        # makes it redundant is not a property anybody guards -- so it is
        # driven where it is the only thing in play.
        from tests import command_gates as cg
        stub = ast.parse(
            "async def _cmd_demo(self, u, c) -> None: ...\n").body[0]
        real = ast.parse(
            "async def _cmd_demo(self, u, c):\n    return 1\n").body[0]
        assert cg._is_stub(stub) is True
        assert cg._is_stub(real) is False

    def test_a_docstring_only_body_is_still_a_stub(self):
        # `_is_stub` strips the docstring first; a Protocol stub may carry one.
        from tests import command_gates as cg
        node = ast.parse(
            'async def _cmd_demo(self, u, c) -> None:\n'
            '    """what it will do."""\n'
            '    ...\n').body[0]
        assert cg._is_stub(node) is True

    def test_module_level_commands_are_reached(self):
        # `handler_sources()` walks the handler's MRO, so bare functions in
        # `user_middleware` are invisible to it.
        gates = command_gates()
        assert gates.get("me") == "require_registered"
        assert gates.get("sync") == "require_registered"

    def test_a_spelling_the_walk_does_not_know_reads_as_none(self):
        # The vocabulary is hand-written, and this is the property that makes
        # that safe: a SEVENTH spelling fails loudly rather than acquitting.
        # Planted, because no command in the tree carries one today.
        from tests import command_gates as cg
        tree = ast.parse(
            "async def _cmd_demo(self, u, c):\n"
            "    if not await self._brand_new_gate(u, 'trade'):\n"
            "        return\n")
        node = tree.body[0]
        assert cg._gates_of(node) == [], (
            "an unknown gate call must not be counted as a gate")

    def test_a_known_spelling_is_counted(self):
        from tests import command_gates as cg
        tree = ast.parse(
            "async def _cmd_demo(self, u, c):\n"
            "    if not await self._guard(u, 'trade'):\n"
            "        return\n")
        assert cg._gates_of(tree.body[0]) == ["guard"]

    def test_both_registration_spellings_are_read(self):
        # `("x", self._cmd_x)` and `("x", _cmd_x)`.
        reg = registered_commands()
        assert reg.get("status") == "_cmd_status"      # bound method
        assert reg.get("link") == "_cmd_link"          # module-level name


@pytest.mark.parametrize("cmd,perm", [
    ("session", "guard"), ("alpha", "guard"),
    ("funding", "guard"), ("duel", "guard"),
])
def test_the_four_that_had_none_now_have_one(cmd, perm):
    assert command_gates()[cmd] == perm


def test_the_baseline_file_explains_itself():
    head = BASELINE.read_text(encoding="utf-8").split("\n#\n")[0]
    assert "REGISTERED" in head


def test_an_unresolvable_handler_is_never_reported_as_ungated(monkeypatch):
    """PLANTED, because no command in the tree is unresolvable today.

    A rule no input can reach is a claim that there is a check, so the
    distinction is driven on a tree where it is the only thing in play: drop
    one command's definition and the walk must say `unresolved`, not `none`.
    Folding the two would put a command nobody measured into the same bucket
    as one measured and found gateless -- the confident negative this file is
    about, inside the file about it.
    """
    from tests import command_gates as cg
    real = cg._definitions()
    victim = "_cmd_status"
    assert victim in real, "fixture: pick a command that does resolve"
    monkeypatch.setattr(cg, "_definitions",
                        lambda: {k: v for k, v in real.items() if k != victim})
    assert cg.command_gates()["status"] == "unresolved"


@pytest.mark.parametrize("line,flagged", [
    ("duel none", True),                       # no `#` at all
    ("duel none  #", True),                    # a hash and nothing after it
    ("duel none  #    ", True),                # whitespace is not a reason
    ("duel none  # it is the linking door", False),
    ("duel guard", False),                     # gated rows need no reason
    ("# a comment", False),
    ("", False),
])
def test_the_reason_rule(line, flagged):
    """PLANTED. The real baseline has no reasonless row, so a mutation of this
    rule changes no verdict against the file alone -- a guard reporting
    coverage it does not have. Driven where the rule is the only thing in
    play, the way the unresolved distinction and the gate vocabulary are."""
    assert reasonless_rows(line) == (["duel"] if flagged else [])

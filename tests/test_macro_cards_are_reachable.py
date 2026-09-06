"""Two registered skills advertised slash commands nothing dispatched.

`check_event_risk` and `compliance_status` each carried `command = "/eventrisk"`
/ `"/compliance"` in their own class bodies, and no handler existed for either.
The string was documentation of a command that did not run — the #999 shape at
the transport layer, and the reason nobody noticed that all seven of
`macro_skills.py`'s attribute probes named fields the real objects do not have.
Those were fixed in #213 against tests, which left the module correct-if-wired.

WHAT THIS FILE GUARDS, AND WHY IT IS NOT JUST "THE HANDLER EXISTS"
------------------------------------------------------------------
A handler existing is what a source scan can see, and it is the weaker half.
Three things have to line up before either command is reachable by the person
it is meant for, and each has bitten this repo already:

  the handler          — dispatches the skill and passes its arguments
  the permission       — `@guard(...)` must name a permission a real ROLE
                         holds. `exposure`, `networth`, `research` and `rwa`
                         were invented at the decorator and never added to
                         ROLE_PERMISSIONS, so four "user" commands were
                         admin-only in fact for their whole lives.
  the catalogue        — audience must match what is enforced, or /help
                         advertises a command that refuses the reader

THE TWO ARE DELIBERATELY NOT SYMMETRIC. `/eventrisk` is read-only macro data
scoped to a symbol, so it reuses `macro` — held by trader and paper. But
`/compliance` renders the GLOBAL consent ledger: up to 5,000 authorization
decisions across every user, with trade ids and the locks each failed. No
subject id is shown, but a stream of other people's grant/deny outcomes is
operator information, and read-only is not the same as shared. So it takes a
permission no role but admin holds, and the catalogue files it under an
operator group to match.
"""
from __future__ import annotations

import inspect

import pytest

from bot.skills.command_catalog import GROUPS
from bot.skills.skill_permissions import SKILL_PERMISSION, WEB_CHAT_SKILLS
from bot.utils.user_store import ROLE_PERMISSIONS

WIRED = {
    "check_event_risk": ("eventrisk", "macro"),
    "compliance_status": ("compliance", "compliance"),
}


def _handler_src(name: str) -> str:
    from bot.skills.telegram_handler import TelegramHandler
    return inspect.getsource(getattr(TelegramHandler, f"_cmd_{name}"))


def _flat(src: str) -> str:
    """Whitespace-normalised, so a wrapped call still matches.

    The first draft asserted `dispatch("check_event_risk"` against the raw
    source and failed on a line break inside the argument list — the assertion
    testing the formatter, not the wiring.
    """
    return " ".join(src.split())


def _audience(command: str) -> str:
    for _title, audience, entries in GROUPS:
        for entry, _desc in entries:
            if entry == command:
                return audience
    raise AssertionError(f"/{command} is not in the catalogue at all")


class TestTheCommandsExistAndDispatch:
    @pytest.mark.parametrize("skill,command", [(s, c) for s, (c, _) in WIRED.items()])
    def test_the_handler_dispatches_the_skill_it_advertises(self, skill, command):
        src = _flat(_handler_src(command))
        assert f'dispatch( "{skill}"' in src or f'dispatch("{skill}"' in src, (
            f"/{command} does not dispatch {skill} — the skill still advertises "
            "a command that runs something else, or nothing")

    def test_eventrisk_passes_the_symbol_through(self):
        # The skill answers "Usage: /eventrisk <SYMBOL>" on an empty symbol, so
        # a handler that forgot the kwarg would render a usage string forever
        # and look like a working command.
        src = _handler_src("eventrisk")
        assert "symbol=" in src, "the symbol never reaches the skill"
        assert "ctx.args" in src, "the symbol is not read from the command line"

    def test_both_are_registered_with_the_application(self):
        from bot.skills.telegram_handler import TelegramHandler
        src = inspect.getsource(TelegramHandler.build_app)
        for _skill, (command, _perm) in WIRED.items():
            assert f'("{command}", self._cmd_{command})' in src, (
                f"/{command} has a handler and nothing registers it — present "
                "code, zero dispatches, which is exactly what it was before")


class TestThePermissionIsOneARoleActuallyHolds:
    """A permission string no role holds makes a command admin-only in fact."""

    def test_eventrisk_is_reachable_by_a_normal_trader(self):
        assert "macro" in ROLE_PERMISSIONS["trader"]
        assert "macro" in ROLE_PERMISSIONS["paper"], (
            "self-admitted paper users can already run /macro; scoping the "
            "same data to a symbol must not need a higher role")

    def test_compliance_is_held_by_no_role_but_admin(self):
        holders = [r for r, perms in ROLE_PERMISSIONS.items()
                   if r != "admin" and "compliance" in perms]
        assert not holders, (
            f"{holders} can read the GLOBAL consent ledger — every user's "
            "grant/deny outcomes. If that is intended, the catalogue entry "
            "has to move out of the operator group in the same commit")
        assert ROLE_PERMISSIONS["admin"] == {"*"}, (
            "admin no longer holds everything, so /compliance may now be "
            "reachable by nobody at all — which is the mirror defect")

    @pytest.mark.parametrize("skill,perm", [(s, p) for s, (_, p) in WIRED.items()])
    def test_the_shared_table_carries_the_decorator_permission(self, skill, perm):
        assert SKILL_PERMISSION.get(skill) == perm, (
            "skill_permissions.py is the FACT both transports read; a skill "
            "missing from it is refused by permission_for() on every surface")
        src = _handler_src(WIRED[skill][0])
        assert f'@guard("{perm}")' in src, (
            "the table and the decorator disagree — the exact drift "
            "skill_permissions.py exists to make impossible")

    def test_web_chat_inherits_both_and_the_role_gate_still_refuses(self):
        # WEB_CHAT_SKILLS is derived from SKILL_PERMISSION by subtraction, so
        # both arrive automatically. That is correct: reachability is not
        # authorisation, and the role gate is what refuses a non-admin.
        assert "check_event_risk" in WEB_CHAT_SKILLS
        assert "compliance_status" in WEB_CHAT_SKILLS


class TestTheCatalogueTellsTheTruthAboutWhoCanRunThem:
    def test_eventrisk_is_documented_for_users(self):
        assert _audience("eventrisk") == "user"

    def test_compliance_is_documented_as_operator_only(self):
        assert _audience("compliance") == "admin", (
            "/compliance renders other users' authorization decisions; "
            "documenting it for everyone advertises a command that refuses "
            "the reader, which is what makes commands feel broken")


def _baseline_entries() -> set[str]:
    from pathlib import Path
    text = Path("tests/unreachable_skills_baseline.txt").read_text(encoding="utf-8")
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


class TestTheBaselineMovedWithTheWiring:
    def test_none_of_the_three_is_still_recorded_as_dark(self):
        entries = _baseline_entries()
        assert "check_event_risk" not in entries
        assert "compliance_status" not in entries
        assert "macro_brief" not in entries

    def test_the_two_left_behind_are_still_there(self):
        # Not a tidy-up: both argue against themselves (a second emergency
        # halt beside /halt; an approval manager that does not exist).
        # Leaving them recorded is the honest state, and the ratchet is what
        # keeps that a decision rather than an oversight.
        entries = _baseline_entries()
        for still_dark in ("kill_switch", "request_live_approval"):
            assert still_dark in entries, (
                f"{still_dark} left the baseline without this file being "
                "updated — if it was wired, say here why it was safe to")


class TestMacroBriefIsASubModeOfMacro:
    """The third macro card, wired the one way that does not collide.

    `macro_brief` advertised `/macro`, and `/macro` already dispatches
    `macro_calendar` — the events list. Two commands under one name kept the
    brief parked. It answers a different question (the macro GATE's posture:
    risk state, the size multiplier on new entries, stale/blind), so it is a
    sub-mode of the same command — `/macro brief` — behind the same
    `@guard("macro")`, and a chat tool on both surfaces. The Telegram half is
    not optional: `test_web_and_scan_authorization` holds web ⊆ Telegram per
    skill, and a skill reachable from web chat with no guarded Telegram
    dispatch has nothing to be compared against.
    """

    def test_it_reuses_the_macro_permission(self):
        assert SKILL_PERMISSION.get("macro_brief") == "macro"
        assert "macro" in ROLE_PERMISSIONS["trader"] and "macro" in ROLE_PERMISSIONS["paper"]
        src = _handler_src("macro")
        assert '@guard("macro")' in src, "the table and the decorator disagree"

    def test_it_is_offered_as_a_chat_tool_on_both_surfaces(self):
        from bot.nlp.chat_tools import CHAT_TOOLS
        tool = next((t for t in CHAT_TOOLS if t.name == "macro_brief"), None)
        assert tool is not None, "permissioned but not in the tool catalogue — chat cannot call it"
        assert "macro_calendar" in tool.description, (
            "the description must tell the model which of the two macro tools is which")
        assert "macro_brief" in WEB_CHAT_SKILLS

    def test_macro_dispatches_both_cards_and_advertises_the_sub_mode(self):
        from bot.skills.macro_skills import MacroBriefSkill
        assert MacroBriefSkill.command == "/macro brief", (
            "a command of its own collides with /macro or advertises one that "
            "does not run — both are the defect this module records")
        src = _flat(_handler_src("macro"))
        for skill in ("macro_calendar", "macro_brief"):
            assert f'dispatch("{skill}"' in src or f'dispatch( "{skill}"' in src, skill

    @pytest.mark.asyncio
    async def test_the_argument_picks_the_card(self):
        """Driven, not scanned: the bare command is still the calendar, and
        the word selects the brief whatever its case."""
        from types import SimpleNamespace

        from bot.skills.telegram_handler import TelegramHandler

        dispatched, sent = [], []
        h = TelegramHandler.__new__(TelegramHandler)

        async def _guard(update, command="", ctx=None):
            return True

        async def _send(update, text, *a, **k):
            sent.append(text)

        async def _dispatch(name, engine, **kw):
            dispatched.append(name)
            return name

        h._guard, h._send = _guard, _send
        h.registry = SimpleNamespace(dispatch=_dispatch)
        h.engine = SimpleNamespace()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                 effective_chat=SimpleNamespace(id=1))
        for args in ([], ["BRIEF"], ["brief", "extra"], ["calendar"]):
            await h._cmd_macro(update, SimpleNamespace(args=args))
        assert dispatched == ["macro_calendar", "macro_brief", "macro_brief", "macro_calendar"]
        assert sent == dispatched

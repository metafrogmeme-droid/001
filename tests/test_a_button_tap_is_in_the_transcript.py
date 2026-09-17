"""A tapped button is a turn the user can SEE and the model cannot.

`bot/nlp/skill_memory.py` exists for that shape — the model "told an answer
exists and not what it was, which is the one prompt shape most likely to be
filled in with something plausible". Slice 132 closed it for 147 slash
commands, and the registration for the OTHER door sits directly below the
wrapper it wrote:

    handler = self._remembering(cmd, handler)
    app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(CallbackQueryHandler(self._handle_callback))   # nothing

Driven on 2026-09-17: `bot/skills/callback_handler.py` is 1,776 lines with 34
callback literals and **zero** records of any kind, against 68 in
`telegram_handler.py`. `_REPLY_CAPTURE`'s own comment says *"the free-text
path, alerts and callbacks are untouched"*, so it was filed rather than
missed.

The sharpest instance is a trade. The free-text limit-price path records the
execution it performs, under a comment that reads:

    # A turn that PLACES A TRADE recorded nothing at all, so "did that go
    # through?" reached the model with the confirmation missing from its own
    # history.

and the `confirm:` BUTTON, which places the same trade, recorded nothing —
fixed for the typed door and left standing on the tapped one, in the same
file.

These tests DRIVE it. A source scan cannot see what reached the store, and
what reached the store is the whole subject.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from bot.nlp.button_actions import BUTTON_ACTIONS, UNNAMED, action_label, button_action
from bot.nlp.skill_memory import (
    button_reply_memory,
    button_turn_text,
    card_shown_memory,
    command_reply_memory,
    not_run_memory,
    routed_answer_memory,
    skill_result_memory,
    web_answer_memory,
)
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parent.parent
CB = ROOT / "bot" / "skills" / "callback_handler.py"
TH = ROOT / "bot" / "skills" / "telegram_handler.py"


def _dispatcher_literals() -> set[str]:
    """Every string `data` is compared against in `_handle_callback`.

    DERIVED, never restated: a hand-kept list is the `/setllm`
    ten-of-eleven shape, where the row added tomorrow is the one missing.
    The walk covers `==`, `in (...)` and `.startswith(...)` and reaches
    nested branches, because `reject:` is an `elif` inside the `confirm:`
    block and a top-level-only walk would acquit it.
    """
    fn = next(n for n in ast.walk(ast.parse(CB.read_text(encoding="utf-8")))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_handle_callback")
    found: set[str] = set()

    def _consts(node) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return {e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        return set()

    for n in ast.walk(fn):
        if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
                and n.left.id == "data"):
            for c in n.comparators:
                found |= _consts(c)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "startswith"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "data"):
            for a in n.args:
                found |= _consts(a)
    return found


# ──────────────────────────────────────────────────────────────────────
# The table is the dispatcher's, and it cannot fall behind it
# ──────────────────────────────────────────────────────────────────────

class TestTheTableIsDerivedFromTheDispatcher:
    def test_every_branch_has_a_row_and_every_row_has_a_branch(self):
        found = _dispatcher_literals()
        assert found, "the walk found nothing — it is measuring the wrong thing"
        missing = sorted(found - set(BUTTON_ACTIONS))
        stale = sorted(set(BUTTON_ACTIONS) - found)
        assert not missing, (
            f"the dispatcher answers {missing} and the table does not name "
            "them, so those taps record as unnamed")
        assert not stale, (
            f"the table names {stale} and no branch answers them")

    def test_the_walk_reaches_a_nested_branch(self):
        """`reject:` is an `elif` INSIDE the `confirm:` block.

        A top-level-only walk acquits it, which is the `_web_aliases` lesson:
        a scan bounded by the wrong thing is bounded by nothing.
        """
        assert "reject:" in _dispatcher_literals()
        assert "reject:" in BUTTON_ACTIONS


class TestTheActionIsTheLongestMatch:
    @pytest.mark.parametrize("data,expected", [
        ("confirm:T-1758059112:4242", "confirm:"),
        ("reject:T-1758059112:4242", "reject:"),
        ("policy_cancel", "policy_cancel"),
        ("policy_apply", "policy_"),
        ("yld:stake:usdt", "yld:"),
        ("yldf:go", "yldf:"),
        ("mode_aggressive", "mode_"),
        ("pane:risk", "pane:"),
        ("positions", "positions"),
    ])
    def test_it_matches_the_branch_the_dispatcher_takes(self, data, expected):
        assert button_action(data) == expected

    def test_a_shorter_row_does_not_win_over_a_longer_one(self):
        """`policy_cancel` is a branch of its own inside `policy_`.

        Read as the shorter row, a CANCELLATION is filed as a policy change —
        the opposite fact, from the same tap.
        """
        assert button_action("policy_cancel") != "policy_"

    @pytest.mark.parametrize("data", ["", None, 4242, "nope:1", "  "])
    def test_anything_else_is_unnamed_never_raw(self, data):
        assert button_action(data) is None
        assert action_label(data) == UNNAMED


# ──────────────────────────────────────────────────────────────────────
# THE PAYLOAD IS NEVER RECORDED
# ──────────────────────────────────────────────────────────────────────

class TestThePayloadNeverReachesTheStore:
    def test_a_trade_id_is_not_in_the_turn(self):
        turn = button_turn_text(action_label("confirm:T-1758059112:4242"))
        assert "T-1758059112" not in turn
        assert "4242" not in turn
        assert "confirm" in turn

    def test_another_users_telegram_id_is_not_in_the_turn(self):
        """`admit:<uid>` carries SOMEBODY ELSE'S identifier.

        A slash argument is at worst the caller's own secret; this one would
        put one user's id into another user's prompt and into a file on disk.
        """
        turn = button_turn_text(action_label("admit:99887766"))
        assert "99887766" not in turn
        assert "admit" in turn

    def test_an_unnamed_button_records_no_payload_either(self):
        """The fail-safe direction. A table one row short costs the model
        information; recording the raw data would cost a user their id."""
        turn = button_turn_text(action_label("brandnew:99887766:secret"))
        assert "99887766" not in turn and "secret" not in turn
        assert "does not name" in turn

    def test_no_row_in_the_table_leaks_its_payload(self):
        """Every row, not the two I happened to pick.

        *Write the assertion, then re-run the search.* `confirm:` and `admit:`
        are the two whose payload is worth naming, and a rule that holds for
        them and not for the other thirty-two is not the rule this module
        claims. The probe is a payload no dispatcher emits, so a row that
        passed it through would be unmistakable.
        """
        probe = "XPAYLOADX"
        for row in BUTTON_ACTIONS:
            label = action_label(row + probe)
            assert label and label != UNNAMED, (
                f"{row!r} + a payload labels to {label!r}")
            turn = button_turn_text(label)
            assert probe not in turn, f"{row!r} leaks its payload: {turn!r}"
            assert probe not in button_reply_memory(label, ["card"]), row

    def test_the_turn_says_no_text_was_typed(self):
        """The person typed nothing, so a turn shaped like a message would be
        the first false thing in the record."""
        assert "no text typed" in button_turn_text("confirm")
        assert "no text typed" in button_turn_text(UNNAMED)


# ──────────────────────────────────────────────────────────────────────
# The seventh record
# ──────────────────────────────────────────────────────────────────────

class TestItIsASeventhRecordAndNotOneOfTheSix:
    def test_seven_records_and_no_two_open_alike(self):
        heads = {skill_result_memory("s", "x")[:30],
                 routed_answer_memory("s", "x")[:30],
                 card_shown_memory("s")[:30],
                 not_run_memory("s", "x")[:30],
                 web_answer_memory("s", "x")[:30],
                 command_reply_memory("s", ["x"])[:30],
                 button_reply_memory("s", ["x"])[:30]}
        assert len(heads) == 7, heads

    def test_it_does_not_wear_the_tool_result_shape(self):
        """Both tool rules tell the model an `[x] result:` block "was written
        by the runtime after a tool really ran". The Close button is no tool
        the model holds."""
        out = button_reply_memory("confirm", ["executed"])
        assert "result:" not in out
        assert "SHOWN" in out

    def test_it_never_offers_a_slash_command_the_caller_cannot_type(self):
        """`command_reply_memory` spells `/x` in every sentence it has. A
        button is not a command, and a model that learnt this record from
        that one would offer `/confirm` — which does not exist."""
        for out in (button_reply_memory("confirm", ["executed"]),
                    button_reply_memory("confirm", []),
                    button_turn_text("confirm")):
            assert "/confirm" not in out

    def test_nothing_captured_is_not_nothing_sent(self):
        out = button_reply_memory("pane", [])
        assert "CONTENTS NOT RECORDED" in out
        assert "may have sent nothing" in out
        assert "tapped that button" in out

    def test_the_two_capture_records_share_one_tail(self):
        """Three records already share `_headed` because a mutation driver's
        anchor matched two byte-identical copies. The not-captured sentence
        was about to be a fourth copy."""
        from bot.nlp import skill_memory as sm
        src = code_only((ROOT / "bot" / "nlp" / "skill_memory.py")
                        .read_text(encoding="utf-8"))
        # Anchored on the clause the two CAPTURE records share.
        # `card_shown_memory` also opens "CONTENTS NOT RECORDED" and then says
        # something different on purpose — "was sent to the user", because its
        # callers watched the send happen — so counting the opening would
        # accuse a record that is right to differ.
        assert src.count("may have sent nothing") == 1, (
            "the not-captured sentence is written once")
        assert "_nothing_captured" in src and "_captured" in src
        # and both readers really take it
        assert sm._nothing_captured("x", "y did a thing").startswith("[x] SHOWN")


# ──────────────────────────────────────────────────────────────────────
# THE DRIVE: what actually reaches the store
# ──────────────────────────────────────────────────────────────────────

class _Store:
    def __init__(self):
        self.rows: list[tuple[str, str, str, dict]] = []

    def append(self, user_id, role, content, metadata=None):
        self.rows.append((user_id, role, content, dict(metadata or {})))


def _host(*, authorized: bool = True, data: str = "confirm:T-999:4242"):
    """A stand-in `self` carrying only what the wrapper and `_send` reach for.

    The REAL `_send` is bound, not a stub: the claim under test is that the
    chokepoint captures, and a stub `_send` is exactly the fixture
    CLAUDE.md records as unable to see it.
    """
    from bot.skills.telegram_handler import TelegramHandler

    delivered: list[str] = []

    async def _edit(text, **kw):
        delivered.append(text)

    store = _Store()
    msg = SimpleNamespace(reply_text=_edit)
    query = SimpleNamespace(data=data, edit_message_text=_edit, message=msg)
    update = SimpleNamespace(callback_query=query, message=None,
                             effective_user=SimpleNamespace(id=4242))

    host = SimpleNamespace(
        conversations=store, store=store, delivered=delivered, update=update,
        users={"4242": {"authorized": authorized}},
        _get_tg_id=lambda u: "4242",
        _is_allowlisted=lambda u: True,
        _lang=lambda u: "en",
    )
    for name in ("_remembering_button", "_remember_button", "_transcript_user",
                 "_send"):
        setattr(host, name, getattr(TelegramHandler, name).__get__(host))
    # `_split_message` is a staticmethod: binding it would pass `host` as the
    # text. The first draft did, and `_send` raised inside its own try —
    # which is a fixture breaking the code it is driving.
    host._split_message = TelegramHandler._split_message
    return host


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class TestTheWrapperRecordsWhatTheTapDid:
    def test_a_tap_that_replies_reaches_the_store_with_its_card(self):
        h = _host()

        async def _dispatch(update, ctx):
            await h._send(update, "<b>Trade executed</b> PENDLE LONG @ 2.367",
                          edit=True)

        _run(h._remembering_button(_dispatch)(h.update, None))

        assert h.delivered, "the card really went out"
        roles = [r[1] for r in h.store.rows]
        assert roles == ["user", "assistant"], roles
        user, assistant = h.store.rows[0][2], h.store.rows[1][2]
        assert "confirm" in user and "no text typed" in user
        assert "T-999" not in user, "the payload must not reach the store"
        assert "PENDLE LONG" in assistant, (
            "the card the user saw is what the model reads — this is the "
            "assertion the old code could not make, because nothing recorded")
        assert h.store.rows[1][3].get("via") == "button"

    def test_a_tap_that_replies_nothing_records_the_absence(self):
        h = _host(data="pane:risk")

        async def _dispatch(update, ctx):
            return None

        _run(h._remembering_button(_dispatch)(h.update, None))
        assistant = h.store.rows[1][2]
        assert "CONTENTS NOT RECORDED" in assistant
        assert "pane" in h.store.rows[0][2]

    def test_a_tap_that_raises_records_the_failure_and_re_raises(self):
        h = _host()

        async def _dispatch(update, ctx):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            _run(h._remembering_button(_dispatch)(h.update, None))
        assert [r[1] for r in h.store.rows] == ["user", "assistant"]
        assert "FAILED" in h.store.rows[1][2]
        assert "boom" not in h.store.rows[1][2], (
            "a driver message must not reach the model's record")

    def test_not_admitted_no_transcript(self):
        """The store evicts its least-recent users at 200, so strangers
        pressing buttons would push admitted users' history out."""
        h = _host(authorized=False)

        async def _dispatch(update, ctx):
            await h._send(update, "Access denied", edit=True)

        _run(h._remembering_button(_dispatch)(h.update, None))
        assert h.delivered, "they are still answered"
        assert h.store.rows == [], "and nothing is recorded"

    def test_the_capture_does_not_leak_out_of_the_tap(self):
        """`_REPLY_CAPTURE` is reset in a `finally`, so a later send outside
        any tap appends to nobody's transcript."""
        from bot.skills.telegram_handler import _REPLY_CAPTURE
        h = _host()

        async def _dispatch(update, ctx):
            await h._send(update, "card", edit=True)
            assert _REPLY_CAPTURE.get() == ["card"], "it is set while the tap runs"

        async def _drive():
            await h._remembering_button(_dispatch)(h.update, None)
            # READ IN THE SAME CONTEXT. `run_until_complete` copies the
            # context, so a `.set()` inside the coroutine never reaches the
            # caller's — asserting out here passed under the mutation that
            # deletes the reset, which is a test passing for a reason
            # unrelated to the rule it names.
            return _REPLY_CAPTURE.get()

        assert _run(_drive()) is None

    def test_the_record_is_written_after_the_dispatch_ran(self):
        """A tap that admits its own caller is recorded and a stranger's is
        not, which only holds if the record is made afterwards."""
        seen: list[int] = []
        h = _host(authorized=False)

        async def _dispatch(update, ctx):
            h.users["4242"]["authorized"] = True
            seen.append(len(h.store.rows))

        _run(h._remembering_button(_dispatch)(h.update, None))
        assert seen == [0], "nothing was recorded before the branch ran"
        assert len(h.store.rows) == 2, "and the self-admitting tap is recorded"


# ──────────────────────────────────────────────────────────────────────
# The wiring, and the chokepoint the six branches used to skip
# ──────────────────────────────────────────────────────────────────────

class TestItIsActuallyReached:
    def test_the_one_registration_is_wrapped(self):
        """The AST, not a literal: a comment naming `_remembering_button`
        satisfies a string search, and this guard exists because the line
        above it was wrapped and this one was not."""
        tree = ast.parse(TH.read_text(encoding="utf-8"))
        regs = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "CallbackQueryHandler"]
        assert len(regs) == 1, f"one callback registration, found {len(regs)}"
        arg = regs[0].args[0]
        assert isinstance(arg, ast.Call), ast.unparse(arg)
        assert isinstance(arg.func, ast.Attribute)
        assert arg.func.attr == "_remembering_button", ast.unparse(arg)

    def test_no_branch_sends_outside_the_chokepoint(self):
        """Six branches sent through `query.edit_message_text` by hand, which
        skips `reply_safe`, the 4000-char split, the HTML->plain fallback
        (five of the six had NONE) and the capture. `_send` has all four and
        already takes this exact edit path."""
        src = code_only(CB.read_text(encoding="utf-8"))
        for spelling in ("query.edit_message_text(", "query.message.reply_text("):
            assert spelling not in src, (
                f"{spelling} bypasses _send: the reply is unscrubbed and the "
                "transcript never sees it")
        # `ctx.bot.send_message(` is NOT in that list, and the reason is in
        # the guard so the next reader does not "close" it: the `admit:`
        # branch uses it to message the ADMITTED PERSON in their own chat.
        # That is not this caller's reply, it is not on this caller's screen,
        # and routing it through `_send` would send it to the wrong chat. The
        # first draft of this assertion accused it.
        assert "ctx.bot.send_message(" in src, (
            "if that third-party notice ever leaves, so should this note")

    def test_the_capture_comment_no_longer_says_callbacks_are_untouched(self):
        """It said so, correctly, and that sentence is how the gap stayed
        filed. A stale comment claiming a hole that is closed sends the next
        reader to re-scope finished work."""
        src = TH.read_text(encoding="utf-8")
        assert "alerts and callbacks are untouched" not in src

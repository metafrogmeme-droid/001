"""A tapped Close button closes a real position, and nothing asked who tapped it.

`_handle_callback` has carried a destructive-callback gate since Audit F-11,
and its own comment says what it is for -- *"this stops an authorized
non-privileged user from pausing, emergency-stopping, or switching strategy
mode via an inline button."* It was five literals in a dict LOCAL to a
1,400-line method:

    _DESTRUCTIVE_CB_PERM = {
        "risk_safe_mode": "halt", "risk_pause": "halt",
        "risk_emergency_stop": "halt", "emergency_confirm": "halt",
        "closeall_confirm": "halt",
    }

`closeall_confirm` is there. `pos_close_` is not. **Closing EVERY position
needed a permission and closing ONE needed none**, which is the tell that the
door was missed rather than weighed -- and the map was unreadable from outside
the method, so nothing could notice. That is `vault_fix_hint`'s defect one
surface over (*"the map was nested in a 150-line method, so nothing could read
the instruction an operator is given"*), and it sat in the same function as
`BUTTON_ACTIONS`, whose module docstring says twenty lines in that a map kept
in step by hand is the `/setllm` ten-of-eleven shape and which is AST-pinned
against the dispatcher for exactly that reason. **The lesson reached the
TRANSCRIPT table and not the PERMISSION table one screen away.**

Driven on the shipped default (`PER_USER_LIVE_ENABLED=False`, LIVE mode):
`viewer` holds `portfolio` -- so `_cmd_open_positions` renders the card and
builds `pos_close_<tid>:<their own uid>` for them -- and does NOT hold `trade`.
The tap is ordinary: correctly tagged, so the IDOR guard passes BY DESIGN.
`close_position` was awaited on the OPERATOR's live executor.
`/liveclose` is the same call on the same account and is `@guard("admin")`.

**This does not reopen the owner-tag decision.**
`test_callback_owner_guard_is_fail_closed.py` records `pos_close_` as fail-open
on an ABSENT owner tag by design, reasoning that it "resolves the position
through `user_portfolios.get(user_id)` and `_caller_executor(update)`, both
keyed by the caller". That is true of the CALL and false of the ACCOUNT: the
resolver's own docstring says that with per-user live off it is "ALWAYS the
shared operator executor". Routing is one axis and ROLE is another, and only
the first had ever been asked. The tap driven here carries its owner tag, so
that predicate never fires.

**Two gates, because two things were missing.** The role gate at dispatch
(`trade`, not `admin` -- the same button closes the caller's OWN paper book in
paper mode and `paper` holds `trade`, so gating on admin would take a paper
user's own positions away from them, which is the over-strict half of a fix
being its own defect). And H-18 in the branch: `confirm:` refuses a live
PLACEMENT to a caller without live authority, and the CLOSE door refused
nothing, so a role that may not open a live position could still close one.

**And the ratchet, because one row is not the class.** Every `BUTTON_ACTIONS`
row must now be declared gated-with-a-permission or harmless-with-a-REASON.
A reason rather than a bare set, because "gated somewhere else" and "changes
nothing" are different facts and only the first can stop applying --
`command_gates.py`'s rule, where an unrecognised spelling reads as `none` and
a `none` row must say why. The runtime cannot tell "nobody decided" from
"decided harmless", so the decision is forced where it can be read.
"""

from __future__ import annotations

import ast
import asyncio
from types import SimpleNamespace

import bot.config as bot_config
from bot.nlp.button_actions import (
    BUTTON_ACTIONS,
    CALLBACK_NO_PERMISSION,
    CALLBACK_PERMISSION,
    required_permission,
)
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import ROLE_PERMISSIONS
from tests.source_scan import code_only, handler_sources

UID = "424242"
TRADE_ID = "TI-op-1"


class _OperatorExecutor:
    """The OPERATOR's live book. Nobody but an operator should move it."""

    user_id = "OPERATOR"

    def __init__(self, sink):
        self.sink = sink
        self.open_positions = [
            SimpleNamespace(
                trade_id=TRADE_ID, symbol="BTC/USDT", direction="LONG",
                entry_price=63000.0, quantity=0.5, stop_loss=61000.0,
                take_profit=66000.0, cost_usd=3150.0, leverage=10,
            )
        ]

    async def close_position(self, trade_id, reason):
        self.sink.append((self.user_id, trade_id, reason))
        return {"ok": True, "pnl_usd": 12.34}


class _PaperPortfolio:
    """The CALLER's own paper book."""

    def __init__(self, sink):
        self.sink = sink
        self.open_positions = [
            SimpleNamespace(trade_id="TI-paper-1", asset="BTC/USDT")
        ]

    def close_position(self, trade_id, close_price):
        self.sink.append((trade_id, close_price))
        return SimpleNamespace(
            pnl=1.0, quantity=0.5, entry_price=63000.0, exit_price=63500.0,
            gross_pnl=1.2, commission=0.2, opened_at=None, closed_at=None,
        )


def _tap(role, *, can_live, is_admin, live, owner_tag=UID, paper_sink=None,
         ident=TRADE_ID, per_user_live=False):
    """One ordinary tap through the REAL dispatcher. Returns (closed, replies)."""
    closed: list = []
    replies: list = []
    op = _OperatorExecutor(closed)
    portfolio = _PaperPortfolio(paper_sink if paper_sink is not None else [])

    class _Users:
        def get(self, tid):
            return {"role": role, "authorized": True}

        def has_permission(self, tid, perm):
            perms = ROLE_PERMISSIONS[role]
            return perm in perms or "*" in perms

        def live_trading_revoked(self, *a, **k):
            return False

        def can_trade_live(self, tid):
            return can_live

        def is_authorized(self, *a, **k):
            return True

        # `test_user_admission.py` derives what a UserStore double must carry
        # from what the web gateway CALLS on the store, so any class with a
        # `has_permission` is held to it. This double never reaches the
        # gateway; implementing the four is cheaper and more honest than an
        # exemption, which is how that ratchet stops being able to see the
        # ninth drifted double.
        def is_admitted(self, *a, **k):
            return True

        def permission_denial(self, *a, **k):
            return None

        def get_tier(self, *a, **k):
            return "free"

        def register(self, *a, **k):
            return None

    class _Exchange:
        async def fetch_ticker(self, sym):
            return {"last": 63500.0, "close": 63500.0, "bid": 63499.0, "ask": 63501.0}

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = SimpleNamespace(
        live_executor=op,
        _executor_for=lambda uid="": op,
        user_portfolios=SimpleNamespace(get=lambda uid: portfolio),
        get_exchange=lambda: _exchange_coro(),
    )

    async def _exchange_coro():
        return _Exchange()

    h.engine.get_exchange = _exchange_coro
    h.users = _Users()
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h._check_auth = lambda update: True
    h._is_admin = lambda update: is_admin
    h._can_trade_live = lambda tg_id: can_live
    h._lang = lambda update: "en"
    h._live_refusal_key = lambda: "live_not_enabled"

    async def _send(update, text, **kw):
        replies.append(text)

    h._send = _send

    async def _report(update, executor, lp, pair, result):
        replies.append("REPORTED CLOSE %s" % pair)
        return True

    h._report_manual_close = _report

    data = f"pos_close_{ident}"
    if owner_tag is not None:
        data += f":{owner_tag}"

    query = SimpleNamespace(
        data=data,
        message=SimpleNamespace(edit_reply_markup=_noop, chat_id=int(UID)),
        answer=_noop,
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=int(UID), first_name="X"),
        effective_chat=SimpleNamespace(id=int(UID)),
    )

    orig = type(bot_config.CONFIG).is_live
    type(bot_config.CONFIG).is_live = lambda self: live
    # `per_user_live_enabled` is a frozen field, so this is the only door --
    # the same `object.__setattr__` the halt suite needs, restored in a
    # `finally` because it sits outside monkeypatch's bookkeeping.
    prev_pul = bot_config.CONFIG.per_user_live_enabled
    object.__setattr__(bot_config.CONFIG, "per_user_live_enabled", per_user_live)
    # `_uid_matches` answers True when the expected id is EMPTY ("allow all",
    # its own documented semantic for the broadcast it was written for), so a
    # blank operator chat id makes every caller read as the operator and
    # `_caller_executor` never returns None. A fixture that cannot produce the
    # state it names measures nothing: the operator here is somebody else.
    prev_chat = bot_config.CONFIG.telegram.chat_id
    object.__setattr__(bot_config.CONFIG.telegram, "chat_id", "999999")
    try:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            h._handle_callback(update, SimpleNamespace())
        )
    finally:
        type(bot_config.CONFIG).is_live = orig
        object.__setattr__(bot_config.CONFIG, "per_user_live_enabled", prev_pul)
        object.__setattr__(bot_config.CONFIG.telegram, "chat_id", prev_chat)
    return bool(closed), replies


async def _noop(*a, **k):
    return None


# ---------------------------------------------------------------------------


class TestTheDoorIsGated:
    """Driven through the real dispatcher, both arms, four roles."""

    def test_a_viewer_cannot_close_the_operators_live_position(self):
        """THE defect. `viewer` holds `portfolio` (the card renders) and not
        `trade`, and the button is the one this build hands them."""
        closed, replies = _tap("viewer", can_live=False, is_admin=False, live=True)
        assert closed is False
        assert replies, "a refusal that says nothing is a bot that looks broken"
        assert "viewer" in replies[0]

    def test_a_paper_role_cannot_close_a_live_position(self):
        """H-18, the OPEN door's authority. `paper` holds `trade`, so the role
        gate passes; it may not OPEN a live position, so it may not close one."""
        closed, replies = _tap("paper", can_live=False, is_admin=False, live=True)
        assert closed is False
        assert replies

    def test_a_trader_with_live_authority_still_closes(self):
        """The over-strict half of a fix is its own defect. Driven, not promised."""
        closed, _ = _tap("trader", can_live=True, is_admin=False, live=True)
        assert closed is True

    def test_an_admin_still_closes(self):
        closed, _ = _tap("admin", can_live=True, is_admin=True, live=True)
        assert closed is True

    def test_paper_mode_is_untouched(self):
        """The same button closes the CALLER'S OWN paper book, and `paper`
        holds `trade`. Gating on `admin` would have taken that away."""
        sink: list = []
        # The paper branch matches `pos.asset` against the payload, so a
        # paper button carries the SYMBOL -- a trade-id fixture cannot reach
        # it, and a fixture that cannot produce the state it names measures
        # nothing.
        closed, replies = _tap(
            "paper", can_live=False, is_admin=False, live=False,
            paper_sink=sink, ident="BTCUSDT",
        )
        assert sink, f"paper close did not reach the caller's own book: {replies}"
        assert sink[0][0] == "TI-paper-1"

    def test_a_paper_user_keeps_their_own_book_under_per_user_live(self):
        """The over-strict half, driven rather than promised.

        With per-user live ON, a caller with no live account resolves through
        the REAL `_caller_executor` to None and falls through to their own
        paper book. The live gate asks only when there IS a live executor to
        act on, so this close still happens; a gate placed one line earlier
        would have taken a paper user's own positions away from them over an
        authority that book never needed.
        """
        sink: list = []
        _tap("paper", can_live=False, is_admin=False, live=True,
             paper_sink=sink, ident="BTCUSDT", per_user_live=True)
        assert sink, "a paper user lost their own book to the live gate"
        assert sink[0][0] == "TI-paper-1"

    def test_a_viewer_cannot_close_a_paper_position_either(self):
        """`viewer` is the role that may look and not act, in either mode."""
        sink: list = []
        closed, replies = _tap(
            "viewer", can_live=False, is_admin=False, live=False,
            paper_sink=sink, ident="BTCUSDT",
        )
        assert sink == []
        assert "viewer" in replies[0]


class TestTheTwoRefusalsAreDifferentFacts:
    """A role that may not act and a role with no live authority are two
    states with two remedies. One sentence for both sends somebody to ask for
    the wrong thing."""

    def test_they_are_not_the_same_sentence(self):
        _, role_refusal = _tap("viewer", can_live=False, is_admin=False, live=True)
        _, live_refusal = _tap("paper", can_live=False, is_admin=False, live=True)
        assert role_refusal[0] != live_refusal[0]

    def test_the_role_refusal_names_the_role(self):
        _, replies = _tap("viewer", can_live=False, is_admin=False, live=True)
        assert "viewer" in replies[0]

    def test_the_live_refusal_does_not_name_the_role(self):
        """It is not a fact about the role: a trader without live authority
        gets the same sentence, and telling them their ROLE cannot do it sends
        them to ask for a promotion they do not need."""
        _, replies = _tap("paper", can_live=False, is_admin=False, live=True)
        assert "paper" not in replies[0].lower()


class TestEveryButtonIsDeclared:
    """The ratchet. One row is not the class."""

    def test_every_action_is_declared_exactly_once(self):
        gated = set(CALLBACK_PERMISSION)
        harmless = set(CALLBACK_NO_PERMISSION)
        undeclared = set(BUTTON_ACTIONS) - gated - harmless
        assert not undeclared, (
            f"{len(undeclared)} callback(s) the dispatcher branches on are "
            f"declared neither gated nor harmless: {sorted(undeclared)}. "
            "The runtime answers None for both, so an undeclared row is an "
            "acquittal by nobody having thought about it -- which is how "
            "pos_close_ closed the operator's live position for a viewer."
        )

    def test_no_action_is_declared_both_ways(self):
        both = set(CALLBACK_PERMISSION) & set(CALLBACK_NO_PERMISSION)
        assert not both, f"declared gated AND harmless: {sorted(both)}"

    def test_no_declaration_is_stale(self):
        """The `known_failures.txt` rule: a row naming a button this build no
        longer has is a decision about nothing, and the next reader trusts it."""
        known = set(BUTTON_ACTIONS)
        stale = (set(CALLBACK_PERMISSION) | set(CALLBACK_NO_PERMISSION)) - known
        assert not stale, (
            f"declared rows the dispatcher no longer branches on: {sorted(stale)}"
        )

    def test_every_harmless_row_carries_a_reason(self):
        bare = [k for k, v in CALLBACK_NO_PERMISSION.items()
                if not isinstance(v, str) or len(v.strip()) < 12]
        assert not bare, (
            f"harmless rows with no reason: {bare}. 'Gated somewhere else' and "
            "'changes nothing' are different facts and only the first can stop "
            "applying."
        )

    def test_every_gated_row_names_a_permission_some_role_holds(self):
        """A permission no role holds refuses everybody -- `market_commands`
        records that mistake -- and one every role holds refuses nobody."""
        for action, perm in CALLBACK_PERMISSION.items():
            holders = [r for r, s in ROLE_PERMISSIONS.items()
                       if perm in s or "*" in s]
            assert holders, f"{action} names {perm!r}, which no role holds"
            assert len(holders) < len(ROLE_PERMISSIONS), (
                f"{action} names {perm!r}, which every role holds: no gate"
            )


class TestTheRuleItselfBites:
    """On the real tree every row is declared, so a mutation of the RULE
    changes no verdict there. Driven where it is the only thing in play."""

    def test_an_undeclared_row_is_reported(self):
        planted = ("closeall_confirm", "brand_new_button:")
        gated = {"closeall_confirm": "halt"}
        harmless: dict = {}
        undeclared = set(planted) - set(gated) - set(harmless)
        assert undeclared == {"brand_new_button:"}

    def test_a_stale_row_is_reported(self):
        planted = ("closeall_confirm",)
        gated = {"closeall_confirm": "halt", "button_that_left": "halt"}
        stale = set(gated) - set(planted)
        assert stale == {"button_that_left"}

    def test_a_reasonless_harmless_row_is_reported(self):
        harmless = {"a:": "a read of one card", "b:": ""}
        bare = [k for k, v in harmless.items() if len(v.strip()) < 12]
        assert bare == ["b:"]


class TestTheSeamIsTheOneReading:
    """A map local to the dispatcher is a map nothing can read."""

    def test_the_dispatcher_asks_the_seam(self):
        src = _dispatcher_source()
        calls = [
            n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "required_permission"
        ]
        assert calls, "the dispatcher no longer asks the shared reading"

    def test_the_dispatcher_keeps_no_map_of_its_own(self):
        """A second copy of a map is a second answer. The mutation this kills
        is the one a later reader makes: re-adding a local dict 'just for this
        branch', which agrees with the table on every fixture."""
        src = _dispatcher_source()
        known = set(BUTTON_ACTIONS)
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            assert not (keys & known), (
                "the dispatcher carries its own callback->permission map "
                f"again ({sorted(keys & known)}); the table lives in "
                "bot/nlp/button_actions.py so a test can read it"
            )

    def test_longest_match_keeps_a_cancellation_a_cancellation(self):
        """`policy_cancel` is a branch of its own inside `policy_`. Reading it
        as the shorter row refuses a button that changes nothing."""
        assert required_permission("policy_cancel") is None
        assert required_permission("policy_apply_now") == "mode"

    def test_the_close_row_is_the_one_that_was_missing(self):
        assert required_permission(f"pos_close_{TRADE_ID}:{UID}") == "trade"
        assert CALLBACK_PERMISSION["pos_close_"] == "trade"

    def test_a_non_string_payload_is_not_a_permission(self):
        assert required_permission(None) is None
        assert required_permission(object()) is None


class TestTheCloseDoorMirrorsTheOpenDoor:
    """`confirm:` places and `pos_close_` closes, on the same account. They
    ask the same authority now; for the life of the branch only one did."""

    def test_both_branches_read_live_authority(self):
        src = _dispatcher_source()
        tree = ast.parse(src)
        found = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            lits = {c.value for c in ast.walk(node.test)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            for row in ("confirm:", "pos_close_"):
                if row not in lits:
                    continue
                body = "\n".join(
                    ast.get_source_segment(src, s) or "" for s in node.body)
                if "_can_trade_live" in body:
                    found[row] = True
        assert found.get("confirm:"), "the OPEN door lost its H-18 reading"
        assert found.get("pos_close_"), (
            "the CLOSE door does not ask live authority; a role that may not "
            "open a live position can still close one"
        )


def _dispatcher_source() -> str:
    """`_handle_callback`'s own body, comments blanked.

    Bounded by the `ast.FunctionDef` rather than a character window, because a
    boundary that is 'whatever happens to be next' is a boundary that
    manufactures accusations. Comments are blanked because this module's own
    prose quotes the map it forbids -- read raw, the scan would report the
    explanation as the defect.
    """
    for path in handler_sources():
        raw = path.read_text()
        if "_handle_callback" not in raw:
            continue
        for node in ast.walk(ast.parse(raw)):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "_handle_callback"
                    and (node.end_lineno - node.lineno) > 100):
                lines = raw.splitlines()[node.lineno - 1:node.end_lineno]
                import textwrap
                return code_only(textwrap.dedent("\n".join(lines)))
    raise AssertionError("the callback dispatcher was not found")

"""The other half of H4: a vouched-for trader could still stop the whole bot.

The self-admission fix (`SELF_ADMISSION_ROLE`) closed the door a stranger walked
through. It did not touch the thing on the other side of it. `trader` — what
`/approve` grants, and what an id in `LIVE_TRADER_TELEGRAM_IDS` gets on first
contact — still held `halt`, `reset` and `mode`, and `_guard` authorises those on
role membership plus a 24-hour freshness check and nothing else. No command
asked whether the caller owns the engine they were about to stop.

The audit names the principal precisely: LIVE_TRADER_TELEGRAM_IDS is documented
in `telegram_handler.py` as a NON-operator live user. They are allowlisted, they
are registered, and `/reset` called `engine.reset_circuit_breaker_all()` —
which resets the shared RiskEngine, every per-user RiskEngine, and clears
`engine._halted`, the flag the pre-execute gate reads. A breaker the risk engine
tripped on a real drawdown could be cleared, and live execution re-armed, by
somebody who does not own the account.

THE SHAPE OF THE FIX

Not "remove the permission". `/reset` and `/pause` have a legitimate per-account
meaning — `engine.risk_for(uid)` already returns a caller's own RiskEngine when
PER_USER_LIVE_ENABLED is on — and deleting the commands from a trader's /help
would have been a bigger change than the defect. So the authority moved from the
ROLE to the ENGINE: `_control_scope()` answers which RiskEngine this caller may
stop and start, and returns nothing at all when the answer would be "everyone's".

The refusal branch is the part worth reading twice. With PER_USER_LIVE_ENABLED
off — the default, and this deployment — `risk_for()` honestly returns the
SHARED engine for every caller, because that is the engine their trades run
through. Scoping `/reset` to `risk_for(uid)` and stopping there would have
produced a command that reads as personal and clears the operator's breaker:
the original defect, wearing a helper. So "you resolved to the shared engine and
you are not the operator" is told apart from "here is yours", and is refused.

WHAT HAS NO SCOPED VERSION. `/halt` transitions the whole engine to HALTED and
clears the shared idea book; `/emergency_stop` flattens every account, operator
and per-user alike; `/mode` writes `RUNTIME.asset_universe`, which decides what
the scan loop pulls for everybody. None of these mean anything per-account, so
they are operator-only outright — and the buttons that reach the same code
(`risk_pause`, `emergency_confirm`) are gated the same way, because a permission
map that says `halt` says the caller may pause SOMETHING, not everybody.

`/mode` keeps its read. Gating the whole command would have hidden from a user
which universe their own scans run against, and answered a mistyped `/mode
slana` with a permission refusal instead of the card listing the valid names.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.utils.user_store import UserStore

OPERATOR = "111"
TRADER = "4242"          # /approve'd by the operator — a real, vouched-for user
ADMIN2 = "222"


# ── harness ──────────────────────────────────────────────────────────

class _Risk:
    def __init__(self, tripped=True, streak=4, label="shared"):
        self.circuit_breaker_active = tripped
        self.consecutive_losses = streak
        self.label = label
        self.calls: list[str] = []

    def emergency_halt(self, reason=""):
        self.calls.append("emergency_halt")
        self.circuit_breaker_active = True

    def reset_circuit_breaker(self):
        self.calls.append("reset_circuit_breaker")
        self.circuit_breaker_active = False

    def pending_retrip_reason(self):
        return ""


class _Engine:
    """Real routing, stub state.

    `_is_operator_user` and `risk_for` are the ACTUAL engine methods, bound to
    this stub — they are the logic under test, and a hand-rolled copy here would
    be testing the copy. Everything they read is stubbed; nothing they decide is.
    """
    from bot.core.engine import RuneClawEngine as _E
    _is_operator_user = _E._is_operator_user
    risk_for = _E.risk_for
    del _E

    def __init__(self):
        self.risk = _Risk(label="shared")
        self._halted = True
        self._pending_ideas = {"idea-1": object()}
        self._user_risk: dict = {}
        self._user_store = None
        self.global_calls: list[str] = []

    def _sync_risk_market_context(self, eng):
        pass

    def reset_circuit_breaker_all(self):
        self.global_calls.append("reset_circuit_breaker_all")
        self.risk.circuit_breaker_active = False
        self._halted = False
        for eng in self._user_risk.values():
            eng.reset_circuit_breaker()
        return 1 + len(self._user_risk)

    async def emergency_halt_all(self, reason=""):
        self.global_calls.append("emergency_halt_all")
        self.risk.circuit_breaker_active = True
        return {"engines_halted": 1, "pending_cleared": 1, "accounts": []}


class _Registry:
    def __init__(self):
        self.dispatched: list[str] = []

    async def dispatch(self, name, engine, **kwargs):
        self.dispatched.append(name)
        return "dispatched"


class _Limiter:
    def allow(self, uid):
        return True


class _Query:
    def __init__(self, data):
        self.data = data
        self.message = SimpleNamespace(message_id=1)

    async def answer(self):
        pass


def _handler(tmp_path):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = _Engine()
    h.engine._user_store = h.users
    h.registry = _Registry()
    h._limiter = _Limiter()
    h.sent: list[str] = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _request_operator_admission(*a, **kw):
        return False

    h._send = _send
    h._request_operator_admission = _request_operator_admission

    # An operator, and a trader the operator explicitly vouched for. The trader
    # is the audit's principal: approved, allowlisted, session fresh.
    h.users.authorize(OPERATOR, role="admin", by=OPERATOR)
    h.users.register(TRADER, name="Vouched")
    h.users.authorize(TRADER, role="trader", by=OPERATOR)
    return h


def _update(uid, text="", data=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid), first_name="T",
                                       language_code="en"),
        effective_chat=SimpleNamespace(id=int(uid)),
        message=SimpleNamespace(text=text),
        callback_query=_Query(data) if data else None)


@pytest.fixture
def bot(tmp_path):
    """Production shape: one operator configured, per-user live trading OFF."""
    h = _handler(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = TRADER
        mc.paper_auto_accept = True
        mc.per_user_live_enabled = False
        mc.is_live.return_value = True
    yield h
    patch.stopall()


# ── the vouched-for trader ───────────────────────────────────────────

class TestAVouchedTraderCannotStopEveryone:
    @pytest.mark.asyncio
    async def test_reset_does_not_clear_the_shared_breaker(self, bot):
        await bot._cmd_reset(_update(TRADER, "/reset"), None)
        assert bot.engine.risk.circuit_breaker_active is True, (
            "an approved trader cleared the operator's tripped breaker")
        assert bot.engine.global_calls == [], "reset_circuit_breaker_all ran"
        assert bot.engine._halted is True, "the global kill switch was cleared"

    @pytest.mark.asyncio
    async def test_resume_does_not_re_arm_execution(self, bot):
        await bot._cmd_resume(_update(TRADER, "/resume"), None)
        assert bot.engine.risk.circuit_breaker_active is True
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_pause_does_not_stop_everyone(self, bot):
        bot.engine.risk.circuit_breaker_active = False      # healthy engine
        await bot._cmd_pause(_update(TRADER, "/pause"), None)
        assert bot.engine.risk.circuit_breaker_active is False
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_halt_does_not_reach_the_skill(self, bot):
        await bot._cmd_halt(_update(TRADER, "/halt"), None)
        assert bot.registry.dispatched == []

    @pytest.mark.asyncio
    async def test_emergency_stop_is_not_even_offered(self, bot):
        """Refused at the PROMPT, not only at the confirm. A confirm keyboard
        that refuses on confirm is its own defect — it tells somebody they may
        do a thing and then says no after they commit to it."""
        await bot._cmd_emergency_stop(_update(TRADER, "/emergency_stop"), None)
        assert not any("CONFIRM STOP" in s for s in bot.sent), (
            "the trader was shown a kill-switch button they cannot press")

    @pytest.mark.asyncio
    async def test_mode_does_not_switch_the_universe(self, bot):
        from bot.config import RUNTIME
        before = RUNTIME.asset_universe
        try:
            await bot._cmd_mode(_update(TRADER, "/mode solana"), None)
            assert RUNTIME.asset_universe == before
        finally:
            RUNTIME.asset_universe = before

    @pytest.mark.asyncio
    async def test_but_mode_still_SHOWS_them_the_universe(self, bot):
        """The read stays open. Gating the whole command would hide from a user
        which universe their own scans run against."""
        bot.sent.clear()
        await bot._cmd_mode(_update(TRADER, "/mode"), None)
        assert bot.sent, "the status card was refused along with the write"
        assert "ASSET UNIVERSE" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_a_mistyped_mode_gets_the_card_not_a_refusal(self, bot):
        bot.sent.clear()
        await bot._cmd_mode(_update(TRADER, "/mode slana"), None)
        assert "ASSET UNIVERSE" in bot.sent[-1], (
            "a typo was answered with a permission error instead of the list "
            "of valid names")

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_authority_and_reassures(self, bot):
        bot.sent.clear()
        await bot._cmd_reset(_update(TRADER, "/reset"), None)
        assert bot.sent, "silence"
        text = bot.sent[-1]
        assert "every" in text.lower(), "the refusal does not say why"
        assert "operator" in text.lower()
        assert "/reset" in text or "reset" in text


class TestTheButtonsAgreeWithTheCommands:
    """`_DESTRUCTIVE_CB_PERM` gates these on the `halt` PERMISSION, which says
    the caller may pause something — not that they may pause everybody. A
    button that outranks its own command is a gate with a hole beside it."""

    @pytest.mark.asyncio
    async def test_the_risk_panel_pause_button_is_refused(self, bot):
        bot.engine.risk.circuit_breaker_active = False
        await bot._handle_callback(_update(TRADER, data="risk_pause"), None)
        assert bot.engine.risk.circuit_breaker_active is False, (
            "the risk-panel button paused every account")
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_the_emergency_confirm_button_is_refused(self, bot):
        await bot._handle_callback(_update(TRADER, data="emergency_confirm"), None)
        assert bot.engine.global_calls == [], (
            "the confirm button flattened every account for a non-operator")

    @pytest.mark.asyncio
    async def test_the_operator_can_still_press_them(self, bot):
        await bot._handle_callback(_update(OPERATOR, data="emergency_confirm"), None)
        assert bot.engine.global_calls == ["emergency_halt_all"]


class TestTheOperatorIsUnaffected:
    """Every authority test needs this half, or it cannot tell a working gate
    from a fixture too broken to do anything."""

    @pytest.mark.asyncio
    async def test_reset_still_clears_everything(self, bot):
        await bot._cmd_reset(_update(OPERATOR, "/reset"), None)
        assert bot.engine.global_calls == ["reset_circuit_breaker_all"]
        assert bot.engine.risk.circuit_breaker_active is False
        assert bot.engine._halted is False

    @pytest.mark.asyncio
    async def test_halt_still_dispatches(self, bot):
        await bot._cmd_halt(_update(OPERATOR, "/halt"), None)
        assert bot.registry.dispatched == ["halt"]

    @pytest.mark.asyncio
    async def test_pause_still_stops_the_engine(self, bot):
        bot.engine.risk.circuit_breaker_active = False
        await bot._cmd_pause(_update(OPERATOR, "/pause"), None)
        assert bot.engine.risk.circuit_breaker_active is True

    @pytest.mark.asyncio
    async def test_mode_still_switches(self, bot):
        from bot.config import RUNTIME
        before = RUNTIME.asset_universe
        try:
            await bot._cmd_mode(_update(OPERATOR, "/mode solana"), None)
            assert RUNTIME.asset_universe == "solana"
        finally:
            RUNTIME.asset_universe = before

    @pytest.mark.asyncio
    async def test_emergency_stop_still_offers_the_button(self, bot):
        await bot._cmd_emergency_stop(_update(OPERATOR, "/emergency_stop"), None)
        assert bot.sent

    @pytest.mark.asyncio
    async def test_an_admin_by_STORE_role_counts_too(self, bot):
        bot.users.authorize(ADMIN2, role="admin", by=OPERATOR)
        await bot._cmd_halt(_update(ADMIN2, "/halt"), None)
        assert bot.registry.dispatched == ["halt"]

    def test_an_operator_known_ONLY_by_chat_id_still_counts(self, bot):
        """The case that makes `_is_operator`'s second clause load-bearing.

        `_is_admin_id` reads the user-store role and ADMIN_TELEGRAM_IDS.
        `engine._is_operator_user` reads the store role and TELEGRAM_CHAT_ID.
        Only the second knows the operator's own chat id, and a deployment with
        TELEGRAM_CHAT_ID set and ADMIN_TELEGRAM_IDS empty is the ordinary
        single-operator shape.

        The first draft of this class tested an admin by STORE ROLE instead, and
        a mutation dropping the clause passed — that case is one `_is_admin_id`
        already covers on its own. This is the one that distinguishes them, so
        it is asserted on `_is_operator` directly: routing it through /halt would
        have gone through `_guard` first, which refuses an unknown id for its own
        reasons and would have made the test pass for the wrong cause.
        """
        bot.users._users.pop(OPERATOR, None)          # no store record at all
        assert bot._is_admin(_update(OPERATOR)) is False, (
            "premise gone: _is_admin now recognises a bare chat_id operator, so "
            "this no longer distinguishes the two checks")
        assert bot._is_operator(_update(OPERATOR)) is True, (
            "the operator is not recognised by TELEGRAM_CHAT_ID alone")

    def test_and_a_boot_puts_the_record_back(self, bot):
        """The other half, because `_is_operator` is not the first gate a
        command meets — `_guard` checks the ROLE first, and since H4 the role a
        re-registered user gets is `paper`, which does not hold `halt`.

        `TelegramHandler.__init__` calls `users.seed_admin(TELEGRAM_CHAT_ID)`,
        so a lost `data/users.json` is repaired at the next start rather than
        leaving the operator role-gated out of their own kill switch. Pinned
        because H4 narrowed the auto-registration role, which is exactly the
        change that would have made this bite.
        """
        bot.users._users.pop(OPERATOR, None)
        bot.users.seed_admin(OPERATOR)
        assert bot.users.get(OPERATOR)["role"] == "admin"
        assert bot.users.permission_denial(OPERATOR, "halt") is None


# ── the scoped path, when per-user live trading is on ────────────────

@pytest.fixture
def per_user_bot(tmp_path):
    h = _handler(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = TRADER
        mc.per_user_live_enabled = True
        mc.is_live.return_value = True
    # Their own engine, pre-bound so risk_for returns it from cache rather than
    # constructing a real RiskEngine over a real portfolio file.
    h.engine._user_risk[TRADER] = _Risk(tripped=True, streak=5, label="own")
    yield h
    patch.stopall()


class TestAUserWithTheirOwnEngineControlsThatOne:
    @pytest.mark.asyncio
    async def test_reset_clears_their_breaker_and_nobody_elses(self, per_user_bot):
        bot = per_user_bot
        await bot._cmd_reset(_update(TRADER, "/reset"), None)
        assert bot.engine._user_risk[TRADER].circuit_breaker_active is False, (
            "their own breaker was not cleared — the scoped path is dead")
        assert bot.engine.risk.circuit_breaker_active is True, (
            "clearing their own breaker cleared the operator's")
        assert bot.engine.global_calls == []
        assert bot.engine._halted is True

    @pytest.mark.asyncio
    async def test_the_card_says_it_was_only_theirs(self, per_user_bot):
        bot = per_user_bot
        bot.sent.clear()
        await bot._cmd_reset(_update(TRADER, "/reset"), None)
        assert "only" in bot.sent[-1].lower(), (
            f"a personal reset reads as a global one: {bot.sent[-1]!r}")

    @pytest.mark.asyncio
    async def test_pause_stops_their_account_only(self, per_user_bot):
        bot = per_user_bot
        bot.engine.risk.circuit_breaker_active = False
        bot.engine._user_risk[TRADER].circuit_breaker_active = False
        await bot._cmd_pause(_update(TRADER, "/pause"), None)
        assert bot.engine._user_risk[TRADER].circuit_breaker_active is True
        assert bot.engine.risk.circuit_breaker_active is False

    @pytest.mark.asyncio
    async def test_halt_is_STILL_refused(self, per_user_bot):
        """Having your own engine does not buy you the global ones. HaltSkill
        transitions the whole engine and clears the shared idea book; there is
        no per-account version of that to scope to."""
        bot = per_user_bot
        await bot._cmd_halt(_update(TRADER, "/halt"), None)
        assert bot.registry.dispatched == []

    @pytest.mark.asyncio
    async def test_and_so_is_mode(self, per_user_bot):
        from bot.config import RUNTIME
        bot = per_user_bot
        before = RUNTIME.asset_universe
        try:
            await bot._cmd_mode(_update(TRADER, "/mode stocks"), None)
            assert RUNTIME.asset_universe == before
        finally:
            RUNTIME.asset_universe = before


# ── the cards ────────────────────────────────────────────────────────

class TestTheCardDoesNotOverstateItsScope:
    """Plant the state, assert what the card SAYS. Every line of the pause card
    was a claim about the whole bot; once /pause could act on one account those
    lines became false for exactly the person reading them."""

    def test_a_scoped_pause_does_not_claim_the_bot_stopped(self):
        from bot.warroom.warroom_bot import render_pause
        text = render_pause(scope="own")["text"]
        for forbidden in ("All trading activity", "BOT PAUSED"):
            assert forbidden not in text, (
                f"a per-account pause card says {forbidden!r}")
        assert "Scanning" in text and "PAUSED" not in text.split("Scanning")[1][:40], (
            "the card says scanning stopped; it did not")
        assert "only" in text.lower()

    def test_the_operator_card_is_unchanged(self):
        from bot.warroom.warroom_bot import render_pause
        text = render_pause()["text"]
        assert "BOT PAUSED" in text
        assert "All trading activity" in text
        assert "only" not in text.lower()

    def test_a_scoped_resume_does_not_claim_the_engine_is_back(self):
        from bot.warroom.warroom_bot import render_resume
        text = render_resume(scope="own")["text"]
        assert "back online" not in text, (
            "a per-account resume claims the engine came back online")
        assert "BOT RESUMED" not in text

    def test_the_operator_resume_card_is_unchanged(self):
        from bot.warroom.warroom_bot import render_resume
        text = render_resume()["text"]
        assert "BOT RESUMED" in text and "back online" in text

    def test_the_retrip_warning_survives_scoping(self):
        """The honesty that was already there must not be lost to the one being
        added. `render_resume` warns when the breaker will re-trip; a scope
        parameter that dropped it would trade one true statement for another."""
        from bot.warroom.warroom_bot import render_resume
        for scope in ("shared", "own"):
            text = render_resume(retrip_warning="daily loss limit still hit",
                                 scope=scope)["text"]
            assert "daily loss limit still hit" in text, scope
            assert "CLEAR*" in text, scope


# ── the red herring ──────────────────────────────────────────────────

class TestTheRedHerring:
    @pytest.mark.asyncio
    async def test_every_older_gate_says_yes(self, bot):
        """This trader is not a stranger and not unauthorised. An admin vouched
        for them, the allowlist admits them, their role carries `reset`, and
        their session is fresh — `permission_denial` returns None. Every gate
        this repository added before H4 answers yes.

        That is why the check had to move to the ENGINE. A fix that leaned on
        the role, the allowlist, the admission stamp or the session window would
        have passed its own test and shipped the hole.
        """
        assert bot._is_allowlisted(_update(TRADER)) is True
        assert bot.users.is_admitted(TRADER) is True
        assert bot.users.get(TRADER)["role"] == "trader"
        assert bot.users.permission_denial(TRADER, "reset") is None
        assert bot.users.permission_denial(TRADER, "halt") is None
        # ...and the engine still says no.
        assert bot._control_scope(_update(TRADER)) == (None, "")
        assert bot._is_operator(_update(TRADER)) is False

    @pytest.mark.asyncio
    async def test_the_shared_engine_is_not_handed_out_as_someones_own(self, bot):
        """The subtle wrong fix: scope /reset to `engine.risk_for(uid)` and stop
        there. With PER_USER_LIVE_ENABLED off that returns the SHARED engine for
        everybody — honestly, because that is the engine their trades run
        through — so the "scoped" reset would clear the operator's breaker while
        reading as personal."""
        assert bot.engine.risk_for(TRADER) is bot.engine.risk, (
            "premise changed: risk_for no longer returns the shared engine in "
            "single-account mode, so this test guards nothing")
        risk, scope = bot._control_scope(_update(TRADER))
        assert risk is None and scope == "", (
            "_control_scope handed a non-operator the shared engine as if it "
            "were their own")

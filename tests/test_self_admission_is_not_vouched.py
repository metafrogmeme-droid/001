"""A stranger's first message bought them the operator's kill switch.

`PAPER_AUTO_ACCEPT` defaults **True**. Any unknown Telegram id whose first
message is a guarded command falls into `_guard`'s not-allowlisted branch and
reaches:

    self.users.authorize(tg_id, role="trader", by="auto-accept")

`authorize(by=...)` stamps `admitted_at` + `admitted_by`, which is exactly what
`is_admitted()` reads, which is one of the two doors `_is_allowlisted` accepts.
So the same call that admits them also decides what they are, and it said
"trader" — the role an admin's `/approve` grants to somebody a human vouched
for. `trader` holds `halt`, `reset` and `mode`:

    /reset  → engine.reset_circuit_breaker_all(), whose own docstring says it
              resets "the shared engine AND every per-user RiskEngine", and
              clears engine._halted
    /halt   → engine.risk.emergency_halt(), every per-user risk engine halted,
              every pending idea cancelled, engine transitioned to HALTED
    /mode   → RUNTIME.asset_universe = ..., the universe every account scans

An operator whose breaker tripped on a real drawdown could have it cleared, and
trading resumed, by anyone on the internet who found the bot.

WHY THE COMMENT ON THAT LINE DID NOT CATCH IT

It said the door "grants BOT ACCESS ONLY. `_can_trade_live` is a separate
authority a self-admitted user cannot satisfy". True, and the wrong axis.
Live-trade eligibility gates whose *money* moves; `halt`/`reset`/`mode` are
gated on the ROLE and move nobody's money — they stop and start the machine.
The live door was checked and shut. The kill switch was never on the list.

THE SAME HOLE, ALREADY FOUND, ON THE OTHER SURFACE

`test_web_and_scan_authorization.py` found web signups reaching a global halt
through `/api/chat` and fixed it by leaving `halt` out of
`_WEB_SKILL_PERMISSION` — with a comment stating the map cannot include it
*because* "Web ids are auto-provisioned with DEFAULT_AUTO_ROLE, which holds the
'halt' permission". The authority was known to be wrong and the TRANSPORT was
patched. Telegram then grew its own self-provisioning door, and the same
authority was still wrong underneath it.

`test_user_admission.py` names the consequence outright — F-2 closed a hole
where "any /start made a stranger an authorized trader who could /halt, /reset,
/mode and emergency-stop a live bot" — and stayed green throughout, because it
tests `register()`, and auto-accept calls `authorize()`.

THE FIX is that self-admission and vouching stop being the same principal.
`SELF_ADMISSION_ROLE` ("paper") holds the whole Arena surface and none of
`OPERATOR_CONTROL_PERMISSIONS`, `DEFAULT_AUTO_ROLE` points at it so the web
door is covered too, and `authorize()` clamps to it whenever the admitting
party is `SELF_ADMISSION_BY` — one funnel, so the next call site cannot forget.

WHAT THIS DOES NOT FIX, deliberately: `trader` still holds all three. A person
an admin explicitly `/approve`d can still trip the shared breaker. That is a
product decision about a vouched-for teammate, not a hole a stranger walks
through, and it is recorded in the PR rather than decided here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.utils.user_store import (DEFAULT_AUTO_ROLE, OPERATOR_CONTROL_PERMISSIONS,
                                  ROLE_PERMISSIONS, ROLES, SELF_ADMISSION_BY,
                                  SELF_ADMISSION_ROLE, UserStore)

STRANGER = 999
OPERATOR = 111


# ── harness ──────────────────────────────────────────────────────────

class _Risk:
    """A risk engine with a TRIPPED breaker — the planted state. Every mutation
    is recorded rather than refused, so a test can assert the stranger's command
    reached nothing, instead of asserting it raised."""

    def __init__(self):
        self.circuit_breaker_active = True
        self.consecutive_losses = 4
        self.calls: list[str] = []

    def emergency_halt(self, reason=""):
        self.calls.append(f"emergency_halt({reason})")
        self.circuit_breaker_active = True

    def reset_circuit_breaker(self):
        self.calls.append("reset_circuit_breaker")
        self.circuit_breaker_active = False

    def pending_retrip_reason(self):
        return ""


class _Engine:
    def __init__(self):
        self.risk = _Risk()
        self._halted = True
        self._pending_ideas = {"idea-1": object()}
        self._user_risk: dict = {}

    def reset_circuit_breaker_all(self):
        self.risk.calls.append("reset_circuit_breaker_all")
        self.risk.circuit_breaker_active = False
        self._halted = False
        return 1


class _Registry:
    def __init__(self):
        self.dispatched: list[str] = []

    async def dispatch(self, name, engine, **kwargs):
        self.dispatched.append(name)
        return "dispatched"


class _Limiter:
    def allow(self, uid):
        return True


def _handler(tmp_path):
    """A real TelegramHandler with the collaborators the guarded paths touch.

    `__new__` rather than `__init__` because construction pulls in the whole
    engine; the object under test here is the authority chain, and it is the
    real one — real `_guard`, real `@guard` decorators, real UserStore.
    """
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = _Engine()
    h.registry = _Registry()
    h._limiter = _Limiter()
    h.sent: list[str] = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _request_operator_admission(*a, **kw):
        return False

    h._send = _send
    h._request_operator_admission = _request_operator_admission
    return h


def _update(uid, text=""):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, first_name="Stranger",
                                       language_code="en"),
        effective_chat=SimpleNamespace(id=uid),
        message=SimpleNamespace(text=text),
        callback_query=None)


def _live_bot(monkeypatch=None):
    """CONFIG with an operator configured (so the allowlist is real) and
    PAPER_AUTO_ACCEPT on (so the door is open) — production's defaults."""
    mc = patch("bot.skills.telegram_handler.CONFIG").start()
    mc.telegram.chat_id = str(OPERATOR)
    mc.telegram.admin_ids = ""
    mc.telegram.live_trader_ids = ""
    mc.paper_auto_accept = True
    return mc


@pytest.fixture
def bot(tmp_path):
    h = _handler(tmp_path)
    mc = _live_bot()
    yield h
    patch.stopall()
    del mc


async def _admit_stranger(bot):
    """Drive the real door: an unknown id issues a command they are allowed."""
    assert await bot._guard(_update(STRANGER), "scan", None) is True, (
        "the auto-accept on-ramp stopped working — this test would then be "
        "asserting the hole is shut by accident")
    return bot.users.get(STRANGER)


# ── the door ─────────────────────────────────────────────────────────

class TestTheDoorStillOpens:
    """The feature is a zero-friction Arena on-ramp. Closing the hole by
    closing the door would 'pass' this file and delete the thing it guards."""

    @pytest.mark.asyncio
    async def test_a_stranger_is_admitted_on_their_first_command(self, bot):
        user = await _admit_stranger(bot)
        assert user is not None
        assert bot.users.is_admitted(STRANGER) is True
        assert bot._is_allowlisted(_update(STRANGER)) is True

    @pytest.mark.asyncio
    async def test_they_reach_the_whole_paper_surface(self, bot):
        await _admit_stranger(bot)
        for cmd in ("scan", "deepscan", "analyze", "portfolio", "trade",
                    "dashboard", "backtest", "journal", "risk", "mystrategy"):
            assert await bot._guard(_update(STRANGER), cmd, None) is True, (
                f"/{cmd} is part of the paper on-ramp and was refused")


class TestTheDoorDoesNotVouch:
    @pytest.mark.asyncio
    async def test_it_grants_the_self_admission_role_not_trader(self, bot):
        user = await _admit_stranger(bot)
        assert user["role"] == SELF_ADMISSION_ROLE
        assert user["role"] != "trader", (
            "a stranger who let themselves in is indistinguishable from a "
            "person the operator vouched for")

    @pytest.mark.asyncio
    async def test_the_record_still_says_no_human_approved_them(self, bot):
        """The role separation is the fix; the attribution stays as F-2 left
        it. Both, so /users can show who knocked and who was let in."""
        user = await _admit_stranger(bot)
        assert user["admitted_by"] == SELF_ADMISSION_BY
        assert user["admitted_by"] != str(OPERATOR)

    @pytest.mark.asyncio
    async def test_the_door_ASKS_for_the_weaker_role_too(self, bot):
        """Both layers, verified separately.

        Mutating the call site back to `role="trader"` left every other test in
        this file green, because `authorize()`'s clamp caught it. That is the
        clamp doing its job — and it is also how one of two layers rots
        unnoticed until the day the other is refactored out. Depth is only
        depth if each layer is observed on its own, so this watches the call
        rather than its result.
        """
        seen = []
        real = bot.users.authorize

        def spy(tg_id, role="trader", by=""):
            seen.append((role, by))
            return real(tg_id, role=role, by=by)

        bot.users.authorize = spy
        await _admit_stranger(bot)
        assert seen, "the auto-accept branch did not call authorize()"
        assert seen[0] == (SELF_ADMISSION_ROLE, SELF_ADMISSION_BY), (
            f"the door asked for {seen[0][0]!r}; only the clamp in authorize() "
            "is keeping a stranger out of the operator's controls")

    def test_an_admin_approval_still_grants_trader(self, tmp_path):
        """The clamp must key on the ADMITTING PARTY, not on the role asked
        for. A clamp that fired on `role="trader"` would silently demote every
        /approve — the fix breaking the feature it is protecting."""
        store = UserStore(tmp_path / "users.json")
        store.register(4242, name="Ann")
        assert store.authorize(4242, role="trader", by=str(OPERATOR)) is True
        assert store.get(4242)["role"] == "trader"


class TestTheClampIsInTheFunnel:
    """`authorize()` is where the admission stamp is written, so it is where
    the ceiling belongs. A rule enforced at the call site is a rule the next
    call site does not know about — which is how this one arrived."""

    @pytest.mark.parametrize("asked", ["trader", "admin", "viewer"])
    def test_self_admission_cannot_ask_for_a_stronger_role(self, tmp_path, asked):
        store = UserStore(tmp_path / "users.json")
        store.register(STRANGER, name="Stranger")
        assert store.authorize(STRANGER, role=asked, by=SELF_ADMISSION_BY) is True
        assert store.get(STRANGER)["role"] == SELF_ADMISSION_ROLE

    def test_it_still_admits_them(self, tmp_path):
        """Clamping DOWN, not refusing. A refusal here turns the newcomer away
        with no explanation, which is the friction the door exists to remove."""
        store = UserStore(tmp_path / "users.json")
        store.register(STRANGER, name="Stranger")
        store.authorize(STRANGER, role="trader", by=SELF_ADMISSION_BY)
        assert store.is_admitted(STRANGER) is True

    def test_a_clamp_is_audited(self, tmp_path):
        """Silently granting something other than what was asked for is how a
        caller keeps believing it got what it asked for."""
        store = UserStore(tmp_path / "users.json")
        store.register(STRANGER, name="Stranger")
        with patch("bot.utils.user_store.audit") as rec:
            store.authorize(STRANGER, role="trader", by=SELF_ADMISSION_BY)
        actions = [c.kwargs.get("action") for c in rec.call_args_list]
        assert "self_admission_clamped" in actions


# ── the controls, driven end to end ──────────────────────────────────

class TestAStrangerCannotTouchTheSharedEngine:
    """Not "the permission is absent" — the COMMAND, through its real
    decorator, against a planted tripped breaker. A source scan cannot tell a
    guard that is present from a guard that is reached."""

    @pytest.mark.asyncio
    async def test_reset_does_not_clear_the_operators_breaker(self, bot):
        await _admit_stranger(bot)
        await bot._cmd_reset(_update(STRANGER, "/reset"), None)
        assert bot.engine.risk.circuit_breaker_active is True, (
            "a stranger cleared the operator's tripped circuit breaker")
        assert bot.engine._halted is True
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_resume_does_not_restart_trading(self, bot):
        await _admit_stranger(bot)
        await bot._cmd_resume(_update(STRANGER, "/resume"), None)
        assert bot.engine.risk.circuit_breaker_active is True
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_halt_does_not_stop_the_engine(self, bot):
        await _admit_stranger(bot)
        await bot._cmd_halt(_update(STRANGER, "/halt"), None)
        assert bot.registry.dispatched == [], (
            "a stranger reached HaltSkill — global breaker, every per-user "
            "risk engine, every pending idea")

    @pytest.mark.asyncio
    async def test_pause_does_not_trip_the_breaker(self, bot):
        bot.engine.risk.circuit_breaker_active = False   # a HEALTHY engine
        await _admit_stranger(bot)
        await bot._cmd_pause(_update(STRANGER, "/pause"), None)
        assert bot.engine.risk.circuit_breaker_active is False, (
            "a stranger paused trading for every account")
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_mode_does_not_switch_the_universe(self, bot):
        from bot.config import RUNTIME
        before = RUNTIME.asset_universe
        await _admit_stranger(bot)
        try:
            await bot._cmd_mode(_update(STRANGER, "/mode solana"), None)
            assert RUNTIME.asset_universe == before, (
                "a stranger changed the asset universe every account scans")
        finally:
            RUNTIME.asset_universe = before

    @pytest.mark.asyncio
    async def test_the_refusal_says_why(self, bot):
        """A silent refusal is the "commands feel broken" failure the command
        catalogue exists to prevent — and it teaches the operator nothing when
        a real user hits it."""
        await _admit_stranger(bot)
        bot.sent.clear()
        await bot._cmd_reset(_update(STRANGER, "/reset"), None)
        assert bot.sent, "the stranger got silence"
        assert SELF_ADMISSION_ROLE in " ".join(bot.sent)

    @pytest.mark.asyncio
    async def test_the_red_herring(self, bot):
        """The planted misleading signal: this stranger IS allowlisted, IS
        admitted, IS authorized, and their session is fresh. Every gate F-2
        added answers yes. Only the role says no — so a fix that leaned on any
        of the others would pass its own test and ship the hole."""
        await _admit_stranger(bot)
        assert bot._is_allowlisted(_update(STRANGER)) is True
        assert bot.users.is_admitted(STRANGER) is True
        assert bot.users.is_authorized(STRANGER) is True
        assert bot.users.permission_denial(STRANGER, "scan") is None
        assert bot.users.permission_denial(STRANGER, "reset") == "role"

    @pytest.mark.asyncio
    async def test_an_admin_still_can(self, bot):
        """The other half of every authority test: proving the refusal is about
        this caller and not about the fixture being too broken to work."""
        bot.users.authorize(OPERATOR, role="admin", by=str(OPERATOR))
        await bot._cmd_reset(_update(OPERATOR, "/reset"), None)
        assert bot.engine.risk.calls == ["reset_circuit_breaker_all"]
        assert bot.engine.risk.circuit_breaker_active is False


# ── the sets themselves ──────────────────────────────────────────────

class TestTheRoleSets:
    def test_the_self_admission_role_holds_no_operator_control(self):
        held = ROLE_PERMISSIONS[SELF_ADMISSION_ROLE] & OPERATOR_CONTROL_PERMISSIONS
        assert not held, f"{SELF_ADMISSION_ROLE!r} holds {sorted(held)}"

    def test_it_is_trader_minus_exactly_those(self):
        """The maintenance cost of writing `paper` out by hand, paid here.

        Adding a permission to `trader` without deciding about `paper` fails
        this, naming both directions — rather than either silently granting a
        stranger a new operator control (if `paper` were derived) or silently
        hiding a new feature from every self-admitted user (if it were not
        pinned)."""
        missing = ROLE_PERMISSIONS["trader"] - ROLE_PERMISSIONS[SELF_ADMISSION_ROLE]
        assert missing == set(OPERATOR_CONTROL_PERMISSIONS), (
            f"trader - {SELF_ADMISSION_ROLE} is {sorted(missing)}, expected "
            f"{sorted(OPERATOR_CONTROL_PERMISSIONS)}.\n"
            f"Granting it to both: add it to ROLE_PERMISSIONS[{SELF_ADMISSION_ROLE!r}].\n"
            f"Vouched-for only: add it to OPERATOR_CONTROL_PERMISSIONS and "
            f"check it against the derivation test.")
        extra = ROLE_PERMISSIONS[SELF_ADMISSION_ROLE] - ROLE_PERMISSIONS["trader"]
        assert not extra, f"{SELF_ADMISSION_ROLE} holds {sorted(extra)} that trader does not"

    def test_both_self_provisioning_doors_land_on_it(self):
        """Telegram auto-accept and website signup are the same thing wearing
        different clothes: nobody vouched. `register()` is called with no
        auto_role everywhere, so DEFAULT_AUTO_ROLE covers the web."""
        assert DEFAULT_AUTO_ROLE == SELF_ADMISSION_ROLE

    def test_a_web_signup_gets_it(self, tmp_path):
        store = UserStore(tmp_path / "users.json")
        assert store.register("web:stranger", name="")["role"] == SELF_ADMISSION_ROLE

    def test_the_role_is_declared(self):
        assert SELF_ADMISSION_ROLE in ROLES
        assert SELF_ADMISSION_ROLE in ROLE_PERMISSIONS

    def test_it_is_not_the_empty_set(self):
        """Guards the guard. A `paper` role holding nothing would pass every
        refusal test above while making the on-ramp useless."""
        assert len(ROLE_PERMISSIONS[SELF_ADMISSION_ROLE]) > 20


class TestTheOperatorSeesThem:
    """`/users` is the operator's headcount. A role missing from its render
    loop is a group of real users shown as absent — this repo's founding rule,
    applied to people."""

    def test_every_role_is_rendered(self):
        import pathlib
        import re
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
        code = "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))
        m = re.search(r"role_icons\s*=\s*\{([^}]*)\}", code)
        assert m, "the /users role icon map moved — re-point this check"
        for role in ROLES:
            assert f'"{role}"' in m.group(1), (
                f"/users has no icon for {role!r}; those users render blank")

    @pytest.mark.asyncio
    async def test_the_count_includes_a_self_admitted_user(self, bot):
        await _admit_stranger(bot)
        assert bot.users.count().get(SELF_ADMISSION_ROLE) == 1

    def test_approve_advertises_every_role_it_accepts(self):
        """`/approve` grew a fourth role. Its help text listed three.

        An operator who cannot see that `paper` is grantable has exactly two
        moves for a "trader" they no longer trust — leave them, or /revoke —
        and the step between is invisible. The same drift in the other
        direction is worse: a role offered in the help text that the validator
        rejects.
        """
        import pathlib
        import re
        from bot.utils.i18n import SUPPORTED_LANGS, t
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
        code = "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))
        m = re.search(r'if role not in \(([^)]*)\)', code)
        assert m, "the /approve role validator moved — re-point this check"
        accepted = set(re.findall(r'"([a-z]+)"', m.group(1)))
        accepted.discard("")
        accepted |= {SELF_ADMISSION_ROLE} if "SELF_ADMISSION_ROLE" in m.group(1) else set()
        assert accepted, "no roles parsed out of the validator"
        # Every language, rendered through the real `t()`. A role listed only in
        # English is invisible to exactly the operators who most need the help
        # text to be complete.
        #
        # BOTH directions, because the first draft asserted only one: narrowing
        # the validator back to three roles left this green, so the "worse"
        # drift named in the docstring above — help text offering a role the
        # validator rejects — was a claim the test did not back.
        for lang in SUPPORTED_LANGS:
            for key in ("approve_usage", "invalid_role"):
                text = t(key, lang, role="whatever")
                advertised = {c for c in re.findall(r"<code>([^<]+)</code>", text)
                              if c in ROLES}
                assert advertised == accepted, (
                    f"{key!r} [{lang}] advertises {sorted(advertised)}; "
                    f"/approve accepts {sorted(accepted)}")

"""H3: typing three words reached the kill switch that two PRs had just gated.

`_handle_message` routes free text through `intent_router`, and on a
high-confidence match did:

    skill = self.registry.get(intent.skill)
    if skill:
        result = await skill.execute(self.engine, user_id=tg_id, **intent.kwargs)

`skill.execute()` is the raw skill. It is not the command, so the `@guard`
decorator never runs — and `_handle_message` carries no `@guard` of its own. In
350 lines it calls `_is_allowlisted` once and `permission_denial` never. Every
skill the router could name ran for any allowlisted caller, whatever their role.

One of those skills is `halt`. `intent_router.py` maps a single regex —

    halt the bot | stop the bot | stop trading | stop everything |
    emergency stop | kill the bot | pause trading

— to it, and `HaltSkill.execute` trips the shared circuit breaker, halts every
per-user risk engine, clears every pending idea and transitions the engine to
HALTED.

SO BOTH HALVES OF H4 WERE BYPASSABLE BY TYPING ENGLISH.

  * The role separation (self-admission grants `paper`, which does not hold
    `halt`) — bypassed, because this path checks no permission.
  * The operator gate (`_cmd_halt` refuses a non-operator) — bypassed, because
    this path never calls `_cmd_halt`.

A self-admitted stranger typed "stop trading" and stopped trading for every
account. The commands and the buttons were gated; the third door was not
checked. That is the "ask which OTHER surface makes the same claim" discipline
failing on the very fix that quoted it.

THE SAME DEFECT, ALREADY FIXED, ON THE OTHER TRANSPORT

`_WEB_SKILL_PERMISSION` exists because web chat executed any registry skill by
name and a website signup could POST "halt the bot" to `/api/chat`. Correct fix,
scoped to one transport. Telegram free text reached the same skills by the same
English words and never got it. Two transports, one defect, one fixed — so the
table now lives in `bot/skills/skill_permissions.py` and both read it, because a
second copy is how this becomes a third instance.

TWO GATES, NOT ONE, because they answer different questions:

  * `permission_for()` + `permission_denial()` — may this ROLE run this skill?
    Fixes the viewer who could type "backtest BTC" for a backtest /backtest
    refuses them. Unmapped DENIES.
  * `DANGEROUS_SKILLS` — routed to the guarded COMMAND instead of executed.
    A role check alone is not enough for `halt`: `trader` HOLDS `halt`, so a
    permission gate would still have let a vouched-for teammate stop every
    account. The command carries the operator check; free text borrows it
    rather than keeping a copy. `get_orders` was already routed this way.
"""
from __future__ import annotations

import ast
import pathlib
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.skills.skill_permissions import (DANGEROUS_SKILLS, SKILL_PERMISSION,
                                          WEB_CHAT_SKILLS, permission_for)
from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore

REPO = pathlib.Path(__file__).resolve().parent.parent
ROUTER = REPO / "bot" / "nlp" / "intent_router.py"

OPERATOR = "111"
TRADER = "4242"
VIEWER = "5353"
STRANGER = "999"


# ── harness ──────────────────────────────────────────────────────────

class _Risk:
    def __init__(self):
        self.circuit_breaker_active = False
        self.consecutive_losses = 0
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
    from bot.core.engine import RuneClawEngine as _E
    _is_operator_user = _E._is_operator_user
    risk_for = _E.risk_for
    del _E

    def __init__(self):
        self.risk = _Risk()
        self._halted = False
        self._pending_ideas: dict = {}
        self._user_risk: dict = {}
        self._user_store = None
        self.pending_ideas: list = []

    def _sync_risk_market_context(self, eng):
        pass

    def reset_circuit_breaker_all(self):
        self.risk.calls.append("reset_circuit_breaker_all")
        return 1


class _Skill:
    def __init__(self, name, log):
        self.name = name
        self._log = log

    async def execute(self, engine, **kwargs):
        self._log.append(self.name)
        return f"[{self.name} ran]"


class _Registry:
    """Answers for every skill the router can name, so a refusal in these tests
    is always the gate and never a missing fixture."""

    def __init__(self):
        self.executed: list[str] = []
        self.dispatched: list[str] = []

    def get(self, name):
        return _Skill(name, self.executed) if name in SKILL_PERMISSION else None

    async def dispatch(self, name, engine, **kwargs):
        self.dispatched.append(name)
        return "dispatched"


class _Limiter:
    def allow(self, uid):
        return True


class _Conversations:
    def append(self, *a, **kw):
        pass

    def get(self, *a, **kw):
        return []


def _handler(tmp_path):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = _Engine()
    h.engine._user_store = h.users
    h.registry = _Registry()
    h._limiter = _Limiter()
    h.conversations = _Conversations()
    h.forwarder = SimpleNamespace(detect_group=lambda *a, **kw: None)
    h._pending_limit_input = {}
    from bot.nlp.intent_router import IntentRouter
    h.intent_router = IntentRouter()
    h.sent: list[str] = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _request_operator_admission(*a, **kw):
        return False

    async def _send_photo(*a, **kw):
        return False

    h._send = _send
    h._request_operator_admission = _request_operator_admission
    h._send_photo = _send_photo

    h.users.authorize(OPERATOR, role="admin", by=OPERATOR)
    h.users.register(TRADER, name="Vouched")
    h.users.authorize(TRADER, role="trader", by=OPERATOR)
    h.users.register(VIEWER, name="Watcher")
    h.users.authorize(VIEWER, role="viewer", by=OPERATOR)
    h.users.register(STRANGER, name="Walkin")
    h.users.authorize(STRANGER, role=SELF_ADMISSION_ROLE, by=SELF_ADMISSION_BY)
    return h


def _update(uid, text):
    msg = SimpleNamespace(text=text)

    async def _reply(*a, **kw):
        pass

    msg.reply_text = _reply
    chat = SimpleNamespace(id=int(uid), type="private", title="")

    async def _action(*a, **kw):
        pass

    chat.send_chat_action = _action
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid), first_name="T",
                                       language_code="en"),
        effective_chat=chat, message=msg, callback_query=None)


@pytest.fixture
def bot(tmp_path):
    h = _handler(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
    yield h
    patch.stopall()


HALT_PHRASINGS = ["stop trading", "halt the bot", "kill the bot",
                  "emergency stop", "stop everything", "pause trading"]


# ── the router really does route these ───────────────────────────────

def test_the_premise_the_router_maps_these_words_to_halt():
    """Guards the guard. If the regex stopped matching, every refusal below
    would pass for the wrong reason — nothing to refuse."""
    from bot.nlp.intent_router import IntentRouter
    r = IntentRouter()
    matched = [p for p in HALT_PHRASINGS
               if getattr(r.classify_rules(p), "skill", None) == "halt"]
    assert len(matched) >= 4, (
        f"only {matched} still route to the halt skill — this file's premise "
        "moved and its refusals may now be vacuous")


# ── the reported defect ──────────────────────────────────────────────

class TestTypingItDoesNotStopTheBot:
    @pytest.mark.parametrize("phrase", HALT_PHRASINGS)
    @pytest.mark.asyncio
    async def test_a_self_admitted_user_cannot_halt_by_typing(self, bot, phrase):
        await bot._handle_message(_update(STRANGER, phrase), None)
        assert bot.registry.executed == [], (
            f"{phrase!r} executed {bot.registry.executed} for a "
            f"{SELF_ADMISSION_ROLE} user")
        assert bot.registry.dispatched == []
        assert bot.engine.risk.circuit_breaker_active is False, (
            f"typing {phrase!r} tripped the shared circuit breaker")
        assert bot.engine.risk.calls == []

    @pytest.mark.asyncio
    async def test_nor_can_a_vouched_for_trader(self, bot):
        """The H4-second-half principal. `trader` HOLDS `halt`, so a role check
        alone would have let this through — the routed command's operator gate
        is what refuses it."""
        await bot._handle_message(_update(TRADER, "stop trading"), None)
        assert bot.registry.executed == []
        assert bot.registry.dispatched == []
        assert bot.engine.risk.circuit_breaker_active is False

    @pytest.mark.asyncio
    async def test_the_operator_still_can(self, bot):
        """Otherwise this file passes by breaking the feature."""
        await bot._handle_message(_update(OPERATOR, "stop trading"), None)
        assert bot.registry.dispatched == ["halt"], (
            "the operator can no longer halt by typing — the intent was "
            "refused rather than routed")

    @pytest.mark.asyncio
    async def test_it_goes_through_the_command_not_the_skill(self, bot):
        """Routed, not re-gated. The operator's halt must arrive via
        `_cmd_halt`'s dispatch — if free text kept its own copy of the
        authority, the two would drift the moment one changed."""
        seen = []
        real = bot._cmd_halt

        async def spy(update, ctx):
            seen.append("cmd_halt")
            return await real(update, ctx)

        bot._cmd_halt = spy
        await bot._handle_message(_update(OPERATOR, "halt the bot"), None)
        assert seen == ["cmd_halt"], "free text bypassed the guarded command"


# ── the class of defect, not just the instance ───────────────────────

class TestFreeTextObeysTheRoleGate:
    @pytest.mark.asyncio
    async def test_a_viewer_cannot_type_their_way_to_a_backtest(self, bot):
        """`backtest` is not in the viewer set, so /backtest refuses them. Free
        text ran the same skill anyway — the paywall-as-spelling-test shape the
        scan-mode branch above it already names."""
        assert bot.users.permission_denial(VIEWER, "backtest") == "role"
        await bot._handle_message(_update(VIEWER, "run a backtest on BTC"), None)
        assert "run_backtest" not in bot.registry.executed

    @pytest.mark.asyncio
    async def test_but_a_trader_still_can(self, bot):
        await bot._handle_message(_update(TRADER, "run a backtest on BTC"), None)
        assert "run_backtest" in bot.registry.executed, (
            "the role gate refused a role that holds the permission")

    @pytest.mark.asyncio
    async def test_a_read_only_intent_still_works_for_a_viewer(self, bot):
        """The gate must narrow to the role, not to the strictest role."""
        await bot._handle_message(_update(VIEWER, "scan the market"), None)
        assert bot.registry.executed, "a viewer lost a skill their role holds"

    @pytest.mark.asyncio
    async def test_the_refusal_says_why(self, bot):
        bot.sent.clear()
        await bot._handle_message(_update(VIEWER, "run a backtest on BTC"), None)
        assert bot.sent, "silence — reads as a broken bot"
        assert "viewer" in " ".join(bot.sent).lower()

    @pytest.mark.asyncio
    async def test_an_unmapped_skill_is_denied_not_allowed(self, bot):
        """Fail closed, matching the web path. A skill added later must be
        unreachable from free text until somebody decides what it needs."""
        fake = SimpleNamespace(matched=True, confidence=0.99, skill="some_new_skill",
                               kwargs={}, source="test")
        bot.intent_router = SimpleNamespace(classify_rules=lambda _t: fake)
        bot.registry.get = lambda n: _Skill(n, bot.registry.executed)
        await bot._handle_message(_update(OPERATOR, "do the new thing"), None)
        assert bot.registry.executed == [], (
            "an unmapped skill ran for an admin — unmapped must DENY, or the "
            "next skill added arrives ungated and silently")


# ── the table is one table, and it is derived ────────────────────────

class TestTheTableDoesNotDrift:
    def test_the_web_map_is_unchanged_by_the_move(self):
        """The H3 fix moved `_WEB_SKILL_PERMISSION` into a shared module. Moving
        a security table is exactly when its contents quietly change, so every
        pair is pinned here rather than trusted.

        WRITTEN OUT, NOT DERIVED, and that is the point: this assertion is
        supposed to fail whenever the table grows. It did, on the two macro
        cards below, which is a decision being surfaced rather than a
        regression — a derived assertion would have accepted them in silence,
        which on a permission table is the whole failure mode.
        """
        gw = pytest.importorskip("bot.web.user_gateway",
                                 reason="web gateway needs aiohttp")
        assert gw._WEB_SKILL_PERMISSION == {
            "analyze_asset": "analyze", "check_risk": "risk", "costs": "costs",
            "deepscan": "deepscan", "get_portfolio": "portfolio",
            "learning": "learn", "macro_calendar": "macro",
            "optimize": "optimize", "patterns": "patterns",
            "playbook": "playbook", "pro_scan": "scan", "proposals": "proposals",
            "rejected_trades": "rejected", "run_backtest": "backtest",
            "run_strategy": "run", "scan_market": "scan",
            "walk_forward": "walkforward", "whynot": "rejected",
            # Added when /eventrisk and /compliance were wired. Reachability is
            # not authorisation: both arrive on web chat and the ROLE gate is
            # what refuses. `check_event_risk` reuses `macro` (trader and paper
            # hold it); `compliance_status` takes a permission no role but
            # admin holds, because the card renders the global consent ledger.
            "check_event_risk": "macro",
            "compliance_status": "compliance",
            # The third macro card. The macro GATE's posture (risk state, the
            # size multiplier on new entries, stale/blind) — the same
            # read-only data as the calendar, so the same permission, and a
            # sub-mode of the same command (`/macro brief`) because the one
            # it advertised, /macro, is the calendar's. A chat tool too.
            "macro_brief": "macro",
        }

    def test_halt_reaches_no_chat_transport(self):
        gw = pytest.importorskip("bot.web.user_gateway",
                                 reason="web gateway needs aiohttp")
        assert "halt" not in gw._WEB_SKILL_PERMISSION
        assert "halt" not in WEB_CHAT_SKILLS
        assert "halt" in DANGEROUS_SKILLS, (
            "halt is neither web-reachable nor routed to its guarded command — "
            "one of the two must hold it or free text silently drops it")

    def test_every_router_skill_the_registry_knows_is_declared(self):
        """Derived from the router's own rules. A new intent rule pointing at a
        real skill fails here until somebody declares its permission — which is
        the whole reason this file exists."""
        src = ROUTER.read_text(encoding="utf-8")
        code = "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))
        emitted = set(re.findall(
            r'_rule\(\s*r?["\'].*?["\']\s*,\s*["\']([a-z_0-9]+)["\']', code, re.S))
        assert len(emitted) > 15, f"only {len(emitted)} intent rules parsed — extractor broke"

        from bot.skills.skill_registry import build_default_registry
        registry = build_default_registry()
        undeclared = sorted(s for s in emitted
                            if registry.get(s) is not None
                            and permission_for(s) is None)
        assert not undeclared, (
            f"the intent router can name these real skills and nothing declares "
            f"what they need: {undeclared}\n"
            f"Add them to SKILL_PERMISSION (and to DANGEROUS_SKILLS if they "
            f"mutate shared state).")

    def test_each_declared_permission_matches_its_commands_guard(self):
        """`SKILL_PERMISSION` claims to be derived from the `@guard` decorators.
        Checked, not asserted in a docstring: for every skill some @guard-ed
        handler dispatches, the permission here must be that handler's."""
        # Every file that contributes methods to the handler class, not just
        # telegram_handler.py: a guarded command that moved into a mixin
        # still dispatches a skill, and the pairing derived here must see it.
        from tests.source_scan import handler_sources
        nodes = [node for path in handler_sources()
                 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))]
        from_guard: dict[str, set[str]] = {}
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            perm = next((d.args[0].value for d in node.decorator_list
                         if isinstance(d, ast.Call)
                         and getattr(d.func, "id", "") == "guard"
                         and d.args and isinstance(d.args[0], ast.Constant)), None)
            if perm is None:
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and getattr(sub.func, "attr", "") == "dispatch"
                        and sub.args and isinstance(sub.args[0], ast.Constant)):
                    from_guard.setdefault(sub.args[0].value, set()).add(perm)
        assert len(from_guard) > 10, f"only {len(from_guard)} skill→guard pairs found"

        for skill, perms in sorted(from_guard.items()):
            declared = permission_for(skill)
            if declared is None or len(perms) != 1:
                continue          # undeclared is the other test; ambiguous is not drift
            assert declared in perms, (
                f"SKILL_PERMISSION says {skill!r} needs {declared!r}, but the "
                f"command dispatching it is @guard({perms.pop()!r})")

    def test_dangerous_skills_name_real_handlers(self):
        from bot.skills.telegram_handler import TelegramHandler
        for skill, method in DANGEROUS_SKILLS.items():
            assert hasattr(TelegramHandler, method), (
                f"DANGEROUS_SKILLS routes {skill!r} to {method!r}, which does "
                "not exist — free text would raise instead of refusing")

"""Two doors opened deliberately, and the wall between them.

The operator asked for paper trading to auto-accept new traders, and for live
trading to be open to all traders. Those are very different grants and the
whole risk lives in not letting the first one imply the second.

WHOSE MONEY. `engine._executor_for` falls back to the shared OPERATOR executor
for a user with no linked keys — by design, and its docstring says the
eligibility policy is layered on top. So "open live trading" has exactly one
safe reading: a trader may go live once they bring their OWN account. Anything
else points strangers at the operator's balance, and a filled order is not
recoverable.

THE MASTER SWITCH IS PART OF THE RULE. With PER_USER_LIVE_ENABLED off,
`_executor_for` returns the operator executor for everyone and
`per_user_live_eligibility` is a documented no-op — so opening the policy alone
would do precisely the thing the policy exists to prevent. Both halves refuse
non-operators in that state rather than trusting two env vars to be set
consistently.

RED HERRING, in TestARevokeIsADecisionNotADefault: `register()` writes
`can_trade_live: False` on every new account as the paper-only default. Reading
that as "revoked" is the obvious implementation and it bans the entire user base
the moment live opens. The first draft here did exactly that.
"""
from __future__ import annotations

import dataclasses
import types

import pytest

import bot.config as cfg_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine


def _cfg(monkeypatch, *, per_user=True, open_live=True):
    new = dataclasses.replace(CONFIG, per_user_live_enabled=per_user,
                              live_open_to_key_holders=open_live)
    monkeypatch.setattr(cfg_mod, "CONFIG", new)
    monkeypatch.setattr("bot.core.engine.CONFIG", new, raising=False)
    monkeypatch.setattr("bot.skills.telegram_handler.CONFIG", new, raising=False)
    return new


def _keys(monkeypatch, holders):
    fake = types.SimpleNamespace(get=lambda uid: ("k" if str(uid) in holders else None))
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: fake)


class _Store:
    """The two questions the gate asks, answered independently."""

    def __init__(self, revoked=(), granted=()):
        self._revoked, self._granted = set(revoked), set(granted)

    def live_trading_revoked(self, uid):
        return str(uid) in self._revoked

    def can_trade_live(self, uid):
        return str(uid) in self._granted


def _engine(monkeypatch, *, store=None, operator=()):
    eng = RuneClawEngine()
    eng._user_store = store if store is not None else _Store()
    monkeypatch.setattr(type(eng), "_is_operator_user",
                        lambda _s, uid: str(uid) in {str(o) for o in operator},
                        raising=False)
    return eng


class TestLiveExecutionRequiresYourOwnAccount:
    def test_a_key_holder_is_eligible_with_no_admin_step(self, monkeypatch):
        _cfg(monkeypatch)
        _keys(monkeypatch, {"222"})
        ok, reason = _engine(monkeypatch).per_user_live_eligibility("222")
        assert ok is True, reason

    def test_a_keyless_trader_is_rejected(self, monkeypatch):
        """The one that matters: without keys their order fills on the
        operator's account."""
        _cfg(monkeypatch)
        _keys(monkeypatch, set())
        ok, reason = _engine(monkeypatch).per_user_live_eligibility("333")
        assert ok is False
        assert "connect" in reason.lower()

    def test_an_explicit_revoke_beats_owning_keys(self, monkeypatch):
        _cfg(monkeypatch)
        _keys(monkeypatch, {"444"})
        eng = _engine(monkeypatch, store=_Store(revoked={"444"}))
        ok, reason = eng.per_user_live_eligibility("444")
        assert ok is False and "revoked" in reason.lower()

    def test_the_operator_still_trades_the_shared_account(self, monkeypatch):
        _cfg(monkeypatch)
        _keys(monkeypatch, set())
        eng = _engine(monkeypatch, operator={"111"})
        ok, _ = eng.per_user_live_eligibility("111")
        assert ok is True, "the operator must not need to link keys to themselves"

    def test_an_unreadable_store_denies_rather_than_assuming_nobody_is_banned(
            self, monkeypatch):
        """Fail-closed matters MORE under the open policy, not less: 'no store'
        must never read as 'no revokes exist'."""
        _cfg(monkeypatch)
        _keys(monkeypatch, {"555"})
        eng = _engine(monkeypatch)
        eng._user_store = None
        ok, reason = eng.per_user_live_eligibility("555")
        assert ok is False and "fail-closed" in reason

    def test_a_raising_store_denies_too(self, monkeypatch):
        _cfg(monkeypatch)
        _keys(monkeypatch, {"556"})

        class _Boom:
            def live_trading_revoked(self, uid):
                raise OSError("store unreadable")
        ok, _ = _engine(monkeypatch, store=_Boom()).per_user_live_eligibility("556")
        assert ok is False

    def test_the_staged_rollout_is_restored_when_the_switch_is_off(self, monkeypatch):
        _cfg(monkeypatch, open_live=False)
        _keys(monkeypatch, {"777"})
        eng = _engine(monkeypatch, store=_Store(granted=set()))
        ok, reason = eng.per_user_live_eligibility("777")
        assert ok is False and "grant_live" in reason
        eng._user_store = _Store(granted={"777"})
        assert eng.per_user_live_eligibility("777")[0] is True

    def test_with_the_master_switch_off_the_gate_is_a_noop(self, monkeypatch):
        """Documented: it returns ok because EVERY order is the operator's in
        that mode. `_can_trade_live` is what must refuse non-operators there,
        and TestTheMasterSwitchIsPartOfTheRule pins that."""
        _cfg(monkeypatch, per_user=False)
        _keys(monkeypatch, set())
        ok, _ = _engine(monkeypatch).per_user_live_eligibility("888")
        assert ok is True


class TestARevokeIsADecisionNotADefault:
    def test_the_paper_default_is_not_a_revoke(self, tmp_path):
        from bot.utils.user_store import UserStore
        s = UserStore(str(tmp_path / "u.json"))
        s.register(901, name="fresh")
        assert s.get(901).get("can_trade_live") is False
        assert s.live_trading_revoked(901) is False, (
            "reading the paper-only default as a ban locks out every trader")

    def test_a_revoke_is_one(self, tmp_path):
        from bot.utils.user_store import UserStore
        s = UserStore(str(tmp_path / "u.json"))
        s.register(902, name="banned")
        s.set_live_trading(902, False)
        assert s.live_trading_revoked(902) is True

    def test_a_grant_clears_it(self, tmp_path):
        from bot.utils.user_store import UserStore
        s = UserStore(str(tmp_path / "u.json"))
        s.register(903, name="x")
        s.set_live_trading(903, False)
        s.set_live_trading(903, True)
        assert s.live_trading_revoked(903) is False

    def test_it_survives_a_restart(self, tmp_path):
        from bot.utils.user_store import UserStore
        path = str(tmp_path / "u.json")
        UserStore(path).register(904, name="banned")
        UserStore(path).set_live_trading(904, False)
        assert UserStore(path).live_trading_revoked(904) is True, (
            "a ban held only in memory lifts itself on the next restart")


class TestPaperAutoAcceptGrantsBotAccessOnly:
    def test_a_newcomer_is_admitted(self, tmp_path):
        from bot.utils.user_store import UserStore
        s = UserStore(str(tmp_path / "u.json"))
        s.register(1001, name="newcomer")
        s.authorize(1001, role="trader", by="auto-accept")
        assert s.is_admitted(1001) is True

    def test_the_admission_records_that_no_human_vouched(self, tmp_path):
        """F-2 exists because `register()` could not name an approver. A door
        that forged one would be worse than the hole it replaced — /users must
        still separate a vouched-for member from one the door let in."""
        from bot.utils.user_store import UserStore
        s = UserStore(str(tmp_path / "u.json"))
        s.register(1002, name="newcomer")
        s.authorize(1002, role="trader", by="auto-accept")
        assert s.get(1002)["admitted_by"] == "auto-accept"

    def test_it_does_not_grant_live_trading(self, tmp_path):
        from bot.utils.user_store import UserStore
        s = UserStore(str(tmp_path / "u.json"))
        s.register(1003, name="newcomer")
        s.authorize(1003, role="trader", by="auto-accept")
        assert s.can_trade_live(1003) is False

    def test_and_the_live_gate_still_refuses_them_without_keys(self, monkeypatch,
                                                               tmp_path):
        """The wall. Self-admission plus the open live policy must still not
        reach the operator's balance."""
        from bot.utils.user_store import UserStore
        _cfg(monkeypatch)
        _keys(monkeypatch, set())
        s = UserStore(str(tmp_path / "u.json"))
        s.register(1004, name="newcomer")
        s.authorize(1004, role="trader", by="auto-accept")
        eng = _engine(monkeypatch, store=s)
        assert eng.per_user_live_eligibility("1004")[0] is False

    def test_the_admission_survives_a_restart(self, tmp_path):
        from bot.utils.user_store import UserStore
        path = str(tmp_path / "u.json")
        s = UserStore(path)
        s.register(1005, name="newcomer")
        s.authorize(1005, role="trader", by="auto-accept")
        assert UserStore(path).is_admitted(1005) is True, (
            "the failure that started this: 40+ members became 1 across restarts")


class TestTheMasterSwitchIsPartOfTheRule:
    """With PER_USER_LIVE_ENABLED off every order routes to the operator, so the
    handler-side authority must refuse non-operators outright — the
    misconfiguration is made unreachable, not merely discouraged."""

    def _handler(self):
        from bot.skills.telegram_handler import TelegramHandler
        return TelegramHandler(RuneClawEngine())

    def test_a_key_holder_is_refused_while_the_switch_is_off(self, monkeypatch):
        _cfg(monkeypatch, per_user=False, open_live=True)
        _keys(monkeypatch, {"222"})
        h = self._handler()
        h.users.register(222, name="trader")
        assert h._can_trade_live(222) is False

    def test_and_allowed_once_it_is_on(self, monkeypatch):
        _cfg(monkeypatch, per_user=True, open_live=True)
        _keys(monkeypatch, {"222"})
        h = self._handler()
        h.users.register(222, name="trader")
        assert h._can_trade_live(222) is True


class TestTheGuardActuallyAdmits:
    """The renderer/store tests above prove the pieces. This drives `_guard`
    itself, because "the code is present" and "the code is reached" are the two
    things a source scan cannot tell apart — and this repo has paid for that.
    """

    def _run(self, monkeypatch, *, auto_accept, allowlist="111", tg="4242"):
        import asyncio
        from bot.skills.telegram_handler import TelegramHandler
        new = dataclasses.replace(
            CONFIG,
            telegram=dataclasses.replace(CONFIG.telegram, chat_id=allowlist,
                                         admin_ids=""),
            paper_auto_accept=auto_accept)
        monkeypatch.setattr(cfg_mod, "CONFIG", new)
        monkeypatch.setattr("bot.skills.telegram_handler.CONFIG", new, raising=False)
        monkeypatch.setattr("bot.core.engine.CONFIG", new, raising=False)

        h = TelegramHandler(RuneClawEngine())
        sent = []

        async def _send(_u, text, **_k):
            sent.append(text)
        monkeypatch.setattr(h, "_send", _send)

        async def _no_ping(*_a, **_k):
            return False
        monkeypatch.setattr(h, "_request_operator_admission", _no_ping)

        update = types.SimpleNamespace(
            effective_user=types.SimpleNamespace(id=int(tg), first_name="Newcomer",
                                                 language_code="en"),
            effective_chat=types.SimpleNamespace(id=int(tg)),
            message=types.SimpleNamespace(text="/scan"))
        allowed = asyncio.run(h._guard(update, "scan", None))
        return h, allowed, sent

    def test_a_newcomer_is_let_in_rather_than_turned_away(self, monkeypatch):
        h, allowed, sent = self._run(monkeypatch, auto_accept=True)
        assert h.users.is_admitted("4242") is True, "auto-accept never ran"
        assert not any("locked" in s.lower() for s in sent), sent

    def test_the_admission_names_the_door_not_an_admin(self, monkeypatch):
        h, _, _ = self._run(monkeypatch, auto_accept=True)
        assert h.users.get("4242")["admitted_by"] == "auto-accept"

    def test_with_auto_accept_off_they_are_still_turned_away(self, monkeypatch):
        h, allowed, sent = self._run(monkeypatch, auto_accept=False)
        assert allowed is False
        assert h.users.is_admitted("4242") is False
        assert sent, "a refused newcomer must be told something"

    def test_auto_accept_does_not_make_them_an_admin(self, monkeypatch):
        h, _, _ = self._run(monkeypatch, auto_accept=True)
        assert h.users.get("4242").get("role") == "trader"
        assert h._is_admin_id("4242") is False


class TestTheRefusalNamesTheStepThatActuallyWorks:
    """A member refused live trading is told what to do next. Under the open
    policy an admin grant does nothing — bringing your own account IS the
    opt-in — so "ask an admin for /grant_live" sends them to someone who cannot
    help. The gate changed; the copy has to change with it or the bot lies at
    exactly the moment somebody is trying to comply.
    """

    def _handler(self):
        from bot.skills.telegram_handler import TelegramHandler
        return TelegramHandler(RuneClawEngine())

    def test_the_open_policy_points_at_connect(self, monkeypatch):
        from bot.utils.i18n import t
        _cfg(monkeypatch, open_live=True)
        text = t(self._handler()._live_refusal_key(), "en")
        assert "/connect" in text
        assert "/grant_live" not in text

    def test_the_staged_rollout_still_points_at_an_admin(self, monkeypatch):
        from bot.utils.i18n import t
        _cfg(monkeypatch, open_live=False)
        text = t(self._handler()._live_refusal_key(), "en")
        assert "/grant_live" in text

    def test_it_says_whose_account_the_order_would_hit(self, monkeypatch):
        """The reassurance that makes /connect make sense — and the promise the
        gate actually keeps."""
        from bot.utils.i18n import t
        _cfg(monkeypatch, open_live=True)
        assert "your" in t(self._handler()._live_refusal_key(), "en").lower()

    def test_both_refusals_are_translated(self, monkeypatch):
        from bot.utils.i18n import t
        for key in ("live_not_enabled", "live_needs_own_account"):
            for lang in ("en", "zh"):
                assert t(key, lang), f"{key}/{lang} missing"
        assert t("live_needs_own_account", "zh") != t("live_needs_own_account", "en")

    def test_every_site_uses_the_shared_chooser(self):
        """Three call sites, one rule. A hardcoded string at any of them is a
        second policy that stops agreeing the moment the switch moves."""
        import pathlib
        for path in ("bot/skills/telegram_handler.py", "bot/skills/scan_skill.py"):
            src = pathlib.Path(path).read_text(encoding="utf-8")
            assert "An admin must grant it with /grant_live" not in src, (
                f"{path} hardcodes the staged-rollout refusal")
        h = pathlib.Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        assert h.count("self._live_refusal_key()") >= 2


def test_every_live_trade_double_answers_both_questions():
    """The live gate now asks the store TWO questions, and a double that
    answers one of them fails somewhere else entirely.

    `_can_trade_live` and `per_user_live_eligibility` ask "is this user
    granted?" and "has an operator revoked them?" — separate because
    `register()` writes `can_trade_live: False` on every account as the
    paper-only default, so one flag cannot carry both facts.

    Adding the second question broke three suites at once. `_Users` in
    test_live_trader_allowlist.py implements `can_trade_live` and nothing else,
    and the miss surfaced as an AttributeError two files away. The existing
    scan in test_user_admission.py could not have caught it twice over: it
    derives its requirement from bot/web/user_gateway.py (this caller is the
    handler) and triggers on classes implementing `has_permission` (this double
    implements neither).

    So this triggers on the shape that actually matters — a double standing in
    for the live-trade surface — and derives the pair from the callers, so a
    third question added later is covered without anyone updating a list.

    It cannot catch a MagicMock store, which answers anything with a truthy
    Mock and so silently REVOKES everyone while passing. That is why the suites
    using one now set the policy explicitly rather than inheriting it.
    """
    import ast
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent
    callers = ("bot/skills/telegram_handler.py", "bot/core/engine.py")
    asked = set()
    for rel in callers:
        src = (root.parent / rel).read_text(encoding="utf-8")
        asked |= set(re.findall(r"(?:users|store)\.((?:can_trade_live|live_trading_revoked))\(",
                                src))
    assert asked == {"can_trade_live", "live_trading_revoked"}, (
        f"the gate stopped asking both questions ({asked}) — if that is "
        "deliberate this scan should change with it")

    offenders = []
    for path in sorted(root.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = {n.name for n in node.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if "can_trade_live" in names and "live_trading_revoked" not in names:
                offenders.append(f"{path.name}::{node.name} (line {node.lineno})")
    assert not offenders, (
        "these stand in for the user store's live-trade surface but answer "
        "only half of what the gate asks:\n  " + "\n  ".join(offenders))

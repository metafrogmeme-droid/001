"""Paper is the main mode; live is the operator's alone; `/golive OFF` means it.

The owner's call on 2026-10-18: "live is admin only, paper is the main for
now". Before it, `/golive OFF` cleared only the runtime arm and the
LIVE_TRADE permission. A deployment that sets live in its environment stayed
live (`CONFIG.is_live()` read the environment), the operator's next confirm
took the live branch and compliance refused it for the permission OFF had just
removed, and the reply had said "Reverted to paper-trade mode". Nothing was
paper-traded.

Now:
  * OFF saves a suspension (memory and disk) that outranks the environment in
    `is_live()` and survives a restart. A file that cannot be read is
    suspended (fail closed); absent is not.
  * While suspended every confirm a person taps is a paper fill
    (`confirm_is_practice`), the operator's included. An unattended confirm
    ("auto") is not a person and places nothing, and says so.
  * While live, a person who is not an operator confirms onto their paper
    book (LIVE_ADMIN_ONLY, default on). The operator's confirm stays live.
  * OFF is refused while the live book holds a position or resting entry,
    or cannot be read: turning live off over a position leaves it unmanaged.
  * CONFIRM clears the suspension before it arms, and refuses to claim it
    armed when the clear did not reach disk.
  * The mode card reads PAPER, not IDLE, while suspended.

Both arms each.
"""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from bot.config import CONFIG, RUNTIME, RuntimeState
from bot.core import live_readiness
from bot.skills.engine_ops_commands import live_book_open

OPERATOR = "6307156912"
MEMBER = "424242"


@pytest.fixture
def suspended_file(tmp_path, monkeypatch):
    """Point the suspension at a file this test owns."""
    path = tmp_path / "state" / "live_suspended.json"
    monkeypatch.setattr(RuntimeState, "_live_suspended_path", classmethod(lambda cls: path))
    return path


def _set(name, value):
    """Set a field on the frozen CONFIG; `_restore_config` hands it back."""
    _UNDO.append((name, getattr(CONFIG, name)))
    object.__setattr__(CONFIG, name, value)


_UNDO: list = []


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    while _UNDO:
        name, old = _UNDO.pop()
        object.__setattr__(CONFIG, name, old)


@pytest.fixture
def env_live():
    """A deployment whose ENVIRONMENT says live, with an operator chat set."""
    _set("live_trading_enabled", True)
    _set("simulation_mode", False)
    _set("telegram", dataclasses.replace(CONFIG.telegram, chat_id=OPERATOR, admin_ids=""))
    RUNTIME.live_mode = False
    return CONFIG


# ── the suspension itself ────────────────────────────────────────────────────

class TestTheSuspensionIsKept:
    def test_absent_is_not_suspended_and_off_survives_a_restart(self, suspended_file):
        assert RuntimeState().live_suspended is False, "no file is no suspension"
        assert RuntimeState().set_live_suspended(True, by=OPERATOR) is True
        assert json.loads(suspended_file.read_text())["suspended"] is True
        # A fresh RuntimeState is a restart: memory is gone, the disk answers.
        assert RuntimeState().live_suspended is True

    def test_clearing_it_removes_the_file(self, suspended_file):
        rt = RuntimeState()
        rt.set_live_suspended(True)
        assert rt.set_live_suspended(False) is True
        assert not suspended_file.exists()
        assert RuntimeState().live_suspended is False

    @pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"suspended": null}'])
    def test_a_file_that_cannot_be_read_is_suspended(self, suspended_file, raw):
        suspended_file.parent.mkdir(parents=True)
        suspended_file.write_text(raw)
        assert RuntimeState().live_suspended is True, "unreadable is not 'live may run'"

    def test_an_explicit_false_is_not_suspended(self, suspended_file):
        suspended_file.parent.mkdir(parents=True)
        suspended_file.write_text('{"suspended": false}')
        assert RuntimeState().live_suspended is False

    def test_a_write_that_failed_still_holds_in_memory_and_says_so(self, tmp_path, monkeypatch):
        blocker = tmp_path / "a_file"
        blocker.write_text("x")
        monkeypatch.setattr(RuntimeState, "_live_suspended_path",
                            classmethod(lambda cls: blocker / "live_suspended.json"))
        rt = RuntimeState()
        assert rt.set_live_suspended(True) is False, "the disk write failed and must say so"
        assert rt.live_suspended is True, "memory holds it until a restart"


class TestIsLive:
    def test_the_environment_is_live_until_the_operator_turns_it_off(self, suspended_file, env_live):
        assert CONFIG.is_live() is True
        RUNTIME.set_live_suspended(True)
        assert CONFIG.is_live() is False, "OFF outranks the environment"
        RUNTIME.set_live_suspended(False)
        assert CONFIG.is_live() is True


# ── whose confirm is paper ───────────────────────────────────────────────────

def _engine():
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._user_store = None
    return eng


class TestWhoseConfirmIsPaper:
    def test_suspended_every_person_is_paper_and_the_unattended_is_not(self, suspended_file):
        eng = _engine()
        assert eng.confirm_is_practice(OPERATOR) is False
        RUNTIME.set_live_suspended(True)
        assert eng.confirm_is_practice(OPERATOR) is True, "the operator's tap is paper too"
        assert eng.confirm_is_practice(MEMBER) is True
        for unattended in ("auto", ""):
            assert eng.confirm_is_practice(unattended) is False, "auto is not a person's book"

    def test_live_a_member_is_paper_and_the_operator_is_live(self, suspended_file, env_live):
        eng = _engine()
        assert CONFIG.is_live() is True
        assert eng.confirm_is_practice(MEMBER) is True
        assert eng.confirm_is_practice(OPERATOR) is False

    def test_an_admin_in_the_store_is_an_operator(self, suspended_file, env_live):
        eng = _engine()
        eng._user_store = SimpleNamespace(get=lambda uid: {"role": "admin"} if uid == MEMBER else None)
        assert eng.confirm_is_practice(MEMBER) is False

    def test_admin_only_off_restores_the_old_reading(self, suspended_file, env_live):
        _set("live_admin_only", False)
        eng = _engine()
        assert eng.confirm_is_practice(MEMBER) is False, "the per-user policies decide again"

    def test_not_live_and_not_suspended_changes_nothing(self, suspended_file):
        assert CONFIG.is_live() is False
        assert _engine().confirm_is_practice(MEMBER) is False


# ── the mode card ────────────────────────────────────────────────────────────

class TestTheModeCard:
    def test_suspended_reads_paper_and_otherwise_idle(self, suspended_file):
        cfg = SimpleNamespace(is_live=lambda: False, simulation_mode=False)
        assert live_readiness.mode_label(cfg) == "IDLE"
        RUNTIME.set_live_suspended(True)
        assert live_readiness.mode_label(cfg) == "PAPER"

    def test_live_still_reads_live(self, suspended_file):
        cfg = SimpleNamespace(is_live=lambda: True, simulation_mode=False)
        RUNTIME.set_live_suspended(True)
        assert live_readiness.mode_label(cfg) == "LIVE"


# ── /golive OFF and CONFIRM ──────────────────────────────────────────────────

def _pos(symbol, status):
    return SimpleNamespace(symbol=symbol, status=status)


class TestTheLiveBookReading:
    def test_open_resting_and_closing_count_and_closed_does_not(self):
        ex = SimpleNamespace(_positions={
            "a": _pos("BTC/USDT", "open"), "b": _pos("ETH/USDT", "pending_fill"),
            "c": _pos("SOL/USDT", "closing"), "d": _pos("XRP/USDT", "closed"),
            "e": _pos("ADA/USDT", "error")})
        assert sorted(live_book_open(ex)) == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def test_no_executor_is_flat_and_an_unread_book_is_none(self):
        assert live_book_open(None) == []

        class Broken:
            @property
            def _positions(self):
                raise RuntimeError("boom")
        assert live_book_open(Broken()) is None


def _handler():
    from tests.test_telegram_commands import _make_handler
    return _make_handler()


def _update(args):
    from tests.test_telegram_commands import _make_update
    return _make_update(text="/golive " + " ".join(args), args=args)


def _said(update) -> str:
    return "\n".join((c[0][0] if c[0] else c.kwargs.get("text", ""))
                     for c in update.message.reply_text.call_args_list)


class TestGoliveOff:
    @pytest.mark.asyncio
    async def test_off_with_a_flat_book_turns_live_off_for_real(self, suspended_file, env_live):
        from bot.compliance.compliance_engine import Permission
        h = _handler()
        h.engine.live_executor._positions = {}
        h.engine.compliance_profile.permissions.add(Permission.LIVE_TRADE)
        update, ctx = _update(["OFF"])
        await h._cmd_golive(update, ctx)
        assert RUNTIME.live_suspended is True and suspended_file.exists()
        assert CONFIG.is_live() is False, "the environment no longer arms it"
        assert Permission.LIVE_TRADE not in h.engine.compliance_profile.permissions
        text = _said(update)
        assert "LIVE TRADING OFF" in text and "paper book" in text
        assert "stays off across restarts" in text

    @pytest.mark.asyncio
    async def test_off_over_an_open_position_changes_nothing_and_names_it(self, suspended_file, env_live):
        h = _handler()
        h.engine.live_executor._positions = {"p": _pos("BTC/USDT", "open")}
        update, ctx = _update(["OFF"])
        await h._cmd_golive(update, ctx)
        assert RUNTIME.live_suspended is False and not suspended_file.exists()
        assert CONFIG.is_live() is True
        text = _said(update)
        assert "Live stays ON" in text and "BTC/USDT" in text
        assert "LIVE TRADING OFF" not in text

    @pytest.mark.asyncio
    async def test_off_over_an_unread_book_changes_nothing(self, suspended_file, env_live, monkeypatch):
        import bot.skills.engine_ops_commands as eoc
        monkeypatch.setattr(eoc, "live_book_open", lambda ex: None)
        h = _handler()
        update, ctx = _update(["OFF"])
        await h._cmd_golive(update, ctx)
        assert RUNTIME.live_suspended is False
        assert "could not be read" in _said(update)

    @pytest.mark.asyncio
    async def test_off_that_could_not_be_saved_says_a_restart_re_arms(self, tmp_path, monkeypatch, env_live):
        blocker = tmp_path / "a_file"
        blocker.write_text("x")
        monkeypatch.setattr(RuntimeState, "_live_suspended_path",
                            classmethod(lambda cls: blocker / "live_suspended.json"))
        h = _handler()
        h.engine.live_executor._positions = {}
        update, ctx = _update(["OFF"])
        await h._cmd_golive(update, ctx)
        assert CONFIG.is_live() is False, "memory holds it"
        text = _said(update)
        assert "could not be saved" in text and "stays off across restarts" not in text


class TestGoliveConfirm:
    @pytest.mark.asyncio
    async def test_confirm_clears_the_suspension_before_it_arms(self, suspended_file, env_live, monkeypatch):
        monkeypatch.setattr(live_readiness, "from_engine",
                            lambda *a, **k: {"can_execute": True, "blockers": [], "unverified": []})
        RUNTIME.set_live_suspended(True)
        h = _handler()
        update, ctx = _update(["CONFIRM"])
        await h._cmd_golive(update, ctx)
        assert RUNTIME.live_suspended is False and not suspended_file.exists()
        assert RUNTIME.live_mode is True and CONFIG.is_live() is True
        assert "LIVE TRADING ARMED" in _said(update)

    @pytest.mark.asyncio
    async def test_confirm_whose_clear_failed_arms_nothing(self, suspended_file, env_live, monkeypatch):
        from bot.compliance.compliance_engine import Permission
        monkeypatch.setattr(live_readiness, "from_engine",
                            lambda *a, **k: {"can_execute": True, "blockers": [], "unverified": []})
        monkeypatch.setattr(RuntimeState, "set_live_suspended", lambda self, v, by="": False)
        h = _handler()
        h.engine.compliance_profile.permissions.discard(Permission.LIVE_TRADE)
        update, ctx = _update(["CONFIRM"])
        await h._cmd_golive(update, ctx)
        assert RUNTIME.live_mode is False
        assert Permission.LIVE_TRADE not in h.engine.compliance_profile.permissions
        text = _said(update)
        assert "Live is still OFF" in text and "LIVE TRADING ARMED" not in text


# ── an unattended confirm while live is off ─────────────────────────────────

class TestTheUnattendedRefusal:
    """Driven to the branch through the real confirm: an idea pending, the
    re-check passing, the engine not live."""

    def _drive(self, monkeypatch, tmp_path, user_id):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from bot.core.confirm_result import placed_nothing, refusal_words
        from tests.test_a_live_decision_is_recorded_as_live import _confirming_engine
        monkeypatch.setattr("bot.utils.website_sync.sync_in_background", lambda *a, **k: None)
        engine, idea = _confirming_engine(tmp_path)
        loop = asyncio.new_event_loop()
        try:
            with patch("bot.core.engine.get_exchange_position_count", new=AsyncMock(return_value=0)), \
                 patch("bot.core.engine.invalidate_position_count_cache"):
                out = loop.run_until_complete(engine.confirm_trade(idea.id, user_id=user_id))
        finally:
            loop.close()
        engine.live_executor.execute.assert_not_awaited()
        return out, placed_nothing(out), refusal_words(out)

    def test_suspended_it_names_live_off(self, suspended_file, monkeypatch, tmp_path):
        RUNTIME.set_live_suspended(True)
        out, nothing, words = self._drive(monkeypatch, tmp_path, "auto")
        assert nothing and "/golive OFF" in out and "LIVE-ONLY" not in out, out
        assert words == "the engine was not live, so nothing could be placed"

    def test_not_suspended_it_is_the_live_only_sentence(self, suspended_file, monkeypatch, tmp_path):
        out, nothing, _ = self._drive(monkeypatch, tmp_path, "auto")
        assert nothing and "LIVE-ONLY" in out, out

    def test_suspended_a_person_s_tap_is_a_paper_fill(self, suspended_file, monkeypatch, tmp_path):
        RUNTIME.set_live_suspended(True)
        out, nothing, _ = self._drive(monkeypatch, tmp_path, OPERATOR)
        assert "[PAPER]" in out and not nothing, out


# ── the engine's own confirms while live is off ─────────────────────────────
#
# Reported the same day: "/golive OFF works but I don't see open paper orders
# or positions". The tick and /forcescan confirm under "auto", and every one
# ended "an unattended confirm places nothing", so the paper book stayed empty.
# They open on the OPERATOR's paper book now, the one /positions reads for
# them, and "auto" is still not a person (`_human_confirmed`).

def _operator_configured(ids=OPERATOR + ",999"):
    _set("telegram", dataclasses.replace(CONFIG.telegram, chat_id=ids, admin_ids=""))


class TestTheEnginesOwnConfirmsOpenOnTheOperatorsPaperBook:
    def test_the_owner_is_the_first_operator_only_while_live_is_off(self, suspended_file):
        eng = _engine()
        _operator_configured()
        assert eng._unattended_paper_owner() is None, "live may run: no paper owner"
        RUNTIME.set_live_suspended(True)
        assert eng._unattended_paper_owner() == OPERATOR
        assert eng._practice_book_user("auto") == OPERATOR
        assert eng._practice_book_user("") == OPERATOR
        assert eng._practice_book_user(MEMBER) == MEMBER, "a person keeps their own book"

    def test_no_operator_configured_is_no_owner(self, suspended_file):
        RUNTIME.set_live_suspended(True)
        eng = _engine()
        assert eng._unattended_paper_owner() is None
        assert eng._practice_book_user("auto") == "auto"
        assert eng.confirm_is_practice("auto") is False

    def test_auto_is_paper_and_still_not_a_person(self, suspended_file):
        from bot.core.engine import RuneClawEngine
        eng = _engine()
        _operator_configured()
        assert eng.confirm_is_practice("auto") is False
        RUNTIME.set_live_suspended(True)
        assert eng.confirm_is_practice("auto") is True
        assert RuneClawEngine._human_confirmed("auto") is False, (
            "a gate that waives itself for a human tap must not waive itself here")

    def test_driven_an_auto_confirm_opens_on_the_operators_paper_book(
            self, suspended_file, monkeypatch, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from bot.core.confirm_result import placed_nothing
        from tests.test_a_live_decision_is_recorded_as_live import _confirming_engine
        monkeypatch.setattr("bot.utils.website_sync.sync_in_background", lambda *a, **k: None)
        _operator_configured()
        RUNTIME.set_live_suspended(True)
        engine, idea = _confirming_engine(tmp_path)
        loop = asyncio.new_event_loop()
        try:
            with patch("bot.core.engine.get_exchange_position_count", new=AsyncMock(return_value=0)), \
                 patch("bot.core.engine.invalidate_position_count_cache"):
                out = loop.run_until_complete(engine.confirm_trade(idea.id, user_id="auto"))
        finally:
            loop.close()
        engine.live_executor.execute.assert_not_awaited()
        assert "[PAPER]" in out and not placed_nothing(out), out
        book = engine.user_portfolios.get(OPERATOR)
        assert [p.asset for p in book.open_positions] == [idea.asset], (
            "the row is on the operator's book, the one /positions reads for them")
        assert not engine.user_portfolios.has_user("auto"), "no book named 'auto'"


# ── /paper says why the confirms are paper ──────────────────────────────────

class TestPaperSaysWhy:
    @pytest.mark.asyncio
    async def test_live_off_says_paper_and_where_to_look(self, suspended_file):
        h = _handler()
        RUNTIME.set_live_suspended(True)
        update, ctx = _update([])
        await h._cmd_paper(update, ctx)
        text = _said(update)
        assert "Your confirms are paper" in text and "/golive OFF" in text
        assert "/positions" in text and "not enabled" not in text

    @pytest.mark.asyncio
    async def test_the_operator_is_told_the_bots_confirms_land_there_too(self, suspended_file):
        h = _handler()
        _operator_configured("6307156912")
        RUNTIME.set_live_suspended(True)
        update, ctx = _update([])
        await h._cmd_paper(update, ctx)
        assert "The bot's own confirms open there too" in _said(update)

    @pytest.mark.asyncio
    async def test_a_member_on_a_live_bot_is_told_live_is_for_admins(self, suspended_file, env_live):
        from bot.skills.trading_commands import paper_mode_reason
        h = _handler()
        assert CONFIG.is_live() is True
        said = paper_mode_reason(h.engine, MEMBER)
        assert said is not None and "for admins only" in said
        assert paper_mode_reason(h.engine, OPERATOR) is None, "the operator's confirms are live"

    @pytest.mark.asyncio
    async def test_otherwise_it_is_the_opt_in_answer_as_before(self, suspended_file):
        h = _handler()
        update, ctx = _update([])
        await h._cmd_paper(update, ctx)
        text = _said(update)
        assert "Your confirms are paper" not in text and "PAPER_SIM_OPT_IN_ENABLED" in text

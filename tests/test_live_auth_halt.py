"""
Live-auth safe-halt (recurring naked-position incident class).

When the trading account cannot authenticate with the venue (missing
passphrase, Bitget 40006 / 40012), it cannot place the protective stop — so it
must NOT open new positions it could not protect. The engine tracks per-account
auth health; the pre-execute gate refuses NEW live entries on an account marked
down (open positions stay monitored). The boot preflight sets operator health;
a per-user sweep probes every linked account so a revoked key surfaces at boot,
not at first stop-placement (live-readiness audit C3).
"""

from unittest.mock import AsyncMock, Mock, patch

from bot.core.engine import RuneClawEngine


# ── per-account auth-health state machine ──────────────────────────────

class TestAuthStatus:
    def _eng(self):
        e = RuneClawEngine.__new__(RuneClawEngine)
        e._live_auth_ok = {}
        e._live_auth_detail = {}
        return e

    def test_unknown_defaults_healthy(self):
        e = self._eng()
        assert e.live_auth_healthy() is True          # operator
        assert e.live_auth_healthy("alice") is True    # any user

    def test_mark_down_blocks_then_recovers(self):
        e = self._eng()
        e.set_live_auth_status(False, "40012 apikey/password is incorrect")
        assert e.live_auth_healthy() is False
        assert "40012" in e._live_auth_detail[""]
        e.set_live_auth_status(True)
        assert e.live_auth_healthy() is True

    def test_per_account_isolation(self):
        e = self._eng()
        e.set_live_auth_status(False, "bad key", user_id="alice")
        assert e.live_auth_healthy("alice") is False
        assert e.live_auth_healthy() is True           # operator unaffected
        assert e.live_auth_healthy("bob") is True       # other user unaffected


# ── per-user boot sweep (C3) ───────────────────────────────────────────

class _Exec:
    def __init__(self, bal):
        self._bal = bal

    async def fetch_balance(self):
        return self._bal


def _cfg(per_user=True, live=True):
    p = patch("bot.main.CONFIG")
    m = p.start()
    m.simulation_mode = not live
    m.live_trading_enabled = live
    m.per_user_live_enabled = per_user
    m.telegram.chat_id = "999"
    m.telegram.admin_ids = ""
    return p


class TestPerUserSweep:
    async def test_marks_failing_accounts_down_and_alerts(self):
        from bot import main as m

        operator = object()
        marks = []
        eng = Mock()
        eng.live_executor = operator
        execs = {
            "alice": _Exec({"total": 100.0}),                       # healthy
            "bob": _Exec({"error": "40012 apikey/password is incorrect"}),
        }
        eng._executor_for = lambda uid: execs.get(uid, operator)
        eng.set_live_auth_status = (
            lambda ok, detail="", user_id="": marks.append((user_id, ok, detail)))

        store = Mock()
        store.user_ids = lambda: ["alice", "bob"]
        bot = Mock()
        bot.send_message = AsyncMock()

        p = _cfg(per_user=True)
        st = patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store)
        st.start()
        try:
            await m._per_user_credential_preflight(eng, bot)
        finally:
            st.stop()
            p.stop()

        by_user = {u: (ok, det) for u, ok, det in marks}
        assert by_user["alice"][0] is True
        assert by_user["bob"][0] is False
        assert "40012" in by_user["bob"][1]
        bot.send_message.assert_awaited()               # operator alerted re: bob

    async def test_noop_when_per_user_off(self):
        from bot import main as m

        eng = Mock()
        called = []
        eng.set_live_auth_status = lambda *a, **k: called.append(1)
        bot = Mock()
        bot.send_message = AsyncMock()

        p = _cfg(per_user=False)
        try:
            await m._per_user_credential_preflight(eng, bot)
        finally:
            p.stop()

        assert called == []
        bot.send_message.assert_not_awaited()

    async def test_skips_accounts_that_fell_back_to_operator(self):
        from bot import main as m

        operator = object()
        marks = []
        eng = Mock()
        eng.live_executor = operator
        # "carol" has no usable keys → _executor_for returns the operator exec.
        eng._executor_for = lambda uid: operator
        eng.set_live_auth_status = (
            lambda ok, detail="", user_id="": marks.append((user_id, ok, detail)))

        store = Mock()
        store.user_ids = lambda: ["carol"]
        bot = Mock()
        bot.send_message = AsyncMock()

        p = _cfg(per_user=True)
        st = patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store)
        st.start()
        try:
            await m._per_user_credential_preflight(eng, bot)
        finally:
            st.stop()
            p.stop()

        assert marks == []                              # nothing probed/marked
        bot.send_message.assert_not_awaited()


def _latch_engine():
    """A real engine shell with the auth latch and the balance cache."""
    e = RuneClawEngine.__new__(RuneClawEngine)
    e._live_auth_ok = {}
    e._live_auth_detail = {}
    e._live_balance_cache = {}
    e._live_balance_cache_ts = 0.0
    e._LIVE_BALANCE_TTL = 30.0
    e._user_live_balance_cache = {}
    e._user_live_balance_cache_ts = {}
    e.live_executor = Mock()
    e.live_executor.user_id = None
    e.live_executor.fetch_balance = AsyncMock()
    return e


class TestASuccessfulReadClearsTheLatch:
    """The boot preflight marks venue auth down, and that used to stick for
    the life of the process. A later authenticated balance — the same call
    the drawdown card is quoting — did not clear it, so resting limits were
    cancelled as "venue auth marked down" on an account the venue had just
    answered."""

    def _down(self):
        e = _latch_engine()
        e.set_live_auth_status(False, "40012 rejected")
        return e

    async def test_a_fresh_balance_clears_the_operator(self, monkeypatch):
        import bot.core.engine as engine_mod
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        e = self._down()
        e.live_executor.fetch_balance = AsyncMock(
            return_value={"total": 400.0, "free": 80.0, "used": 0.0, "holdings": []})
        bal = await e.get_live_equity()
        assert bal["total"] == 400.0
        assert e.live_auth_healthy("") is True
        assert e.live_auth_probed("") is True

    async def test_an_error_payload_does_not_clear_it(self, monkeypatch):
        import bot.core.engine as engine_mod
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        e = self._down()
        # total > 0 must not rescue an error key. The cache condition accepts
        # that shape; auth must not.
        e.live_executor.fetch_balance = AsyncMock(return_value={
            "error": "40012 rejected", "total": 400.0, "free": 0, "used": 0,
            "holdings": []})
        await e.get_live_equity()
        assert e.live_auth_healthy("") is False

    async def test_a_raised_read_does_not_clear_it(self, monkeypatch):
        import bot.core.engine as engine_mod
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        e = self._down()
        e.live_executor.fetch_balance = AsyncMock(side_effect=RuntimeError("timeout"))
        await e.get_live_equity()
        assert e.live_auth_healthy("") is False

    async def test_a_cached_balance_is_not_a_new_check(self, monkeypatch):
        """The cache can outlive the preflight that marked auth down. Serving
        it is not a fresh authentication."""
        import time

        import bot.core.engine as engine_mod
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        e = self._down()
        e._live_balance_cache = {"total": 400.0, "free": 80.0}
        e._live_balance_cache_ts = time.monotonic()
        e.live_executor.fetch_balance = AsyncMock(
            return_value={"total": 400.0, "free": 80.0, "holdings": []})
        got = await e.get_live_equity()
        assert got["total"] == 400.0
        e.live_executor.fetch_balance.assert_not_awaited()
        assert e.live_auth_healthy("") is False

    def test_a_non_dict_is_not_auth_up(self):
        e = self._down()
        e.note_venue_auth_reading(None)
        e.note_venue_auth_reading(["total", 1])
        e.note_venue_auth_reading("40012")
        assert e.live_auth_healthy("") is False

    def test_an_authenticated_empty_account_is_auth_up(self):
        """No error and total 0 is what the preflight calls authenticated.
        Equity can be zero; that is not a rejected key."""
        e = self._down()
        e.note_venue_auth_reading({"total": 0.0, "free": 0.0, "holdings": []})
        assert e.live_auth_healthy("") is True

    async def test_a_per_user_read_does_not_clear_the_operator(self, monkeypatch):
        import bot.core.engine as engine_mod
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        original = engine_mod.CONFIG.per_user_live_enabled
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", True)
        try:
            e = self._down()
            e.set_live_auth_status(False, "40012 rejected", user_id="111")
            own = Mock()
            own.user_id = "111"
            own.fetch_balance = AsyncMock(return_value={
                "total": 50.0, "free": 50.0, "used": 0.0, "holdings": []})
            e._executor_for = lambda uid="": own
            bal = await e.get_user_live_equity("111")
            assert bal["total"] == 50.0
            assert e.live_auth_healthy("111") is True
            assert e.live_auth_healthy("") is False
        finally:
            object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)

    def test_auto_asks_the_operators_book(self):
        e = _latch_engine()
        assert e.auth_account_id(e.live_executor, "auto") == ""
        assert e.auth_account_id(e.live_executor, "12345") == ""
        other = Mock()
        other.user_id = "111"
        assert e.auth_account_id(other, "111") == "111"
        assert e.auth_account_id(other, "auto") == "111"

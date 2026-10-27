"""
/setcap visibility in /accounts.

account_risk_overview() now carries each user's operator-set per-trade margin cap
(cap_usd) so the admin can see configured limits alongside live state — closing
the loop on /setcap (#128), which was previously set-but-invisible. Read-only.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.core.engine import RuneClawEngine


class _FakeExec:
    def __init__(self, user_id=None, positions=()):
        self.user_id = user_id
        self.open_positions = list(positions)


class _FakeStore:
    def __init__(self, caps=None, raises=False):
        self._caps = dict(caps or {})
        self._raises = raises

    def max_margin(self, uid):
        if self._raises:
            raise RuntimeError("store boom")
        return self._caps.get(str(uid))


def _engine(store=None, user_execs=None):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = _FakeExec(positions=[SimpleNamespace(cost_usd=10.0)])
    eng._user_executors = dict(user_execs or {})
    eng.risk = SimpleNamespace(circuit_breaker_active=False, consecutive_losses=0,
                               live_performance_state=lambda: None)
    eng._user_risk = {}
    eng._user_store = store
    eng.get_live_equity = AsyncMock(return_value={"total": 1000.0})
    eng.get_user_live_equity = AsyncMock(return_value={"total": 100.0})
    return eng


class TestAccountsCapDisplay:
    async def test_cap_surfaced_for_capped_user(self):
        eng = _engine(
            store=_FakeStore({"alice": 25.0}),
            user_execs={"alice": _FakeExec(user_id="alice", positions=[])},
        )
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["alice"]["cap_usd"] == 25.0
        # Operator row never carries a per-user cap.
        assert rows["operator"]["cap_usd"] is None

    async def test_uncapped_user_is_none(self):
        eng = _engine(
            store=_FakeStore({}),  # no cap set
            user_execs={"bob": _FakeExec(user_id="bob", positions=[])},
        )
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["bob"]["cap_usd"] is None

    async def test_no_store_is_none(self):
        eng = _engine(store=None,
                      user_execs={"carol": _FakeExec(user_id="carol", positions=[])})
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["carol"]["cap_usd"] is None

    async def test_store_error_is_fail_open(self):
        eng = _engine(store=_FakeStore(raises=True),
                      user_execs={"dave": _FakeExec(user_id="dave", positions=[])})
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        # The error doesn't break the row — cap just reads None.
        assert rows["dave"]["cap_usd"] is None
        assert rows["dave"]["error"] is None


# ── the venue router's read lands on /accounts (VENUE_ROUTER_ENABLED) ───────

import re  # noqa: E402
from unittest import mock  # noqa: E402

import bot.core.venue_router as venue_router  # noqa: E402
from bot.skills.engine_ops_commands import EngineOpsCommands  # noqa: E402


def _card(eng):
    sent: list[str] = []

    class Host(EngineOpsCommands):
        def __init__(self, e):
            self.engine = e

        def _is_admin(self, u):
            return True

        def _lang(self, u):
            return "en"

        async def _send(self, u, text, **kw):
            sent.append(text)

    import asyncio
    asyncio.run(Host(eng)._cmd_accounts(None, None))
    return re.sub(r"</?(pre|b|i|code)>", "", sent[0])


def test_a_router_row_prints_the_venue_read_beside_the_bot_record():
    eng = _engine(user_execs={"alice": _FakeExec(user_id="alice", positions=[])})
    reads = {
        ("operator", "?"): {
            "venue": "bybit", "venue_state": "open", "venue_open": 2,
            "venue_latency_ms": 41.0,
            "venue_note": "bybit: 2 position(s) still open at the venue"},
        ("alice", "?"): {
            "venue": "hyperliquid", "venue_state": "unread", "venue_open": None,
            "venue_latency_ms": None,
            "venue_note": "hyperliquid: not read (TimeoutError); flat NOT verified"},
    }
    with mock.patch.object(venue_router, "router_reads",
                           AsyncMock(return_value=reads)):
        card = _card(eng)
    assert "Venue read:" in card
    op = next(ln for ln in card.splitlines() if ln.startswith("operator")
              and "venue" in ln)
    assert "2 position(s) still open at the venue" in op
    assert "41 ms" in op
    assert "bot record 1" in op              # the executor holds 1, the venue 2
    alice = next(ln for ln in card.splitlines() if ln.startswith("alice")
                 and "hyperliquid" in ln)
    assert "flat NOT verified" in alice
    assert "ms" not in alice and "bot record" not in alice   # nothing read: nothing printed


def test_the_card_has_no_venue_section_when_the_router_did_not_run():
    eng = _engine()
    card = _card(eng)
    assert "Venue read" not in card


async def test_account_rows_gain_no_venue_key_when_the_router_is_off():
    rows = await _engine().account_risk_overview()
    assert not any(k.startswith("venue") for k in rows[0])

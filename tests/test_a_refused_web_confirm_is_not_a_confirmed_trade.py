"""A refused web confirm is not a confirmed trade, and a web-live order never
runs on the operator's account.

TWO DEFECTS IN ONE HANDLER (`user_gateway.handle_trade_confirm`), both driven
against the unfixed tree before either was touched.

1. EVERY ANSWER WAS A 200 AUDITED OK. `engine.confirm_trade` answers with a
   sentence, and the handler relayed every sentence as `{result_html}` with an
   audit line reading `result="OK"` -- the risk gate's refusal, the duplicate
   skip, "Paper trading is disabled", the chosen-strategy refusal, the
   practice cooldown. Both browser surfaces then read the 200 as a trade (the
   dashboard modal on a green "Trade confirmed."). The handler also dropped the
   proposer entry on every answer, so a refused idea -- which most refusals
   leave PENDING -- could be neither re-confirmed nor cancelled by the person
   who proposed it: both doors check that map. `placed_nothing` is the one
   reading of the sentence (the Telegram Confirm button asks it), and every
   literal answer `confirm_trade` can give is driven through the gateway here,
   enumerated by the same AST walk `test_a_refusal_is_never_announced_as_a_trade`
   uses, so a refusal added tomorrow is read here without anybody listing it.

2. THE WEB-LIVE GATE SAID WHO SHOULD PAY AND NOTHING ASKED WHO WOULD. With per-user
   live at its shipped default (off) the engine's resolver answers the
   OPERATOR's executor for every caller, and the gate -- whose premise is an
   order "on THEIR OWN connected exchange keys" -- allowed a web-only id with
   every other input satisfied. The gate reads the flag now
   (`test_the_web_live_gate_needs_the_users_own_account.py`), and the handler
   asks the resolver itself, because the resolver still answers the operator's
   executor for a user whose keys it cannot use: a flag is not a measurement.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from bot.core.confirm_result import placed_nothing
from bot.web import user_gateway as ug
from tests.test_a_refusal_is_never_announced_as_a_trade import _answers, _render
from tests.test_web_gateway import (
    AUTHED,
    HDRS,
    SECRET,
    FakeEngine,
    FakeHandler,
    FakeUsers,
    _propose,
    gateway_client,
)


@pytest.fixture
def audits():
    """Every audit record the system channel writes during the test."""
    from bot.utils.logger import system_log
    seen: list = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record)

    h = _H(level=logging.DEBUG)
    system_log.addHandler(h)
    try:
        yield seen
    finally:
        system_log.removeHandler(h)


def _confirm_audits(seen, action="web_trade_confirm"):
    return [r for r in seen if getattr(r, "action", "") == action]


class _Planted(FakeEngine):
    """`confirm_trade` answers a planted sentence. `drops_idea` says whether the
    planted answer is one the real engine gives after the idea has left the
    book (a placement, the duplicate skip, "not found") or one that leaves it
    pending (most refusals)."""

    def __init__(self, answer, drops_idea):
        super().__init__()
        self.answer = answer
        self.drops_idea = drops_idea

    async def confirm_trade(self, trade_id, user_id=""):
        self.confirm_calls.append((trade_id, user_id))
        if self.drops_idea:
            self._pending_ideas.pop(trade_id, None)
        return self.answer


def _literal_answers():
    out = []
    for fn, node in _answers():
        text = _render(node)
        if text is not None:
            out.append(pytest.param(text, id=f"{fn}:{text[:28]}"))
    return out


# ── 1. the answer is read, not relayed as a success ───────────────────────


@pytest.mark.parametrize("answer", _literal_answers())
async def test_every_literal_answer_is_read_by_the_gateway(answer, monkeypatch, audits):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    refused = placed_nothing(answer)
    engine = _Planted(answer, drops_idea=not refused)
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        body = await r.json()
        assert r.status == 200, "older clients read the 200; `placed` carries the verdict"
        assert body["result_html"] == answer
        assert body["placed"] is (not refused), answer
        rows = _confirm_audits(audits)
        assert [row.result for row in rows] == ["REFUSED" if refused else "OK"], answer


async def test_a_placement_is_audited_ok_and_placed(monkeypatch, audits):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = _Planted("✅ LIVE LONG SOL/USDT opened", drops_idea=True)
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert (await r.json())["placed"] is True
        assert tid not in c.server.app["proposers"], "a placed idea is done"
        assert [row.result for row in _confirm_audits(audits)] == ["OK"]


async def test_a_refusal_that_leaves_the_idea_pending_can_be_confirmed_again_or_cancelled(
        monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = _Planted("\U0001f6e1 Your chosen strategy 'safe' only trades BTC and ETH.",
                      drops_idea=False)
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert (await r.json())["placed"] is False
        assert c.server.app["proposers"].get(tid) == "7", (
            "a refused, still-pending idea lost its proposer: neither Confirm nor "
            "Cancel can reach it")
        # Confirm again (the person fixed what was refused): reaches the engine.
        r = await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 200
        assert len(engine.confirm_calls) == 2
        # And Cancel works on it.
        r = await c.post("/trade/cancel", json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 200 and (await r.json())["cancelled"] is True
        assert tid not in engine._pending_ideas


async def test_a_refusal_that_dropped_the_idea_drops_the_proposer(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = _Planted("⏭️ Skipped SOL/USDT: already have an open/pending order for it "
                      "(duplicate suppressed).", drops_idea=True)
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        tid = await _propose(c)
        await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                     headers=HDRS)
        assert tid not in c.server.app["proposers"]


async def test_the_refusal_audit_carries_the_why_without_a_dollar_figure_or_a_secret(
        monkeypatch, audits):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    for answer, gone in (
        ("Trade REJECTED: price $63,000.12 already below SL $62,900.00 — would be "
         "instantly stopped out.\nsecond line", "$6"),
        ("Trade REJECTED: re-check failed (error logged): api_key=abcd1234efgh5678ijkl",
         "abcd1234efgh5678"),
    ):
        audits.clear()
        engine = _Planted(answer, drops_idea=False)
        async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
            tid = await _propose(c)
            await c.post("/trade/confirm", json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        (row,) = _confirm_audits(audits)
        reason = row.data["reason"]
        assert reason.startswith("Trade REJECTED"), reason
        assert gone not in reason, reason
        assert "second line" not in reason, reason
        assert row.data["user"] == "7"


# ── 2. a web-live order never runs on the operator's account ──────────────


class _WebUsers(FakeUsers):
    def web_live_enabled(self, tg):
        return True


class _WebHandler(FakeHandler):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.users = _WebUsers(kw.get("users"))

    def _can_trade_live(self, tg):
        return False


OPERATOR = object()
OWN = object()


class _LiveEngine(FakeEngine):
    def __init__(self, resolves_to):
        super().__init__()
        self.live_executor = OPERATOR
        self._resolves_to = resolves_to
        self.resolved: list = []

    def _executor_for(self, user_id="", venue=""):
        self.resolved.append(user_id)
        if isinstance(self._resolves_to, Exception):
            raise self._resolves_to
        return self._resolves_to


@pytest.fixture
def web_live(monkeypatch, tmp_path):
    """Every web-live gate input satisfied; `per_user` decides the sixth."""
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_LIVE_TRADING_ENABLED", "1")
    monkeypatch.setenv("WEB_LIVE_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", None)
    import bot.core.exchange_credentials as xc
    import bot.guardian.user_authority_store as uas
    monkeypatch.setattr(xc, "get_credential_store", lambda: SimpleNamespace(
        credential_state=lambda tg: "readable", get_venue=lambda tg: "bitget"))
    monkeypatch.setattr(uas, "get_user_authority_store", lambda: SimpleNamespace(
        is_enforcing=lambda tg: True, get=lambda tg: {"mode": "enforce"}))
    authorized: list = []

    def _authorize(app, engine, tg_id, trade_id):
        authorized.append(trade_id)
        return True, []

    monkeypatch.setattr(ug, "_authorize_web_live_trade", _authorize)

    def configure(per_user):
        monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
            telegram=SimpleNamespace(admin_ids=""), is_live=lambda: True,
            per_user_live_enabled=per_user,
            exchange=SimpleNamespace(default_leverage=5)))

    return SimpleNamespace(configure=configure, authorized=authorized)


async def _web_live_confirm(engine, web_live, per_user):
    async with gateway_client(engine, _WebHandler(users={})) as c:
        # Propose while paper so the proposal exists, then turn the bot live
        # for the confirm, as test_web_gateway's live tests do.
        tid = await _propose(c, tg="web:5")
        web_live.configure(per_user)
        r = await c.post("/trade/confirm", json={"telegram_id": "web:5", "trade_id": tid},
                         headers=HDRS)
        return r.status, await r.json()


async def test_per_user_live_off_refuses_at_the_gate_by_name(web_live):
    engine = _LiveEngine(resolves_to=OPERATOR)   # what the real resolver answers here
    status, body = await _web_live_confirm(engine, web_live, per_user=False)
    assert status == 403
    assert body["error"] == "live_not_enabled"
    assert "PER_USER_LIVE_ENABLED" in body["detail"]
    assert body["checklist"]["routes_to_own_account"] is False
    assert engine.confirm_calls == []
    assert web_live.authorized == [], "nothing may reach the envelope's spend ledger"


async def test_the_flag_on_and_the_resolver_answering_the_operator_is_refused(web_live, audits):
    """The flag says what SHOULD happen; the resolver says what WOULD. With
    per-user live on, the real resolver still answers the operator's executor
    for a user whose keys it cannot use."""
    engine = _LiveEngine(resolves_to=OPERATOR)
    status, body = await _web_live_confirm(engine, web_live, per_user=True)
    assert status == 403
    assert body["error"] == "not_own_account"
    assert "operator's account" in body["detail"]
    assert engine.resolved == ["web:5"]
    assert engine.confirm_calls == [], "an order went to the operator's account"
    assert web_live.authorized == [], (
        "the envelope recorded spend for an order refused before it was placed")
    assert [r.result for r in _confirm_audits(audits, "web_live_own_account")] == ["REFUSED"]


@pytest.mark.parametrize("resolves_to", [None, RuntimeError("store down")],
                         ids=["answers-nothing", "raises"])
async def test_a_resolver_that_cannot_say_whose_account_is_a_refusal(resolves_to, web_live):
    engine = _LiveEngine(resolves_to=resolves_to)
    status, body = await _web_live_confirm(engine, web_live, per_user=True)
    assert status == 403
    assert body["error"] == "not_own_account"
    assert engine.confirm_calls == []


async def test_the_users_own_executor_reaches_the_envelope_and_the_confirm(web_live):
    """The other arm: a refusal-only table passes just as happily against a
    handler that refuses everyone."""
    engine = _LiveEngine(resolves_to=OWN)
    status, body = await _web_live_confirm(engine, web_live, per_user=True)
    assert status == 200, body
    assert len(web_live.authorized) == 1
    assert engine.confirm_calls and engine.confirm_calls[0][1] == "web:5"
    assert body["placed"] is True

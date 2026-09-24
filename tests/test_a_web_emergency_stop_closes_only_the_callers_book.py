"""The website's Emergency Stop closes the requester's own book, never the operator's.

`POST /api/controls/stop` needs only a signed-in website account with a linked
Telegram id — web registration is open and `/link <token>` is ungated — and it
queues a flatten the bot processes in `_maybe_flatten_web_requests`. That pump
resolved the executor with `_executor_for(tg)` and refused the operator's
executor for a non-operator only `if per_user and ...`. PER_USER_LIVE_ENABLED
ships OFF, and with it off `_executor_for` answers the operator's executor for
EVERY caller, so the refusal never ran: any linked account's Emergency Stop
closed every open and resting position on the operator's live account, and the
ack reported `ok: True, closed: N`. Telegram's `/emergency_stop` is
`@guard("halt")`, held by trader and admin only.

The existing suite could not see it: its stand-in replaced `_executor_for` with
a lambda and built the config with `per_user=True`, so the shipped default was
the one arrangement nothing drove. These drive the REAL `_executor_for` and
`_is_operator_user` against the shipped configuration.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bot.core.engine as eng_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine

STRANGER = "222"
OPERATOR = "111"


class _Book:
    """An executor that records whether it was asked to close everything."""

    def __init__(self, n: int = 3):
        self.closed_with: list[str] = []
        self.n = n

    async def close_all_positions(self, reason: str = ""):
        self.closed_with.append(reason)
        return [f"Closed #{i}" for i in range(self.n)]


class _Users:
    def get(self, uid):
        return {"role": "admin"} if str(uid) == OPERATOR else {"role": "trader"}


def _engine(monkeypatch, *, rows, operator_book, audits):
    monkeypatch.setattr("bot.utils.control_pull.fetch_flatten_pending", lambda: rows)
    acked: dict = {}
    monkeypatch.setattr("bot.utils.control_pull.ack_flatten",
                        lambda acks: acked.update({"acks": acks}))
    monkeypatch.setattr(eng_mod, "audit",
                        lambda _log, msg, **kw: audits.append((msg, kw)))
    eng = SimpleNamespace(live_executor=operator_book, _user_store=_Users(),
                          _last_flatten_pull=0.0, _user_executors={})
    eng._executor_for = RuneClawEngine._executor_for.__get__(eng)
    eng._is_operator_user = RuneClawEngine._is_operator_user.__get__(eng)
    return eng, acked


def _run(eng):
    asyncio.run(RuneClawEngine._maybe_flatten_web_requests(eng))


def _shipped_default():
    assert getattr(CONFIG, "per_user_live_enabled", None) is False, (
        "these drives are about the shipped default, and it has moved")
    ids = {s.strip() for raw in (CONFIG.telegram.chat_id, CONFIG.telegram.admin_ids)
           for s in str(raw or "").split(",") if s.strip()}
    assert STRANGER not in ids and OPERATOR not in ids, ids


class TestTheShippedDefault:
    def test_a_strangers_stop_closes_nothing_on_the_operators_account(self, monkeypatch):
        _shipped_default()
        book, audits = _Book(), []
        eng, acked = _engine(monkeypatch, rows=[{"user_id": 7, "telegram_id": STRANGER}],
                             operator_book=book, audits=audits)
        assert eng._executor_for(STRANGER) is book, "the fallback this guards against"
        assert eng._is_operator_user(STRANGER) is False
        _run(eng)
        assert book.closed_with == []
        # Acked, so the row clears: there is nothing of theirs on this bot to
        # close, and a pending row retried every 20 seconds would never clear.
        assert acked["acks"] == [{"user_id": 7, "ok": True, "closed": 0}]

    def test_the_refusal_is_audited_by_name(self, monkeypatch):
        _shipped_default()
        book, audits = _Book(), []
        eng, _acked = _engine(monkeypatch, rows=[{"user_id": 7, "telegram_id": STRANGER}],
                              operator_book=book, audits=audits)
        _run(eng)
        (msg, kw), = [a for a in audits if a[1].get("action") == "web_flatten"]
        assert kw["result"] == "REFUSED" and STRANGER in msg
        assert "operator" in msg and "nothing" in msg.lower()

    def test_the_operators_own_stop_still_closes_the_operators_book(self, monkeypatch):
        _shipped_default()
        book, audits = _Book(n=2), []
        eng, acked = _engine(monkeypatch, rows=[{"user_id": 1, "telegram_id": OPERATOR}],
                             operator_book=book, audits=audits)
        assert eng._is_operator_user(OPERATOR) is True
        _run(eng)
        assert book.closed_with == ["web_emergency_stop"]
        assert acked["acks"] == [{"user_id": 1, "ok": True, "closed": 2}]

    def test_a_stranger_in_the_same_batch_does_not_ride_on_the_operators_row(self, monkeypatch):
        _shipped_default()
        book, audits = _Book(n=1), []
        rows = [{"user_id": 7, "telegram_id": STRANGER},
                {"user_id": 1, "telegram_id": OPERATOR}]
        eng, acked = _engine(monkeypatch, rows=rows, operator_book=book, audits=audits)
        _run(eng)
        assert book.closed_with == ["web_emergency_stop"]      # once, for the operator
        assert acked["acks"] == [{"user_id": 7, "ok": True, "closed": 0},
                                 {"user_id": 1, "ok": True, "closed": 1}]


class TestPerUserLive:
    def _per_user(self, monkeypatch, creds):
        class _Cfg:
            per_user_live_enabled = True

            def __getattr__(self, name):
                return getattr(CONFIG, name)
        monkeypatch.setattr(eng_mod, "CONFIG", _Cfg())
        store = SimpleNamespace(get=lambda _u: creds, get_venue=lambda _u: "bitget")
        monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                            lambda: store)

    def test_a_stranger_with_no_keys_falls_back_and_is_still_refused(self, monkeypatch):
        self._per_user(monkeypatch, creds=None)
        book, audits = _Book(), []
        eng, acked = _engine(monkeypatch, rows=[{"user_id": 7, "telegram_id": STRANGER}],
                             operator_book=book, audits=audits)
        assert eng._executor_for(STRANGER) is book
        _run(eng)
        assert book.closed_with == []
        assert acked["acks"] == [{"user_id": 7, "ok": True, "closed": 0}]

    def test_a_linked_users_own_book_is_closed(self, monkeypatch):
        self._per_user(monkeypatch, creds=None)
        book, own, audits = _Book(), _Book(n=2), []
        eng, acked = _engine(monkeypatch, rows=[{"user_id": 7, "telegram_id": STRANGER}],
                             operator_book=book, audits=audits)
        eng._executor_for = lambda tg: own      # the user's OWN executor
        _run(eng)
        assert own.closed_with == ["web_emergency_stop"] and book.closed_with == []
        assert acked["acks"] == [{"user_id": 7, "ok": True, "closed": 2}]


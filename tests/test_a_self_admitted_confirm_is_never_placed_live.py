"""A self-admitted account's confirm is practice, never a live order.

#498 and #527 sent a self-admitted (role ``paper``) account's confirm to its
practice book at one choke point, and left reversing that as the owner's
call. The owner decided on 2026-10-07: practice only, never live. Two shapes
still let such an account past the choke point:

* **The role alone was read.** A record admitted before ``authorize()``
  clamped self-admission holds ``role: trader`` with
  ``admitted_by: "auto-accept"``: nobody vouched for it, and it read as
  vouched until someone ran ``scripts/migrate_self_admitted_roles.py``.
  The stamp now counts too. An admin's ``/approve`` rewrites the stamp, so it
  never outlives a vouch, and ``admin`` is left out because ``seed_admin``
  promotes the operator's record without touching it.
* **An unreadable store was read as "not practice".** ``UserStore.get``
  answers None for everyone after a failed load, so the confirm went on as a
  live fill for a person whose admission nobody could read. The engine now
  refuses that fill; the doors refused first already, and the choke point no
  longer depends on them.

Driven through the real ``confirm_trade`` with live mode on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.compliance.compliance_engine import Permission
from bot.core.practice_fill import (
    PRACTICE_FILL,
    admission_unread,
    is_self_admitted_paper,
)
from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore
from tests.test_a_paper_users_first_confirm_is_practice import _armed, _Roles
from tests.test_a_practice_confirm_reads_the_practice_book import _confirm, _uid


class _Record(_Roles):
    """A store whose one record carries a role and an admitting party."""

    def __init__(self, role, admitted_by=None, *, unreadable=False, raises=False):
        super().__init__(role)
        self.admitted_by = admitted_by
        self._unreadable = unreadable
        self._raises = raises

    def get(self, tid):
        if self._raises:
            raise OSError("users.json")
        rec = {"role": self.role, "authorized": True}
        if self.admitted_by is not None:
            rec["admitted_by"] = self.admitted_by
        return rec

    def load_failed(self):
        return self._unreadable


def _drive(store):
    host, engine, idea, _ = _armed("trader")
    engine._user_store = store
    uid = _uid()
    return _confirm(host, engine, idea, uid=uid), engine, uid


# ── who nobody vouched for ────────────────────────────────────────────────

@pytest.mark.parametrize("record, expected", [
    ({"role": SELF_ADMISSION_ROLE}, True),
    ({"role": "trader", "admitted_by": SELF_ADMISSION_BY}, True),
    ({"role": "viewer", "admitted_by": SELF_ADMISSION_BY}, True),
    ({"role": "trader", "admitted_by": "999"}, False),
    ({"role": "trader"}, False),
    ({"role": "admin", "admitted_by": SELF_ADMISSION_BY}, False),
    ({"role": "admin"}, False),
    ({}, False),
    (None, False),
    ({"role": None, "admitted_by": SELF_ADMISSION_BY}, False),
])
def test_who_reads_as_self_admitted(record, expected):
    assert is_self_admitted_paper(record) is expected


def test_a_legacy_auto_accepted_trader_confirm_opens_practice():
    result, engine, uid = _drive(_Record("trader", SELF_ADMISSION_BY))
    assert "PRACTICE" in result, result
    engine.live_executor.execute.assert_not_awaited()
    engine.compliance.issue_approval_token.assert_not_called()
    assert engine.compliance.authorize.call_args.kwargs["action"] == Permission.PAPER_TRADE
    book = engine.user_portfolios.get(uid)
    assert [p.fill_label for p in book.open_positions] == [PRACTICE_FILL]


def test_a_trader_an_admin_vouched_for_is_placed_live():
    """The other arm: the same record with an admin's id as the admitting
    party reaches the executor with a live token."""
    result, engine, _ = _drive(_Record("trader", "999"))
    assert result == "✅ LIVE order placed: BTC/USDT LONG"
    engine.live_executor.execute.assert_awaited_once()
    assert engine.compliance.authorize.call_args.kwargs["action"] == Permission.LIVE_TRADE


def test_the_operator_seeded_over_an_auto_accept_stamp_is_placed_live():
    result, engine, _ = _drive(_Record("admin", SELF_ADMISSION_BY))
    assert result == "✅ LIVE order placed: BTC/USDT LONG"
    engine.live_executor.execute.assert_awaited_once()


# ── a store that could not be read ────────────────────────────────────────

def test_an_unreadable_store_refuses_the_live_fill():
    result, engine, uid = _drive(_Record("trader", "999", unreadable=True))
    assert result.startswith("Trade REJECTED"), result
    assert "could not be read" in result and "Nothing was placed" in result
    engine.live_executor.execute.assert_not_awaited()
    engine.compliance.issue_approval_token.assert_not_called()
    assert engine.user_portfolios.get(uid).open_positions == []


def test_a_record_read_that_raises_refuses_the_live_fill():
    result, engine, _ = _drive(_Record("trader", "999", raises=True))
    assert result.startswith("Trade REJECTED"), result
    assert "could not be read" in result
    engine.live_executor.execute.assert_not_awaited()


def test_a_readable_store_is_placed_live():
    """The other arm of both refusals above: same record, a store that read."""
    result, engine, _ = _drive(_Record("trader", "999", unreadable=False))
    assert result == "✅ LIVE order placed: BTC/USDT LONG"


def test_a_real_store_that_failed_to_load_refuses_and_one_that_read_places(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    broken = UserStore(str(bad))
    assert broken.load_failed() is True
    result, engine, _ = _drive(broken)
    assert "could not be read" in result, result
    engine.live_executor.execute.assert_not_awaited()

    good = UserStore(str(tmp_path / "good.json"))
    assert good.load_failed() is False
    host, engine, idea, _ = _armed("trader")
    uid = _uid()
    assert good.authorize(uid, "trader", by="999")
    engine._user_store = good
    result = _confirm(host, engine, idea, uid=uid)
    assert result == "✅ LIVE order placed: BTC/USDT LONG"


def test_a_real_store_holding_a_legacy_auto_accepted_trader_opens_practice(tmp_path):
    """The record as the pre-clamp door wrote it, read by the real store."""
    store = UserStore(str(tmp_path / "users.json"))
    host, engine, idea, _ = _armed("trader")
    uid = _uid()
    assert store.authorize(uid, "trader", by="999")
    store._users[uid]["admitted_by"] = SELF_ADMISSION_BY
    engine._user_store = store
    result = _confirm(host, engine, idea, uid=uid)
    assert "PRACTICE" in result, result
    engine.live_executor.execute.assert_not_awaited()


class TestTheUnreadReading:
    def test_no_person_has_no_record_to_read(self):
        store = _Record("trader", unreadable=True)
        for uid in ("", "auto", None):
            assert admission_unread(store, uid) is False

    def test_no_store_is_not_unread(self):
        assert admission_unread(None, "42") is False

    def test_a_stand_in_that_answers_no_bool_is_read_through(self):
        """A mock store answers every attribute; only a literal True from
        `load_failed` counts, and the record read still runs."""
        assert admission_unread(MagicMock(), "42") is False

    def test_a_probe_that_raises_is_unread(self):
        store = _Record("trader")
        store.load_failed = MagicMock(side_effect=RuntimeError("io"))
        assert admission_unread(store, "42") is True

    def test_a_store_with_no_reader_is_unread(self):
        assert admission_unread(object(), "42") is True

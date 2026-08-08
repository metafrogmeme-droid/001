"""An unreadable users.json emptied itself on the next registration.

    def _load(self):
        try:
            self._users = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._users = {}          # unreadable -> EMPTY

    def _save(self):
        atomic_write_json(self._path, self._users, ...)   # writes {} over it

`register()` saves. It runs for every user on first contact — `/start`, or
any message from someone unrecognised. So a single failed read at startup,
followed by literally one person messaging the bot, replaced the whole user
list with that one person.

The catch includes **OSError**, which is what moves this from exotic to
likely: disk full, a permissions blip, EINTR. No corruption required. And the
loss is silent, because registering that user looks like it worked.

This is the same defect, line for line, that bot/core/exchange_credentials.py
was fixed for and that tests/test_credential_store_never_self_destructs.py
documents:

    AN UNREADABLE STORE IS NOT AN EMPTY STORE, AND THE DIFFERENCE IS
    EVERY KEY IN IT.

That fix landed on the credential store and nobody asked which OTHER store
had the same shape. This one did. Found on 2026-08-08 while investigating why
a bot reported 1 registered user where the operator expected 40+ — the
archives showed 1 as far back as they went, so the loss predates them and
cannot be confirmed as this bug. It is, at minimum, a live mechanism for
exactly that outcome.

One deliberate difference from the credential store: `_save` REFUSES but does
not raise. `register()` sits on the message path for every user, and an
exception there takes the bot down rather than degrading it. Refusing the
write preserves the file; the CRITICAL log is the alarm.
"""
from __future__ import annotations

import json
import os

import pytest

from bot.utils.user_store import UserStore


def _populated(tmp_path, n=3):
    path = tmp_path / "users.json"
    users = {str(1000 + i): {"name": f"user{i}", "role": "trader",
                             "tier": "basic", "authorized": True}
             for i in range(n)}
    path.write_text(json.dumps(users), encoding="utf-8")
    return path, users


class TestAMissingFileIsStillLegitimatelyEmpty:
    """First boot must keep working, or the fix costs every new deployment."""

    def test_absent_file_loads_empty_and_saves(self, tmp_path):
        path = tmp_path / "users.json"
        store = UserStore(path)
        assert store._users == {}
        assert store._load_failed is False
        store.register(4242, name="First")
        assert json.loads(path.read_text())["4242"]["name"] == "First"

    def test_an_empty_json_object_is_not_a_failure(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text("{}", encoding="utf-8")
        store = UserStore(path)
        assert store._load_failed is False
        store.register(1, name="X")
        assert "1" in json.loads(path.read_text())


class TestAnUnreadableStoreIsNeverOverwritten:
    def test_corrupt_json_sets_the_flag(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text("{not json", encoding="utf-8")
        store = UserStore(path)
        assert store._load_failed is True

    def test_registering_does_NOT_wipe_the_file(self, tmp_path):
        """The whole incident in one test: one bad read, one new user, and
        previously every other user was gone."""
        path = tmp_path / "users.json"
        path.write_text("{not json", encoding="utf-8")
        store = UserStore(path)
        store.register(9999, name="Newcomer")      # must not raise
        # The damaged original was moved aside; nothing wrote a 1-user file
        # back to the live path.
        assert not path.exists() or "not json" in path.read_text()

    def test_the_damaged_file_is_preserved(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text("{not json", encoding="utf-8")
        UserStore(path)
        corrupt = tmp_path / "users.json.corrupt"
        assert corrupt.exists()
        assert "not json" in corrupt.read_text()

    def test_an_existing_corrupt_sidecar_is_not_clobbered(self, tmp_path):
        (tmp_path / "users.json.corrupt").write_text("FIRST RESCUE",
                                                     encoding="utf-8")
        (tmp_path / "users.json").write_text("{bad again", encoding="utf-8")
        UserStore(tmp_path / "users.json")
        assert "FIRST RESCUE" in (tmp_path / "users.json.corrupt").read_text()

    def test_an_oserror_read_is_treated_the_same_as_corruption(self, tmp_path,
                                                               monkeypatch):
        """OSError is the likely one — disk full, permissions, EINTR — and it
        is in the same except clause. No corruption needed."""
        path, _ = _populated(tmp_path)
        real_open = open

        def boom(p, *a, **k):
            if str(p) == str(path):
                raise OSError("simulated transient read failure")
            return real_open(p, *a, **k)

        monkeypatch.setattr("builtins.open", boom)
        store = UserStore(path)
        assert store._load_failed is True


class TestItDoesNotTakeTheBotDown:
    """register() is on the message path for every user."""

    def test_save_refuses_without_raising(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text("{not json", encoding="utf-8")
        store = UserStore(path)
        store._save()                    # must not raise
        store.register(1, name="A")      # nor here

    def test_a_healthy_store_still_saves_normally(self, tmp_path):
        path, users = _populated(tmp_path, n=3)
        store = UserStore(path)
        assert store._load_failed is False
        store.register(5555, name="Fourth")
        on_disk = json.loads(path.read_text())
        assert len(on_disk) == 4, "a healthy save must still persist everyone"
        for key in users:
            assert key in on_disk, "existing users must survive a registration"


class TestTheOtherStoresWithTheSameShape:
    """Ask which OTHER surface makes the same claim.

    The credential store was fixed and nobody checked its siblings. This scans
    for the shape rather than trusting that two known cases are all of them —
    a JSON store whose load collapses a failed read to a default and whose save
    then persists it.
    """

    CANDIDATES = (
        "bot/utils/user_store.py",
        "bot/core/exchange_credentials.py",
    )

    @pytest.mark.parametrize("rel", CANDIDATES)
    def test_each_guards_its_save_against_a_failed_load(self, rel):
        import pathlib
        from tests.source_scan import code_only
        src = code_only(pathlib.Path(rel).read_text(encoding="utf-8"))
        assert "_load_failed" in src, (
            f"{rel} collapses an unreadable store to a default; its _save must "
            "refuse rather than persist that over the real file"
        )

"""A purge that reports success must have deleted something.

RC-2026-019. `handle_account_purge` covered six stores and never touched
`bot/db/models.py` at all. Driven when the finding was filed: the endpoint
returned **HTTP 200, `purged: true`, and changed zero rows** in all seven
tables, and afterwards the user's `llm_api_key`, `user_news_keys.api_key`,
`user_portfolio.trade_history` and `user_ingest_notes.body` were still
readable.

WHAT THE NAIVE FIX WOULD HAVE DONE. `DELETE FROM users WHERE id =
settings_user_id(telegram_id)` inherits the RC-2026-026 collision: the row at
that id may belong to somebody else, and the cascade would take their account
with it. Deleting less than asked is this bug; deleting somebody else's data is
a worse one. So the parent delete is CONDITIONAL on the row being a stub — a
website-linked bridge row or an identity stub — and a bot-native account (a
real password hash) is refused and reported, never silently removed.
"""
import pathlib

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import bot.db.models as M

    monkeypatch.setattr(M, "DB_PATH", pathlib.Path(tmp_path) / "t.db")
    M.init_db()
    return M


def _plant(M, uid, key="sk-KEY", news="NEWS-KEY", note="private text"):
    """Give an identity a row in every child table."""
    M.ensure_settings_parent(uid)
    s = M.get_user_settings(uid)
    s.llm_api_key = key
    M.save_user_settings(s)
    M.save_user_news_key(uid, "cryptopanic", news)
    M.add_user_ingest_note(uid, "t", note, "src")
    return uid


def test_a_purge_removes_every_table_the_schema_carries(db):
    uid = _plant(db, 700700700)
    res = db.purge_user_data(uid)

    assert db.get_user_settings(uid).llm_api_key == ""
    assert db.get_user_news_key(uid) == ("", "")
    assert db.list_user_ingest_notes(uid) == []
    assert db.get_user_by_id(uid) is None
    # and it says so, per table
    assert res["users"] == "deleted"
    assert all(v in ("deleted", "none") for v in res.values()), res


def test_a_second_user_is_untouched(db):
    """A deletion test that only checks the target is half a test."""
    victim = _plant(db, 700700700, key="sk-THEIRS")
    other = _plant(db, 800800800, key="sk-BYSTANDER", news="BYSTANDER-NEWS")

    db.purge_user_data(victim)

    assert db.get_user_settings(other).llm_api_key == "sk-BYSTANDER"
    assert db.get_user_news_key(other) == ("cryptopanic", "BYSTANDER-NEWS")
    assert db.list_user_ingest_notes(other) != []
    assert db.get_user_by_id(other) is not None


def test_purging_an_identity_that_has_nothing_says_none_not_deleted(db):
    res = db.purge_user_data(999888777)
    assert res["users"] == "none"
    assert all(v == "none" for v in res.values()), res


def test_it_refuses_to_delete_a_bot_native_account(db):
    """The RC-2026-026 collision, applied to deletion instead of reading.

    A row carrying a real password hash is somebody's actual account. An
    identity-keyed purge landing on it must not cascade it away.
    """
    alice = db.create_user("alice@real.com", "correct-horse-battery")
    s = db.get_user_settings(alice)
    s.llm_api_key = "sk-ALICE"
    db.save_user_settings(s)

    res = db.purge_user_data(alice)

    assert db.get_user_by_id(alice) is not None, "it deleted a real account"
    assert db.get_user_settings(alice).llm_api_key == "sk-ALICE"
    assert res["users"] == "error", res
    # And the caller must be able to tell: not a clean purge.
    assert not all(v in ("deleted", "none") for v in res.values())


def test_a_website_linked_bridge_row_is_purgeable(db):
    """That row IS this person -- MySQL ids are unique -- so it must go."""
    from bot.skills.user_middleware import _ensure_local_user

    _ensure_local_user(55, "them@web.com", "pro")
    s = db.get_user_settings(55)
    s.llm_api_key = "sk-WEBUSER"
    db.save_user_settings(s)

    res = db.purge_user_data(55)
    assert db.get_user_by_id(55) is None
    assert res["users"] == "deleted"


def test_every_table_in_the_schema_is_named_by_the_purge(db):
    """The enumeration is the point: a table added later and not added here is
    a store somebody's deletion will silently miss, which is how this finding
    happened in the first place."""
    import re

    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA))
    covered = set(db.PURGE_TABLES) | {"users"}
    assert tables == covered, (
        f"schema has {sorted(tables)}, purge covers {sorted(covered)} — "
        f"unreached: {sorted(tables - covered)}"
    )


def test_the_link_token_verdict_survives_the_audit_redactor():
    """`_SENSITIVE_KEY_RE` matches 'token', so a verdict keyed
    `sqlite_link_tokens` logged as ***REDACTED*** -- the store's outcome erased
    from the record that IS the legal artifact. Two of seven were affected:
    link_tokens and exchange_credentials.
    """
    from bot.utils.logger import _redact_dict
    from bot.web.user_gateway import _purge_audit_payload

    payload = _purge_audit_payload({
        "exchange_credentials": "deleted",
        "link_tokens": "deleted",
        "user_record": "none",
    })
    redacted = _redact_dict(payload)
    assert "***REDACTED***" not in repr(redacted), redacted
    # and the verdicts are still legible
    assert "deleted" in repr(redacted) and "none" in repr(redacted)


def test_an_unreadable_user_store_does_not_report_nothing_to_delete(tmp_path):
    """`forget` returned False for a store it could not READ.

    On a failed load `_users` is {} and `_save` refuses to write, so the record
    may still be on disk while the purge reports "none" -- an erasure request
    recorded as satisfied because the file was corrupt. `none` and `could not
    look` are different facts, and this is the surface where conflating them
    matters most.
    """
    from bot.utils.user_store import UserStore

    bad = tmp_path / "users.json"
    bad.write_text("{ this is not json")
    store = UserStore(path=bad)
    assert getattr(store, "_load_failed", False), "fixture did not corrupt the load"

    with pytest.raises(RuntimeError):
        store.forget("12345")


def test_a_readable_store_still_reports_none_for_an_absent_record(tmp_path):
    from bot.utils.user_store import UserStore

    good = tmp_path / "users.json"
    good.write_text("{}")
    assert UserStore(path=good).forget("12345") is False

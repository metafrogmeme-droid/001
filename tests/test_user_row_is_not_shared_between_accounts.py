"""One database row must never serve two different people.

RC-2026-026 (CRITICAL) and RC-2026-027 (HIGH). Found while prosecuting the
RC-2026-019 purge remedy; neither is a purge bug.

THREE WRITERS SHARE ONE INTEGER KEY in the bot's SQLite ``users`` table:

    create_user()            id = AUTOINCREMENT from 1, password_hash = PBKDF2
                             (POST /auth/register, mounted at api_bridge.py:366)
    _ensure_local_user()     id = the WEBSITE's MySQL id,
                             password_hash = 'website-linked:no-local-password'
    ensure_settings_parent() id = settings_user_id(identity), password_hash = ''

The first two both start at 1. `_ensure_local_user` looked the row up by id
alone and **returned early if one existed**, never checking it belonged to this
person -- so a website user landed on a bot-native account's row and read its
``user_settings.llm_api_key``, ``user_news_keys.api_key``,
``user_portfolio.trade_history`` and ``user_ingest_notes.body``.

The discriminator used here is ``password_hash``, not ``email``: the website is
the authority on a user's email and can change it at any time, so requiring a
match would turn an ordinary email change into a permanent refusal. Nothing in
the tree ever updates ``password_hash`` after insert, so the marker is stable.
"""
import pathlib

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A real schema on a temp file. Never touches the deployment's data/."""
    import bot.db.models as M

    monkeypatch.setattr(M, "DB_PATH", pathlib.Path(tmp_path) / "t.db")
    M.init_db()
    return M


# ── RC-2026-026 ───────────────────────────────────────────────────────────

def test_a_website_user_does_not_land_on_a_bot_native_account(db):
    """The reproduction, exactly as it was driven when the finding was filed."""
    from bot.skills.user_middleware import LocalUserConflict, _ensure_local_user

    alice = db.create_user("alice@real.com", "correct-horse-battery")
    s = db.get_user_settings(alice)
    s.llm_api_key = "sk-ALICE-PRIVATE"
    db.save_user_settings(s)

    # Bob is a DIFFERENT person who happens to be website MySQL id `alice`.
    with pytest.raises(LocalUserConflict):
        _ensure_local_user(alice, "bob@other.com", "pro")

    # And Alice's row is untouched by the refusal.
    assert db.get_user_by_id(alice).email == "alice@real.com"
    assert db.get_user_settings(alice).llm_api_key == "sk-ALICE-PRIVATE"


def test_the_secret_never_becomes_readable_through_the_bridge(db):
    """The consequence, stated as the harm rather than as the mechanism."""
    from bot.skills.user_middleware import LocalUserConflict, _ensure_local_user

    alice = db.create_user("alice@real.com", "correct-horse-battery")
    db.save_user_news_key(alice, "cryptopanic", "ALICE-NEWS-KEY")

    try:
        _ensure_local_user(alice, "bob@other.com", "pro")
    except LocalUserConflict:
        pass
    else:
        pytest.fail("the bridge bound Bob to Alice's row")

    # Whatever the caller does next, it must not have been handed Alice's key
    # as if it were Bob's. The refusal is what guarantees that.
    assert db.get_user_news_key(alice) == ("cryptopanic", "ALICE-NEWS-KEY")


def test_an_identity_stub_is_not_a_website_account_either(db):
    """`ensure_settings_parent` rows carry '' and belong to a third id space."""
    from bot.skills.user_middleware import LocalUserConflict, _ensure_local_user

    db.ensure_settings_parent(4242)
    with pytest.raises(LocalUserConflict):
        _ensure_local_user(4242, "someone@web.com", "free")


def test_a_first_link_still_works(db):
    from bot.skills.user_middleware import _ensure_local_user

    _ensure_local_user(77, "new@web.com", "pro")
    row = db.get_user_by_id(77)
    assert row is not None and row.email == "new@web.com"
    # The child rows the bridge is responsible for exist.
    assert db.get_user_settings(77).user_id == 77


def test_relinking_the_same_website_user_is_idempotent(db):
    from bot.skills.user_middleware import _ensure_local_user

    _ensure_local_user(77, "new@web.com", "pro")
    s = db.get_user_settings(77)
    s.llm_api_key = "sk-THEIR-OWN"
    db.save_user_settings(s)

    _ensure_local_user(77, "new@web.com", "pro")          # link again
    assert db.get_user_settings(77).llm_api_key == "sk-THEIR-OWN"


def test_an_email_change_on_the_website_does_not_lock_the_user_out(db):
    """The website owns the email. Requiring a match would break /link forever
    after an ordinary email change, which is why the marker is password_hash."""
    from bot.skills.user_middleware import _ensure_local_user

    _ensure_local_user(77, "old@web.com", "pro")
    _ensure_local_user(77, "new@web.com", "pro")          # they changed it
    assert db.get_user_by_id(77) is not None


# ── RC-2026-027 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("ident", ["١٢٣٤٥", "１２３４５", "web:١٢", "web:１２"])
def test_unicode_digits_do_not_reach_another_users_row(ident):
    """`str.isdigit()` is True for these and `int()` accepts them, so they
    normalised onto the ASCII spelling's row -- which holds llm_api_key."""
    from bot.db.models import settings_user_id

    assert settings_user_id(ident) is None, (
        f"{ident!r} maps to {settings_user_id(ident)!r}, another user's key"
    )


def test_ascii_identities_still_map():
    from bot.db.models import settings_user_id

    assert settings_user_id("12345") == 12345
    assert settings_user_id("web:12") == -12
    assert settings_user_id(" 12345 ") == 12345          # trimming is intended


def test_zero_does_not_collide_the_two_id_spaces():
    """'0' and 'web:0' both mapped to 0 -- the one point where the positive
    Telegram space and the negative web space meet."""
    from bot.db.models import settings_user_id

    assert settings_user_id("0") is None
    assert settings_user_id("web:0") is None


@pytest.mark.parametrize("ident", ["²", "⁵", "½"])
def test_a_non_decimal_numeral_returns_none_rather_than_raising(ident):
    """isdigit() is True but int() rejects these, so the function raised where
    its docstring promises None -- 500ing its callers instead of rejecting."""
    from bot.db.models import settings_user_id

    assert settings_user_id(ident) is None


@pytest.mark.parametrize("ident", ["web:١٢", "web:１２"])
def test_the_gateway_gate_rejects_unicode_web_ids(ident):
    """`_WEB_ID_RE` is a str pattern, so its flags are re.UNICODE and \\d
    matched these -- the gate that exists to validate the identity let them by."""
    from bot.web.user_gateway import _is_web_id

    assert _is_web_id(ident) is False


def test_the_gateway_gate_still_accepts_real_web_ids():
    from bot.web.user_gateway import _is_web_id

    assert _is_web_id("web:12") is True
    assert _is_web_id("web:99999") is True


# ── the second door ───────────────────────────────────────────────────────
#
# CLAUDE.md's corollary: ask which OTHER surface makes the same claim. It does.

def test_an_identity_write_does_not_land_on_a_bot_native_account(db):
    """`ensure_settings_parent` was `INSERT OR IGNORE`, so on a collision the
    insert quietly did nothing and the caller wrote its settings into somebody
    else's row.

    The collision is self-reinforcing: this function inserts EXPLICIT ids and
    SQLite's AUTOINCREMENT tracks max(id), so a Telegram-keyed stub drags the
    counter into Telegram-id range, the next `create_user` lands there, and a
    Telegram user whose chat id is that number then maps onto it.
    """
    db.ensure_settings_parent(1877654321)          # drags AUTOINCREMENT up
    carol = db.create_user("carol@real.com", "correct-horse-battery")
    assert carol > 1877654321, "fixture no longer reproduces the drag"
    s = db.get_user_settings(carol)
    s.llm_api_key = "sk-CAROL-PRIVATE"
    db.save_user_settings(s)

    # A Telegram user whose chat id IS that number now arrives.
    with pytest.raises(db.IdentityCollision):
        db.ensure_settings_parent(carol)

    assert db.get_user_settings(carol).llm_api_key == "sk-CAROL-PRIVATE"


def test_a_fresh_identity_still_gets_its_parent_row(db):
    db.ensure_settings_parent(555000111)
    assert db.get_user_by_id(555000111) is not None


def test_repeating_an_identity_write_is_idempotent(db):
    db.ensure_settings_parent(555000111)
    db.ensure_settings_parent(555000111)           # must not raise
    assert db.get_user_by_id(555000111) is not None


def test_a_web_identity_may_write_through_its_own_bridge_row(db):
    """The bridge and the identity path describe the SAME person when a web
    identity resolves here, so a website-linked row must stay writable."""
    from bot.skills.user_middleware import _ensure_local_user

    _ensure_local_user(88, "them@web.com", "pro")
    db.ensure_settings_parent(88)                  # must not raise
    assert db.get_user_by_id(88).email == "them@web.com"


# ── the guard must be REACHED, not merely present ─────────────────────────

def test_every_settings_write_site_handles_the_collision():
    """`ensure_settings_parent` can now raise, and five call sites reach it.

    A site that does not catch it turns a refusal into a 500 -- fail-closed, so
    not dangerous, but not an answer either. A site that catches it too broadly
    would be worse: it would report success for a write that did not happen.
    This counts sites rather than asserting a string, because the number is the
    thing that rots when somebody adds a sixth.
    """
    import inspect

    import bot.web.user_gateway as gw
    from tests.source_scan import handler_sources

    # The Telegram half is counted across every file the handler class is
    # made of: its one site lives in a mixin since the handler split, and a
    # count scoped to telegram_handler.py alone read the move as the site
    # vanishing (0 of 1) — and would read a copy left behind as a sixth.
    telegram_src = "\n".join(p.read_text(encoding="utf-8") for p in handler_sources())
    for name, src, expected in (("bot.web.user_gateway", inspect.getsource(gw), 4),
                                ("the Telegram handler and its mixins", telegram_src, 1)):
        calls = src.count("ensure_settings_parent(uid)")
        catches = src.count("except IdentityCollision:")
        assert calls == expected, (
            f"{name}: {calls} call sites, expected {expected} — a new "
            "one was added without a collision branch"
        )
        assert catches == expected, (
            f"{name}: {calls} call sites but {catches} handlers"
        )


def test_the_gateway_refuses_with_a_coarse_code_not_the_driver_message(db, monkeypatch):
    """The refusal text names the OTHER account's id. It must not reach a caller."""
    import inspect

    import bot.web.user_gateway as gw

    src = inspect.getsource(gw)
    assert '{"error": "identity_conflict"}' in src
    # The driver message is built inside models.py and must not be echoed.
    assert "refusing to write another identity" not in src


def test_the_collision_message_does_not_leak_the_other_account(db):
    """What the exception says is fine for a log and wrong for a user."""
    carol = db.create_user("carol@real.com", "correct-horse-battery")
    try:
        db.ensure_settings_parent(carol)
    except db.IdentityCollision as exc:
        # It names an internal id for the operator's log...
        assert str(carol) in str(exc)
        # ...and never the other account's identifying details.
        assert "carol@real.com" not in str(exc)
    else:
        pytest.fail("expected IdentityCollision")

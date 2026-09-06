"""The premise a migration is standing on, driven rather than assumed.

`scripts/migrate_self_admitted_roles.py` refuses to guess: a record with no
`admitted_by` is reported, never downgraded, because absence is not a
measurement and demoting somebody an operator promoted on purpose is the worst
outcome a tidying script has.

For ONE shape of id that absence is not ambiguous. A "web:<n>" identity exists
only because `bot/web/user_gateway._guard_user` called `register()` on a first
request, and `register()` cannot stamp an admission. If no reachable path can
put a human's id in `admitted_by` for a web account, then an absent one proves
the gateway provisioned it — and the migration downgrades it on that proof
instead of leaving it in the "cannot tell" pile.

WHICH MAKES THE PROOF LOAD-BEARING, AND PROOFS ROT. It held on 2026-08-15 as
two `target_id.isdigit()` calls in two handlers, several thousand lines from
the script reasoning about them, with nothing connecting the two. A third admin
surface added later would have silently made the migration wrong — and wrong in
the direction of demoting a real person, which is the exact failure the script's
caution exists to prevent.

So the rule moved into `authorize()`, the one funnel every admission passes
through, and this file drives it there AND at both handlers. The handler tests
are not redundant with the store test: they pin that the user is TOLD, rather
than silently refused by a `False` nobody surfaces.

RED HERRING, planted below: a web account whose record says
`authorized: True, role: trader`. That is what a vouched-for teammate's record
looks like at a glance, and it is what every one of the three web accounts on
production looked like. It means nothing — `register()` writes both for every
first contact — and reading it as a decision is the whole confusion this file
resolves.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils.user_store import (DEFAULT_AUTO_ROLE, SELF_ADMISSION_BY,
                                  SELF_ADMISSION_ROLE, UserStore, is_vouchable)

OPERATOR = "111"
WEB_ID = "web:90001"


def _store(tmp_path) -> UserStore:
    return UserStore(tmp_path / "users.json")


# ── the predicate ────────────────────────────────────────────────────

def test_a_telegram_id_is_vouchable_and_a_web_id_is_not():
    assert is_vouchable("6307156912") is True
    assert is_vouchable(6307156912) is True
    assert is_vouchable(WEB_ID) is False


def test_nothing_that_is_not_a_bare_number_is_vouchable():
    """The two admin surfaces take a string a human typed. Anything that is not
    a plain id is either a typo or somebody's idea of an injection."""
    for junk in ("", " 123", "123 ", "12.3", "-123", "web:1", "1e5",
                 "123abc", None):
        assert is_vouchable(junk) is False, junk


def test_a_non_ascii_numeral_is_not_a_telegram_id():
    """`"٣٤".isdigit()` and `"²".isdigit()` are both True, which the raw
    `isdigit()` calls this predicate replaced accepted. /approve on one opened
    a record under a key no Telegram id can ever equal — a phantom account,
    permanently unreachable by the person it claims to name."""
    for numeral in ("٣٤", "²", "１２３", "๓"):
        assert is_vouchable(numeral) is False, numeral


# ── the funnel ───────────────────────────────────────────────────────

def test_the_store_refuses_to_record_a_human_admission_for_a_web_id(tmp_path):
    """The load-bearing one. Every admission goes through authorize(), so a
    refusal here holds for admin surfaces that do not exist yet — which is the
    only kind that could invalidate the migration."""
    s = _store(tmp_path)
    s.register(WEB_ID, name="Web user")
    assert s.authorize(WEB_ID, role="trader", by=OPERATOR) is False
    assert s.get(WEB_ID).get("admitted_by") is None
    assert s.is_admitted(WEB_ID) is False


def test_the_refusal_does_not_quietly_promote_them_anyway(tmp_path):
    """Returning False while having already written the role would be worse
    than allowing it: the caller reports failure and the account is elevated."""
    s = _store(tmp_path)
    s.register(WEB_ID, name="Web user")
    before = s.get(WEB_ID)["role"]
    s.authorize(WEB_ID, role="admin", by=OPERATOR)
    assert s.get(WEB_ID)["role"] == before


def test_a_web_id_can_still_be_provisioned_by_the_door(tmp_path):
    """The refusal is about a HUMAN admission. Self-admission is how web
    accounts are meant to arrive and must keep working, or the gateway stops
    admitting anyone."""
    s = _store(tmp_path)
    assert s.authorize(WEB_ID, role=SELF_ADMISSION_ROLE,
                       by=SELF_ADMISSION_BY) is True
    assert s.get(WEB_ID)["role"] == SELF_ADMISSION_ROLE


def test_a_normal_approval_is_untouched(tmp_path):
    s = _store(tmp_path)
    s.register("999", name="Ann")
    assert s.authorize("999", role="trader", by=OPERATOR) is True
    assert s.get("999")["admitted_by"] == OPERATOR
    assert s.is_admitted("999") is True


# ── the red herring ──────────────────────────────────────────────────

def test_an_auto_provisioned_web_record_looks_exactly_like_a_vouched_one(tmp_path):
    """Planted, and true. `authorized: True` plus a real role is what
    register() writes for every first contact — it is not a decision, and the
    three production web accounts looked precisely like this."""
    s = _store(tmp_path)
    s.register(WEB_ID, name="Web user")
    rec = s.get(WEB_ID)
    assert rec["authorized"] is True
    assert rec["role"] == DEFAULT_AUTO_ROLE
    # And the only thing that separates it from an admission:
    assert "admitted_by" not in rec
    assert s.is_admitted(WEB_ID) is False


# ── both admin surfaces, driven ──────────────────────────────────────

def _handler(tmp_path):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.forwarder = MagicMock()
    h._limiter = SimpleNamespace(allow=lambda _uid: True)
    h.engine = MagicMock()
    h.sent = []

    async def _send(update, text, **kw):
        h.sent.append(text)
    h._send = _send
    h.users.seed_admin(OPERATOR)
    return h


def _admin_update():
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = int(OPERATOR)
    update.effective_user.first_name = "Ops"
    update.effective_user.language_code = "en"
    update.effective_chat = MagicMock()
    update.effective_chat.id = int(OPERATOR)
    update.effective_chat.type = "private"
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _configured():
    p = patch("bot.skills.telegram_handler.CONFIG")
    mc = p.start()
    mc.telegram.chat_id = OPERATOR
    mc.telegram.admin_ids = OPERATOR
    mc.telegram.live_trader_ids = ""
    mc.simulation_mode = True
    mc.paper_auto_accept = False
    mc.live_open_to_key_holders = False
    return p


@pytest.mark.asyncio
async def test_approve_refuses_a_web_id_and_says_so(tmp_path):
    """Not just refused — ANSWERED. A store returning False with no message is
    an operator typing a command and getting silence."""
    h = _handler(tmp_path)
    h.users.register(WEB_ID, name="Web user")
    update, ctx = _admin_update()
    ctx.args = [WEB_ID, "admin"]
    p = _configured()
    try:
        await h._cmd_approve(update, ctx)
    finally:
        p.stop()
    assert h.users.is_admitted(WEB_ID) is False
    assert h.users.get(WEB_ID)["role"] != "admin"
    assert h.sent, "the operator was told nothing"


@pytest.mark.asyncio
async def test_the_admit_button_refuses_a_web_id_too(tmp_path):
    """The callback_data is constructed by us but arrives from the client, so
    the second surface needs the check as much as the first."""
    h = _handler(tmp_path)
    h.users.register(WEB_ID, name="Web user")
    update, ctx = _admin_update()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.data = f"admit:{WEB_ID}"
    update.callback_query.edit_message_text = AsyncMock()
    p = _configured()
    try:
        await h._handle_callback(update, ctx)
    finally:
        p.stop()
    assert h.users.is_admitted(WEB_ID) is False
    assert h.users.get(WEB_ID).get("admitted_by") is None


@pytest.mark.asyncio
async def test_approve_still_works_for_a_real_telegram_id(tmp_path):
    """The refusal has to be about the id shape and nothing else. A check that
    also broke ordinary approval would be found in production, by an operator
    who could no longer add anyone."""
    h = _handler(tmp_path)
    h.users.register("999", name="Ann")
    update, ctx = _admin_update()
    ctx.args = ["999", "trader"]
    p = _configured()
    try:
        await h._cmd_approve(update, ctx)
    finally:
        p.stop()
    assert h.users.is_admitted("999") is True
    assert h.users.get("999")["admitted_by"] == OPERATOR


# ── refused at the surface, not attempted and rejected ───────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["command", "button"])
async def test_a_malformed_id_never_reaches_the_store(tmp_path, surface):
    """Observing the CALL, not its result — because the result is the same
    either way and that is what makes this worth pinning.

    `"٣٤".isdigit()` is True, so a surface still using the raw check hands it
    to `authorize()`, which refuses it — same outcome, different route, and the
    user is told "approval failed" instead of "that is not an id". Both
    handlers validating through the shared predicate is what keeps the funnel a
    backstop rather than the only thing standing there.
    """
    h = _handler(tmp_path)
    h.users.authorize = MagicMock(return_value=False)
    update, ctx = _admin_update()
    p = _configured()
    try:
        if surface == "command":
            ctx.args = ["٣٤", "trader"]
            await h._cmd_approve(update, ctx)
        else:
            update.callback_query = MagicMock()
            update.callback_query.answer = AsyncMock()
            update.callback_query.data = "admit:٣٤"
            update.callback_query.edit_message_text = AsyncMock()
            await h._handle_callback(update, ctx)
    finally:
        p.stop()
    h.users.authorize.assert_not_called()


# ── the gate is REACHED, not merely present ──────────────────────────

@pytest.mark.asyncio
async def test_approve_routes_through_the_shared_predicate(tmp_path):
    """Source can show a check exists; only this can show it runs. Widening
    `is_vouchable` must widen /approve — if it does not, the handler is
    enforcing its own copy and the migration is reasoning about the wrong
    function."""
    h = _handler(tmp_path)
    h.users.register(WEB_ID, name="Web user")
    update, ctx = _admin_update()
    ctx.args = [WEB_ID, "trader"]
    p = _configured()
    try:
        # /approve reads `is_vouchable` from the access mixin's module since
        # the handler split; a patch on the handler's copy would land on a
        # name the gate no longer reads.
        with patch("bot.skills.access_commands.is_vouchable", return_value=True), \
             patch("bot.utils.user_store.is_vouchable", return_value=True):
            await h._cmd_approve(update, ctx)
    finally:
        p.stop()
    assert h.users.is_admitted(WEB_ID) is True, (
        "/approve refused a web id for some reason OTHER than is_vouchable; "
        "the migration's proof rests on a predicate this call site ignores")


def test_the_store_routes_through_the_shared_predicate(tmp_path):
    s = _store(tmp_path)
    s.register(WEB_ID, name="Web user")
    with patch("bot.utils.user_store.is_vouchable", return_value=True):
        assert s.authorize(WEB_ID, role="trader", by=OPERATOR) is True


# ── and the migration reads the same one ─────────────────────────────

def test_the_migration_reads_the_same_predicate(tmp_path):
    """The join. If these ever diverge, the script downgrades accounts an admin
    could in fact have approved."""
    import importlib.util
    import json
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_migrate_pred", repo / "scripts" / "migrate_self_admitted_roles.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    path = tmp_path / "users.json"
    path.write_text(json.dumps({WEB_ID: {
        "telegram_id": WEB_ID, "name": "Web user", "role": "trader",
        "tier": "basic", "authorized": True}}), encoding="utf-8")

    assert [r["id"] for r in mig.plan(UserStore(path))["migrate"]] == [WEB_ID]
    with patch.object(mig, "is_vouchable", return_value=True):
        p = mig.plan(UserStore(path))
        assert p["migrate"] == []
        assert [r["id"] for r in p["unattributable"]] == [WEB_ID], (
            "the script classified a web id without consulting is_vouchable")

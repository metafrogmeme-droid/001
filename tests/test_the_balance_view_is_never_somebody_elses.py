"""/livebalance shows the caller's own account, the shared one, or nothing.

`balance_view_executor` answered the operator's executor for every caller
without readable linked keys. Two cases made that wrong:

* **Per-user live.** Under PER_USER_LIVE_ENABLED every other card refuses a
  non-operator the operator's book (`viewer_executor`), and `/livebalance`,
  which a viewer holds through `portfolio`, printed the operator's balance,
  open positions and realized P&L in dollars as theirs.
* **A link that cannot be read, in either mode.** Keys that will not decrypt
  and a credential store nobody could ask both fell back to the operator's
  account, so a person who HAS an account of their own was shown somebody
  else's under "your balance".

Single-account mode is unchanged on purpose: with per-user live off there is
one shared account, and a caller who never linked is shown it, as every other
card shows them.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.config as bot_config
from bot.core.engine import RuneClawEngine

OPERATOR = "999999"
PERSON = "424242"


class _Store:
    def __init__(self, state, creds=None, raises=False):
        self.state, self.creds, self.raises = state, creds, raises

    def _check(self):
        if self.raises:
            raise OSError("store unavailable")

    def get(self, uid):
        self._check()
        return self.creds

    def get_venue(self, uid):
        self._check()
        return "bitget"

    def credential_state(self, uid):
        self._check()
        return self.state


@pytest.fixture
def mode():
    """Set per-user live and the operator id. Both are frozen fields, so the
    write is `object.__setattr__`, restored in a finally."""
    prev_pul = bot_config.CONFIG.per_user_live_enabled
    prev_chat = bot_config.CONFIG.telegram.chat_id
    object.__setattr__(bot_config.CONFIG.telegram, "chat_id", OPERATOR)

    def _set(per_user):
        object.__setattr__(bot_config.CONFIG, "per_user_live_enabled", per_user)

    try:
        yield _set
    finally:
        object.__setattr__(bot_config.CONFIG, "per_user_live_enabled", prev_pul)
        object.__setattr__(bot_config.CONFIG.telegram, "chat_id", prev_chat)


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()          # the operator's (shared) account
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    eng._user_store = None
    return eng


def _view(uid, store):
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store",
               return_value=store):
        return eng, eng.balance_view_executor(uid)


@pytest.mark.parametrize("per_user, uid, store, expect", [
    # per-user live: a person with no link sees nothing, the operator the global
    (True, PERSON, _Store("absent"), "none"),
    (True, OPERATOR, _Store("absent"), "operator"),
    # single-account: one shared account, shown to a person who never linked
    (False, PERSON, _Store("absent"), "operator"),
    # a link that will not decrypt is an account of their own, in both modes
    (False, PERSON, _Store("unreadable"), "none"),
    (True, PERSON, _Store("unreadable"), "none"),
    # a store nobody could ask says nothing about whether they linked
    (False, PERSON, _Store("absent", raises=True), "none"),
    (True, PERSON, _Store("absent", raises=True), "none"),
    (True, OPERATOR, _Store("absent", raises=True), "operator"),
], ids=["per-user-none", "per-user-operator", "shared-unlinked",
        "shared-unreadable", "per-user-unreadable", "shared-store-fault",
        "per-user-store-fault", "operator-store-fault"])
def test_the_resolver(mode, per_user, uid, store, expect):
    mode(per_user)
    eng, ex = _view(uid, store)
    if expect == "none":
        assert ex is None, "somebody else's account was handed to this caller"
    else:
        assert ex is eng.live_executor


def test_a_readable_link_is_their_own_account(mode):
    """The control: linking still shows your own account, in either mode."""
    mode(False)
    creds = {"api_key": "K" * 16, "api_secret": "S" * 16, "passphrase": "pp"}
    eng, ex = _view(PERSON, _Store("readable", creds=creds))
    assert ex is not None and ex is not eng.live_executor
    assert ex._credentials == creds


def test_a_link_on_a_venue_this_build_does_not_know_is_not_the_operators(mode):
    """A record another build wrote, on a venue this one cannot build: the
    person has an account of their own, so they are shown nothing rather
    than the shared one."""
    mode(False)

    class _Unknown(_Store):
        def get_venue(self, uid):
            return "kraken"

    eng, ex = _view(PERSON, _Unknown("readable", creds={"api_key": "K" * 16}))
    assert ex is None
    eng, ex = _view(OPERATOR, _Unknown("readable", creds={"api_key": "K" * 16}))
    assert ex is eng.live_executor


# ── the card ───────────────────────────────────────────────────────────────


def test_the_card_says_which_absence_and_reads_nothing(monkeypatch):
    from bot.skills.telegram_handler import TelegramHandler

    h = TelegramHandler(RuneClawEngine())
    # Past the guard as a seeded id (the guard reads `_get_tg_id` too, so a
    # stranger's id is onboarded instead of reaching the card); the view and
    # the absence are planted, so whose id it is does not matter below.
    h.users.seed_admin(OPERATOR)
    sent: list[str] = []
    monkeypatch.setattr(h.engine, "balance_view_executor", lambda *_a, **_k: None)
    monkeypatch.setattr(h, "_get_tg_id", lambda *_a, **_k: OPERATOR)
    monkeypatch.setattr(
        "bot.skills.chat_runtime.live_account_absence", lambda uid: "unreadable")

    async def _send(update, text, **kw):
        sent.append(text)

    monkeypatch.setattr(h, "_send", _send)
    operator_fetch = AsyncMock()
    monkeypatch.setattr(h.engine.live_executor, "fetch_balance", operator_fetch,
                        raising=False)
    update, ctx = MagicMock(), MagicMock()
    update.effective_user = SimpleNamespace(id=int(OPERATOR), first_name="Op")
    update.callback_query = None
    ctx.args = []
    asyncio.run(h._cmd_livebalance(update, ctx))

    out = "\n".join(sent)
    assert "could not be decrypted" in out, out
    assert "$" not in out, "a figure was printed for an account nobody read"
    assert operator_fetch.await_count == 0

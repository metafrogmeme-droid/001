"""One Bitget account, one executor.

8 October: the operator linked NEW API credentials for the SAME Bitget
sub-account with /connect. The operator's executor (the .env keys) and a
per-user executor (the linked keys) then managed one account, and each took
the other's orders for strangers. One adopted the other's resting OPEN/USDT
limit with no stop on record and booked its fill with stop 0. Its record
stayed "open" after the other executor closed the position, so the unprotected
alert sent a person to place a stop on a position that no longer existed, and
the close was booked twice.

Keys are not the identity (rotated keys differ); Bitget's account UID is.
Linking keys that open the operator's own account is refused on /connect and
on the website's pull, and a stored link of that kind gets no executor: the
operator's ask is the operator's book, anyone else's gets none.

Driven through the real ExchangeCredentialStore, `/connect`, the web pull's
validator and `_executor_for`; only Bitget's account-info reply is replaced.
"""
from __future__ import annotations

import asyncio
import dataclasses
import html
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

import bot.config as config_mod
from bot.core import exchange_credentials as ec
from bot.core.engine import RuneClawEngine
from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.core.live_executor import LiveExecutor

OPERATOR = {"api_key": "OPKEY" + "K" * 10, "api_secret": "OPSEC" + "S" * 10, "passphrase": "oppass"}
ROTATED = {"api_key": "NEWKEY" + "K" * 10, "api_secret": "NEWSEC" + "S" * 10, "passphrase": "newpass"}
OTHER = {"api_key": "USRKEY" + "K" * 10, "api_secret": "USRSEC" + "S" * 10, "passphrase": "usrpass"}

#: Which account each key opens: ROTATED is new keys on the operator's account.
UID_OF = {OPERATOR["api_key"]: "7001", ROTATED["api_key"]: "7001", OTHER["api_key"]: "8002"}


def _probe(uids=UID_OF):
    calls: list = []

    async def probe(api_key, api_secret, passphrase, sandbox=False):
        calls.append(api_key)
        return {"withdraw": "off", "ip_allowlist": [], "account_uid": uids.get(api_key)}

    probe.calls = calls
    return probe


@pytest.fixture
def operator_keys():
    """The operator's Bitget keys on the real (frozen) CONFIG.exchange."""
    ex = config_mod.CONFIG.exchange
    saved = {k: getattr(ex, k) for k in ("api_key", "api_secret", "passphrase", "sandbox")}
    for k, v in OPERATOR.items():
        object.__setattr__(ex, k, v)
    object.__setattr__(ex, "sandbox", False)
    try:
        yield ex
    finally:
        for k, v in saved.items():
            object.__setattr__(ex, k, v)


def _store(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    return ExchangeCredentialStore(creds_file=str(tmp_path / "creds.enc"),
                                   key_file=str(tmp_path / ".key"))


# ── the reading ─────────────────────────────────────────────────────────────

def test_the_account_uid_is_bitgets_userid():
    assert ec.bitget_account_uid({"userId": "7001", "authorities": []}) == "7001"
    assert ec.bitget_account_uid({"userId": 7001}) == "7001"
    for absent in ({}, {"userId": ""}, {"userId": None}, None, "x"):
        assert ec.bitget_account_uid(absent) is None


def test_the_scope_probe_reads_the_uid_off_bitgets_reply(monkeypatch):
    import ccxt.async_support as ccxt_async

    real = ccxt_async.bitget

    class _Bitget(real):
        """ccxt's own client; only the account-info reply is Bitget's stand-in.
        ccxt re-defines its API methods on the class at construction, so the
        reply is bound on the instance."""

        def __init__(self, config=None):
            super().__init__(config)

            async def info(params=None):
                return {"code": "00000", "data": {"userId": "7001",
                                                  "authorities": ["readonly"], "ips": ""}}

            self.privateSpotGetV2SpotAccountInfo = info

    monkeypatch.setattr(ccxt_async, "bitget", _Bitget)
    out = asyncio.run(ec.probe_bitget_key_scope(**{k: v for k, v in OPERATOR.items()}))
    assert out["account_uid"] == "7001"
    assert out["withdraw"] == "off"


def test_rotated_keys_on_the_same_account_are_the_same_account(operator_keys):
    probe = _probe()
    assert asyncio.run(ec.same_bitget_account_as_operator(ROTATED, probe=probe)) is True
    assert asyncio.run(ec.same_bitget_account_as_operator(OTHER, probe=probe)) is False


def test_an_unread_uid_is_not_a_verdict(operator_keys):
    unread = _probe({OPERATOR["api_key"]: "7001"})           # the user's key reads nothing
    assert asyncio.run(ec.same_bitget_account_as_operator(OTHER, probe=unread)) is None
    op_unread = _probe({OTHER["api_key"]: "8002"})           # the operator's reads nothing
    assert asyncio.run(ec.same_bitget_account_as_operator(OTHER, probe=op_unread)) is None


def test_no_operator_keys_asks_nothing():
    ex = config_mod.CONFIG.exchange
    saved = {k: getattr(ex, k) for k in ("api_key", "api_secret", "passphrase")}
    for k in saved:
        object.__setattr__(ex, k, "")
    try:
        probe = _probe()
        assert asyncio.run(ec.same_bitget_account_as_operator(ROTATED, probe=probe)) is None
        assert probe.calls == []
    finally:
        for k, v in saved.items():
            object.__setattr__(ex, k, v)


def test_the_sentence_fits_the_website_s_ack():
    assert len(ec.OPERATOR_ACCOUNT_REFUSAL) <= 180
    assert "operator's own Bitget account" in ec.OPERATOR_ACCOUNT_REFUSAL
    assert "Nothing was stored" in ec.OPERATOR_ACCOUNT_REFUSAL


# ── linking is refused ──────────────────────────────────────────────────────

class _Host:
    def __init__(self):
        self.sent: list = []
        self.engine = SimpleNamespace(invalidate_user_executor=lambda t: None)

    async def _guard(self, update, command="", ctx=None):
        return True

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update):
        return "1001"


def _connect(monkeypatch, store, fields):
    from bot.skills.telegram_handler import TelegramHandler

    async def _ok(venue, f, sandbox=None):
        return True, "52.56 USDT free"

    monkeypatch.setattr(ec, "validate_venue_credentials", _ok)
    monkeypatch.setattr(ec, "get_credential_store", lambda: store)
    monkeypatch.setattr(ec, "probe_bitget_key_scope", _probe())

    async def _delete():
        return None

    update = SimpleNamespace(message=SimpleNamespace(delete=_delete),
                             effective_chat=SimpleNamespace(type="private"))
    host = _Host()
    asyncio.run(TelegramHandler._cmd_connect(
        host, update, SimpleNamespace(args=[fields["api_key"], fields["api_secret"],
                                            fields["passphrase"]])))
    return host.sent[-1]


def test_connect_refuses_new_keys_on_the_operators_account(monkeypatch, tmp_path, operator_keys):
    s = _store(tmp_path)
    out = _connect(monkeypatch, s, ROTATED)
    # Telegram HTML: the card escapes the sentence (its apostrophe included).
    assert html.escape(ec.OPERATOR_ACCOUNT_REFUSAL) in out
    assert not s.has("1001"), "keys for the operator's own account were stored"


def test_connect_links_when_the_uid_cannot_be_read(monkeypatch, tmp_path, operator_keys):
    """Unread is not a match: a key without account-read permission links as
    it always did, rather than being refused on a guess."""
    s = _store(tmp_path)
    from bot.skills.telegram_handler import TelegramHandler

    async def _ok(venue, f, sandbox=None):
        return True, "52.56 USDT free"

    monkeypatch.setattr(ec, "validate_venue_credentials", _ok)
    monkeypatch.setattr(ec, "get_credential_store", lambda: s)
    monkeypatch.setattr(ec, "probe_bitget_key_scope", _probe({OPERATOR["api_key"]: "7001"}))

    async def _delete():
        return None

    host = _Host()
    asyncio.run(TelegramHandler._cmd_connect(
        host, SimpleNamespace(message=SimpleNamespace(delete=_delete),
                              effective_chat=SimpleNamespace(type="private")),
        SimpleNamespace(args=[OTHER["api_key"], OTHER["api_secret"], OTHER["passphrase"]])))
    assert "Bitget account linked" in host.sent[-1]
    assert s.get("1001") == OTHER


def test_connect_links_a_different_account(monkeypatch, tmp_path, operator_keys):
    s = _store(tmp_path)
    out = _connect(monkeypatch, s, OTHER)
    assert "Bitget account linked" in out
    assert s.get("1001") == OTHER


def test_the_web_pull_refuses_them_too(monkeypatch, operator_keys):
    from bot.utils import credential_pull as cp

    async def _ok(venue, f, sandbox=None):
        return True, "ok"

    monkeypatch.setattr(ec, "validate_venue_credentials", _ok)
    monkeypatch.setattr(ec, "probe_bitget_key_scope", _probe())
    verdict, why = cp.default_validator({"venue": "bitget", **ROTATED})
    assert (verdict, why) == (False, ec.OPERATOR_ACCOUNT_REFUSAL)
    verdict, why = cp.default_validator({"venue": "bitget", **OTHER})
    assert (verdict, why) == (True, "")


# ── a link already stored gets no second executor ───────────────────────────

def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()     # the operator's executor, a sentinel
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    eng._executors_to_rebind = set()
    eng._user_live_balance_cache = {}
    eng._user_live_balance_cache_ts = {}
    eng.risk = object()
    eng.slippage = None
    return eng


def _cfg(cfg):
    cfg.per_user_live_enabled = True
    cfg.telegram = SimpleNamespace(chat_id="1001", admin_ids="")


def test_a_stored_link_to_the_operators_account_builds_no_executor(tmp_path, operator_keys):
    s = _store(tmp_path)
    s.set_venue("1001", "bitget", ROTATED)   # the operator, linked to their own account
    s.set_venue("2002", "bitget", ROTATED)   # somebody else holding those keys
    s.set_venue("3003", "bitget", OTHER)     # an account of its own
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=s), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: None):
        _cfg(cfg)
        # Before the check, the duplicate is built (the defect).
        assert isinstance(eng._executor_for("1001"), LiveExecutor)
        marked = asyncio.run(eng.check_operator_account_links(probe=_probe()))
        assert marked == {"1001", "2002"}
        assert "1001" not in eng._user_executors, "the second executor was not dropped"
        # The operator's ask is the operator's book; anyone else gets none.
        assert eng._executor_for("1001") is eng.live_executor
        assert eng._executor_for("2002") is None
        # The other arm: an account of its own keeps its own executor.
        own = eng._executor_for("3003")
        assert isinstance(own, LiveExecutor) and own is not eng.live_executor


def test_an_unread_uid_marks_nobody(tmp_path, operator_keys):
    s = _store(tmp_path)
    s.set_venue("1001", "bitget", ROTATED)
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=s), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: None):
        _cfg(cfg)
        marked = asyncio.run(eng.check_operator_account_links(probe=_probe({})))
        assert marked == set()
        assert isinstance(eng._executor_for("1001"), LiveExecutor)


def test_the_check_runs_at_boot_before_the_rehydrate_and_on_a_clock():
    import ast
    import inspect
    src = inspect.getsource(RuneClawEngine)
    tree = ast.parse(__import__("textwrap").dedent(src))
    calls = [(n.lineno, n.func.attr) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("check_operator_account_links", "_rehydrate_user_executors")]
    lines = sorted(calls)
    first_check = next(ln for ln, a in lines if a == "check_operator_account_links")
    first_rehydrate = next(ln for ln, a in lines if a == "_rehydrate_user_executors")
    assert first_check < first_rehydrate
    assert sum(1 for _, a in lines if a == "check_operator_account_links") >= 2, \
        "no periodic re-check beside the boot one"
    assert RuneClawEngine.OPERATOR_LINK_CHECK_EVERY_S == 6 * 3600.0
    assert dataclasses  # imported for the fixture style above

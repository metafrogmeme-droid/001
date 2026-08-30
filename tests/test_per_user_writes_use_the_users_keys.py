"""RC-2026-011 — a user's stop-loss was signed with the OPERATOR's keys.

`LiveExecutor.__init__` is built for per-user trading: it takes `user_id` and a
`credentials` dict, and its own comment says "credentials, when set, {api_key,
api_secret, passphrase}". The v3 channel ignored them. Every call went through

    BitgetV3Client.from_config()

which reads the global `CONFIG.exchange` — the operator's keys. Four sites, two
of them writes:

    L1390  GET   /api/v3/account/settings          read
    L4996  GET   /api/v3/position/current-position  read
    L5208  POST  /api/v3/trade/place-strategy-order WRITE — the SL/TP
    L8765  POST  /api/v3/trade/close-positions      WRITE — the flash close

THE STOP. A per-user executor holding that user's keys placed their protective
stop on the OPERATOR's account. Two failures at once: the user's live position
carries no stop of its own, and the operator's account acquires a strategy
order for a position it does not hold.

THE FLASH CLOSE IS WORSE. The user's position is not closed, so they stay
exposed — and if the operator holds a position on the same symbol and side,
*theirs* is closed instead. That path runs when something has already gone
wrong.

THE READS MATTER TOO, less loudly: a per-user executor reconciling its user
against the operator's open positions is reading someone else's book.

LATENT, NOT DORMANT. `PER_USER_LIVE_ENABLED` defaults False, so today every
executor IS the operator executor and `from_config()` is the correct source.
It becomes real the moment an operator enables a documented, supported feature
the repo has built machinery for — per-user credential storage, `web_live_gate`
preconditions, per-user eligibility — and nothing warns that stops will land on
the wrong account when they do.

TWO OF THE FOUR ARE @staticmethod and cannot reach `self._credentials`, which
is why the fix threads them as a parameter rather than reading `self`
everywhere. `BitgetV3Client.for_account()` is the single place the question
"whose keys is this?" gets answered.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from bot.core.bitget_v3_client import BitgetV3Client

USER = {"api_key": "USER-KEY", "api_secret": "USER-SECRET",
        "passphrase": "USER-PASS"}


# ── the one decision point ────────────────────────────────────────────
def test_a_user_client_carries_the_users_keys():
    c = BitgetV3Client.for_account(USER)
    assert c._api_key == "USER-KEY"
    assert c._api_secret == "USER-SECRET"
    assert c._passphrase == "USER-PASS"


def test_no_credentials_falls_back_to_the_operator():
    # The operator executor is constructed with credentials=None, and that
    # path must stay byte-identical.
    with patch.object(BitgetV3Client, "from_config",
                      return_value=BitgetV3Client("OP", "OPSEC", "OPPASS")) as m:
        c = BitgetV3Client.for_account(None)
    m.assert_called_once()
    assert c._api_key == "OP"


@pytest.mark.parametrize("creds", [
    None, {}, {"api_key": "K"}, {"api_secret": "S"},
    {"api_key": "", "api_secret": "S"}, {"api_key": "K", "api_secret": ""},
])
def test_incomplete_credentials_fall_back_rather_than_sign_with_half(creds):
    # A key without a secret cannot sign. Treating a half-filled dict as
    # usable would send an unsigned request instead of falling back.
    with patch.object(BitgetV3Client, "from_config",
                      return_value=BitgetV3Client("OP", "OPSEC", "OPPASS")):
        assert BitgetV3Client.for_account(creds)._api_key == "OP"


def test_a_passphrase_is_optional_but_a_secret_is_not():
    c = BitgetV3Client.for_account({"api_key": "K", "api_secret": "S"})
    assert c._api_key == "K" and c._passphrase == ""


# ── the writes, driven ────────────────────────────────────────────────
def _executor(tmp_path, credentials):
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(user_id=7 if credentials else None,
                      credentials=credentials, state_dir=str(tmp_path))
    ex._save_positions = MagicMock()
    return ex


def _capture_keys(monkeypatch):
    """Record which api_key signs each v3 request."""
    import bot.core.bitget_v3_client as v3mod
    seen = []
    real_for_account = BitgetV3Client.for_account

    class _Spy(BitgetV3Client):
        def request(self, method, path, body=None):
            seen.append({"key": self._api_key, "method": method, "path": path})
            return {"code": "00000", "data": {"orderId": "OID-1"}}

    def _for_account(credentials):
        base = real_for_account(credentials)
        return _Spy(base._api_key, base._api_secret, base._passphrase)

    monkeypatch.setattr(v3mod.BitgetV3Client, "for_account",
                        staticmethod(_for_account))
    return seen


def test_a_per_user_stop_is_signed_with_the_users_keys(tmp_path, monkeypatch):
    # THE headline. Before the fix this request carried the operator's key.
    from bot.core.live_executor import LiveExecutor
    from bot.utils.models import Direction

    seen = _capture_keys(monkeypatch)
    monkeypatch.setattr(LiveExecutor, "_fetch_position_margin_mode_v3",
                        staticmethod(lambda _s, *_: "isolated"))
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: asyncio.sleep(0))

    ex = _executor(tmp_path, USER)
    ex._actual_margin_mode = "isolated"
    ex._hedge_mode = False
    ex._last_sltp_error = {}
    ex._client_oid = lambda s: "oid"
    ex._venue = MagicMock()
    try:
        asyncio.run(ex._place_sl_tp_v3("TAO/USDT", Direction.LONG, 1.0,
                                       stop_loss=204.82, take_profit=205.65,
                                       price_precision=2))
    except Exception:
        pass  # transport shape is not what this test is about

    writes = [s for s in seen if s["method"] == "POST"]
    assert writes, "no v3 write was attempted — the path did not run"
    for w in writes:
        assert w["key"] == "USER-KEY", (
            f"a per-user stop was signed with {w['key']!r} — the user's "
            f"position would carry no stop on their own account")


def test_the_operator_executor_still_signs_with_the_operator_keys(
        tmp_path, monkeypatch):
    # The default path (credentials=None) must be unchanged.
    from bot.core.live_executor import LiveExecutor
    from bot.utils.models import Direction

    monkeypatch.setattr(BitgetV3Client, "from_config",
                        staticmethod(lambda: BitgetV3Client("OP", "OPSEC", "P")))
    seen = _capture_keys(monkeypatch)
    monkeypatch.setattr(LiveExecutor, "_fetch_position_margin_mode_v3",
                        staticmethod(lambda _s, *_: "isolated"))
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: asyncio.sleep(0))

    ex = _executor(tmp_path, None)
    ex._actual_margin_mode = "isolated"
    ex._hedge_mode = False
    ex._last_sltp_error = {}
    ex._client_oid = lambda s: "oid"
    ex._venue = MagicMock()
    try:
        asyncio.run(ex._place_sl_tp_v3("TAO/USDT", Direction.LONG, 1.0,
                                       stop_loss=204.82, take_profit=205.65,
                                       price_precision=2))
    except Exception:
        pass
    for s in [s for s in seen if s["method"] == "POST"]:
        assert s["key"] == "OP"


# ── the reads ─────────────────────────────────────────────────────────
def test_the_positions_read_asks_for_the_named_account(monkeypatch):
    # A @staticmethod cannot see self, so the credentials must arrive as an
    # argument or the read silently returns the OPERATOR's book.
    import bot.core.bitget_v3_client as v3mod
    from bot.core.live_executor import LiveExecutor
    seen = {}

    class _Spy(BitgetV3Client):
        def request(self, *_a, **_k):
            seen["key"] = self._api_key
            return {"code": "00000", "data": []}

    monkeypatch.setattr(v3mod.BitgetV3Client, "for_account",
                        staticmethod(lambda c: _Spy(
                            (c or {}).get("api_key", "OP"),
                            (c or {}).get("api_secret", "OPSEC"), "")))
    with patch("bot.core.live_executor.get_venue") as gv:
        gv.return_value.id = "bitget"
        LiveExecutor._fetch_v3_positions_raw(USER)
    assert seen.get("key") == "USER-KEY", (
        "the positions read used the operator's book for a per-user executor")


def test_the_margin_mode_lookup_threads_credentials_through(monkeypatch):
    # Two staticmethods deep: _fetch_position_margin_mode_v3 ->
    # _fetch_v3_positions_raw. Dropping the argument at either hop puts the
    # read back on the operator's account.
    from bot.core.live_executor import LiveExecutor
    got = {}

    def _fake(credentials=None):
        got["creds"] = credentials
        return []

    monkeypatch.setattr(LiveExecutor, "_fetch_v3_positions_raw",
                        staticmethod(_fake))
    LiveExecutor._fetch_position_margin_mode_v3("TAOUSDT", USER)
    assert got["creds"] == USER, "credentials were dropped at the second hop"


# ── the wiring, which no unit test can see ────────────────────────────
def test_no_v3_call_site_still_reads_the_global_config():
    # The defect was four call sites each answering "whose keys?" for
    # themselves. A source scan is the right tool here: it asserts a property
    # of the CALL SITES, which a unit test cannot reach.
    from pathlib import Path

    from tests.source_scan import code_only

    src = code_only(Path("bot/core/live_executor.py").read_text(encoding="utf-8"))
    assert "from_config()" not in src, (
        "a v3 call site still builds its client from the global CONFIG — on a "
        "per-user executor that signs someone else's account")

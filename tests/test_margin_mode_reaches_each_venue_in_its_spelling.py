"""The configured margin mode reaches each venue in a spelling it reads as meant.

`MARGIN_MODE` was read raw, and each consumer compared it to a spelling of its
own. Bitget's word for cross margin is "crossed", and the Bitget path accepts
either. ccxt's Hyperliquid `set_leverage` is cross only for exactly "cross", so
"crossed" went out ISOLATED; its Bybit `set_margin_mode` refuses "crossed", and
the executor swallowed that at debug, leaving the account in whatever mode it
was in. Hyperliquid is one of `PER_USER_EXECUTION_VENUES`, and the operator's
own venue can be set to it.

The setting is validated at boot now (`_env_choice`, the TRADE_MODE rule: a
value that would quietly change behaviour refuses to start), and the non-Bitget
path sends ccxt's spelling. What Bitget receives is left byte-identical: ccxt's
Bitget client reads the order's `marginMode` differently on classic and unified
accounts, and what Bitget's unified endpoint does with each spelling cannot be
checked from here, so no Bitget request changes.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt.async_support as ca
import pytest

from bot.config import CONFIG as REAL
from bot.core.live_executor import LiveExecutor
from bot.core.venues import ccxt_margin_mode, get_venue

ROOT = Path(__file__).resolve().parents[1]


def _boot(mode: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, MARGIN_MODE=mode, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-c",
         "from bot.config import CONFIG; print('MODE=' + CONFIG.exchange.margin_mode)"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)


# ── the setting ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,read", [("isolated", "isolated"), ("cross", "cross"),
                                      ("crossed", "crossed"), ("Crossed", "crossed"),
                                      (" ISOLATED ", "isolated")])
def test_a_margin_mode_it_implements_is_read(raw, read):
    out = _boot(raw)
    assert out.returncode == 0, out.stderr[-400:]
    assert f"MODE={read}" in out.stdout


@pytest.mark.parametrize("raw", ["isolate", "crossmargin", "portfolio"])
def test_any_other_margin_mode_refuses_to_start(raw):
    out = _boot(raw)
    assert out.returncode != 0
    assert "MARGIN_MODE" in (out.stdout + out.stderr)
    assert "margin mode it does not name" in (out.stdout + out.stderr)


def test_the_trade_mode_refusal_keeps_its_own_sentence():
    # The consequence became a parameter; TRADE_MODE's default sentence is the
    # one it always printed.
    env = dict(os.environ, TRADE_MODE="spot", PYTHONPATH=str(ROOT))
    out = subprocess.run([sys.executable, "-c", "import bot.config"], capture_output=True,
                         text=True, env=env, cwd=str(ROOT), timeout=120)
    assert out.returncode != 0
    assert "silently treat it as 'not futures'" in (out.stdout + out.stderr)


def test_ccxts_spelling():
    assert [ccxt_margin_mode(m) for m in ("cross", "crossed", "isolated")] == \
        ["cross", "cross", "isolated"]


# ── the non-Bitget path, driven through real ccxt ─────────────────────────

class _Cfg:
    def __init__(self, mode):
        self.exchange = dataclasses.replace(REAL.exchange, margin_mode=mode)

    def __getattr__(self, name):
        return getattr(REAL, name)


def _executor(tmp_path, venue, creds):
    ex = LiveExecutor(user_id="u1", credentials=creds, venue=venue, state_dir=str(tmp_path))
    ex._compute_target_leverage = lambda symbol, idea=None: 5
    return ex


def _hyperliquid():
    ex = ca.hyperliquid({"walletAddress": "0x" + "ab" * 20, "privateKey": "0x" + "01" * 32,
                         "options": {"defaultType": "swap"}})
    ex.set_markets([ex.safe_market_structure(dict(
        id="0", symbol="BTC/USDC:USDC", base="BTC", quote="USDC", settle="USDC", baseId="0",
        quoteId="USDC", settleId="USDC", type="swap", spot=False, swap=True, contract=True,
        linear=True, inverse=False, contractSize=1, active=True,
        precision={"amount": 0.00001, "price": 0.1}, limits={"leverage": {"max": 40}},
        info={}))])
    return ex


def _stub(ex, answer):
    sent: list = []

    async def fetch(url, method="GET", headers=None, body=None):
        sent.append((url, json.loads(body) if body else None))
        return answer(url, body)

    async def load_markets(reload=False, params={}):
        return ex.markets

    ex.fetch = fetch
    ex.load_markets = load_markets
    return sent


@pytest.mark.parametrize("mode,is_cross", [("crossed", True), ("cross", True),
                                           ("isolated", False)])
def test_hyperliquid_is_set_to_the_margin_mode_configured(tmp_path, mode, is_cross):
    ex = _hyperliquid()
    sent = _stub(ex, lambda url, body: {"status": "ok", "response": {"type": "default"}})
    exr = _executor(tmp_path, "hyperliquid",
                    {"wallet_address": "0x" + "ab" * 20, "agent_private_key": "0x" + "01" * 32})
    with patch("bot.core.live_executor.CONFIG", _Cfg(mode)):
        try:
            asyncio.run(exr._ensure_leverage_generic(ex, "BTC/USDT"))
        finally:
            asyncio.run(ex.close())
    actions = [b["action"] for _u, b in sent
               if isinstance(b, dict) and isinstance(b.get("action"), dict)
               and b["action"].get("type") == "updateLeverage"]
    assert actions, sent
    # Before: "crossed" -> isCross False, an ISOLATED position on a cross config.
    assert all(a["isCross"] is is_cross for a in actions), actions


def _bybit():
    ex = ca.bybit({"apiKey": "k", "secret": "s", "options": {"defaultType": "swap"}})
    ex.set_markets([ex.safe_market_structure(dict(
        id="BTCUSDT", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
        baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
        contract=True, linear=True, inverse=False, contractSize=1, active=True,
        precision={"amount": 0.001, "price": 0.1}, info={}))])
    return ex


def _bybit_answer(url, body):
    if "/v5/user/query-api" in url:
        return {"retCode": 0, "result": {"unified": 1, "uta": 1, "userID": 1}}
    if "/v5/account/info" in url:
        return {"retCode": 0, "result": {"unifiedMarginStatus": 6,
                                         "marginMode": "REGULAR_MARGIN"}}
    return {"retCode": 0, "retMsg": "OK", "result": {"list": []}}


@pytest.mark.parametrize("mode,wanted", [("crossed", "REGULAR_MARGIN"),
                                         ("isolated", "ISOLATED_MARGIN")])
def test_bybit_is_asked_for_the_margin_mode_configured(tmp_path, mode, wanted):
    ex = _bybit()
    sent = _stub(ex, _bybit_answer)
    exr = _executor(tmp_path, "bybit", {"api_key": "k", "api_secret": "s"})
    with patch("bot.core.live_executor.CONFIG", _Cfg(mode)):
        try:
            asyncio.run(exr._ensure_leverage_generic(ex, "BTC/USDT"))
        finally:
            asyncio.run(ex.close())
    sets = [b.get("setMarginMode") for u, b in sent
            if "set-margin-mode" in u and isinstance(b, dict)]
    # Before: ccxt refused "crossed" before sending anything, at debug.
    assert sets == [wanted], sent


def test_the_mismatch_alarm_reads_the_same_spelling(tmp_path, caplog):
    # A venue reporting "cross" under MARGIN_MODE=crossed is what was asked for.
    ex = MagicMock()
    ex.market = MagicMock(return_value={"limits": {}})
    ex.set_leverage = AsyncMock()
    ex.fetch_leverage = AsyncMock(return_value={"leverage": 5})
    ex.fetch_positions = AsyncMock(return_value=[{"marginMode": "cross"}])
    exr = _executor(tmp_path, "hyperliquid",
                    {"wallet_address": "0x" + "ab" * 20, "agent_private_key": "0x" + "01" * 32})
    with patch("bot.core.live_executor.CONFIG", _Cfg("crossed")), \
            caplog.at_level("CRITICAL"):
        asyncio.run(exr._ensure_leverage_generic(ex, "BTC/USDT"))
    assert not [r for r in caplog.records if "MARGIN MODE MISMATCH" in r.getMessage()]
    ex.fetch_positions = AsyncMock(return_value=[{"marginMode": "isolated"}])
    with patch("bot.core.live_executor.CONFIG", _Cfg("crossed")), \
            caplog.at_level("CRITICAL"):
        asyncio.run(exr._ensure_leverage_generic(ex, "BTC/USDT"))
    assert [r for r in caplog.records if "MARGIN MODE MISMATCH" in r.getMessage()]


# ── Bitget receives what it always received ────────────────────────────────

@pytest.mark.parametrize("mode", ["cross", "crossed", "isolated"])
def test_bitget_order_params_carry_the_configured_word(mode):
    assert get_venue("bitget").entry_params(mode, 5)["marginMode"] == mode


@pytest.mark.parametrize("mode", ["cross", "crossed", "isolated"])
def test_bitget_margin_set_carries_the_configured_word(tmp_path, mode):
    ex = MagicMock()
    ex.set_margin_mode = AsyncMock(side_effect=RuntimeError("stop here"))
    ex.privateMixGetV2MixAccountAccount = AsyncMock(side_effect=RuntimeError("stop"))
    exr = LiveExecutor(user_id="u1", credentials={"api_key": "k", "api_secret": "s",
                                                  "passphrase": "p"},
                       venue="bitget", state_dir=str(tmp_path))
    exr._get_exchange = AsyncMock(return_value=ex)
    with patch("bot.core.live_executor.CONFIG", _Cfg(mode)):
        try:
            asyncio.run(exr._ensure_leverage("BTC/USDT:USDT", "LONG"))
        except Exception:
            pass
    assert ex.set_margin_mode.await_args is not None, "the Bitget path set no margin mode"
    assert ex.set_margin_mode.await_args.args[0] == mode


def test_the_leverage_retry_sends_the_same_spelling(tmp_path):
    # The venue reports 3x against a 5x target once: the retry sets leverage
    # again, and it must not fall back to the configured word.
    ex = MagicMock()
    ex.market = MagicMock(return_value={"limits": {}})
    ex.set_leverage = AsyncMock()
    ex.fetch_leverage = AsyncMock(side_effect=[{"leverage": 3}, {"leverage": 5}])
    ex.fetch_positions = AsyncMock(return_value=[])
    exr = _executor(tmp_path, "hyperliquid",
                    {"wallet_address": "0x" + "ab" * 20, "agent_private_key": "0x" + "01" * 32})
    with patch("bot.core.live_executor.CONFIG", _Cfg("crossed")):
        asyncio.run(exr._ensure_leverage_generic(ex, "BTC/USDT"))
    calls = ex.set_leverage.await_args_list
    assert len(calls) == 2, calls
    assert [c.kwargs["params"]["marginMode"] for c in calls] == ["cross", "cross"]

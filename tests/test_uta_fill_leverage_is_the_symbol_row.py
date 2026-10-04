"""The post-fill guard was left on the position row after the UTA settings read.

A unified account's symbol row can confirm 5x — the leverage the order was
approved at — while ``fetch_positions`` still reports the sticky default
(~20). The guard flattened that fill in the same minute. The symbol row is
the reading. A missing row is not 20 and not 0. A position row of 20 on an
account that is not UTA is still a confirmed overshoot and still closes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from bot.config import CONFIG
from bot.core.live_executor import (
    LiveExecutor,
    bitget_margin_mode,
    uta_symbol_leverage,
)


def _order_mode() -> str:
    mode = bitget_margin_mode(CONFIG.exchange.margin_mode)
    assert mode is not None
    return mode


def _other_mode() -> str:
    return "crossed" if _order_mode() == "isolated" else "isolated"


def _run(coro):
    return asyncio.run(coro)


def _settings(*rows: dict) -> dict:
    return {"code": "00000", "data": {"symbolConfigList": list(rows)}}


def _row(leverage: str, mode: str) -> dict:
    return {
        "category": "USDT-FUTURES",
        "symbol": "JUPUSDT",
        "marginMode": mode,
        "leverage": leverage,
    }


def _exchange(position_leverage: int):
    exchange = type("E", (), {})()

    async def fetch_positions(symbols=None, params=None):
        return [{
            "symbol": "JUP/USDT:USDT",
            "side": "long",
            "contracts": 12.0,
            "leverage": position_leverage,
            "entryPrice": 0.5,
            "markPrice": 0.50015,
            "unrealizedPnl": 0.0,
            "initialMargin": 1.0,
        }]

    exchange.fetch_positions = fetch_positions
    return exchange


def _executor(tmp_path, payload: dict, *, uta: bool):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._is_uta = uta
    seen: dict = {}

    async def _read(symbol: str, margin_mode=None):
        seen["symbol"] = symbol
        seen["mode"] = margin_mode
        return uta_symbol_leverage(payload, symbol, margin_mode)

    ex._read_uta_symbol_leverage = _read
    ex.close_position = AsyncMock(return_value="closed at $0.50")
    ex._seen = seen
    return ex


def _pos():
    from types import SimpleNamespace
    return SimpleNamespace(
        symbol="JUP/USDT:USDT", direction="LONG", leverage=5,
        sl_order_id=None, tp_order_id=None)


def test_a_settings_read_of_the_approved_leverage_does_not_flatten(tmp_path):
    """Position row 20, isolated symbol row 5, crossed row 20. The order is
    isolated. The 20 on the position is not the fill."""
    ex = _executor(
        tmp_path,
        _settings(_row("5", _order_mode()), _row("20", _other_mode())),
        uta=True)
    verify = _run(ex._verify_position_exists(
        _exchange(20), "JUP/USDT:USDT", "LONG"))
    assert verify["confirmed"] is True
    assert verify["leverage"] == 5
    msg = _run(ex._guard_fill_leverage(
        _exchange(20), "t1", _pos(), 5, "market fill"))
    assert msg is None
    ex.close_position.assert_not_awaited()


def test_a_confirmed_20_against_5_still_closes(tmp_path):
    ex = _executor(
        tmp_path,
        _settings(_row("20", _order_mode()), _row("5", _other_mode())),
        uta=True)
    verify = _run(ex._verify_position_exists(
        _exchange(5), "JUP/USDT:USDT", "LONG"))
    assert verify["leverage"] == 20
    msg = _run(ex._guard_fill_leverage(
        _exchange(5), "t1", _pos(), 5, "market fill"))
    assert msg is not None
    assert "20x" in msg and "5x" in msg
    ex.close_position.assert_awaited()
    assert ex.close_position.await_args.kwargs.get("reason") == "leverage_overshoot"


def test_an_unknown_symbol_row_does_not_flatten_the_sticky_20(tmp_path):
    """The only row is crossed. The order is isolated. That is not a
    measurement of 20, and the position row's 20 does not become one."""
    ex = _executor(
        tmp_path, _settings(_row("20", _other_mode())), uta=True)
    verify = _run(ex._verify_position_exists(
        _exchange(20), "JUP/USDT:USDT", "LONG"))
    assert verify["confirmed"] is True
    assert verify["leverage"] == 0
    msg = _run(ex._guard_fill_leverage(
        _exchange(20), "t1", _pos(), 5, "market fill"))
    assert msg is None
    ex.close_position.assert_not_awaited()


def test_a_classic_position_at_20_still_closes(tmp_path):
    """The symbol row is a UTA document. A classic account's position
    leverage is still the reading the guard closes on."""
    ex = _executor(
        tmp_path, _settings(_row("5", _order_mode())), uta=False)
    verify = _run(ex._verify_position_exists(
        _exchange(20), "JUP/USDT:USDT", "LONG"))
    assert verify["leverage"] == 20
    assert "symbol" not in ex._seen
    msg = _run(ex._guard_fill_leverage(
        _exchange(20), "t1", _pos(), 5, "market fill"))
    assert msg is not None
    ex.close_position.assert_awaited()

"""The position sync rewrote a unified account's fill from the row the guard discards.

PR 523 made the post-fill guard read a unified (UTA) account's leverage off
the symbol-config row of ``GET /api/v3/account/settings``, because the
position row's ``leverage`` can stay at Bitget's sticky default (~20) after
that row has confirmed the approved 5x. ``execute()`` then recorded
``position.leverage = 5`` and ``cost_usd = notional / 5``, and the guard
kept the fill. ``sync_positions_from_exchange`` was not touched: at startup,
every five minutes and on the limit-fill path it read the same v3 position
document, found ``leverage: "20"`` against the recorded 5, audited
``leverage_sync 5x → 20x``, rewrote the leverage, quartered ``cost_usd``,
cleared the unread markers and saved. The card printed 20x and a quarter of
the margin committed; the published "on margin" return was computed on it.
Two readings of one quantity, with the sync's the one the record kept.

One reading now. The sync asks ``governing_fill_leverage`` the way the guard
does: the symbol row on a unified account, read ONCE per pass for the mode
the orders use; the position row on a classic one. Unknown is unknown — an
account no probe has answered, a settings document that would not read, or
a symbol with no row leaves the record alone and audits ``leverage_sync``
UNREAD. The pre-order check reads the same row: a unified account's position
row used to refuse an order on a held symbol with a CRITICAL
``leverage_abort`` on a field its own text said does not decide the fill,
before the symbol row was asked. And a fill the venue confirmed with no
leverage the guard may act on is recorded at the requested figure and SAYS
so, on the card and in the audit, instead of falling through in silence.

The refuter's reproduction from the review of 523 is the first test.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.bitget_v3_client as v3mod
from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core.live_executor import (
    LiveExecutor,
    LivePosition,
    bitget_margin_mode,
    governing_fill_leverage,
)
from tests.leverage_drive import drive_ensure_leverage

ORDER_MODE = bitget_margin_mode(CONFIG.exchange.margin_mode)
assert ORDER_MODE is not None
OTHER_MODE = "crossed" if ORDER_MODE == "isolated" else "isolated"

SYMBOL = "JUP/USDT:USDT"
BITGET_SYMBOL = "JUPUSDT"
ENTRY, QTY = 0.5, 12.0
NOTIONAL = ENTRY * QTY


def _run(coro):
    return asyncio.run(coro)


def _v3_row(leverage: str = "20", symbol: str = BITGET_SYMBOL) -> dict:
    """One row of ``/api/v3/position/current-position``, ccxt's UTA sample
    shape, carrying the sticky leverage."""
    return {
        "category": "USDT-FUTURES", "symbol": symbol, "marginCoin": "USDT",
        "holdMode": "hedge_mode", "posSide": "long", "marginMode": ORDER_MODE,
        "total": str(QTY), "available": str(QTY), "leverage": leverage,
        "avgPrice": str(ENTRY), "markPrice": "0.50015", "unrealisedPnl": "0",
    }


def _row(leverage: str, mode: str, symbol: str = BITGET_SYMBOL) -> dict:
    return {"category": "USDT-FUTURES", "symbol": symbol,
            "marginMode": mode, "leverage": leverage}


def _settings(*rows: dict) -> dict:
    return {"code": "00000", "data": {"symbolConfigList": list(rows)}}


APPROVED = _settings(_row("5", ORDER_MODE), _row("20", OTHER_MODE))


class _V3Client:
    """Stands in for ``BitgetV3Client`` at the wire only.

    ``for_account`` is patched to hand this back, so ``_fetch_v3_positions_raw``,
    ``_read_uta_settings`` and ``_read_uta_symbol_leverage`` are the real
    methods reading real documents. ``paths`` is every path asked, in order,
    so a test can count the settings reads.
    """

    has_credentials = True

    def __init__(self, positions, settings) -> None:
        self.positions = positions
        self.settings = settings
        self.paths: list[str] = []

    def request(self, method: str, path: str, body_dict=None, timeout: float = 10):
        self.paths.append(path)
        if path.startswith("/api/v3/account/settings"):
            if isinstance(self.settings, Exception):
                raise self.settings
            return self.settings
        if path.startswith("/api/v3/position/current-position"):
            return {"code": "00000", "data": {"list": list(self.positions)}}
        raise AssertionError(f"unexpected v3 path {path}")

    def get(self, path: str, timeout: float = 10):
        return self.request("GET", path, None, timeout)


def _position(leverage: int = 5, **over) -> LivePosition:
    base = dict(trade_id="T1", symbol=SYMBOL, direction="LONG", entry_price=ENTRY,
                quantity=QTY, cost_usd=NOTIONAL / leverage, stop_loss=0.45,
                take_profit=0.6, leverage=leverage, status="open")
    base.update(over)
    return LivePosition(**base)


def _executor(tmp_path, monkeypatch, *, uta, positions=None, settings=APPROVED):
    """A real executor over a stubbed v3 wire. Returns it, the wire, the
    audits it wrote and the saves it made."""
    client = _V3Client(positions if positions is not None else [_v3_row()], settings)
    monkeypatch.setattr(v3mod.BitgetV3Client, "for_account",
                        classmethod(lambda cls, creds: client))
    audits: list[dict] = []
    monkeypatch.setattr(le, "audit", lambda log, msg, **kw: audits.append({"msg": msg, **kw}))
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._is_uta = uta
    ex._risk_engine = MagicMock()
    saves: list[int] = []
    ex._save_positions = lambda *a, **k: saves.append(1)
    return ex, client, audits, saves


def _by(audits, action, result=None):
    return [a for a in audits if a.get("action") == action
            and (result is None or a.get("result") == result)]


class _Exchange:
    """ccxt-shaped: ``fetch_positions`` answers the parsed v3 row."""

    options = {"uta": True}

    def __init__(self, leverage: int = 20) -> None:
        self.leverage = leverage

    async def fetch_positions(self, symbols=None, params=None):
        return [{"symbol": SYMBOL, "side": "long", "contracts": QTY,
                 "leverage": self.leverage, "entryPrice": ENTRY,
                 "markPrice": 0.50015, "unrealizedPnl": 0.0,
                 "initialMargin": NOTIONAL / self.leverage}]


# ── the reproduction: guard, record, sync ─────────────────────────────────

def test_a_unified_fill_the_guard_kept_at_5x_is_not_rewritten_to_the_sticky_20(tmp_path, monkeypatch):
    """Step 1: the guard reads 5 off the symbol row against a position row of
    20. Step 2: the record holds 5x and notional/5. Step 3: the sync reads the
    SAME document and keeps it."""
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=True)
    verify = _run(ex._verify_position_exists(_Exchange(20), SYMBOL, "LONG"))
    assert verify["confirmed"] is True and verify["leverage"] == 5
    pos = _position(leverage=verify["leverage"])
    ex._positions = {"T1": pos}
    wire.paths.clear()

    _run(ex.sync_positions_from_exchange())

    assert (pos.leverage, pos.cost_usd) == (5, pytest.approx(NOTIONAL / 5))
    assert _by(audits, "leverage_sync") == []
    assert saves == []
    assert [p for p in wire.paths if p.startswith("/api/v3/account/settings")] == [
        "/api/v3/account/settings"]


def test_the_guard_and_the_sync_are_one_reading():
    """The function the sync asks is the guard's. A symbol row of 5 governs
    whatever the position row says; no row is unknown, not 0 and not 20."""
    row5 = {"value": 5, "field": "symbolConfigList.leverage", "governs": True, "mode": ORDER_MODE}
    unknown = {"value": None, "field": None, "governs": None, "mode": None}
    assert governing_fill_leverage("20", row5, uta=True) == 5
    assert governing_fill_leverage("20", unknown, uta=True) is None
    assert governing_fill_leverage("20", row5, uta=False) == 20
    assert governing_fill_leverage("abc", None, uta=False) is None


def test_a_symbol_row_that_differs_from_the_record_is_written_and_names_its_source(tmp_path, monkeypatch):
    """The sync still corrects a stale record — from the row that states the
    fill's leverage, and the audit says which row."""
    ex, wire, audits, saves = _executor(
        tmp_path, monkeypatch, uta=True,
        settings=_settings(_row("10", ORDER_MODE), _row("20", OTHER_MODE)))
    pos = _position(leverage=5)
    setattr(pos, "adoption_unread", ("margin", "leverage"))
    ex._positions = {"T1": pos}

    _run(ex.sync_positions_from_exchange())

    assert (pos.leverage, pos.cost_usd) == (10, pytest.approx(NOTIONAL / 10))
    assert pos.adoption_unread == ()
    updated = _by(audits, "leverage_sync", "UPDATED")
    assert len(updated) == 1
    assert updated[0]["data"] == {"trade_id": "T1", "old": 5, "new": 10, "source": "symbol_row"}
    assert "symbol row" in updated[0]["msg"]
    assert saves == [1]


def test_the_settings_document_is_read_once_for_several_positions(tmp_path, monkeypatch):
    ex, wire, audits, saves = _executor(
        tmp_path, monkeypatch, uta=True,
        positions=[_v3_row(), _v3_row(symbol="NEARUSDT")],
        settings=_settings(_row("5", ORDER_MODE), _row("5", ORDER_MODE, "NEARUSDT")))
    ex._positions = {
        "T1": _position(leverage=5),
        "T2": _position(leverage=5, trade_id="T2", symbol="NEAR/USDT:USDT"),
    }
    _run(ex.sync_positions_from_exchange())
    assert wire.paths.count("/api/v3/account/settings") == 1
    assert all(p.leverage == 5 for p in ex._positions.values())


# ── unknown is unknown ────────────────────────────────────────────────────

def test_a_document_that_will_not_read_leaves_every_record_and_says_so(tmp_path, monkeypatch):
    ex, wire, audits, saves = _executor(
        tmp_path, monkeypatch, uta=True, settings=RuntimeError("502"))
    pos = _position(leverage=5)
    ex._positions = {"T1": pos}

    _run(ex.sync_positions_from_exchange())

    assert (pos.leverage, pos.cost_usd) == (5, pytest.approx(NOTIONAL / 5))
    assert _by(audits, "leverage_sync", "UPDATED") == []
    unread = _by(audits, "leverage_sync", "UNREAD")
    assert len(unread) == 1
    assert unread[0]["data"] == {"open_positions": 1, "reason": "settings_unread"}
    assert "not rewritten from the position row" in unread[0]["msg"]
    ex._risk_engine.record_warning.assert_any_call("position_sync_settings")
    assert saves == []


def test_a_symbol_with_no_row_for_the_orders_mode_keeps_its_record_and_says_so(tmp_path, monkeypatch):
    """The only row is the OTHER margin mode's 20. That is not this fill's
    leverage, and neither is the position row's 20."""
    ex, wire, audits, saves = _executor(
        tmp_path, monkeypatch, uta=True, settings=_settings(_row("20", OTHER_MODE)))
    pos = _position(leverage=5)
    ex._positions = {"T1": pos}

    _run(ex.sync_positions_from_exchange())

    assert (pos.leverage, pos.cost_usd) == (5, pytest.approx(NOTIONAL / 5))
    unread = _by(audits, "leverage_sync", "UNREAD")
    assert len(unread) == 1
    assert unread[0]["data"]["reason"] == "symbol_row_missing"
    assert unread[0]["data"]["position_row"] == "20"
    assert f"no {ORDER_MODE} row" in unread[0]["msg"]
    assert "sticky figure" in unread[0]["msg"]
    assert saves == []


def test_a_classic_account_still_reads_the_position_row(tmp_path, monkeypatch):
    """Both arms: the position row IS the reading there, and the settings
    document is never asked for."""
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=False)
    pos = _position(leverage=5)
    ex._positions = {"T1": pos}

    _run(ex.sync_positions_from_exchange())

    assert (pos.leverage, pos.cost_usd) == (20, pytest.approx(NOTIONAL / 20))
    updated = _by(audits, "leverage_sync", "UPDATED")
    assert len(updated) == 1 and updated[0]["data"]["source"] == "position_row"
    assert "/api/v3/account/settings" not in wire.paths
    assert saves == [1]


def test_an_account_no_probe_has_answered_is_asked_once_and_left_alone(tmp_path, monkeypatch):
    """`_is_uta` is None until a probe answers. The sync asks through the
    monitoring pass's spaced probe; a probe that does not answer leaves the
    record alone rather than reading either row."""
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=None)
    probes: list[int] = []

    async def _probe():
        probes.append(1)

    ex._probe_hold_mode_if_unknown = _probe
    pos = _position(leverage=5)
    ex._positions = {"T1": pos}

    _run(ex.sync_positions_from_exchange())

    assert probes == [1]
    assert (pos.leverage, pos.cost_usd) == (5, pytest.approx(NOTIONAL / 5))
    unread = _by(audits, "leverage_sync", "UNREAD")
    assert len(unread) == 1
    assert unread[0]["data"] == {"open_positions": 1, "reason": "account_type_unknown"}
    assert "/api/v3/account/settings" not in wire.paths
    assert saves == []


def test_a_probe_that_answers_unified_reads_the_symbol_row_in_the_same_pass(tmp_path, monkeypatch):
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=None)

    async def _probe():
        ex._is_uta = True

    ex._probe_hold_mode_if_unknown = _probe
    pos = _position(leverage=5)
    ex._positions = {"T1": pos}

    _run(ex.sync_positions_from_exchange())

    assert (pos.leverage, pos.cost_usd) == (5, pytest.approx(NOTIONAL / 5))
    assert _by(audits, "leverage_sync") == []
    assert wire.paths.count("/api/v3/account/settings") == 1


def test_a_probe_that_raises_does_not_take_the_sync_down(tmp_path, monkeypatch):
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=None)

    async def _probe():
        raise RuntimeError("probe exploded")

    ex._probe_hold_mode_if_unknown = _probe
    pos = _position(leverage=5)
    ex._positions = {"T1": pos}
    _run(ex.sync_positions_from_exchange())
    assert pos.leverage == 5
    assert _by(audits, "leverage_sync", "UNREAD")[0]["data"]["reason"] == "account_type_unknown"


def test_the_document_read_names_the_exception_class_only(tmp_path, monkeypatch, caplog):
    ex, wire, audits, saves = _executor(
        tmp_path, monkeypatch, uta=True, settings=RuntimeError("secret-looking text"))
    import logging
    with caplog.at_level(logging.DEBUG, logger="bot.core.live_executor"):
        assert _run(ex._read_uta_settings()) is None
    assert "RuntimeError" in caplog.text
    assert "secret-looking" not in caplog.text


def test_a_document_that_is_not_a_document_is_unread(tmp_path, monkeypatch):
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=True, settings=["not", "a", "dict"])
    assert _run(ex._read_uta_settings()) is None
    read = _run(ex._read_uta_symbol_leverage(SYMBOL, ORDER_MODE))
    assert read["value"] is None


def test_a_document_in_hand_is_not_read_again(tmp_path, monkeypatch):
    ex, wire, audits, saves = _executor(tmp_path, monkeypatch, uta=True)
    read = _run(ex._read_uta_symbol_leverage(SYMBOL, ORDER_MODE, settings=APPROVED))
    assert read["value"] == 5
    assert wire.paths == []


# ── the pre-order check reads the same row ────────────────────────────────

# The shared driver places its order on TRX/USDT; the rows are that symbol's.
DRIVE_SYMBOL = "TRX/USDT"
DRIVE_ROW = "TRXUSDT"


def _held_row(leverage: int) -> dict:
    return {"symbol": DRIVE_SYMBOL, "side": "long", "contracts": QTY, "leverage": leverage,
            "marginMode": ORDER_MODE, "info": {"marginMode": ORDER_MODE},
            "entryPrice": ENTRY, "markPrice": 0.50015}


def _drive_settings(leverage: str) -> dict:
    # One row: the shared driver's stub reads the document WITHOUT the mode
    # filter (its own comment says why), so a second mode's row would be the
    # reading here and the question would change.
    return _settings(_row(leverage, ORDER_MODE, DRIVE_ROW))


def test_the_pre_order_check_reads_the_symbol_row_before_a_held_symbols_position_row(monkeypatch):
    """UTA (the v2 read answers 40085), a position row of the sticky 20 on
    the symbol, the symbol row 5. The row that refused the order is not
    asked; the row that states the fill is, and the order proceeds."""
    out = drive_ensure_leverage(
        [Exception("40085 unified account")], positions=[_held_row(20)], target=5,
        margin_mode=ORDER_MODE, side="long", uta_settings=_drive_settings("5"),
        monkeypatch=monkeypatch)
    assert out.aborted is False, out.why
    assert out.position_reads == 0
    assert out.uta_reads == 1


def test_a_symbol_row_past_the_ratio_still_aborts_before_the_order(monkeypatch):
    """The same drive with the symbol row at 20: the row IS read (the
    position row is not), and a confirmed overshoot still refuses."""
    out = drive_ensure_leverage(
        [Exception("40085 unified account")], positions=[_held_row(20)], target=5,
        margin_mode=ORDER_MODE, side="long", uta_settings=_drive_settings("20"),
        monkeypatch=monkeypatch)
    assert out.aborted is True
    assert "symbol config at 20x" in out.why
    assert out.position_reads == 0


def test_a_classic_pre_order_check_still_reads_the_position_row(monkeypatch):
    """The v2 read fails for another reason: not unified, and the position
    row is still the second reading there."""
    out = drive_ensure_leverage(
        [Exception("timeout")], positions=[_held_row(20)], target=5,
        margin_mode=ORDER_MODE, side="long", uta_settings=_drive_settings("5"),
        monkeypatch=monkeypatch)
    assert out.position_reads == 1
    assert out.uta_reads == 0
    assert out.aborted is True
    assert "position at 20x" in out.why


# ── a confirmed fill with no leverage the guard may act on says so ───────

from tests.test_an_estimated_entry_says_so import Venue, _idea  # noqa: E402

STATED_ENTRY = 4012.5


def _drive_fill(tmp_path, *, symbol_row):
    """The real ``execute()`` on a unified account whose settings document
    answers ``symbol_row`` for the order's mode (None: no row)."""
    venue = Venue(row_entry=STATED_ENTRY)
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    ex._is_uta = True
    ex._hedge_mode = False
    ex._slippage_tracker = SimpleNamespace(record=lambda **kw: None)
    RUNTIME.leverage_override = None
    audits: list[dict] = []
    ex._record_warning = lambda key: None

    async def _read(symbol, margin_mode=None, *, settings=None):
        if symbol_row is None:
            return {"value": None, "field": None, "governs": None, "mode": None}
        return {"value": symbol_row, "field": "symbolConfigList.leverage",
                "governs": True, "mode": bitget_margin_mode(margin_mode)}

    ex._read_uta_symbol_leverage = _read

    async def _sl_tp(*a, **k):
        return ("sl1", "tp1")

    async def _sync(*a, **k):
        return None

    try:
        with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"msg": msg, **kw})), \
             patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
             patch.object(type(CONFIG), "is_live", return_value=True), \
             patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
             patch.object(LiveExecutor, "sync_positions_from_exchange", _sync), \
             patch.object(LiveExecutor, "_post_fill_slippage_guard", AsyncMock(return_value=(None, ""))):
            card = asyncio.run(ex.execute(_idea(), size_usd=200.0, order_type="market"))
    finally:
        RUNTIME.leverage_override = None
    return ex._positions["TI-EST"], card, audits


def _leverage_line(card: str) -> str:
    return next(ln for ln in card.splitlines() if ln.startswith("- Leverage:"))


def test_a_found_fill_with_no_symbol_row_is_recorded_at_the_requested_figure_and_says_so(tmp_path):
    pos, card, audits = _drive_fill(tmp_path, symbol_row=None)
    assert pos.leverage == 5
    assert pos.cost_usd == pytest.approx(pos.entry_price * pos.quantity / 5)
    line = _leverage_line(card)
    assert line.startswith("- Leverage: <code>5x</code> ⚠️ as requested")
    assert "NOT verified" in line
    unread = [a for a in audits if a.get("action") == "leverage_unverified_on_fill"]
    assert len(unread) == 1 and unread[0]["result"] == "UNREAD"
    assert unread[0]["data"]["requested"] == 5
    assert unread[0]["data"]["reason"] == "symbol_row_unread"
    assert "position was found but" in unread[0]["msg"]
    assert "requested 5x" in unread[0]["msg"]


def test_a_found_fill_with_a_symbol_row_carries_no_such_note(tmp_path):
    pos, card, audits = _drive_fill(tmp_path, symbol_row=5)
    assert pos.leverage == 5
    assert _leverage_line(card) == "- Leverage: <code>5x</code>"
    assert [a for a in audits if a.get("action") == "leverage_unverified_on_fill"] == []


def test_the_card_line_is_driven_directly(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    idea = SimpleNamespace(id="t1", asset="BTC/USDT", direction=le.Direction.LONG,
                           entry_price=100.0, stop_loss=98.0, take_profit=104.0,
                           strategy_type="swing")
    kw = dict(idea=idea, side="buy", leverage=5, is_futures=True, fill_price=100.0,
              filled_qty=5.0, cost=100.0, order_id="o1", sl_id="sl1", tp_id="tp1",
              trailing_st=None, confirmed=True, position_confirmed=True,
              verify={"failure_stage": ""}, exchange_fees=0.0, _lev_mismatch=None,
              _lev_close_failed=False)
    plain = ex._entry_filled_card(**kw)
    noted = ex._entry_filled_card(**kw, leverage_unverified=True)
    assert _leverage_line(plain) == "- Leverage: <code>5x</code>"
    assert _leverage_line(noted).startswith("- Leverage: <code>5x</code> ⚠️ as requested")
    assert noted.count("- SL:") == 1 and plain.count("- SL:") == 1

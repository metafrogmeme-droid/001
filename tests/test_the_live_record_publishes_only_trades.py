"""The live publishing path counts what every other surface counts.

`bot.utils.trade_filter` is the one rule for what counts as a trade: an
adopted or injected position is not the engine's decision, and an order that
never filled has no outcome. `sync_portfolio` applies it before anything
reaches the website, whose schema stores neither `trade_id` nor
`close_reason` and so cannot apply it itself.

On the LIVE path the rule never ran. `_sync_live_state_to_website` turned each
closed position into a dict carrying neither field, and the rule read them
with `getattr`, which a dict answers with the default. Driven: a limit order
that never filled (`stale_pending`, pnl 0.0) and an adopted orphan's stop-out
were both sent, so the public track record counted the unfilled order as a
flat trade in its win rate and the orphan's loss in its profit factor. The
scan payload read the closed-trade FILE -- dicts again -- through no filter
at all.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import bot.skills.scan_skill as ss
import bot.utils.website_sync as ws
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LivePosition, closed_trade_row
from bot.utils.trade_filter import is_adopted, is_countable

UTC = timezone.utc


def _pos(tid, reason, pnl, close):
    return LivePosition(
        trade_id=tid, symbol="SOL/USDT:USDT", direction="LONG",
        entry_price=150.0, quantity=1.0, cost_usd=30.0, stop_loss=140.0,
        take_profit=170.0, leverage=5, status="closed",
        opened_at=datetime(2026, 9, 20, tzinfo=UTC),
        closed_at=datetime(2026, 9, 20, 5, tzinfo=UTC),
        close_price=close, pnl_usd=pnl, close_reason=reason)


def _book():
    return [_pos("T-1", "TP HIT", 12.0, 162.0),
            _pos("T-2", "stale_pending", 0.0, None),       # never filled
            _pos("TI-adopted-SOL-1", "SL HIT", -30.0, 120.0)]  # an orphan


@pytest.fixture
def wire(monkeypatch):
    posts: list = []
    monkeypatch.setattr(
        ws, "_post",
        lambda path, data, **kw: posts.append((path, data)) or {"ok": True})
    monkeypatch.setattr(ws, "sync_in_background",
                        lambda *a: ws.sync_portfolio(*a))
    return posts


def _sync(closed):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = SimpleNamespace(
        open_positions=[], closed_positions=closed,
        closed_trades_read_failed=False)
    eng.resolve_display_equity_sync = lambda: (812.5, "live")
    eng._sync_live_state_to_website()


def test_the_live_sync_sends_only_the_trade(wire):
    _sync(_book())
    sent = wire[-1][1]["closed_trades"]
    assert [(r["pnl"], r["exit_price"]) for r in sent] == [(12.0, 162.0)], (
        "a never-filled order or an adopted orphan reached the public record")


def test_the_wire_does_not_grow_the_two_fields(wire):
    """The rule needs `trade_id` and `close_reason`; the website does not
    store them, and the payload is the same shape it always was."""
    _sync(_book())
    for row in wire[-1][1]["closed_trades"]:
        assert "trade_id" not in row and "close_reason" not in row, row


def test_a_close_with_no_reason_on_record_is_still_published(wire):
    """Fail-safe toward counting: an absent reason is not evidence that
    nothing happened."""
    _sync([_pos("T-9", None, -7.0, 143.0)])
    assert [r["pnl"] for r in wire[-1][1]["closed_trades"]] == [-7.0]


@pytest.mark.parametrize("shape", [dict, SimpleNamespace])
def test_the_rule_reads_a_dict_as_it_reads_an_object(shape):
    assert is_adopted(shape(trade_id="TI-adopted-X"))
    assert is_adopted(shape(trade_id="TI-injected-X"))
    assert not is_adopted(shape(trade_id="T-1"))
    assert not is_countable(shape(trade_id="TI-adopted-X", close_reason="TP"))
    assert not is_countable(shape(trade_id="T", close_reason=" Stale_Pending "))
    assert is_countable(shape(trade_id="T", close_reason="TP HIT"))
    assert is_countable(shape())                 # nothing on record: counted


@pytest.fixture
def record(tmp_path, monkeypatch):
    """The scan reader over a planted file; the venue leg is refused, as in
    `test_scan_reads_the_executors_record.py`."""
    path = tmp_path / "closed_trades.json"
    monkeypatch.setattr(ss, "_closed_trades_file", lambda: str(path))

    class _Refuses:
        def set_sandbox_mode(self, *a, **k):
            pass

        def fetch_balance(self, *a, **k):
            raise RuntimeError("the venue is not part of this test")

        fetch_positions = fetch_balance

    fake = types.ModuleType("ccxt")
    fake.bitget = lambda *a, **k: _Refuses()
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    return path


def test_the_scan_payload_counts_only_the_trade(record):
    record.write_text(json.dumps([closed_trade_row(p) for p in _book()],
                                 default=str))
    data = ss._fetch_live_exchange_data()
    assert data["total_trades"] == 1
    assert data["win_rate"] == 100.0
    assert data["net_pnl"] == 12.0
    assert [t["pnl"] for t in data["closed_trades"]] == [12.0]

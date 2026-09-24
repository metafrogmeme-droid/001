"""Open positions + stop-loss PROTECTION TRUTH web endpoint.

The serializers are pure (object-in, dict-out) so they're unit-tested directly;
the async handler needs a live aiohttp request + engine, so its read-only /
gated contract is checked by source assertion (same approach as the other
gateway handlers).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from bot.web import user_gateway as ug


# ── LIVE serializer: sl_order_id IS the protection truth ─────────────────────

def _live(**kw):
    base = dict(symbol="BTC/USDT:USDT", direction="LONG", entry_price=100.0,
                stop_loss=95.0, take_profit=115.0, quantity=1.0, cost_usd=20.0,
                leverage=5.0, sl_order_id="x1", tp_order_id="y1",
                strategy_type="swing", opened_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_live_row_marks_protected_when_sl_order_id_present():
    row = ug._live_position_row(_live(sl_order_id="abc"))
    assert row["sl_order"] == "exchange"
    assert row["sl_protected"] is True
    assert row["unprotected"] is False
    assert row["pair"] == "BTC"
    # SL distance from entry (100 → 95) = 5%.
    assert row["sl_dist_pct"] == 5.0


def test_live_row_flags_unprotected_when_no_sl_order_but_stop_set():
    row = ug._live_position_row(_live(sl_order_id=None, stop_loss=95.0))
    assert row["sl_order"] == "manual"
    assert row["sl_protected"] is False
    assert row["unprotected"] is True          # live + stop set + no exchange order = real risk


def test_live_row_respects_runtime_unprotected_marker():
    pos = _live(sl_order_id="abc")
    setattr(pos, "unprotected", True)          # escalation path marked it
    row = ug._live_position_row(pos)
    assert row["unprotected"] is True


# ── PAPER serializer: bot-managed, never an "unprotected" alarm ──────────────

def _paper():
    from enum import Enum

    class D(Enum):
        LONG = "LONG"
    return SimpleNamespace(
        trade_id="t1", asset="ETH/USDT:USDT", direction=D.LONG,
        entry_price=100.0, exit_price=None, quantity=2.0, stop_loss=90.0,
        take_profit=120.0, leverage=5.0, pnl=0.0, commission=0.0,
        strategy_type="swing", opened_at=None, closed_at=None)


def test_paper_row_is_bot_managed_not_unprotected():
    row = ug._paper_position_row(_paper())
    # No exchange in paper — the stop is bot-managed in-sim, which is truthful,
    # NOT the red "unprotected" alarm (that only means a LIVE missing stop).
    assert row["sl_order"] == "manual"
    assert row["sl_protected"] is False
    assert row["unprotected"] is False
    assert row["pair"] == "ETH"
    assert row["sl_dist_pct"] == 10.0


# ── handler contract: registered, read-only, gated, sl_order_id truth ────────

def test_positions_route_is_registered():
    src = inspect.getsource(ug.build_gateway)
    assert 'add_get("/positions", handle_positions)' in src


def test_positions_handler_is_read_only_and_uses_sl_order_truth():
    src = inspect.getsource(ug.handle_positions)
    assert '"read_only": True' in src
    # The VIEW reading, never the order-placement one: `_executor_for` falls
    # back to the operator's executor for a caller with no linked keys, and
    # this panel then showed that caller the operator's positions. Driven in
    # tests/test_the_web_positions_panel_reads_the_callers_book.py.
    from tests.source_scan import code_only
    code = code_only(src)          # the comment above the read names the old call
    assert "viewer_executor" in code and "_executor_for" not in code
    assert "unprotected_count" in src
    # No order placement / position-close call in the read path (the docstring
    # says it "closes" nothing; assert on actual method calls, not prose).
    assert "place_order" not in src
    assert "close_position" not in src and ".close(" not in src


# ── book_read: an empty list is not a reading of a flat book ────────────────
#
# `positions: []` used to be the answer for three different facts -- a flat
# account, an executor that could not be resolved, and an executor with no
# book -- and the website rendered every one of them as "No open positions".
# The list stays [] (an older client keeps working); `book_read` says which.
# Driven through the real handler with a mocked request, because the flag is
# decided by a branch a source scan cannot see the reachability of.


def _drive_positions(monkeypatch, *, live: bool, executor, tracker=None):
    app = web.Application()
    # `viewer_executor` ONLY: a handler that went back to `_executor_for`
    # raises on this stand-in and every drive below fails.
    app["engine"] = SimpleNamespace(
        viewer_executor=lambda tg_id: executor,
        user_portfolios=SimpleNamespace(get=lambda tg_id: tracker),
    )
    app["tg_handler"] = SimpleNamespace(users=SimpleNamespace(register=lambda *a, **k: None))
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **k: None)
    # CONFIG is a frozen dataclass: replace the module attribute the handler
    # reads, not a field on the frozen object.
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(is_live=lambda: live))
    req = make_mocked_request("GET", "/positions?telegram_id=123456", app=app)
    resp = asyncio.run(ug.handle_positions(req))
    return resp.status, json.loads(resp.text)


def test_a_live_executor_that_cannot_be_resolved_is_not_a_flat_book(monkeypatch):
    status, body = _drive_positions(monkeypatch, live=True, executor=None)
    assert status == 200
    assert body["positions"] == []
    assert body["count"] == 0
    assert body["live"] is True
    assert body["book_read"] is False, "no executor means nobody read a book"


def test_an_executor_with_no_book_attribute_is_not_a_flat_book(monkeypatch):
    status, body = _drive_positions(monkeypatch, live=True, executor=SimpleNamespace())
    assert status == 200
    assert body["positions"] == []
    assert body["book_read"] is False


def test_a_live_executor_with_an_empty_book_IS_a_read_flat_book(monkeypatch):
    status, body = _drive_positions(monkeypatch, live=True,
                                    executor=SimpleNamespace(open_positions=[]))
    assert status == 200
    assert body["positions"] == []
    assert body["book_read"] is True, "an empty list the executor holds is a measured, flat book"


def test_a_live_book_with_a_position_is_read_and_serialised(monkeypatch):
    status, body = _drive_positions(monkeypatch, live=True,
                                    executor=SimpleNamespace(open_positions=[_live()]))
    assert status == 200
    assert body["book_read"] is True
    assert body["count"] == 1
    assert body["positions"][0]["symbol"] == "BTC/USDT:USDT"


def test_the_paper_tracker_is_a_read_book(monkeypatch):
    status, body = _drive_positions(monkeypatch, live=False, executor=None,
                                    tracker=SimpleNamespace(open_positions=[_paper()]))
    assert status == 200
    assert body["live"] is False
    assert body["book_read"] is True
    assert body["count"] == 1


def test_a_raising_read_is_still_a_503_and_never_a_read_empty_book(monkeypatch):
    class Boom:
        @property
        def open_positions(self):
            raise RuntimeError("venue down")
    status, body = _drive_positions(monkeypatch, live=True, executor=Boom())
    assert status == 503
    assert body == {"error": "positions_unavailable"}

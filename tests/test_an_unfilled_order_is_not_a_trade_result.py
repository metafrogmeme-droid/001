"""A limit that never filled was published as a trade result.

The executor's pending-order paths hand the engine a message when a resting
order ends: expired, cancelled on drift, cancelled or rejected by the venue,
force-closed with its fill unread, or a market fallback refused after a drift
cancel. The engine routed every one of them as a CLOSE, because the only
kinds it knew were fill, sync and close. Driven through the real close door:

  * the operator's card was headed "⚪ Closed" over an order that never
    became a position, and the transcript recorded TRADE_CLOSED;
  * the chain audited "Live position auto-closed";
  * the message went to the PUBLIC channels as "TRADE CLOSED ... #TradeResult";
  * and with an earlier close of the same symbol in the last-close slot (a
    SOL trade closed an hour before a SOL limit expired), the card and the
    public post were THAT close, re-published as a new win:
    "🟢 SOLUSDT LONG closed (TP HIT)".

The partial-fill adoption was the opposite mistake: "LIMIT EXPIRED —
PARTIAL FILL ADOPTED as OPEN" OPENS a position (the filled part, with the
idea's stop and target), and its first line begins like a message that opens
nothing, so it was routed as a close too.

`order_state.unfilled_order_heading` is the one reading: a heading and an
icon for each message that ended an order without a position, None for
everything else. The engine audits those as UNFILLED, the close door heads
them for what they are, records ORDER_NOT_FILLED, wears no card and never
publishes them, and `_is_fill_message` reads the adoption as a fill. The
walk below takes every message the pending-order paths return and requires
each to be read as exactly one kind, so a message added later cannot fall to
"close" by default.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import time
from pathlib import Path

import pytest

import bot.core.engine as engine_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.order_state import (
    PARTIAL_FILL_ADOPTED,
    UNFILLED_ORDER_HEADINGS,
    close_card_is_wrong,
    unfilled_order_heading,
)
from bot.skills import alerts_monitor
from tests.test_a_close_reaches_whoever_holds_the_position import _Ex
from tests.test_the_time_stop_alert_reads_the_plan import _close

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "bot" / "core" / "live_executor.py"

EXPIRED = "LIMIT EXPIRED: LONG SOL/USDT — cancelled after 4.0h"
DRIFT = "LIMIT CANCELLED (price drift): LONG SOL/USDT — market moved away"
VENUE_CANCEL = "LIMIT CANCELED: LONG SOL/USDT — order not filled"
STALE = ("STALE PENDING CLOSED: LONG SOL/USDT -- stuck for 8.1h (hard timeout "
         "8.0h). Its final fill was NEVER READ: order_unreadable. Nothing is "
         "booked as filled; the venue may still hold a resting order or a "
         "filled position this record does not track -- review it there.")
REFUSED = ("⛔ Limit order for SOL/USDT cancelled on drift, but the market "
           "fallback was REFUSED — the engine is halted or a circuit breaker "
           "is open. ")
ADOPTED = ("LIMIT EXPIRED — PARTIAL FILL ADOPTED as OPEN: LONG SOL/USDT\n"
           "Fill: $100.0000 | Qty: 0.500000")
CLOSE = "SOL/USDT LONG closed\nPnL: +$5.00 (+1.20%)"
EARLIER_SOL = {"symbol": "SOL/USDT", "direction": "LONG", "pnl_usd": 12.34,
               "pnl_pct": 2.1, "reason": "TP HIT", "entry": 100.0,
               "exit": 102.1, "margin_usd": 50.0, "leverage": 5}


def _kind(msg: str) -> str:
    """The route the engine's monitor loop takes for ``msg``, in its order."""
    if RuneClawEngine._is_fill_message(msg):
        return "fill"
    if RuneClawEngine._is_sync_message(msg):
        return "sync"
    if RuneClawEngine._is_unfilled_order_message(msg):
        return "unfilled"
    if RuneClawEngine._is_kept_open_message(msg):
        return "kept_open"
    return "close"


# ── the reading ──────────────────────────────────────────────────────────

class TestTheReading:

    @pytest.mark.parametrize("msg,heading", [
        (EXPIRED, "Order not filled"),
        (DRIFT, "Order not filled"),
        (VENUE_CANCEL, "Order not filled"),
        ("LIMIT CANCELLED: LONG SOL/USDT — order not filled", "Order not filled"),
        ("LIMIT REJECTED: LONG SOL/USDT — order not filled", "Order not filled"),
        (STALE, "Order fill not read"),
        (REFUSED, "Market fallback refused"),
    ])
    def test_each_unfilled_message_has_its_heading(self, msg, heading):
        got = unfilled_order_heading(msg)
        assert got is not None and got[1] == heading

    def test_a_partial_fill_adoption_opened_a_position(self):
        assert unfilled_order_heading(ADOPTED) is None
        assert unfilled_order_heading(
            "LIMIT CANCELED — PARTIAL FILL ADOPTED as OPEN: SHORT X") is None

    @pytest.mark.parametrize("msg", [
        CLOSE, "LIMIT FILLED: LONG SOL/USDT [SWING]\nFill: $100",
        "LIMIT → MARKET FALLBACK: LONG SOL/USDT", "", None, 7,
    ])
    def test_everything_else_is_not_an_unfilled_order(self, msg):
        assert unfilled_order_heading(msg) is None

    def test_the_first_line_must_begin_with_it(self):
        """Keyed by how the line BEGINS: a close whose first line mentions an
        expiry is a close."""
        assert unfilled_order_heading(
            "CLOSED LONG SOL/USDT (TIME STOP) after a LIMIT EXPIRED retry") is None

    def test_only_the_first_line_is_read(self):
        """A close whose text quotes an expiry further down is a close, and an
        expiry whose text quotes an adoption further down is still an expiry:
        the adoption marker is read on the first line, where `_is_fill_message`
        reads it. (For the prefixes alone, `startswith` on the whole text is the
        same test; the marker is where the first line decides.)"""
        assert unfilled_order_heading(CLOSE + "\n" + EXPIRED) is None
        quoting = EXPIRED + "\n(an earlier order: " + PARTIAL_FILL_ADOPTED + ")"
        assert unfilled_order_heading(quoting) is not None
        assert not RuneClawEngine._is_fill_message(quoting)

    def test_an_unfilled_message_wears_no_close_card(self):
        for msg in (EXPIRED, DRIFT, VENUE_CANCEL, STALE, REFUSED):
            assert close_card_is_wrong(msg), msg
        assert not close_card_is_wrong(CLOSE)

    def test_the_stale_and_refused_messages_do_not_say_not_filled(self):
        """A stale row's fill was never READ and a refused fallback may sit
        beside a partial fill: "not filled" would be a claim about both."""
        heads = {p: h for p, _, h in UNFILLED_ORDER_HEADINGS}
        assert heads["STALE PENDING CLOSED"] != "Order not filled"
        assert heads["⛔ Limit order for"] != "Order not filled"


# ── every message the pending-order paths return, read as one kind ──────

_PATHS = ("_check_pending_limit", "_adopt_partial_fill",
          "_force_close_stale_pending", "_execute_drift_market_fallback",
          "_reconcile_unverified_submissions")


def _skeleton(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}"
                       for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _skeleton(node.left), _skeleton(node.right)
        return None if left is None or right is None else left + right
    return None


def _messages() -> set:
    """The literal skeleton of every message the paths return or append."""
    tree = ast.parse(EXECUTOR.read_text())
    out = set()
    for fn in ast.walk(tree):
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and fn.name in _PATHS):
            continue
        for n in ast.walk(fn):
            val = None
            if isinstance(n, ast.Return) and n.value is not None:
                val = n.value
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "append" and n.args):
                val = n.args[0]
            sk = None if val is None else _skeleton(val)
            if sk is not None:
                out.add(sk.split("\n", 1)[0][:40])
    return out


#: Every first-line skeleton the pending-order paths produce, each with
#: samples of what it renders to and the route that sample must take.
KINDS = {
    "SUBMISSION: {} {} — the order sent at {} ({} on s": [
        ("SUBMISSION: LONG SOL/USDT — the order sent at 10:00 never landed", "sync")],
    "LIMIT FILLED: {} {} [{}]": [
        ("LIMIT FILLED: LONG SOL/USDT [SWING]\nFill: $100", "fill")],
    "LIMIT {}: {} {} — order not filled": [
        (f"LIMIT {s}: LONG SOL/USDT — order not filled", "unfilled")
        for s in ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED")],
    "LIMIT CANCELLED (price drift): {} {} — market move": [(DRIFT, "unfilled")],
    "LIMIT EXPIRED: {} {} — cancelled after {}h": [(EXPIRED, "unfilled")],
    "LIMIT EXPIRED: {} {} — your ticket rested {}h, the": [
        ("LIMIT EXPIRED: LONG SOL/USDT — your ticket rested 24.1h", "unfilled")],
    "STALE PENDING CLOSED: {} {} -- stuck for {}h (hard": [(STALE, "unfilled")],
    "LIMIT {} — PARTIAL FILL ADOPTED as OPEN: {} {}": [
        (f"LIMIT {c} — {PARTIAL_FILL_ADOPTED}: LONG SOL/USDT\nFill: $1", "fill")
        for c in ("EXPIRED", "PRICE_DRIFT", "CANCELED", "CANCELLED", "REJECTED")],
    "LIMIT → MARKET FALLBACK: {} {}": [
        ("LIMIT → MARKET FALLBACK: LONG SOL/USDT\nOriginal limit: $100", "fill")],
    "⛔ Limit order for {} cancelled on drift, but the ": [(REFUSED, "unfilled")],
}


class TestEveryPendingOrderMessageIsReadAsOneKind:

    def test_the_table_is_every_message_the_paths_return(self):
        found = _messages()
        named = {k[:40] for k in KINDS}
        assert found == named, (
            "a pending-order path returns a message this table does not name "
            "(or a named one is gone); add it with the route it must take:\n"
            f"new: {sorted(found - named)}\ngone: {sorted(named - found)}")

    @pytest.mark.parametrize("sample,kind", [
        row for rows in KINDS.values() for row in rows],
        ids=lambda v: v if isinstance(v, str) and len(v) < 12 else None)
    def test_each_message_takes_its_route(self, sample, kind):
        assert _kind(sample) == kind, sample

    def test_no_pending_order_message_is_read_as_a_close(self):
        kinds = {k for rows in KINDS.values() for _, k in rows}
        assert "close" not in kinds


# ── the close door ───────────────────────────────────────────────────────

@pytest.fixture
def operator_chat():
    from tests.test_the_scheduled_posts_say_whose_book_they_read import OPERATOR
    original = CONFIG.telegram
    object.__setattr__(CONFIG, "telegram", dataclasses.replace(
        original, chat_id=OPERATOR, admin_ids=""))
    yield OPERATOR
    object.__setattr__(CONFIG, "telegram", original)


class TestTheCloseDoor:
    """The real `start_monitor` and the real forwarder, through the time-stop
    suite's harness: `w.bot.public()` is what reached the public channels."""

    @pytest.mark.parametrize("msg", [EXPIRED, DRIFT, VENUE_CANCEL, STALE, REFUSED])
    def test_an_unfilled_order_is_never_published(self, operator_chat, msg):
        assert _close(msg).bot.public() == []

    def test_the_card_names_what_happened(self, operator_chat):
        [(text, _)] = _close(EXPIRED).bot.to(operator_chat)
        assert text.startswith("\u23f9\ufe0f <b>Order not filled</b>"), text
        assert "Closed" not in text.split("\n", 1)[0]
        assert EXPIRED in text

    def test_the_transcript_records_the_order_not_a_close(self, operator_chat):
        w = _close(STALE)
        assert [kind for _, kind, _ in w.recorded] == ["ORDER_NOT_FILLED"]

    def test_an_earlier_close_of_the_symbol_is_not_republished(self, operator_chat):
        """The live defect: the slot's SOL close, printed and posted again as
        a new win over a SOL limit that expired."""
        w = _close(EXPIRED, slot=dict(EARLIER_SOL))
        assert w.bot.public() == []
        [(text, photo)] = w.bot.to(operator_chat)
        assert photo is None, "no close card over an order that never filled"
        assert "TP" not in text and "12.34" not in text

    def test_the_slot_card_was_what_the_old_door_published(self, operator_chat):
        """The control the previous test needs: the same slot with a real SOL
        close text DOES wear the card and IS published, so the refusal above
        is the reading and not a harness that never renders one."""
        w = _close(CLOSE, slot=dict(EARLIER_SOL))
        assert len(w.bot.public()) == 1

    def test_the_owner_door_heads_it_the_same_way(self, operator_chat):
        w = _close(DRIFT, door="owner")
        [(text, _)] = w.bot.to("777")
        assert text.startswith("\u23f9\ufe0f <b>Order not filled</b>"), text
        assert w.bot.public() == []

    def test_a_real_close_is_still_published(self, operator_chat):
        w = _close(CLOSE)
        assert len(w.bot.public()) == 1
        assert [kind for _, kind, _ in w.recorded] == ["TRADE_CLOSED"]


# ── the engine's loop ────────────────────────────────────────────────────

@pytest.fixture
def live_operator():
    orig_live = type(engine_mod.CONFIG).is_live
    orig_sync = engine_mod.sync_portfolio_with_exchange
    type(engine_mod.CONFIG).is_live = lambda self: True

    async def _no_sync(_eng):
        return []

    engine_mod.sync_portfolio_with_exchange = _no_sync
    try:
        yield
    finally:
        type(engine_mod.CONFIG).is_live = orig_live
        engine_mod.sync_portfolio_with_exchange = orig_sync


class TestTheEnginesLoop:

    def test_each_message_reaches_its_door_and_its_audit(self, live_operator, monkeypatch):
        real = RuneClawEngine()
        heard: list = []
        for name in ("close", "fill", "sync"):
            async def _cb(msg, _n=name):
                heard.append((_n, msg))
            setattr(real, f"_{name}_notify_callback", _cb)
        audits: list = []
        real_audit = engine_mod.audit

        def _spy(log, msg, **kw):
            audits.append((kw.get("action"), kw.get("result"), msg))
            return real_audit(log, msg, **kw)

        monkeypatch.setattr(engine_mod, "audit", _spy)
        real.live_executor = _Ex(None, checked=[EXPIRED, ADOPTED, CLOSE])
        real._last_sltp_verify_ts = time.monotonic()
        asyncio.run(real._check_open_positions())
        assert heard == [("close", EXPIRED), ("fill", ADOPTED), ("close", CLOSE)]
        mine = [(a, r) for a, r, m in audits
                if a in ("live_auto_close", "limit_order_ended", "limit_fill_notify")]
        assert mine == [("limit_order_ended", "UNFILLED"),
                        ("limit_fill_notify", "FILLED"),
                        ("live_auto_close", "CLOSED")]


def test_the_close_door_reads_the_one_reading():
    """A scan, stated as one: `_deliver_close` is a closure inside
    `start_monitor`, and the drives above prove what it does; this pins that
    the heading comes from `order_state` rather than a copy of its prefixes."""
    src = inspect.getsource(alerts_monitor)
    assert "unfilled = unfilled_order_heading(msg)" in src
    assert '"LIMIT EXPIRED"' not in src
